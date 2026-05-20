from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from building3d.artifacts import artifact_names
from building3d.batch import derive_building_config, load_inventory
from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.discovery import BuildingInventoryRecord
from building3d.geometry import (
    MeshData,
    dataset_meshes,
    floor_visual_meshes_from_meshes,
    navigation_meshes_from_meshes,
    visual_meshes_from_meshes,
)
from building3d.gltf import write_glb
from building3d.manifest import build_manifest, refresh_generation_hash, write_manifest
from building3d.mapsindoors import fetch_source_data, load_raw_locations, source_urls
from building3d.normalize import FloorRecord, NormalizedDataset, normalize_locations
from building3d.projection import project_dataset
from building3d.unimate import write_unimate_scene


def generate_group(
    solution_config: SolutionConfig,
    group: BuildingGroupConfig,
    *,
    records: list[BuildingInventoryRecord] | None = None,
    fetch_missing: bool = True,
) -> dict[str, Any]:
    records = records or load_inventory(solution_config)
    member_records = _member_records(group, records)
    if len(member_records) != len(group.members):
        found = {record.admin_id for record in member_records}
        missing = [member for member in group.members if member not in found]
        raise ValueError(f"Missing inventory records for group {group.id}: {', '.join(missing)}")

    origin_lon, origin_lat = _group_origin(group, member_records)
    normalized = _combine_member_datasets(solution_config, group, member_records, fetch_missing=fetch_missing)
    remapped = _remap_group_floors(normalized)
    floor_heights = _floor_heights(remapped.floors, solution_config.default_floor_spacing, solution_config.basement_floor_spacing)
    projected = project_dataset(remapped, origin_lon, origin_lat, floor_heights)

    names = artifact_names(group.id)
    processed_dir = solution_config.processed_root / "groups" / group.id
    export_dir = solution_config.export_root / "groups" / group.id
    processed_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    meshes = dataset_meshes(projected)
    manifest = _build_group_manifest(projected, group, member_records, processed_dir, export_dir)
    floor_visual_files = _write_floor_visual_glbs(meshes, manifest.get("floors", []), export_dir, group.id)
    manifest["assets"] = {
        "visual_glb": names.visual_glb,
        "nav_glb": names.nav_glb,
        "floor_visual_glbs": [
            {
                "floor_index": int(floor.get("floor_index", 0)),
                "floor_name": str(floor.get("floor_name", "")),
                "filename": floor_visual_files[int(floor.get("floor_index", 0))],
            }
            for floor in sorted(manifest.get("floors", []), key=lambda item: int(item.get("floor_index", 0)))
            if int(floor.get("floor_index", 0)) in floor_visual_files
        ],
    }
    manifest = refresh_generation_hash(manifest)
    floor_visual_paths = {
        floor_index: f"{_unimate_asset_base(group)}/{filename}"
        for floor_index, filename in floor_visual_files.items()
    }

    _write_json(processed_dir / "dataset.json", projected.to_dict())
    _write_json(processed_dir / "geometry.json", [mesh.to_dict() for mesh in meshes])
    if manifest.get("external_doors"):
        _write_json(processed_dir / "external_doors.json", manifest["external_doors"])
        _write_json(export_dir / "external_doors.json", manifest["external_doors"])
    write_manifest(manifest, processed_dir / names.manifest)
    write_glb(visual_meshes_from_meshes(meshes), export_dir / names.visual_glb)
    write_glb(navigation_meshes_from_meshes(meshes), export_dir / names.nav_glb)
    write_manifest(manifest, export_dir / names.manifest)
    scene_path = write_unimate_scene(
        manifest,
        export_dir / f"{group.id}_unimate.tscn",
        asset_base_path=_unimate_asset_base(group),
        navigation_meshes=meshes,
        floor_visual_paths=floor_visual_paths,
    )
    _write_group_readme(export_dir, group, names, scene_path.name, manifest)

    return {
        "group_id": group.id,
        "rooms": len(manifest.get("rooms", [])),
        "floors": len(manifest.get("floors", [])),
        "portals": len(manifest.get("portals", [])),
        "external_doors": len(manifest.get("external_doors", [])),
        "export_dir": str(export_dir),
        "artifacts": {
            "visual_glb": str(export_dir / names.visual_glb),
            "nav_glb": str(export_dir / names.nav_glb),
            "floor_visual_glbs": [str(export_dir / filename) for filename in floor_visual_files.values()],
            "manifest": str(export_dir / names.manifest),
            "scene": str(scene_path),
            "readme": str(export_dir / names.readme),
        },
        "generation_hash": manifest["generation_hash"],
        "warnings": manifest.get("warnings", []),
    }


def _member_records(group: BuildingGroupConfig, records: list[BuildingInventoryRecord]) -> list[BuildingInventoryRecord]:
    by_admin = {record.admin_id: record for record in records}
    return [by_admin[member] for member in group.members if member in by_admin]


def _group_origin(group: BuildingGroupConfig, records: list[BuildingInventoryRecord]) -> tuple[float, float]:
    primary = next((record for record in records if record.admin_id == group.primary_member), None)
    if primary and len(primary.origin) >= 2:
        return float(primary.origin[0]), float(primary.origin[1])
    origins = [record.origin for record in records if len(record.origin) >= 2]
    if not origins:
        return 0.0, 0.0
    lon = sum(float(origin[0]) for origin in origins) / len(origins)
    lat = sum(float(origin[1]) for origin in origins) / len(origins)
    return lon, lat


def _combine_member_datasets(
    solution_config: SolutionConfig,
    group: BuildingGroupConfig,
    records: list[BuildingInventoryRecord],
    *,
    fetch_missing: bool,
) -> NormalizedDataset:
    rooms = []
    portals = []
    warnings = []
    source_urls_seen: set[str] = set()
    for record in records:
        config = derive_building_config(solution_config, record)
        raw_locations = load_raw_locations(config.raw_dir)
        if not raw_locations and fetch_missing:
            fetch_source_data(config)
            raw_locations = load_raw_locations(config.raw_dir)
        if not raw_locations:
            raise ValueError(f"No raw locations for group member {record.admin_id} at {config.raw_dir}")
        dataset = normalize_locations(
            raw_locations,
            building_admin_id=record.admin_id,
            building_id=group.id,
            building_name=record.display_name,
        )
        rooms.extend(dataset.rooms)
        portals.extend(dataset.portals)
        warnings.extend(f"{record.admin_id}: {warning}" for warning in dataset.warnings)
        source_urls_seen.update(source_urls(config))

    combined = NormalizedDataset(
        building_id=group.id,
        building_admin_id=",".join(group.members),
        building_name=group.display_name,
        rooms=rooms,
        portals=portals,
        warnings=warnings,
    )
    combined.source_urls = sorted(source_urls_seen)  # type: ignore[attr-defined]
    return combined


def _remap_group_floors(dataset: NormalizedDataset) -> NormalizedDataset:
    canonical_names = sorted(
        {
            _canonical_floor_name(record.floor_name)
            for record in [*dataset.rooms, *dataset.portals]
            if str(record.floor_name).strip()
        },
        key=_floor_sort_key,
    )
    index_by_name = {name: index for index, name in enumerate(canonical_names)}
    floors = [
        FloorRecord(floor_name=name, floor_index=index)
        for name, index in index_by_name.items()
    ]
    rooms = [
        replace(room, floor_name=_canonical_floor_name(room.floor_name), floor_index=index_by_name[_canonical_floor_name(room.floor_name)])
        for room in dataset.rooms
    ]
    portals = [
        replace(portal, floor_name=_canonical_floor_name(portal.floor_name), floor_index=index_by_name[_canonical_floor_name(portal.floor_name)])
        for portal in dataset.portals
    ]
    return NormalizedDataset(
        building_id=dataset.building_id,
        building_admin_id=dataset.building_admin_id,
        building_name=dataset.building_name,
        floors=floors,
        rooms=rooms,
        portals=portals,
        warnings=list(dataset.warnings),
    )


def _canonical_floor_name(value: str) -> str:
    clean = str(value).strip().upper()
    if clean in {"", "NONE"}:
        return "G"
    if clean in {"0", "G", "GROUND", "LEVEL 0"}:
        return "G"
    if clean == "B":
        return "B-1"
    return clean


def _floor_sort_key(value: str) -> tuple[float, str]:
    clean = _canonical_floor_name(value)
    if clean.startswith("B-"):
        try:
            return (-float(clean[2:]), clean)
        except ValueError:
            return (-1.0, clean)
    if clean == "G":
        return (0.0, clean)
    if clean.startswith("M") and clean[1:].isdigit():
        return (float(clean[1:]) + 0.5, clean)
    try:
        return (float(clean), clean)
    except ValueError:
        return (10_000.0, clean)


def _floor_heights(floors: list[FloorRecord], default_spacing: float, basement_spacing: float) -> dict[str, float]:
    heights: dict[str, float] = {}
    for floor in floors:
        label = floor.floor_name
        sort_value = _floor_sort_key(label)[0]
        if label.startswith("B-"):
            heights[label] = round(sort_value * basement_spacing, 6)
        elif label == "G":
            heights[label] = 0.0
        else:
            heights[label] = round(sort_value * default_spacing, 6)
    return heights


def _build_group_manifest(
    dataset: NormalizedDataset,
    group: BuildingGroupConfig,
    records: list[BuildingInventoryRecord],
    processed_dir: Path,
    export_dir: Path,
) -> dict[str, Any]:
    urls = []
    for record in records:
        urls.extend(record.source_urls)
    urls = sorted({url for url in urls if url})
    manifest = build_manifest(dataset, urls)
    manifest["schema_version"] = 2
    manifest["building"].update(
        {
            "kind": "logical_group",
            "members": list(group.members),
            "aliases": list(group.aliases),
        }
    )
    manifest["building_aliases"] = {
        _alias_key(alias): group.id
        for alias in [group.id, group.display_name, *group.members, *group.aliases]
        if _alias_key(alias)
    }
    manifest["member_buildings"] = [
        {
            "admin_id": record.admin_id,
            "slug": record.slug,
            "display_name": record.display_name,
            "mapsindoors_id": record.mapsindoors_id,
            "external_id": record.external_id,
        }
        for record in records
    ]
    external_doors = _load_external_doors(processed_dir, export_dir, group, manifest.get("floors", []))
    if external_doors:
        manifest["external_doors"] = external_doors
    _dedupe_node_names(manifest)
    _sync_nav_node_names(manifest)
    return refresh_generation_hash(manifest)


def _load_external_doors(
    processed_dir: Path,
    export_dir: Path,
    group: BuildingGroupConfig,
    floors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = [
        processed_dir / "external_doors.json",
        export_dir / "external_doors.json",
        export_dir / f"{group.id}_external_entry_points_route_derived.json",
    ]
    source_path = next((path for path in candidates if path.exists()), None)
    if source_path is None:
        return []
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    floor_index_by_name = {
        str(floor.get("floor_name", "")).strip().upper(): int(floor.get("floor_index", 0))
        for floor in floors
    }
    records = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        normalized = _normalise_external_door(item, index, group, floor_index_by_name)
        if normalized:
            records.append(normalized)
    records.sort(key=lambda item: (int(item.get("floor_index", 0)), str(item.get("external_id", ""))))
    return records


def _normalise_external_door(
    item: dict[str, Any],
    index: int,
    group: BuildingGroupConfig,
    floor_index_by_name: dict[str, int],
) -> dict[str, Any] | None:
    anchor = item.get("anchor") or item.get("local") or item.get("door_local")
    if not _valid_local_anchor(anchor):
        return None

    entry_id = str(item.get("entry_id") or item.get("external_id") or f"{group.id}_entry_{index:03d}")
    floor_name = _canonical_floor_name(str(item.get("floor_name") or "G"))
    floor_index = item.get("floor_index")
    if floor_index is None:
        floor_index = floor_index_by_name.get(floor_name.upper(), 0)
    node_name = str(item.get("node_name") or _external_door_node_name(index))
    display_name = str(item.get("display_name") or ("Main entrance" if index == 1 else f"Entry {index}"))
    aliases = _external_door_aliases(group, entry_id, node_name, display_name, index)
    return {
        "external_id": entry_id,
        "entry_id": entry_id,
        "display_name": display_name,
        "floor_index": int(floor_index),
        "floor_name": floor_name,
        "kind": "door",
        "logical_building_id": group.id,
        "node_name": node_name,
        "anchor": [float(anchor[0]), float(anchor[1]), float(anchor[2])],
        "aliases": aliases,
        "source": str(item.get("source") or "external_entry_points"),
        "confidence": str(item.get("confidence") or "unknown"),
        "supporting_routes": int(item.get("supporting_routes") or 0),
        "source_floor": item.get("source_floor"),
        "lon": item.get("lon"),
        "lat": item.get("lat"),
        "target_building_admin_ids": list(item.get("target_building_admin_ids") or []),
        "target_external_ids": list(item.get("target_external_ids") or []),
        "source_building_admin_id": ",".join(group.members),
        "source_id": entry_id,
    }


def _external_door_node_name(index: int) -> str:
    if index == 1:
        return "MainDoor"
    return f"Door{index}"


def _external_door_aliases(
    group: BuildingGroupConfig,
    entry_id: str,
    node_name: str,
    display_name: str,
    index: int,
) -> list[str]:
    values = [
        entry_id,
        node_name,
        display_name,
        f"{group.display_name} {display_name}",
        f"{group.id} {display_name}",
        f"{group.id} entry {index}",
        f"{group.id} door {index}",
    ]
    if index == 1:
        values.extend(
            [
                f"{group.id} main entrance",
                f"{group.id} entrance",
                f"{group.display_name} main entrance",
            ]
        )
    return _dedupe_strings(values)


def _valid_local_anchor(anchor: Any) -> bool:
    return isinstance(anchor, list) and len(anchor) >= 3 and all(isinstance(value, int | float) for value in anchor[:3])


def _dedupe_strings(values: list[str]) -> list[str]:
    result = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        key = clean.lower()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
    return result


def _dedupe_node_names(manifest: dict[str, Any]) -> None:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for record in [*manifest.get("rooms", []), *manifest.get("portals", []), *manifest.get("external_doors", [])]:
        node_name = str(record.get("node_name", ""))
        by_name.setdefault(node_name, []).append(record)
    for duplicates in by_name.values():
        if len(duplicates) <= 1:
            continue
        ordered = sorted(duplicates, key=lambda item: (int(item.get("floor_index", 0)), str(item.get("source_id", ""))))
        for index, record in enumerate(ordered[1:], start=2):
            suffix = str(record.get("source_id", ""))[:8] or f"floor{record.get('floor_index', index)}"
            record["node_name"] = _deduped_node_name(str(record["node_name"]), suffix)


def _sync_nav_node_names(manifest: dict[str, Any]) -> None:
    nav = manifest.get("nav", {})
    room_nodes_by_source = {
        str(room.get("source_id")): room.get("node_name")
        for room in manifest.get("rooms", [])
        if room.get("source_id") and room.get("node_name")
    }
    for target in nav.get("room_targets", []):
        node_name = room_nodes_by_source.get(str(target.get("source_id")))
        if node_name:
            target["node_name"] = node_name

    portal_nodes_by_source = {
        str(portal.get("source_id")): portal.get("node_name")
        for portal in manifest.get("portals", [])
        if portal.get("source_id") and portal.get("node_name")
    }
    for link in nav.get("links", []):
        from_node = portal_nodes_by_source.get(str(link.get("from_source_id")))
        to_node = portal_nodes_by_source.get(str(link.get("to_source_id")))
        if from_node:
            link["from_node_name"] = from_node
        if to_node:
            link["to_node_name"] = to_node
    nav["building_entries"] = [
        {
            "external_id": door.get("external_id", ""),
            "entry_id": door.get("entry_id", door.get("external_id", "")),
            "node_name": door.get("node_name", ""),
            "floor_index": door.get("floor_index", 0),
            "floor_name": door.get("floor_name", ""),
            "anchor": door.get("anchor"),
            "kind": "door",
            "bidirectional": True,
            "confidence": door.get("confidence", ""),
            "supporting_routes": door.get("supporting_routes", 0),
        }
        for door in manifest.get("external_doors", [])
        if door.get("anchor") and door.get("node_name")
    ]


def _deduped_node_name(node_name: str, suffix: str) -> str:
    set_match = re.search(r"_Set\w+$", node_name)
    if set_match:
        return f"{node_name[:set_match.start()]}__{suffix}{node_name[set_match.start():]}"
    return f"{node_name}__{suffix}"


def _alias_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _write_floor_visual_glbs(
    meshes: list[MeshData],
    floors: list[dict[str, Any]],
    export_dir: Path,
    group_id: str,
) -> dict[int, str]:
    floor_files: dict[int, str] = {}
    for floor in sorted(floors, key=lambda item: int(item.get("floor_index", 0))):
        floor_index = int(floor.get("floor_index", 0))
        floor_name = str(floor.get("floor_name", floor_index))
        floor_height = float(floor.get("height", 0.0))
        floor_meshes = floor_visual_meshes_from_meshes(meshes, floor_name, floor_height)
        if not floor_meshes:
            continue
        filename = _floor_visual_glb_name(group_id, floor_index)
        write_glb(floor_meshes, export_dir / filename)
        floor_files[floor_index] = filename
    return floor_files


def _floor_visual_glb_name(group_id: str, floor_index: int) -> str:
    if floor_index < 0:
        return f"{group_id}_floor_neg{abs(floor_index)}_visual.glb"
    return f"{group_id}_floor_{floor_index}_visual.glb"


def _unimate_asset_base(group: BuildingGroupConfig) -> str:
    return f"res://Assets/Buildings/{''.join(part.capitalize() for part in re.split(r'[^a-zA-Z0-9]+', group.id) if part)}"


def _write_group_readme(export_dir: Path, group: BuildingGroupConfig, names, scene_filename: str, manifest: dict[str, Any]) -> None:
    text = f"""# {group.display_name} Group Export

Generated by Building3D as a UNIMATE-ready logical building group.

## Logical Building

- ID: `{group.id}`
- Members: {", ".join(f"`{member}`" for member in group.members)}
- Floors: {len(manifest.get("floors", []))}
- Rooms: {len(manifest.get("rooms", []))}
- Portals: {len(manifest.get("portals", []))}
- External doors: {len(manifest.get("external_doors", []))}

## Files

- `{names.visual_glb}`: combined visual geometry.
- `{names.nav_glb}`: simplified navigation/anchor geometry.
- `{group.id}_floor_<index>_visual.glb`: per-floor visual geometry used by UNIMATE floor controls.
- `{names.manifest}`: group manifest with room nodes, aliases, portals, external doors, and provenance.
- `external_doors.json`: route-derived building entry/exit markers, when available.
- `{scene_filename}`: generated Godot scene matching UNIMATE's `BuildingController`/`FloorController` room-node contract.

## UNIMATE Import Target

Copy this package into UNIMATE later under:

```text
Godot/Assets/Buildings/{''.join(part.capitalize() for part in re.split(r'[^a-zA-Z0-9]+', group.id) if part)}/
```

The generated scene expects the visual GLBs under:

```text
res://Assets/Buildings/{''.join(part.capitalize() for part in re.split(r'[^a-zA-Z0-9]+', group.id) if part)}/
```

All generated room markers share logical `building_id = "{group.id}"` while preserving source room prefixes such as `301`, `302`, `303`, `303S`, and `305` in the node names.
"""
    (export_dir / names.readme).write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")

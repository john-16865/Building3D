from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from building3d.batch import load_inventory
from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.discovery import BuildingInventoryRecord
from building3d.geometry import MeshData, WallOpeningMap, _door_wall_openings, dataset_meshes, visual_meshes_from_meshes
from building3d.gltf import write_glb
from building3d.groups import (
    _build_group_manifest,
    _canonical_floor_name,
    _combine_member_datasets,
    _filter_dataset_floors,
    _filter_group_members,
    _floor_heights,
    _group_origin,
    _load_room_door_points,
    _member_records,
    _remap_group_floors,
    _route_clip_footprints_by_floor,
    _route_debug_centerline_meshes_from_cache,
    _route_endpoint_scope_records,
    _route_wall_blockers_by_floor,
    _route_wall_opening_stats,
    _route_wall_openings_from_cache,
)
from building3d.projection import project_dataset


DEBUG_STAGE_FILENAMES = {
    "raw_visual": "stage_01_raw_visual.glb",
    "door_points": "stage_02_door_points.glb",
    "route_lines": "stage_03_route_lines.glb",
    "wall_opening_candidates": "stage_04_wall_opening_candidates.glb",
    "wall_opened_visual": "stage_05_wall_opened_visual.glb",
    "combined_overlay": "stage_06_combined_overlay.glb",
    "summary": "stage_summary.json",
}


def export_group_debug_stages(
    solution_config: SolutionConfig,
    group: BuildingGroupConfig,
    *,
    records: list[BuildingInventoryRecord] | None = None,
    fetch_missing: bool = True,
    only_members: list[str] | None = None,
    only_floors: list[str] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    records = records or load_inventory(solution_config)
    if only_members:
        group = _filter_group_members(group, only_members)
    member_records = _member_records(group, records)
    if len(member_records) != len(group.members):
        found = {record.admin_id for record in member_records}
        missing = [member for member in group.members if member not in found]
        raise ValueError(f"Missing inventory records for group {group.id}: {', '.join(missing)}")

    origin_lon, origin_lat = _group_origin(group, member_records)
    normalized = _combine_member_datasets(solution_config, group, member_records, fetch_missing=fetch_missing)
    if only_floors:
        normalized = _filter_dataset_floors(normalized, only_floors)
    remapped = _remap_group_floors(normalized)
    floor_heights = _floor_heights(remapped.floors, solution_config.default_floor_spacing, solution_config.basement_floor_spacing)
    projected = project_dataset(remapped, origin_lon, origin_lat, floor_heights)

    processed_dir = solution_config.processed_root / "groups" / group.id
    export_dir = solution_config.export_root / "groups" / group.id
    debug_dir = output_dir or export_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    selected_floor_names = {_canonical_floor_name(str(floor.floor_name)) for floor in projected.floors}
    door_records = [
        record
        for record in _load_room_door_points(processed_dir, export_dir, group)
        if _canonical_floor_name(str(record.get("floor_name", ""))) in selected_floor_names
    ]
    raw_meshes = dataset_meshes(projected, door_openings=[])
    door_opened_meshes = dataset_meshes(projected, door_openings=door_records)
    door_wall_openings = _door_wall_openings(projected, door_records)

    wall_blockers_by_floor = _route_wall_blockers_by_floor(door_opened_meshes)
    manifest = _build_group_manifest(
        projected,
        group,
        member_records,
        processed_dir,
        export_dir,
        wall_blockers_by_floor=wall_blockers_by_floor,
    )
    clip_footprints = _route_clip_footprints_by_floor(door_opened_meshes, manifest.get("floors", []))
    route_endpoint_scope = _route_endpoint_scope_records(manifest)
    route_wall_openings = _route_wall_openings_from_cache(
        export_dir / "door_route_cache",
        manifest.get("floors", []),
        origin_lon,
        origin_lat,
        clip_footprints_by_floor=clip_footprints,
        route_endpoint_scope=route_endpoint_scope,
        wall_blockers_by_floor=wall_blockers_by_floor,
    )
    route_debug_meshes = _route_debug_centerline_meshes_from_cache(
        export_dir / "door_route_cache",
        manifest.get("floors", []),
        origin_lon,
        origin_lat,
        clip_footprints_by_floor=clip_footprints,
        route_endpoint_scope=route_endpoint_scope,
    )
    final_meshes = dataset_meshes(projected, door_openings=door_records, wall_openings_by_floor=route_wall_openings)

    floor_height_by_name = {
        str(floor.get("floor_name")): float(floor.get("height", 0.0))
        for floor in manifest.get("floors", [])
        if floor.get("floor_name") is not None
    }
    door_marker_meshes = door_point_marker_meshes(door_records)
    opening_marker_meshes = [
        *wall_opening_marker_meshes(door_wall_openings, floor_height_by_name, "door"),
        *wall_opening_marker_meshes(route_wall_openings, floor_height_by_name, "route"),
    ]

    outputs = {
        "raw_visual": debug_dir / DEBUG_STAGE_FILENAMES["raw_visual"],
        "door_points": debug_dir / DEBUG_STAGE_FILENAMES["door_points"],
        "route_lines": debug_dir / DEBUG_STAGE_FILENAMES["route_lines"],
        "wall_opening_candidates": debug_dir / DEBUG_STAGE_FILENAMES["wall_opening_candidates"],
        "wall_opened_visual": debug_dir / DEBUG_STAGE_FILENAMES["wall_opened_visual"],
        "combined_overlay": debug_dir / DEBUG_STAGE_FILENAMES["combined_overlay"],
        "summary": debug_dir / DEBUG_STAGE_FILENAMES["summary"],
    }

    raw_visual = visual_meshes_from_meshes(raw_meshes)
    final_visual = visual_meshes_from_meshes(final_meshes)
    write_glb(raw_visual, outputs["raw_visual"])
    write_glb([*raw_visual, *door_marker_meshes], outputs["door_points"])
    write_glb([*raw_visual, *route_debug_meshes], outputs["route_lines"])
    write_glb([*raw_visual, *opening_marker_meshes], outputs["wall_opening_candidates"])
    write_glb(final_visual, outputs["wall_opened_visual"])
    write_glb([*final_visual, *door_marker_meshes, *route_debug_meshes, *opening_marker_meshes], outputs["combined_overlay"])

    summary = {
        "group_id": group.id,
        "display_name": group.display_name,
        "members": list(group.members),
        "floors": len(manifest.get("floors", [])),
        "rooms": len(manifest.get("rooms", [])),
        "portals": len(manifest.get("portals", [])),
        "door_points": len(door_records),
        "route_line_meshes": len(route_debug_meshes),
        "door_wall_openings": _route_wall_opening_stats(door_wall_openings),
        "route_wall_openings": _route_wall_opening_stats(route_wall_openings),
        "files": {label: str(path) for label, path in outputs.items() if label != "summary"},
    }
    _write_json(outputs["summary"], summary)
    return {"debug_dir": str(debug_dir), "artifacts": {label: str(path) for label, path in outputs.items()}, **summary}


def door_point_marker_meshes(records: list[dict[str, Any]], *, size: float = 0.45) -> list[MeshData]:
    meshes: list[MeshData] = []
    for index, record in enumerate(records, start=1):
        point = record.get("door_local")
        if not _valid_point(point):
            continue
        confidence = str(record.get("confidence") or "unknown").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "unknown"
        x, y, z = float(point[0]), float(point[1]), float(point[2])
        s = size
        vertices = [
            [x, y + 0.08, z - s],
            [x + s, y + 0.08, z],
            [x, y + 0.08, z + s],
            [x - s, y + 0.08, z],
            [x, y + s * 1.8, z],
        ]
        meshes.append(
            MeshData(
                name=f"debug_door_point__{_safe_name(record.get('external_id'), index)}",
                vertices=vertices,
                faces=[[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4], [3, 2, 1, 0]],
                material=f"door_point_{confidence}",
                metadata={"debug_overlay": "door_point"},
            )
        )
    return meshes


def wall_opening_marker_meshes(
    openings: WallOpeningMap,
    floor_height_by_name: dict[str, float],
    kind: str,
) -> list[MeshData]:
    meshes: list[MeshData] = []
    material = "wall_open_route" if kind == "route" else "wall_open_door"
    y_offset = 0.16 if kind == "route" else 0.08
    for floor_name, edge_keys in sorted(openings.items()):
        base_y = floor_height_by_name.get(str(floor_name), 0.0)
        for index, edge_key in enumerate(sorted(edge_keys), start=1):
            (x1, z1), (x2, z2) = edge_key
            y = round(base_y + y_offset, 6)
            top_y = round(base_y + 1.55 + y_offset, 6)
            meshes.append(
                MeshData(
                    name=f"debug_wall_opening__{kind}__floor_{floor_name}__{index:04d}",
                    vertices=[
                        [float(x1), y, float(z1)],
                        [float(x2), y, float(z2)],
                        [float(x2), top_y, float(z2)],
                        [float(x1), top_y, float(z1)],
                    ],
                    faces=[[0, 1, 2, 3]],
                    material=material,
                    metadata={"debug_overlay": f"wall_opening_{kind}"},
                )
            )
    return meshes


def _valid_point(point: Any) -> bool:
    if not isinstance(point, (list, tuple)) or len(point) < 3:
        return False
    try:
        float(point[0])
        float(point[1])
        float(point[2])
    except (TypeError, ValueError):
        return False
    return True


def _safe_name(value: Any, fallback: int) -> str:
    text = str(value or "").strip()
    return text.replace("/", "_").replace("\\", "_").replace(" ", "_") or f"{fallback:04d}"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")

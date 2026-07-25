from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from shapely import constrained_delaunay_triangles
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from building3d.artifacts import artifact_names
from building3d.batch import derive_building_config, load_inventory
from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.discovery import BuildingInventoryRecord
from building3d.geometry import (
    MeshData,
    WallOpeningMap,
    dataset_meshes,
    floor_visual_meshes_from_meshes,
    localize_mesh_to_floor,
    mesh_floor_name,
    navigation_meshes_from_meshes,
    visual_meshes_from_meshes,
)
from building3d.gltf import write_glb
from building3d.manifest import build_manifest, refresh_generation_hash, write_manifest
from building3d.mapsindoors import fetch_source_data, load_raw_locations, source_urls
from building3d.normalize import FloorRecord, NormalizedDataset, normalize_locations
from building3d.portal_topology import build_portal_topology, write_portal_topology
from building3d.projection import LocalProjector, project_dataset
from building3d.unimate import write_unimate_scene
from building3d.vertical_links import DEFAULT_GRAPH_ID, MapsIndoorsRouteClient, apply_route_derived_vertical_links


ROUTE_NAV_CORRIDOR_RADIUS = 0.6
ROUTE_NAV_POINT_RADIUS = 0.6
ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE = 60.0
ROUTE_NAV_POINT_CONNECTOR_NEIGHBORS = 3
ROUTE_NAV_ROOM_PORTAL_CONNECTOR_NEIGHBORS = 3
ROUTE_NAV_PORTAL_CONNECTOR_NEIGHBORS = 8
ROUTE_NAV_COMPONENT_BRIDGE_MAX_DISTANCE = 125.0
ROUTE_NAV_ANCHOR_ENVELOPE_CELL_SIZE = 20.0
ROUTE_NAV_ANCHOR_ENVELOPE_MARGIN = 2.0
ROUTE_NAV_ANCHOR_ENVELOPE_MIN_CELLS = 4
ROUTE_NAV_GRID_CELL_SIZE = 0.5
ROUTE_NAV_GRID_MIN_CELL_COVERAGE = 0.02
ROUTE_NAV_SIMPLIFY = 0.03
ROUTE_NAV_TRIANGLE_MIN_AREA = 0.001
ROUTE_NAV_FOOTPRINT_CLIP_MARGIN = 2.0
ROUTE_NAV_FOOTPRINT_GAP_ROUTE_MAX_DISTANCE = 8.0
ROUTE_NAV_FOOTPRINT_GAP_ROUTE_MAX_LENGTH = 25.0
ROUTE_NAV_WALL_BLOCKER_CLEARANCE = 0.15
ROUTE_NAV_WALL_INTERSECTION_TOLERANCE = 0.05
ROUTE_NAV_TARGETED_POINT_CONNECTORS = {
    frozenset(("303-412", "301-407")),
    frozenset(("303-412", "301-437")),
    frozenset(("302-491", "302-459")),
    frozenset(("303S-400E4", "305-400C1")),
}
ROUTE_NAV_TARGETED_POINT_CONNECTOR_MAX_DISTANCE = 125.0
ROUTE_NAV_CUSTOM_CONNECTOR_SEGMENTS_BY_FLOOR = {
    # Building 302 floor 4: Godot's runtime nav graph truncates before the
    # lower 302 office wing unless this known corridor gap is made explicit.
    "4": [((20.5, 13.0), (4.031878, -27.795901))],
}
ROUTE_NAV_CUSTOM_CONNECTOR_RADIUS_MULTIPLIER = 1.5
ROUTE_NAV_CUSTOM_CONNECTOR_PAD_MULTIPLIER = 2.0
ROUTE_DEBUG_CENTERLINE_WIDTH = 0.2
ROUTE_CACHE_ENDPOINT_SCOPE_TOLERANCE = 3.0
EXTERNAL_DOOR_NAVIGATION_ANCHOR_MAX_DISTANCE = 30.0


def generate_group(
    solution_config: SolutionConfig,
    group: BuildingGroupConfig,
    *,
    records: list[BuildingInventoryRecord] | None = None,
    fetch_missing: bool = True,
    only_members: list[str] | None = None,
    only_floors: list[str] | None = None,
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

    names = artifact_names(group.id)
    processed_dir = solution_config.processed_root / "groups" / group.id
    export_dir = solution_config.export_root / "groups" / group.id
    processed_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    door_openings = _load_room_door_points(processed_dir, export_dir, group)
    meshes = dataset_meshes(projected, door_openings=door_openings)
    wall_blockers_by_floor = _route_wall_blockers_by_floor(meshes)
    manifest = _build_group_manifest(projected, group, member_records, processed_dir, export_dir, wall_blockers_by_floor=wall_blockers_by_floor)
    clip_footprints = _route_clip_footprints_by_floor(meshes, manifest.get("floors", []))
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
    if route_wall_openings:
        meshes = dataset_meshes(projected, door_openings=door_openings, wall_openings_by_floor=route_wall_openings)
        wall_blockers_by_floor = _route_wall_blockers_by_floor(meshes)
        manifest = _build_group_manifest(projected, group, member_records, processed_dir, export_dir, wall_blockers_by_floor=wall_blockers_by_floor)
        clip_footprints = _route_clip_footprints_by_floor(meshes, manifest.get("floors", []))
        route_endpoint_scope = _route_endpoint_scope_records(manifest)
    base_navigation_meshes = _scene_navigation_meshes_with_floor_fallback(
        [],
        meshes,
        manifest.get("floors", []),
    )
    _assign_external_door_navigation_anchors(manifest, base_navigation_meshes)
    _sync_external_door_navigation_links(manifest)
    _sync_nav_node_names(manifest)
    # _build_group_manifest creates walk links before geometry is available.
    # Run the idempotent linker once more now that external doors have an
    # indoor navigation anchor; otherwise a street-side physical marker can
    # remain isolated from every lift/stair even though the building is
    # routable in MapsIndoors.
    _add_same_floor_walk_links(manifest, wall_blockers_by_floor=wall_blockers_by_floor)
    floor_visual_files = _write_floor_visual_glbs(meshes, manifest.get("floors", []), export_dir, group.id)
    route_navigation_meshes, route_nav_stats = _complete_route_navigation_meshes(
        export_dir / "door_route_cache",
        manifest,
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
    floor_walkable_path_files = _write_floor_walkable_path_glbs(
        route_navigation_meshes,
        manifest.get("floors", []),
        export_dir,
        group.id,
    )
    floor_route_debug_files = _write_floor_route_debug_glbs(
        meshes,
        route_debug_meshes,
        manifest.get("floors", []),
        export_dir,
        group.id,
    )
    scene_navigation_meshes = _scene_navigation_meshes_with_floor_fallback(
        route_navigation_meshes,
        meshes,
        manifest.get("floors", []),
    )
    external_door_nav_stats = _assign_external_door_navigation_anchors(
        manifest,
        scene_navigation_meshes,
    )
    _sync_external_door_navigation_links(manifest)
    _sync_nav_node_names(manifest)
    route_nav_stats = dict(route_nav_stats)
    route_nav_stats["route_wall_openings"] = _route_wall_opening_stats(route_wall_openings)
    route_nav_stats["external_door_navigation"] = external_door_nav_stats
    manifest.setdefault("nav", {})["validation"] = route_nav_stats
    portal_topology_file = f"{group.id}_portal_topology.json"
    manifest["assets"] = {
        "visual_glb": names.visual_glb,
        "nav_glb": names.nav_glb,
        "portal_topology": portal_topology_file,
        "floor_visual_glbs": [
            {
                "floor_index": int(floor.get("floor_index", 0)),
                "floor_name": str(floor.get("floor_name", "")),
                "filename": floor_visual_files[int(floor.get("floor_index", 0))],
            }
            for floor in sorted(manifest.get("floors", []), key=lambda item: int(item.get("floor_index", 0)))
            if int(floor.get("floor_index", 0)) in floor_visual_files
        ],
        "walkable_path_glbs": [
            {
                "floor_index": int(floor.get("floor_index", 0)),
                "floor_name": str(floor.get("floor_name", "")),
                "filename": floor_walkable_path_files[int(floor.get("floor_index", 0))],
            }
            for floor in sorted(manifest.get("floors", []), key=lambda item: int(item.get("floor_index", 0)))
            if int(floor.get("floor_index", 0)) in floor_walkable_path_files
        ],
        "route_debug_glbs": [
            {
                "floor_index": int(floor.get("floor_index", 0)),
                "floor_name": str(floor.get("floor_name", "")),
                "filename": floor_route_debug_files[int(floor.get("floor_index", 0))],
            }
            for floor in sorted(manifest.get("floors", []), key=lambda item: int(item.get("floor_index", 0)))
            if int(floor.get("floor_index", 0)) in floor_route_debug_files
        ],
    }
    manifest = refresh_generation_hash(manifest)
    # Topology proofs must run on the meshes the baked scene actually ships:
    # for route-covered floors these ARE the route meshes, and for fallback
    # floors (incl. whole buildings the directions graph never routes into,
    # e.g. acoustics/kenneth_myers/law_annex) the geometry meshes. Proving on
    # route meshes only left those buildings with zero door->stair transfer
    # edges, so every cross-floor route was rejected at build time.
    portal_topology = build_portal_topology(
        manifest,
        scene_navigation_meshes,
    )
    _assert_external_door_topology_health(portal_topology)
    floor_visual_paths = {
        floor_index: f"{_unimate_asset_base(group)}/{filename}"
        for floor_index, filename in floor_visual_files.items()
    }
    floor_walkable_path_paths = {
        floor_index: f"{_unimate_asset_base(group)}/{filename}"
        for floor_index, filename in floor_walkable_path_files.items()
    }

    _write_json(processed_dir / "dataset.json", projected.to_dict())
    _write_json(processed_dir / "geometry.json", [mesh.to_dict() for mesh in meshes])
    if manifest.get("external_doors"):
        _write_json(processed_dir / "external_doors.json", manifest["external_doors"])
        _write_json(export_dir / "external_doors.json", manifest["external_doors"])
    write_portal_topology(portal_topology, processed_dir / portal_topology_file)
    write_portal_topology(portal_topology, export_dir / portal_topology_file)
    write_manifest(manifest, processed_dir / names.manifest)
    write_glb(visual_meshes_from_meshes(meshes), export_dir / names.visual_glb)
    write_glb(navigation_meshes_from_meshes(scene_navigation_meshes), export_dir / names.nav_glb)
    write_manifest(manifest, export_dir / names.manifest)
    scene_path = write_unimate_scene(
        manifest,
        export_dir / f"{group.id}_unimate.tscn",
        asset_base_path=_unimate_asset_base(group),
        navigation_meshes=scene_navigation_meshes,
        floor_visual_paths=floor_visual_paths,
        floor_walkable_path_visual_paths=floor_walkable_path_paths,
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
            "walkable_path_glbs": [str(export_dir / filename) for filename in floor_walkable_path_files.values()],
            "route_debug_glbs": [str(export_dir / filename) for filename in floor_route_debug_files.values()],
            "manifest": str(export_dir / names.manifest),
            "portal_topology": str(export_dir / portal_topology_file),
            "scene": str(scene_path),
            "readme": str(export_dir / names.readme),
        },
        "generation_hash": manifest["generation_hash"],
        "warnings": manifest.get("warnings", []),
    }


def _member_records(group: BuildingGroupConfig, records: list[BuildingInventoryRecord]) -> list[BuildingInventoryRecord]:
    by_admin = {record.admin_id: record for record in records}
    return [by_admin[member] for member in group.members if member in by_admin]


def _filter_group_members(group: BuildingGroupConfig, only_members: list[str]) -> BuildingGroupConfig:
    requested = [str(member).strip() for member in only_members if str(member).strip()]
    if not requested:
        return group
    known = set(group.members)
    unknown = [member for member in requested if member not in known]
    if unknown:
        raise ValueError(f"Unknown members for group {group.id}: {', '.join(unknown)}")
    members = [member for member in group.members if member in set(requested)]
    if not members:
        raise ValueError(f"Member filter removed every member from group {group.id}")
    excluded_members = known - set(members)
    aliases = [
        alias
        for alias in group.aliases
        if alias not in excluded_members
    ]
    return replace(
        group,
        members=members,
        aliases=aliases,
        primary_member=group.primary_member if group.primary_member in members else members[0],
    )


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


def _filter_dataset_floors(dataset: NormalizedDataset, only_floors: list[str]) -> NormalizedDataset:
    requested = {
        _canonical_floor_name(floor_name)
        for floor_name in only_floors
        if str(floor_name).strip()
    }
    if not requested:
        return dataset
    rooms = [
        room
        for room in dataset.rooms
        if _canonical_floor_name(room.floor_name) in requested
    ]
    portals = [
        portal
        for portal in dataset.portals
        if _canonical_floor_name(portal.floor_name) in requested
    ]
    if not rooms and not portals:
        raise ValueError(
            f"No rooms or portals found for floor filter: {', '.join(sorted(requested))}"
        )
    filtered = NormalizedDataset(
        building_id=dataset.building_id,
        building_admin_id=dataset.building_admin_id,
        building_name=dataset.building_name,
        rooms=rooms,
        portals=portals,
        warnings=list(dataset.warnings),
    )
    if hasattr(dataset, "source_urls"):
        filtered.source_urls = list(dataset.source_urls)  # type: ignore[attr-defined]
    return filtered


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


def _is_basement_floor_name(value: str) -> bool:
    """Is this floor below ground?

    Basements are spelled two ways in this dataset: 'B1'/'B-1' in most
    buildings, but plain negative numbers in others -- music's floors are
    '-1', '1', 'M1', '2' and recreation's start '-2', '-1', 'G'. Recognising
    only the 'B' form made the ground-floor fallback in
    _normalise_external_door resolve music's street entrance onto its
    BASEMENT, and that floor holds no stair or lift, so every room in the
    building became unroutable.
    """
    clean = str(value).strip().upper()
    if clean.startswith("B"):
        return True
    try:
        return float(clean) < 0
    except ValueError:
        return False


def _floor_sort_key(value: str) -> tuple[float, str]:
    clean = _canonical_floor_name(value)
    if clean.startswith("B-"):
        try:
            return (-float(clean[2:]), clean)
        except ValueError:
            return (-1.0, clean)
    if clean == "G":
        return (0.0, clean)
    if clean == "SB":
        # Sub-basement (architecture): between B-1 and B-2. Unrecognized
        # labels used to fall through to the 10_000 sentinel, which became a
        # 42000-unit floor height and a kilometre-tall campus prop.
        return (-1.5, clean)
    if clean.startswith("M") and clean[1:].isdigit():
        return (float(clean[1:]) + 0.5, clean)
    if clean.endswith("M") and clean[:-1].isdigit():
        # Digit-first mezzanine style ("1M" = mezzanine above level 1).
        return (float(clean[:-1]) + 0.5, clean)
    try:
        return (float(clean), clean)
    except ValueError:
        return (10_000.0, clean)


def _floor_heights(floors: list[FloorRecord], default_spacing: float, basement_spacing: float) -> dict[str, float]:
    heights: dict[str, float] = {}
    for floor in floors:
        label = floor.floor_name
        sort_value = _floor_sort_key(label)[0]
        if label == "G":
            heights[label] = 0.0
        elif sort_value < 0.0:
            # Below-ground levels (B-*, SB) use the tighter basement spacing.
            heights[label] = round(sort_value * basement_spacing, 6)
        else:
            heights[label] = round(sort_value * default_spacing, 6)
    return heights


def _build_group_manifest(
    dataset: NormalizedDataset,
    group: BuildingGroupConfig,
    records: list[BuildingInventoryRecord],
    processed_dir: Path,
    export_dir: Path,
    *,
    wall_blockers_by_floor: dict[str, list[LineString]] | None = None,
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
    external_doors = _load_external_doors(
        processed_dir, export_dir, group, manifest.get("floors", []), manifest.get("rooms", [])
    )
    if external_doors:
        manifest["external_doors"] = external_doors
    _apply_room_navigation_anchors(manifest, processed_dir, export_dir, group)
    vertical_link_stats = apply_route_derived_vertical_links(
        dataset,
        manifest,
        route_client=MapsIndoorsRouteClient(cache_dir=export_dir / "vertical_route_cache"),
    )
    if int(vertical_link_stats.get("candidates", 0)) > 0:
        _write_json(processed_dir / f"{group.id}_vertical_links_route_derived.json", vertical_link_stats)
        _write_json(export_dir / f"{group.id}_vertical_links_route_derived.json", vertical_link_stats)
    _dedupe_node_names(manifest)
    _sync_nav_node_names(manifest)
    _ensure_vertical_route_derivation_summary(manifest, vertical_link_stats)
    _add_same_floor_walk_links(manifest, wall_blockers_by_floor=wall_blockers_by_floor)
    return refresh_generation_hash(manifest)


def _apply_room_navigation_anchors(
    manifest: dict[str, Any],
    processed_dir: Path,
    export_dir: Path,
    group: BuildingGroupConfig,
) -> None:
    records = _load_room_door_points(processed_dir, export_dir, group)
    if not records:
        return

    by_source_id = {
        str(record.get("source_id")): record
        for record in records
        if record.get("source_id") and _valid_local_anchor(record.get("door_local"))
    }
    by_external_id = {
        str(record.get("external_id")): record
        for record in records
        if record.get("external_id") and _valid_local_anchor(record.get("door_local"))
    }
    for room in manifest.get("rooms", []):
        record = by_source_id.get(str(room.get("source_id"))) or by_external_id.get(str(room.get("external_id")))
        if not record:
            continue
        door_local = record.get("door_local")
        room["navigation_anchor"] = [float(door_local[0]), float(door_local[1]), float(door_local[2])]
        room["navigation_anchor_source"] = str(record.get("door_source") or "route_derived_room_door")
        room["navigation_anchor_confidence"] = str(record.get("confidence") or "unknown")


def _load_room_door_points(processed_dir: Path, export_dir: Path, group: BuildingGroupConfig) -> list[dict[str, Any]]:
    candidates = [
        processed_dir / f"{group.id}_room_door_points_route_derived.json",
        export_dir / f"{group.id}_room_door_points_route_derived.json",
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
    return [item for item in raw if isinstance(item, dict)]


def _complete_route_navigation_meshes(
    route_cache_dir: Path,
    manifest: dict[str, Any],
    origin_lon: float,
    origin_lat: float,
    *,
    clip_footprints_by_floor: dict[str, Any] | None = None,
    route_endpoint_scope: list[dict[str, Any]] | None = None,
    wall_blockers_by_floor: dict[str, list[LineString]] | None = None,
) -> tuple[list[MeshData], dict[str, Any]]:
    route_meshes, route_stats = _route_navigation_meshes_with_stats_from_cache(
        route_cache_dir,
        manifest.get("floors", []),
        origin_lon,
        origin_lat,
        point_records=_route_navigation_point_records(manifest),
        walk_links=_route_navigation_walk_link_records(manifest),
        clip_footprints_by_floor=clip_footprints_by_floor,
        route_endpoint_scope=route_endpoint_scope,
        wall_blockers_by_floor=wall_blockers_by_floor,
    )
    if not route_meshes:
        return [], route_stats

    # Floors without route coverage (e.g. a two-room mezzanine MapsIndoors
    # never routes through) no longer discard the WHOLE building's route
    # navmesh - that stripped every walkable path and topology transfer edge
    # from Elam/art. The caller falls back to geometry meshes per missing
    # floor instead; the gap is recorded for QA.
    required_floor_names = _required_navigation_floor_names(manifest)
    route_floor_names = {_floor_name_from_route_nav_mesh(mesh.name) for mesh in route_meshes}
    missing_floor_names = sorted(required_floor_names - route_floor_names)
    if missing_floor_names:
        route_stats["floors_missing_route_coverage"] = missing_floor_names
    return route_meshes, route_stats


def _route_navigation_meshes_from_cache(
    route_cache_dir: Path,
    floors: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    *,
    corridor_radius: float = ROUTE_NAV_CORRIDOR_RADIUS,
    point_records: list[dict[str, Any]] | None = None,
    walk_links: list[dict[str, Any]] | None = None,
    clip_footprints_by_floor: dict[str, Any] | None = None,
    route_endpoint_scope: list[dict[str, Any]] | None = None,
    wall_blockers_by_floor: dict[str, list[LineString]] | None = None,
) -> list[MeshData]:
    meshes, _stats = _route_navigation_meshes_with_stats_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        corridor_radius=corridor_radius,
        point_records=point_records,
        walk_links=walk_links,
        clip_footprints_by_floor=clip_footprints_by_floor,
        route_endpoint_scope=route_endpoint_scope,
        wall_blockers_by_floor=wall_blockers_by_floor,
    )
    return meshes


def _route_debug_centerline_meshes_from_cache(
    route_cache_dir: Path,
    floors: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    *,
    point_records: list[dict[str, Any]] | None = None,
    walk_links: list[dict[str, Any]] | None = None,
    clip_footprints_by_floor: dict[str, Any] | None = None,
    route_endpoint_scope: list[dict[str, Any]] | None = None,
) -> list[MeshData]:
    del point_records, walk_links
    if not route_cache_dir.exists():
        return []

    floor_height_by_name = {
        _canonical_floor_name(str(floor.get("floor_name", ""))): float(floor.get("height", 0.0))
        for floor in floors
    }
    if not floor_height_by_name:
        return []

    clip_footprints = _normalise_route_clip_footprints(clip_footprints_by_floor)
    endpoint_scope = _normalise_route_endpoint_scope(route_endpoint_scope)
    projector = LocalProjector(origin_lon, origin_lat)
    lines_by_floor: dict[str, list[LineString]] = {floor_name: [] for floor_name in floor_height_by_name}

    for route_path in sorted(route_cache_dir.glob("route_*.json")):
        try:
            route_data = json.loads(route_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not _route_matches_endpoint_scope(route_data, projector, floor_height_by_name, endpoint_scope):
            continue
        file_lines_by_floor: dict[str, list[Any]] = {floor_name: [] for floor_name in floor_height_by_name}
        _collect_route_step_lines(route_data, projector, floor_height_by_name, file_lines_by_floor)
        for floor_name, geometries in file_lines_by_floor.items():
            for geometry in geometries:
                if not isinstance(geometry, LineString):
                    continue
                lines_by_floor[floor_name].extend(_clip_route_line_to_floor(geometry, floor_name, clip_footprints))

    meshes: list[MeshData] = []
    for floor_name, lines in sorted(lines_by_floor.items(), key=lambda item: _floor_sort_key(item[0])):
        height = floor_height_by_name[floor_name]
        for line_index, line in enumerate(lines, start=1):
            meshes.extend(_route_debug_centerline_meshes_for_line(floor_name, line, height, line_index))
    return meshes


def _route_debug_centerline_meshes_for_line(
    floor_name: str,
    line: LineString,
    height: float,
    line_index: int,
) -> list[MeshData]:
    coords = list(line.coords)
    meshes: list[MeshData] = []
    half_width = ROUTE_DEBUG_CENTERLINE_WIDTH / 2.0
    for segment_index, (start, end) in enumerate(zip(coords, coords[1:]), start=1):
        start_x, start_z = float(start[0]), float(start[1])
        end_x, end_z = float(end[0]), float(end[1])
        dx = end_x - start_x
        dz = end_z - start_z
        length = math.hypot(dx, dz)
        if length < 0.05:
            continue
        nx = -dz / length * half_width
        nz = dx / length * half_width
        vertices = [
            [round(start_x + nx, 6), round(float(height), 6), round(start_z + nz, 6)],
            [round(start_x - nx, 6), round(float(height), 6), round(start_z - nz, 6)],
            [round(end_x - nx, 6), round(float(height), 6), round(end_z - nz, 6)],
            [round(end_x + nx, 6), round(float(height), 6), round(end_z + nz, 6)],
        ]
        meshes.append(
            MeshData(
                name=f"floor__{floor_name}__route_centerline_{line_index:04d}_{segment_index:02d}",
                vertices=vertices,
                faces=[[0, 1, 2, 3]],
                material="route_centerline",
                metadata={"debug_overlay": "route_centerline"},
            )
        )
    return meshes


def _route_navigation_meshes_with_stats_from_cache(
    route_cache_dir: Path,
    floors: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    *,
    corridor_radius: float = ROUTE_NAV_CORRIDOR_RADIUS,
    point_records: list[dict[str, Any]] | None = None,
    walk_links: list[dict[str, Any]] | None = None,
    clip_footprints_by_floor: dict[str, Any] | None = None,
    route_endpoint_scope: list[dict[str, Any]] | None = None,
    wall_blockers_by_floor: dict[str, list[LineString]] | None = None,
) -> tuple[list[MeshData], dict[str, Any]]:
    stats = _route_navigation_validation_stats()
    if not route_cache_dir.exists():
        return [], {}

    floor_height_by_name = {
        _canonical_floor_name(str(floor.get("floor_name", ""))): float(floor.get("height", 0.0))
        for floor in floors
    }
    floor_name_by_index = {
        int(floor.get("floor_index", 0)): _canonical_floor_name(str(floor.get("floor_name", "")))
        for floor in floors
    }
    if not floor_height_by_name:
        return [], stats

    clip_footprints = _normalise_route_clip_footprints(clip_footprints_by_floor)
    wall_blocker_indexes = _route_wall_blocker_indexes(wall_blockers_by_floor or {})
    stats["clip_floor_count"] = len(clip_footprints)
    projector = LocalProjector(origin_lon, origin_lat)
    endpoint_scope = _normalise_route_endpoint_scope(route_endpoint_scope)
    stats["route_cache"]["endpoint_scope_count"] = len(endpoint_scope)
    geometries_by_floor: dict[str, list[Any]] = {floor_name: [] for floor_name in floor_height_by_name}
    for route_path in sorted(route_cache_dir.glob("route_*.json")):
        stats["route_cache"]["files_total"] += 1
        try:
            route_data = json.loads(route_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats["route_cache"]["files_rejected"] += 1
            continue
        if not _route_matches_endpoint_scope(route_data, projector, floor_height_by_name, endpoint_scope):
            stats["route_cache"]["files_rejected"] += 1
            stats["route_cache"]["files_out_of_scope"] += 1
            continue
        file_geometries_by_floor: dict[str, list[Any]] = {floor_name: [] for floor_name in floor_height_by_name}
        _collect_route_step_lines(route_data, projector, floor_height_by_name, file_geometries_by_floor)
        accepted_sources = 0
        rejected_sources = 0
        for floor_name, geometries in file_geometries_by_floor.items():
            for geometry in geometries:
                if not isinstance(geometry, LineString):
                    continue
                stats["route_cache"]["segments_total"] += 1
                clipped_lines = _clip_route_line_to_floor(geometry, floor_name, clip_footprints)
                if clipped_lines:
                    accepted_sources += 1
                    geometries_by_floor[floor_name].extend(clipped_lines)
                else:
                    rejected_sources += 1
        if accepted_sources:
            stats["route_cache"]["files_used"] += 1
        elif rejected_sources:
            stats["route_cache"]["files_rejected"] += 1
        stats["route_cache"]["segments_used"] += accepted_sources
        stats["route_cache"]["segments_rejected"] += rejected_sources

    walk_geometries_by_floor: dict[str, list[Any]] = {floor_name: [] for floor_name in floor_height_by_name}
    _collect_manifest_walk_link_lines(walk_links or [], floor_name_by_index, walk_geometries_by_floor)
    for floor_name, geometries in walk_geometries_by_floor.items():
        for geometry in geometries:
            if not isinstance(geometry, LineString):
                continue
            stats["walk_links"]["segments_total"] += 1
            clipped_lines = _clip_route_line_to_floor(geometry, floor_name, clip_footprints)
            filtered_lines = _filter_route_lines_by_wall_blockers(clipped_lines, floor_name, wall_blocker_indexes)
            stats["wall_filter"]["walk_links_rejected"] += len(clipped_lines) - len(filtered_lines)
            if filtered_lines:
                stats["walk_links"]["segments_used"] += 1
                geometries_by_floor[floor_name].extend(filtered_lines)
            else:
                stats["walk_links"]["segments_rejected"] += 1

    has_route_geometry_by_floor = {
        floor_name: any(isinstance(geometry, LineString) for geometry in geometries)
        for floor_name, geometries in geometries_by_floor.items()
    }
    point_records_by_floor: dict[str, list[dict[str, Any]]] = {floor_name: [] for floor_name in floor_height_by_name}
    for record in point_records or []:
        floor_name = _canonical_floor_name(str(record.get("floor_name", "")))
        anchor = record.get("anchor") or record.get("navigation_anchor") or record.get("local")
        if floor_name not in geometries_by_floor or not _valid_local_anchor(anchor):
            continue
        if not _point_inside_route_clip(float(anchor[0]), float(anchor[2]), floor_name, clip_footprints):
            continue
        point_records_by_floor[floor_name].append(record)
        geometries_by_floor[floor_name].append(Point(float(anchor[0]), float(anchor[2])).buffer(ROUTE_NAV_POINT_RADIUS, quad_segs=8))

    targeted_connector_lines_by_floor = _targeted_route_navigation_connector_lines(point_records_by_floor)

    _append_route_navigation_connectors(
        geometries_by_floor,
        point_records_by_floor,
        wall_blocker_indexes=wall_blocker_indexes,
    )
    final_clip_footprints = _route_gap_clip_footprints(clip_footprints, corridor_radius)
    if clip_footprints:
        geometries_by_floor = {
            floor_name: _clip_route_geometries_to_floor(geometries, floor_name, clip_footprints)
            for floor_name, geometries in geometries_by_floor.items()
        }

    meshes: list[MeshData] = []
    for floor_name, geometries in sorted(geometries_by_floor.items(), key=lambda item: _floor_sort_key(item[0])):
        if not geometries:
            continue
        buffered = [
            geometry.buffer(corridor_radius, cap_style="round", join_style="round", quad_segs=8)
            if isinstance(geometry, LineString)
            else geometry
            for geometry in geometries
            if not geometry.is_empty
        ]
        if not buffered:
            continue
        merged = unary_union(buffered)
        merged = _bridge_route_components(merged, corridor_radius)
        wall_blocker_index = wall_blocker_indexes.get(floor_name)
        merged = _subtract_route_wall_blockers(merged, wall_blocker_index)
        targeted_connector_lines = list(targeted_connector_lines_by_floor.get(floor_name, []))
        custom_connector_lines = [
            LineString([(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))])
            for start, end in ROUTE_NAV_CUSTOM_CONNECTOR_SEGMENTS_BY_FLOOR.get(floor_name, [])
        ]
        custom_connector_geometries = [
            _custom_route_connector_geometry(line, corridor_radius)
            for line in custom_connector_lines
            if not line.is_empty
        ]
        custom_connector_geometries = [geometry for geometry in custom_connector_geometries if not geometry.is_empty]
        custom_connector_geometry = unary_union(custom_connector_geometries).buffer(0) if custom_connector_geometries else None
        targeted_connectors: list[Any] = []
        targeted_connector_geometry = None
        if targeted_connector_lines:
            targeted_connectors = [
                line.buffer(corridor_radius, cap_style="round", join_style="round", quad_segs=8).buffer(0)
                for line in targeted_connector_lines
                if not line.is_empty
            ]
            targeted_connectors = [connector for connector in targeted_connectors if not connector.is_empty]
            if targeted_connectors:
                targeted_connector_geometry = unary_union(targeted_connectors).buffer(0)
                merged = unary_union([merged, targeted_connector_geometry]).buffer(0)
        if final_clip_footprints:
            merged = _clip_route_geometry_to_floor(merged, floor_name, final_clip_footprints)
            if targeted_connector_geometry is not None and not targeted_connector_geometry.is_empty:
                # Explicit connectors repair known same-floor gaps between Science footprints.
                merged = unary_union([merged, targeted_connector_geometry]).buffer(0)
            if merged.is_empty:
                continue
        if custom_connector_geometry is not None and not custom_connector_geometry.is_empty:
            # Custom connectors must be part of the same grid mesh as the route surface.
            # Overlapping separate meshes look connected visually, but Godot path queries
            # can stop on the first island and never transition into the connector.
            merged = unary_union([merged, custom_connector_geometry]).buffer(0)
        wall_bypass_geometries = [
            geometry
            for geometry in (custom_connector_geometry, targeted_connector_geometry)
            if geometry is not None and not geometry.is_empty
        ]
        wall_bypass_geometry = unary_union(wall_bypass_geometries).buffer(0) if wall_bypass_geometries else None
        if ROUTE_NAV_SIMPLIFY:
            merged = merged.simplify(ROUTE_NAV_SIMPLIFY, preserve_topology=True)
        height = floor_height_by_name[floor_name]
        if not has_route_geometry_by_floor.get(floor_name, False):
            hull_mesh = _route_anchor_envelope_mesh(floor_name, point_records_by_floor.get(floor_name, []), height)
            if hull_mesh and hull_mesh.faces:
                meshes.append(hull_mesh)
                continue

        floor_meshes: list[MeshData] = []
        for index, polygon in enumerate(_iter_route_polygons(merged), start=1):
            mesh = _route_polygon_to_mesh(
                floor_name,
                polygon,
                height,
                index,
                wall_blocker_index=wall_blocker_index,
                wall_bypass_geometry=wall_bypass_geometry,
            )
            if not mesh.faces:
                continue
            floor_meshes.append(mesh)
        meshes.extend(floor_meshes)
    stats["navmesh_bboxes"] = _route_mesh_bboxes(meshes)
    return meshes, stats


def _custom_route_connector_geometry(line: LineString, corridor_radius: float) -> Any:
    if line.is_empty:
        return line
    custom_radius = corridor_radius * ROUTE_NAV_CUSTOM_CONNECTOR_RADIUS_MULTIPLIER
    endpoint_radius = corridor_radius * ROUTE_NAV_CUSTOM_CONNECTOR_PAD_MULTIPLIER
    coords = list(line.coords)
    geometries = [
        line.buffer(custom_radius, cap_style="round", join_style="round", quad_segs=8),
        Point(coords[0]).buffer(endpoint_radius, quad_segs=8),
        Point(coords[-1]).buffer(endpoint_radius, quad_segs=8),
    ]
    return unary_union(geometries).buffer(0)


def _targeted_route_navigation_connector_lines(point_records_by_floor: dict[str, list[dict[str, Any]]]) -> dict[str, list[LineString]]:
    lines_by_floor: dict[str, list[LineString]] = {}
    for floor_name, records in point_records_by_floor.items():
        records_by_external_id = {
            str(record.get("external_id", "")): record
            for record in records
            if str(record.get("external_id", ""))
        }
        for connector in ROUTE_NAV_TARGETED_POINT_CONNECTORS:
            if len(connector) != 2:
                continue
            start_id, end_id = sorted(connector)
            start = records_by_external_id.get(start_id)
            end = records_by_external_id.get(end_id)
            if not start or not end:
                continue
            start_anchor = start.get("anchor")
            end_anchor = end.get("anchor")
            if not _valid_local_anchor(start_anchor) or not _valid_local_anchor(end_anchor):
                continue
            distance = _distance_2d(float(start_anchor[0]), float(start_anchor[2]), float(end_anchor[0]), float(end_anchor[2]))
            if distance <= 0.05 or distance > ROUTE_NAV_TARGETED_POINT_CONNECTOR_MAX_DISTANCE:
                continue
            lines_by_floor.setdefault(floor_name, []).append(
                LineString(
                    [
                        (float(start_anchor[0]), float(start_anchor[2])),
                        (float(end_anchor[0]), float(end_anchor[2])),
                    ]
                )
            )
    return lines_by_floor


def _route_navigation_validation_stats() -> dict[str, Any]:
    return {
        "clip_margin": ROUTE_NAV_FOOTPRINT_CLIP_MARGIN,
        "clip_floor_count": 0,
        "route_cache": {
            "files_total": 0,
            "files_used": 0,
            "files_rejected": 0,
            "files_out_of_scope": 0,
            "endpoint_scope_count": 0,
            "segments_total": 0,
            "segments_used": 0,
            "segments_rejected": 0,
        },
        "walk_links": {
            "segments_total": 0,
            "segments_used": 0,
            "segments_rejected": 0,
        },
        "wall_filter": {
            "route_segments_rejected": 0,
            "walk_links_rejected": 0,
        },
        "navmesh_bboxes": {},
    }


def _route_wall_openings_from_cache(
    route_cache_dir: Path,
    floors: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    *,
    clip_footprints_by_floor: dict[str, Any] | None = None,
    route_endpoint_scope: list[dict[str, Any]] | None = None,
    wall_blockers_by_floor: dict[str, list[LineString]] | None = None,
) -> WallOpeningMap:
    if not route_cache_dir.exists() or not wall_blockers_by_floor:
        return {}

    floor_height_by_name = {
        _canonical_floor_name(str(floor.get("floor_name", ""))): float(floor.get("height", 0.0))
        for floor in floors
    }
    if not floor_height_by_name:
        return {}

    clip_footprints = _normalise_route_clip_footprints(clip_footprints_by_floor)
    endpoint_scope = _normalise_route_endpoint_scope(route_endpoint_scope)
    wall_blocker_indexes = _route_wall_blocker_indexes(wall_blockers_by_floor)
    if not wall_blocker_indexes:
        return {}

    projector = LocalProjector(origin_lon, origin_lat)
    openings: WallOpeningMap = {}
    for route_path in sorted(route_cache_dir.glob("route_*.json")):
        try:
            route_data = json.loads(route_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not _route_matches_endpoint_scope(route_data, projector, floor_height_by_name, endpoint_scope):
            continue
        file_geometries_by_floor: dict[str, list[Any]] = {floor_name: [] for floor_name in floor_height_by_name}
        _collect_route_step_lines(route_data, projector, floor_height_by_name, file_geometries_by_floor)
        for floor_name, geometries in file_geometries_by_floor.items():
            wall_blocker_index = wall_blocker_indexes.get(floor_name)
            if wall_blocker_index is None:
                continue
            for geometry in geometries:
                if not isinstance(geometry, LineString):
                    continue
                for line in _clip_route_line_to_floor(geometry, floor_name, clip_footprints):
                    _add_route_wall_openings_for_line(openings, floor_name, line, wall_blocker_index)
    return openings


def _add_route_wall_openings_for_line(
    openings: WallOpeningMap,
    floor_name: str,
    line: LineString,
    wall_blocker_index: tuple[Any, list[LineString]],
) -> None:
    if line.is_empty:
        return
    for blocker in _query_wall_blockers(wall_blocker_index, line):
        if not _line_hits_wall_interior(line, blocker):
            continue
        edge_key = _route_wall_opening_edge_key(blocker)
        if edge_key is not None:
            openings.setdefault(_canonical_floor_name(floor_name), set()).add(edge_key)


def _route_wall_opening_edge_key(line: LineString) -> tuple[tuple[float, float], tuple[float, float]] | None:
    coords = list(line.coords)
    if len(coords) < 2:
        return None
    left = coords[0]
    right = coords[-1]
    a = (round(float(left[0]), 3), round(float(left[1]), 3))
    b = (round(float(right[0]), 3), round(float(right[1]), 3))
    return (a, b) if a <= b else (b, a)


def _route_wall_opening_stats(openings: WallOpeningMap) -> dict[str, Any]:
    return {
        "floors": {floor_name: len(edge_keys) for floor_name, edge_keys in sorted(openings.items())},
        "total_edges": sum(len(edge_keys) for edge_keys in openings.values()),
    }


def _route_endpoint_scope_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for collection_name in ("rooms", "portals", "external_doors"):
        for item in manifest.get(collection_name, []):
            if not isinstance(item, dict):
                continue
            anchor = item.get("anchor") or item.get("local")
            if not _valid_local_anchor(anchor):
                continue
            floor_name = _canonical_floor_name(str(item.get("floor_name", "")))
            if not floor_name:
                continue
            records.append({"floor_name": floor_name, "anchor": list(anchor[:3])})
    return records


def _normalise_route_endpoint_scope(route_endpoint_scope: list[dict[str, Any]] | None) -> list[tuple[str, float, float]]:
    scope: list[tuple[str, float, float]] = []
    for item in route_endpoint_scope or []:
        if not isinstance(item, dict):
            continue
        anchor = item.get("anchor") or item.get("local")
        if not _valid_local_anchor(anchor):
            continue
        floor_name = _canonical_floor_name(str(item.get("floor_name", "")))
        if not floor_name:
            continue
        scope.append((floor_name, float(anchor[0]), float(anchor[2])))
    return scope


def _route_matches_endpoint_scope(
    route_data: dict[str, Any],
    projector: LocalProjector,
    floor_height_by_name: dict[str, float],
    endpoint_scope: list[tuple[str, float, float]],
) -> bool:
    if not endpoint_scope:
        return True
    for route in route_data.get("routes") or []:
        if not isinstance(route, dict):
            continue
        for endpoint in _route_overall_endpoint_points(route, projector, floor_height_by_name):
            if _route_endpoint_matches_scope(endpoint, endpoint_scope):
                return True
    return False


def _route_overall_endpoint_points(
    route: dict[str, Any],
    projector: LocalProjector,
    floor_height_by_name: dict[str, float],
) -> list[tuple[str, float, float]]:
    legs = [leg for leg in route.get("legs") or [] if isinstance(leg, dict)]
    raw_points: list[Any] = []
    if legs:
        raw_points.extend([legs[0].get("start_location"), legs[-1].get("end_location")])

    points: list[tuple[str, float, float]] = []
    for raw_point in raw_points:
        point = _route_location_local_point(raw_point, projector, floor_height_by_name)
        if point is not None:
            points.append(point)
    if points or any(isinstance(raw_point, dict) for raw_point in raw_points):
        return points

    geometry_points: list[Any] = []
    search_steps: list[Any] = []
    if legs:
        for leg in legs:
            search_steps.extend(leg.get("steps") or [])
    else:
        search_steps.extend(route.get("steps") or [])
    for step in search_steps:
        if isinstance(step, dict):
            geometry_points.extend(step.get("geometry") or [])
    for raw_point in ([geometry_points[0], geometry_points[-1]] if geometry_points else []):
        point = _route_location_local_point(raw_point, projector, floor_height_by_name)
        if point is not None:
            points.append(point)
    return points


def _route_location_local_point(
    point: Any,
    projector: LocalProjector,
    floor_height_by_name: dict[str, float],
) -> tuple[str, float, float] | None:
    if not isinstance(point, dict):
        return None
    floor_name = _route_floor_name(point.get("floor_name"))
    if floor_name not in floor_height_by_name:
        return None
    if "lat" not in point or "lng" not in point:
        return None
    local = projector.to_local(float(point["lng"]), float(point["lat"]), floor_height_by_name[floor_name])
    return (floor_name, float(local[0]), float(local[2]))


def _route_endpoint_matches_scope(
    endpoint: tuple[str, float, float],
    endpoint_scope: list[tuple[str, float, float]],
) -> bool:
    endpoint_floor, endpoint_x, endpoint_z = endpoint
    for scope_floor, scope_x, scope_z in endpoint_scope:
        if endpoint_floor != scope_floor:
            continue
        if _distance_2d(endpoint_x, endpoint_z, scope_x, scope_z) <= ROUTE_CACHE_ENDPOINT_SCOPE_TOLERANCE:
            return True
    return False


def _normalise_route_clip_footprints(clip_footprints_by_floor: dict[str, Any] | None) -> dict[str, Any]:
    footprints: dict[str, Any] = {}
    for raw_floor_name, geometry in (clip_footprints_by_floor or {}).items():
        floor_name = _canonical_floor_name(str(raw_floor_name))
        if geometry is None or getattr(geometry, "is_empty", True):
            continue
        footprint = geometry.buffer(0)
        if footprint.is_empty:
            continue
        if ROUTE_NAV_FOOTPRINT_CLIP_MARGIN:
            footprint = footprint.buffer(ROUTE_NAV_FOOTPRINT_CLIP_MARGIN, join_style="mitre").buffer(0)
        if not footprint.is_empty:
            footprints[floor_name] = footprint
    return footprints


def _clip_route_line_to_floor(line: LineString, floor_name: str, clip_footprints_by_floor: dict[str, Any]) -> list[LineString]:
    if not clip_footprints_by_floor:
        return [line] if not line.is_empty and line.length >= 0.05 else []
    footprint = clip_footprints_by_floor.get(_canonical_floor_name(floor_name))
    if footprint is None:
        return [line] if not line.is_empty and line.length >= 0.05 else []
    if _route_line_bridges_footprint_gap(line, footprint):
        return [line]
    return _route_line_strings_from_geometry(line.intersection(footprint))


def _route_line_bridges_footprint_gap(line: LineString, footprint: Any) -> bool:
    if line.is_empty or line.length < 0.05 or line.length > ROUTE_NAV_FOOTPRINT_GAP_ROUTE_MAX_LENGTH:
        return False
    if footprint is None or getattr(footprint, "is_empty", True):
        return False
    try:
        return line.distance(footprint) <= ROUTE_NAV_FOOTPRINT_GAP_ROUTE_MAX_DISTANCE
    except Exception:
        return False


def _clip_route_geometries_to_floor(
    geometries: list[Any],
    floor_name: str,
    clip_footprints_by_floor: dict[str, Any],
) -> list[Any]:
    clipped: list[Any] = []
    footprint = clip_footprints_by_floor.get(_canonical_floor_name(floor_name)) if clip_footprints_by_floor else None
    for geometry in geometries:
        if isinstance(geometry, LineString) and _route_line_bridges_footprint_gap(geometry, footprint):
            clipped.append(geometry)
            continue
        clipped_geometry = _clip_route_geometry_to_floor(geometry, floor_name, clip_footprints_by_floor)
        if clipped_geometry.is_empty:
            continue
        if isinstance(clipped_geometry, LineString | MultiLineString):
            clipped.extend(_route_line_strings_from_geometry(clipped_geometry))
        elif isinstance(clipped_geometry, GeometryCollection):
            for child in clipped_geometry.geoms:
                if child.is_empty:
                    continue
                if isinstance(child, LineString | MultiLineString):
                    clipped.extend(_route_line_strings_from_geometry(child))
                else:
                    clipped.append(child)
        else:
            clipped.append(clipped_geometry)
    return clipped


def _clip_route_geometry_to_floor(geometry: Any, floor_name: str, clip_footprints_by_floor: dict[str, Any]) -> Any:
    if not clip_footprints_by_floor or geometry is None:
        return geometry
    footprint = clip_footprints_by_floor.get(_canonical_floor_name(floor_name))
    if footprint is None:
        return geometry
    try:
        return geometry.intersection(footprint)
    except Exception:
        return GeometryCollection()


def _route_gap_clip_footprints(clip_footprints_by_floor: dict[str, Any], corridor_radius: float) -> dict[str, Any]:
    if not clip_footprints_by_floor:
        return {}
    margin = ROUTE_NAV_FOOTPRINT_GAP_ROUTE_MAX_DISTANCE + corridor_radius
    footprints: dict[str, Any] = {}
    for floor_name, footprint in clip_footprints_by_floor.items():
        if footprint is None or getattr(footprint, "is_empty", True):
            continue
        expanded = footprint.buffer(margin, join_style="mitre").buffer(0)
        if not expanded.is_empty:
            footprints[floor_name] = expanded
    return footprints


def _route_line_strings_from_geometry(geometry: Any) -> list[LineString]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length >= 0.05 else []
    if isinstance(geometry, MultiLineString | GeometryCollection):
        lines: list[LineString] = []
        for child in geometry.geoms:
            lines.extend(_route_line_strings_from_geometry(child))
        return lines
    return []


def _filter_route_lines_by_wall_blockers(
    lines: list[LineString],
    floor_name: str,
    wall_blocker_indexes: dict[str, tuple[Any, list[LineString]]],
) -> list[LineString]:
    wall_blocker_index = wall_blocker_indexes.get(_canonical_floor_name(floor_name))
    if wall_blocker_index is None:
        return lines
    return [line for line in lines if not _line_blocked_by_wall(line, wall_blocker_index)]


def _line_blocked_by_wall(line: LineString, wall_blocker_index: tuple[Any, list[LineString]] | None) -> bool:
    if wall_blocker_index is None or line.is_empty:
        return False
    for blocker in _query_wall_blockers(wall_blocker_index, line):
        if _line_hits_wall_interior(line, blocker):
            return True
    return False


def _line_hits_wall_interior(line: LineString, wall: LineString) -> bool:
    if wall.is_empty or line.is_empty:
        return False
    if line.crosses(wall) or wall.crosses(line):
        return True
    intersection = line.intersection(wall)
    return _wall_intersection_is_blocking(intersection, line, wall)


def _wall_intersection_is_blocking(intersection: Any, line: LineString, wall: LineString) -> bool:
    if intersection is None or intersection.is_empty:
        return False
    geom_type = getattr(intersection, "geom_type", "")
    if geom_type == "Point":
        return (
            ROUTE_NAV_WALL_INTERSECTION_TOLERANCE
            < line.project(intersection)
            < line.length - ROUTE_NAV_WALL_INTERSECTION_TOLERANCE
            and ROUTE_NAV_WALL_INTERSECTION_TOLERANCE
            < wall.project(intersection)
            < wall.length - ROUTE_NAV_WALL_INTERSECTION_TOLERANCE
        )
    if geom_type == "MultiPoint":
        return any(_wall_intersection_is_blocking(point, line, wall) for point in intersection.geoms)
    if geom_type in {"LineString", "MultiLineString"}:
        return intersection.length > ROUTE_NAV_WALL_INTERSECTION_TOLERANCE
    if isinstance(intersection, GeometryCollection):
        return any(_wall_intersection_is_blocking(child, line, wall) for child in intersection.geoms)
    return False


def _subtract_route_wall_blockers(geometry: Any, wall_blocker_index: tuple[Any, list[LineString]] | None) -> Any:
    if wall_blocker_index is None or geometry is None or getattr(geometry, "is_empty", True):
        return geometry
    blockers = _query_wall_blockers(wall_blocker_index, geometry)
    if not blockers:
        return geometry
    wall_cut = unary_union(blockers).buffer(ROUTE_NAV_WALL_BLOCKER_CLEARANCE, cap_style="flat", join_style="mitre")
    if wall_cut.is_empty:
        return geometry
    try:
        return geometry.difference(wall_cut).buffer(0)
    except Exception:
        return geometry


def _route_grid_cell_blocked_by_wall(
    cell: Polygon,
    wall_blocker_index: tuple[Any, list[LineString]] | None,
    *,
    wall_bypass_geometry: Any | None = None,
) -> bool:
    if wall_blocker_index is None:
        return False
    if wall_bypass_geometry is not None and not wall_bypass_geometry.is_empty:
        bypass_area = cell.intersection(wall_bypass_geometry).area
        if bypass_area >= cell.area * 0.2:
            return False
    for blocker in _query_wall_blockers(wall_blocker_index, cell):
        # `intersects` is true for ZERO-AREA contact, so a cell was discarded
        # when it merely touched a wall at a point. Walls carry door openings by
        # this stage, but an opening is bounded by its two jambs, and every grid
        # cell filling a doorway touches a jamb endpoint - so both door cells
        # were vetoed and the doorway vanished, severing the corridor by exactly
        # one cell. That is why baked island-to-island gaps spiked at exactly
        # 0.5 m. Walls were already subtracted from the corridor with a 0.15
        # clearance in _subtract_route_wall_blockers, so only a wall that truly
        # runs THROUGH the cell should veto it.
        if cell.intersection(blocker).length > ROUTE_NAV_WALL_INTERSECTION_TOLERANCE:
            return True
    return False


def _point_inside_route_clip(x: float, z: float, floor_name: str, clip_footprints_by_floor: dict[str, Any]) -> bool:
    if not clip_footprints_by_floor:
        return True
    footprint = clip_footprints_by_floor.get(_canonical_floor_name(floor_name))
    if footprint is None:
        return True
    return footprint.covers(Point(float(x), float(z)))


def _route_mesh_bboxes(meshes: list[MeshData]) -> dict[str, list[float]]:
    bboxes: dict[str, list[float]] = {}
    for mesh in meshes:
        floor_name = _floor_name_from_route_nav_mesh(mesh.name)
        if not floor_name:
            continue
        for vertex in mesh.vertices:
            x = float(vertex[0])
            z = float(vertex[2])
            bbox = bboxes.setdefault(floor_name, [x, z, x, z])
            bbox[0] = min(bbox[0], x)
            bbox[1] = min(bbox[1], z)
            bbox[2] = max(bbox[2], x)
            bbox[3] = max(bbox[3], z)
    return {floor_name: [round(value, 6) for value in bbox] for floor_name, bbox in sorted(bboxes.items())}


def _route_polygon_to_mesh(
    floor_name: str,
    polygon: Polygon,
    height: float,
    index: int,
    *,
    wall_blocker_index: tuple[Any, list[LineString]] | None = None,
    wall_bypass_geometry: Any | None = None,
) -> MeshData:
    """Rasterize wall-safe coverage, then dissolve and triangulate its cells.

    The occupancy grid remains the authority for wall filtering and narrow
    connector survival. Exporting every accepted cell as a Godot polygon,
    however, makes the funnel algorithm alternate across thousands of tiny
    edges. Dissolving those cells and constrained-triangulating each resulting
    component preserves the exact coverage, holes, and shared connectivity
    while removing the internal grid boundaries that caused zigzag paths.
    """
    suffix = "" if index == 1 else f"__part_{index}"
    vertices: list[list[float]] = []
    vertex_index_by_key: dict[tuple[float, float, float], int] = {}
    faces: list[list[int]] = []
    coverage_polygon = polygon.buffer(0)
    if coverage_polygon.is_empty:
        return MeshData(
            name=f"floor__{floor_name}{suffix}",
            vertices=vertices,
            faces=faces,
            material="floor",
        )

    min_x, min_z, max_x, max_z = coverage_polygon.bounds
    cell_size = max(0.25, ROUTE_NAV_GRID_CELL_SIZE)
    start_x = math.floor(min_x / cell_size) * cell_size
    start_z = math.floor(min_z / cell_size) * cell_size
    x_count = max(1, int(math.ceil((max_x - start_x) / cell_size)))
    z_count = max(1, int(math.ceil((max_z - start_z) / cell_size)))
    min_cell_area = max(ROUTE_NAV_TRIANGLE_MIN_AREA, cell_size * cell_size * ROUTE_NAV_GRID_MIN_CELL_COVERAGE)

    def vertex_index(x: float, z: float) -> int:
        key = (round(float(x), 6), round(float(height), 6), round(float(z), 6))
        if key not in vertex_index_by_key:
            vertex_index_by_key[key] = len(vertices)
            vertices.append([key[0], key[1], key[2]])
        return vertex_index_by_key[key]

    selected_cells: list[Polygon] = []
    for z_index in range(z_count):
        z0 = start_z + z_index * cell_size
        z1 = z0 + cell_size
        for x_index in range(x_count):
            x0 = start_x + x_index * cell_size
            x1 = x0 + cell_size
            cell = box(x0, z0, x1, z1)
            if _route_grid_cell_blocked_by_wall(cell, wall_blocker_index, wall_bypass_geometry=wall_bypass_geometry):
                continue
            covered_area = coverage_polygon.intersection(cell).area
            if covered_area < min_cell_area:
                continue
            cell_x0 = max(x0, min_x)
            cell_x1 = min(x1, max_x)
            cell_z0 = max(z0, min_z)
            cell_z1 = min(z1, max_z)
            if cell_x1 - cell_x0 <= 0.001 or cell_z1 - cell_z0 <= 0.001:
                continue
            selected_cells.append(box(cell_x0, cell_z0, cell_x1, cell_z1))

    if selected_cells:
        dissolved_cells = unary_union(selected_cells).buffer(0)
        # This tolerance removes only redundant collinear grid vertices. It is
        # three orders of magnitude below one source cell and cannot bridge a
        # wall gap or erase a meaningful corridor feature.
        boundary_tolerance = cell_size * 0.001
        for component in _iter_route_polygons(dissolved_cells):
            clean_component = component.simplify(
                boundary_tolerance,
                preserve_topology=True,
            )
            triangles = constrained_delaunay_triangles(clean_component)
            for triangle in getattr(triangles, "geoms", []):
                if not isinstance(triangle, Polygon) or triangle.area < ROUTE_NAV_TRIANGLE_MIN_AREA:
                    continue
                if not clean_component.covers(triangle):
                    continue
                coordinates = list(triangle.exterior.coords)[:-1]
                if len(coordinates) != 3:
                    continue
                faces.append(
                    [
                        vertex_index(float(x), float(z))
                        for x, z in coordinates
                    ]
                )

    # Defensive compatibility fallback for an unexpected GEOS triangulation
    # failure. The old cell mesh is noisy but remains connected and walkable.
    if selected_cells and not faces:
        for cell in selected_cells:
            coordinates = list(cell.exterior.coords)[:-1]
            faces.append(
                [
                    vertex_index(float(x), float(z))
                    for x, z in coordinates
                ]
            )

    if faces:
        return MeshData(
            name=f"floor__{floor_name}{suffix}",
            vertices=vertices,
            faces=faces,
            material="floor",
            metadata={
                "godot_nav_overlay": "route_corridor_grid",
                "route_nav_meshing": "constrained_delaunay",
                "source_grid_cells": len(selected_cells),
            },
        )

    return MeshData(
        name=f"floor__{floor_name}{suffix}",
        vertices=vertices,
        faces=faces,
        material="floor",
    )


def _route_anchor_envelope_mesh(floor_name: str, point_records: list[dict[str, Any]], height: float) -> MeshData | None:
    points = [_point_from_anchor(record.get("anchor")) for record in point_records]
    points = [point for point in points if point is not None]
    if len(points) < 2:
        return None
    min_x = min(float(point.x) for point in points) - ROUTE_NAV_ANCHOR_ENVELOPE_MARGIN
    max_x = max(float(point.x) for point in points) + ROUTE_NAV_ANCHOR_ENVELOPE_MARGIN
    min_z = min(float(point.y) for point in points) - ROUTE_NAV_ANCHOR_ENVELOPE_MARGIN
    max_z = max(float(point.y) for point in points) + ROUTE_NAV_ANCHOR_ENVELOPE_MARGIN
    if max_x - min_x <= 0.01 or max_z - min_z <= 0.01:
        return None
    cell_size = max(1.0, ROUTE_NAV_ANCHOR_ENVELOPE_CELL_SIZE)
    min_cells = max(1, ROUTE_NAV_ANCHOR_ENVELOPE_MIN_CELLS)
    x_count = max(min_cells, int((max_x - min_x + cell_size - 0.000001) // cell_size))
    z_count = max(min_cells, int((max_z - min_z + cell_size - 0.000001) // cell_size))
    x_step = (max_x - min_x) / x_count
    z_step = (max_z - min_z) / z_count

    vertices: list[list[float]] = []
    for z_index in range(z_count + 1):
        z = min_z + z_step * z_index
        for x_index in range(x_count + 1):
            x = min_x + x_step * x_index
            vertices.append([round(x, 6), round(float(height), 6), round(z, 6)])

    faces: list[list[int]] = []
    stride = x_count + 1
    for z_index in range(z_count):
        for x_index in range(x_count):
            top_left = z_index * stride + x_index
            top_right = top_left + 1
            bottom_left = top_left + stride
            bottom_right = bottom_left + 1
            faces.append([top_left, top_right, bottom_right])
            faces.append([top_left, bottom_right, bottom_left])

    return MeshData(
        name=f"floor__{floor_name}__anchor_envelope",
        vertices=vertices,
        faces=faces,
        material="floor",
        metadata={"godot_nav_overlay": "anchor_envelope_grid"},
    )


def _bridge_route_components(geometry: Any, corridor_radius: float) -> Any:
    merged = geometry
    for _attempt in range(256):
        polygons = _iter_route_polygons(merged)
        if len(polygons) <= 1:
            return merged

        best: tuple[float, Polygon, Polygon] | None = None
        for start_index, start in enumerate(polygons):
            for end in polygons[start_index + 1 :]:
                distance = start.distance(end)
                if distance > ROUTE_NAV_COMPONENT_BRIDGE_MAX_DISTANCE:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, start, end)
        if best is None:
            return merged

        _distance, start, end = best
        start_point, end_point = nearest_points(start, end)
        bridge_radius = corridor_radius * 2.5
        if start_point.distance(end_point) <= 0.05:
            bridge = Point(float(start_point.x), float(start_point.y)).buffer(bridge_radius, quad_segs=8)
        else:
            bridge = LineString(
                [(float(start_point.x), float(start_point.y)), (float(end_point.x), float(end_point.y))]
            ).buffer(bridge_radius, cap_style="round", join_style="round", quad_segs=8)
        merged = unary_union([merged, bridge])
    return merged


EXTERNAL_DOOR_MAX_DISTANCE_FROM_BUILDING = 60.0
# Two sources describing the same doorway within this distance are one door.
EXTERNAL_DOOR_DEDUPE_DISTANCE = 3.0


def _external_door_anchor_is_plausible(
    anchor: Any,
    rooms: list[dict[str, Any]],
    max_distance: float = EXTERNAL_DOOR_MAX_DISTANCE_FROM_BUILDING,
) -> bool:
    """Is this anchor anywhere near the building it claims to be a door of?

    Route-derived entry points are harvested from routes that run BETWEEN
    buildings, so a waypoint far away gets recorded as this building's
    entrance. In the published campus that is 155 of 266 door records, the
    worst 957 m out. They stay inert while a building also has a real door,
    but music and conference ended up with nothing else and became unroutable.

    Measured against the bounding box of the building's own rooms, with a
    generous margin so a genuine street-side entrance -- which legitimately
    sits outside the footprint -- is kept.
    """
    if not _valid_local_anchor(anchor) or not rooms:
        return True
    xs, zs = [], []
    for room in rooms:
        position = room.get("position") or room.get("local") or room.get("anchor")
        if _valid_local_anchor(position):
            xs.append(float(position[0]))
            zs.append(float(position[2]))
    if not xs:
        return True
    x = float(anchor[0])
    z = float(anchor[2])
    dx = max(min(xs) - x, 0.0, x - max(xs))
    dz = max(min(zs) - z, 0.0, z - max(zs))
    return math.hypot(dx, dz) <= max_distance


def _load_external_doors(
    processed_dir: Path,
    export_dir: Path,
    group: BuildingGroupConfig,
    floors: list[dict[str, Any]],
    rooms: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    # MERGE the sources rather than taking the first that exists. The authored
    # file is usually a single `manual_bbox_nearest_road_node` point that
    # guarantees a campus-road connection; the route-derived entries are the
    # building's actual doors. Letting the authored file win outright discarded
    # the real doors, and regions that only those doors reached lost their entry
    # (old_government_house dropped from 9 usable doors to 1, taking its
    # reachable room count down with it). Authored entries come first so they
    # keep priority when two sources describe the same doorway.
    candidates = [
        processed_dir / "external_doors.json",
        export_dir / "external_doors.json",
        export_dir / f"{group.id}_external_entry_points_route_derived.json",
    ]
    raw: list[Any] = []
    seen_positions: list[tuple[float, float]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            anchor = item.get("anchor") or item.get("local") or item.get("door_local")
            if _valid_local_anchor(anchor):
                x, z = float(anchor[0]), float(anchor[2])
                if any(math.hypot(x - px, z - pz) <= EXTERNAL_DOOR_DEDUPE_DISTANCE
                       for px, pz in seen_positions):
                    continue
                seen_positions.append((x, z))
            raw.append(item)
    if not raw:
        return []

    floor_index_by_name = {
        str(floor.get("floor_name", "")).strip().upper(): int(floor.get("floor_index", 0))
        for floor in floors
    }
    records = []
    dropped = 0
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        anchor = item.get("anchor") or item.get("local") or item.get("door_local")
        if not _external_door_anchor_is_plausible(anchor, rooms or []):
            dropped += 1
            continue
        normalized = _normalise_external_door(item, index, group, floor_index_by_name)
        if normalized:
            records.append(normalized)
    if dropped:
        print(f"  external doors: dropped {dropped} entry point(s) too far from {group.id}")
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

    source_entry_id = str(item.get("entry_id") or item.get("external_id") or "").strip()
    source_external_id = str(item.get("external_id") or item.get("entry_id") or "").strip()
    entry_id = f"{group.id}_entry_{index:03d}"
    floor_name = _canonical_floor_name(str(item.get("floor_name") or "G"))
    floor_index = floor_index_by_name.get(floor_name.upper())
    if floor_index is None:
        # Route-derived entries are stamped with the campus graph's street
        # level ("G"), but not every building names a floor that: Humanities'
        # stack is "1".."9". External entries are street-level by
        # construction, so resolve to the building's lowest non-basement
        # floor instead of dropping the door - dropping every entry left the
        # building without any placement entrance and aborted publishing.
        ground_candidates = {
            name: index
            for name, index in floor_index_by_name.items()
            if not _is_basement_floor_name(name)
        } or floor_index_by_name
        if not ground_candidates:
            return None
        fallback_name, floor_index = min(ground_candidates.items(), key=lambda kv: kv[1])
        floor_name = _canonical_floor_name(fallback_name)
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
        "source_entry_id": source_entry_id or entry_id,
        "source_external_id": source_external_id or entry_id,
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


def _collect_route_step_lines(
    route_data: dict[str, Any],
    projector: LocalProjector,
    floor_height_by_name: dict[str, float],
    geometries_by_floor: dict[str, list[Any]],
) -> None:
    for route in route_data.get("routes") or []:
        if not isinstance(route, dict):
            continue
        for leg in route.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            for step in leg.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                abutters = str(step.get("abutters") or "")
                if abutters and abutters != "InsideBuilding":
                    continue
                points = _route_step_local_points(step, leg, projector, floor_height_by_name)
                _append_same_floor_route_runs(points, geometries_by_floor)


def _append_same_floor_route_runs(
    points: list[tuple[str, float, float]],
    geometries_by_floor: dict[str, list[Any]],
) -> None:
    current_floor = ""
    current_coords: list[tuple[float, float]] = []

    def flush() -> None:
        if current_floor and len(current_coords) >= 2:
            geometries_by_floor[current_floor].append(LineString(current_coords))

    for floor_name, x, z in points:
        coord = (float(x), float(z))
        if floor_name != current_floor:
            flush()
            current_floor = floor_name
            current_coords = [coord]
            continue
        if current_coords and _distance_2d(current_coords[-1][0], current_coords[-1][1], coord[0], coord[1]) < 0.05:
            continue
        current_coords.append(coord)
    flush()


def _collect_manifest_walk_link_lines(
    walk_links: list[dict[str, Any]],
    floor_name_by_index: dict[int, str],
    geometries_by_floor: dict[str, list[Any]],
) -> None:
    for link in walk_links:
        if not isinstance(link, dict):
            continue
        if str(link.get("kind", "")) != "walk":
            continue
        from_floor = int(link.get("from_floor_index", -999))
        to_floor = int(link.get("to_floor_index", -999))
        if from_floor != to_floor:
            continue
        floor_name = floor_name_by_index.get(from_floor)
        if floor_name not in geometries_by_floor:
            continue
        from_anchor = link.get("from_anchor")
        to_anchor = link.get("to_anchor")
        if not _valid_local_anchor(from_anchor) or not _valid_local_anchor(to_anchor):
            continue
        if _distance_2d(float(from_anchor[0]), float(from_anchor[2]), float(to_anchor[0]), float(to_anchor[2])) < 0.05:
            continue
        geometries_by_floor[floor_name].append(
            LineString(
                [
                    (float(from_anchor[0]), float(from_anchor[2])),
                    (float(to_anchor[0]), float(to_anchor[2])),
                ]
            )
        )


def _route_step_local_points(
    step: dict[str, Any],
    leg: dict[str, Any],
    projector: LocalProjector,
    floor_height_by_name: dict[str, float],
) -> list[tuple[str, float, float]]:
    fallback_floor = _route_floor_name(step.get("floor_name") or (leg.get("start_location") or {}).get("floor_name"))
    points: list[tuple[str, float, float]] = []
    for point in step.get("geometry") or []:
        if not isinstance(point, dict):
            continue
        floor_name = _route_floor_name(point.get("floor_name") or fallback_floor)
        if floor_name not in floor_height_by_name:
            continue
        if "lat" not in point or "lng" not in point:
            continue
        local = projector.to_local(float(point["lng"]), float(point["lat"]), floor_height_by_name[floor_name])
        points.append((floor_name, float(local[0]), float(local[2])))
    return points


def _route_floor_name(value: Any) -> str:
    return _canonical_floor_name(str(value or "").strip())


def _iter_route_polygons(geometry: Any) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry] if not geometry.is_empty and geometry.area > 0.01 else []
    if isinstance(geometry, MultiPolygon):
        return [polygon for polygon in sorted(geometry.geoms, key=lambda item: item.area, reverse=True) if polygon.area > 0.01]
    return []


def _distance_2d(x1: float, z1: float, x2: float, z2: float) -> float:
    return ((x2 - x1) ** 2 + (z2 - z1) ** 2) ** 0.5


def _route_navigation_point_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for room in manifest.get("rooms", []):
        if not isinstance(room, dict):
            continue
        navigation_anchor = room.get("navigation_anchor")
        if _valid_local_anchor(navigation_anchor):
            records.append(
                {
                    "kind": "room",
                    "floor_name": room.get("floor_name", ""),
                    "floor_index": int(room.get("floor_index", 0)),
                    "anchor": navigation_anchor,
                    "node_name": room.get("node_name", ""),
                    "external_id": room.get("external_id", ""),
                    "source_id": room.get("source_id", ""),
                }
            )
    for key in ("portals", "external_doors"):
        for record in manifest.get(key, []):
            if not isinstance(record, dict):
                continue
            anchor = (
                record.get("navigation_anchor")
                if key == "external_doors" and _valid_local_anchor(record.get("navigation_anchor"))
                else record.get("anchor")
            )
            if _valid_local_anchor(anchor):
                records.append(
                    {
                        "kind": str(record.get("kind") or key[:-1]),
                        "floor_name": record.get("floor_name", ""),
                        "floor_index": int(record.get("floor_index", 0)),
                        "anchor": anchor,
                        "node_name": record.get("node_name", ""),
                        "external_id": record.get("external_id") or record.get("entry_id", ""),
                        "source_id": record.get("source_id", ""),
                    }
                )
    return records


def _route_navigation_walk_link_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    nav = manifest.get("nav", {})
    if not isinstance(nav, dict):
        return []
    links = nav.get("links", [])
    if not isinstance(links, list):
        return []
    return [
        link
        for link in links
        if isinstance(link, dict)
        and str(link.get("kind", "")) == "walk"
        and int(link.get("from_floor_index", -999)) == int(link.get("to_floor_index", -999))
        and _valid_local_anchor(link.get("from_anchor"))
        and _valid_local_anchor(link.get("to_anchor"))
    ]


def _append_route_navigation_connectors(
    geometries_by_floor: dict[str, list[Any]],
    point_records_by_floor: dict[str, list[dict[str, Any]]],
    *,
    wall_blocker_indexes: dict[str, tuple[Any, list[LineString]]] | None = None,
) -> None:
    wall_blocker_indexes = wall_blocker_indexes or {}
    for floor_name, records in point_records_by_floor.items():
        if not records:
            continue
        geometries = geometries_by_floor.get(floor_name)
        if geometries is None:
            continue

        record_points = [
            (record, point)
            for record in records
            if (point := _point_from_anchor(record.get("anchor"))) is not None
        ]
        if not record_points:
            continue

        wall_blocker_index = wall_blocker_indexes.get(_canonical_floor_name(floor_name))
        seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        route_lines = [geometry for geometry in geometries if isinstance(geometry, LineString)]
        for _record, point in record_points:
            if not route_lines:
                continue
            nearest_line = min(route_lines, key=lambda line: line.distance(point))
            nearest_point = nearest_line.interpolate(nearest_line.project(point))
            _append_connector_line(geometries, seen, point, nearest_point, ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE, wall_blocker_index=wall_blocker_index)
        if route_lines:
            continue

        _append_nearest_point_connectors(geometries, seen, record_points, ROUTE_NAV_POINT_CONNECTOR_NEIGHBORS, wall_blocker_index=wall_blocker_index)
        portal_points = [(record, point) for record, point in record_points if _is_portal_point_record(record)]
        if portal_points:
            _append_nearest_portal_connectors(geometries, seen, record_points, portal_points, wall_blocker_index=wall_blocker_index)


def _append_nearest_point_connectors(
    geometries: list[Any],
    seen: set[tuple[tuple[float, float], tuple[float, float]]],
    record_points: list[tuple[dict[str, Any], Point]],
    neighbor_count: int,
    *,
    wall_blocker_index: tuple[Any, list[LineString]] | None = None,
) -> None:
    for start_index, (_record, point) in enumerate(record_points):
        candidates: list[tuple[float, int, Point]] = []
        for end_index, (_other_record, other) in enumerate(record_points):
            if start_index == end_index:
                continue
            distance = point.distance(other)
            if 0.05 < distance <= ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE:
                candidates.append((distance, end_index, other))
        for _distance, _end_index, other in sorted(candidates, key=lambda item: (item[0], item[1]))[:neighbor_count]:
            _append_connector_line(geometries, seen, point, other, ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE, wall_blocker_index=wall_blocker_index)


def _append_nearest_portal_connectors(
    geometries: list[Any],
    seen: set[tuple[tuple[float, float], tuple[float, float]]],
    record_points: list[tuple[dict[str, Any], Point]],
    portal_points: list[tuple[dict[str, Any], Point]],
    *,
    wall_blocker_index: tuple[Any, list[LineString]] | None = None,
) -> None:
    for record, point in record_points:
        candidates: list[tuple[float, str, Point]] = []
        for portal_record, portal_point in portal_points:
            if record is portal_record:
                continue
            distance = point.distance(portal_point)
            if 0.05 < distance <= ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE:
                candidates.append((distance, _record_link_key(portal_record), portal_point))
        neighbor_count = (
            ROUTE_NAV_PORTAL_CONNECTOR_NEIGHBORS
            if _is_portal_point_record(record)
            else ROUTE_NAV_ROOM_PORTAL_CONNECTOR_NEIGHBORS
        )
        for _distance, _key, portal_point in sorted(candidates, key=lambda item: (item[0], item[1]))[:neighbor_count]:
            _append_connector_line(geometries, seen, point, portal_point, ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE, wall_blocker_index=wall_blocker_index)


def _append_connector_line(
    geometries: list[Any],
    seen: set[tuple[tuple[float, float], tuple[float, float]]],
    start: Point,
    end: Point,
    max_distance: float,
    *,
    wall_blocker_index: tuple[Any, list[LineString]] | None = None,
) -> bool:
    distance = start.distance(end)
    if distance <= 0.05 or distance > max_distance:
        return False
    key = tuple(sorted((_point_key(start), _point_key(end))))  # type: ignore[assignment]
    if key in seen:
        return False
    line = LineString([(float(start.x), float(start.y)), (float(end.x), float(end.y))])
    if _line_blocked_by_wall(line, wall_blocker_index):
        return False
    seen.add(key)
    geometries.append(line)
    return True


def _point_from_anchor(anchor: Any) -> Point | None:
    if not _valid_local_anchor(anchor):
        return None
    return Point(float(anchor[0]), float(anchor[2]))


def _point_key(point: Point) -> tuple[float, float]:
    return (round(float(point.x), 3), round(float(point.y), 3))


def _is_portal_point_record(record: dict[str, Any]) -> bool:
    return str(record.get("kind", "")).lower() in {"stair", "elevator", "door", "portal"}


def _add_same_floor_walk_links(
    manifest: dict[str, Any],
    *,
    wall_blockers_by_floor: dict[str, list[LineString]] | None = None,
) -> None:
    nav = manifest.setdefault("nav", {})
    links = nav.setdefault("links", [])
    if not isinstance(links, list):
        return
    wall_blocker_indexes = _route_wall_blocker_indexes(wall_blockers_by_floor or {})

    existing = {
        (
            str(link.get("kind", "")),
            str(link.get("from_source_id") or link.get("from_node_name") or link.get("from_external_id", "")),
            str(link.get("to_source_id") or link.get("to_node_name") or link.get("to_external_id", "")),
            int(link.get("from_floor_index", -999)),
            int(link.get("to_floor_index", -999)),
        )
        for link in links
        if isinstance(link, dict)
    }

    records_by_floor: dict[int, list[dict[str, Any]]] = {}
    for record in _route_navigation_point_records(manifest):
        anchor = record.get("anchor")
        if not _valid_local_anchor(anchor):
            continue
        records_by_floor.setdefault(int(record.get("floor_index", 0)), []).append(record)

    for floor_index, records in sorted(records_by_floor.items()):
        for start, end in _nearest_point_record_pairs(records):
            floor_name = _canonical_floor_name(str(start.get("floor_name") or end.get("floor_name") or ""))
            if _anchors_cross_wall(start.get("anchor"), end.get("anchor"), wall_blocker_indexes.get(floor_name)):
                continue
            from_key = _record_link_key(start)
            to_key = _record_link_key(end)
            link_key = ("walk", from_key, to_key, floor_index, floor_index)
            reverse_key = ("walk", to_key, from_key, floor_index, floor_index)
            if link_key in existing or reverse_key in existing:
                continue
            existing.add(link_key)
            links.append(
                {
                    "kind": "walk",
                    "group_id": f"floor_{floor_index}",
                    "from_external_id": start.get("external_id", ""),
                    "to_external_id": end.get("external_id", ""),
                    "from_source_id": start.get("source_id", ""),
                    "to_source_id": end.get("source_id", ""),
                    "from_node_name": start.get("node_name", ""),
                    "to_node_name": end.get("node_name", ""),
                    "from_floor_index": floor_index,
                    "to_floor_index": floor_index,
                    "from_anchor": start.get("anchor"),
                    "to_anchor": end.get("anchor"),
                    "distance": round(_anchor_distance(start.get("anchor"), end.get("anchor")), 3),
                    "bidirectional": True,
                }
            )


def _anchors_cross_wall(start: Any, end: Any, wall_blocker_index: tuple[Any, list[LineString]] | None) -> bool:
    if not _valid_local_anchor(start) or not _valid_local_anchor(end):
        return False
    line = LineString([(float(start[0]), float(start[2])), (float(end[0]), float(end[2]))])
    return _line_blocked_by_wall(line, wall_blocker_index)


def _nearest_point_record_pairs(records: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    _append_nearest_point_link_pairs(pairs, records, ROUTE_NAV_POINT_CONNECTOR_NEIGHBORS)
    portal_records = [record for record in records if _is_portal_point_record(record)]
    if portal_records:
        _append_nearest_portal_link_pairs(pairs, records, portal_records)
    return pairs


def _append_nearest_point_link_pairs(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    records: list[dict[str, Any]],
    neighbor_count: int,
) -> None:
    for start_index, record in enumerate(records):
        anchor = record.get("anchor")
        if not _valid_local_anchor(anchor):
            continue
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for end_index, other in enumerate(records):
            if start_index == end_index:
                continue
            other_anchor = other.get("anchor")
            if not _valid_local_anchor(other_anchor):
                continue
            distance = _anchor_distance(anchor, other_anchor)
            if 0.05 < distance <= ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE:
                candidates.append((distance, end_index, other))
        for _distance, _end_index, other in sorted(candidates, key=lambda item: (item[0], item[1]))[:neighbor_count]:
            pairs.append((record, other))


def _append_nearest_portal_link_pairs(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    records: list[dict[str, Any]],
    portal_records: list[dict[str, Any]],
) -> None:
    for record in records:
        anchor = record.get("anchor")
        if not _valid_local_anchor(anchor):
            continue
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        for portal in portal_records:
            if record is portal:
                continue
            portal_anchor = portal.get("anchor")
            if not _valid_local_anchor(portal_anchor):
                continue
            distance = _anchor_distance(anchor, portal_anchor)
            if 0.05 < distance <= ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE:
                candidates.append((distance, _record_link_key(portal), portal))
        neighbor_count = (
            ROUTE_NAV_PORTAL_CONNECTOR_NEIGHBORS
            if _is_portal_point_record(record)
            else ROUTE_NAV_ROOM_PORTAL_CONNECTOR_NEIGHBORS
        )
        for _distance, _key, portal in sorted(candidates, key=lambda item: (item[0], item[1]))[:neighbor_count]:
            pairs.append((record, portal))


def _record_link_key(record: dict[str, Any]) -> str:
    return str(record.get("source_id") or record.get("node_name") or record.get("external_id") or record.get("anchor", ""))


def _anchor_distance(start: Any, end: Any) -> float:
    if not _valid_local_anchor(start) or not _valid_local_anchor(end):
        return 0.0
    return _distance_2d(float(start[0]), float(start[2]), float(end[0]), float(end[2]))


def _scene_navigation_meshes_with_floor_fallback(
    route_meshes: list[MeshData],
    geometry_meshes: list[MeshData],
    floors: list[dict[str, Any]],
) -> list[MeshData]:
    """Route-derived navmesh per floor, geometry navmesh for uncovered floors.

    The scene navmesh must exist for EVERY floor that has rooms, but route
    coverage can legitimately miss small mezzanines. Only the topology's
    same-floor transfer proof stays route-only (a whole-slab fallback would
    fabricate walkable bridges between physically separate wings).
    """
    # Keep this list identical to the NavigationMesh resources emitted by
    # building3d.unimate: floor surfaces are authoritative. Including walls,
    # room prisms, or tiny portal marker meshes here made portal-topology
    # validation prove connectivity on geometry that Godot never shipped.
    geometry_floor_meshes = [
        mesh
        for mesh in geometry_meshes
        if mesh.material == "floor"
    ]
    if not route_meshes:
        return geometry_floor_meshes
    covered_floor_names = {_floor_name_from_route_nav_mesh(mesh.name) for mesh in route_meshes}
    fallback = [
        mesh
        for mesh in geometry_floor_meshes
        if _canonical_floor_name(mesh_floor_name(mesh.name)) not in covered_floor_names
    ]
    return route_meshes + fallback


def _assign_external_door_navigation_anchors(
    manifest: dict[str, Any],
    navigation_meshes: list[MeshData],
    *,
    max_distance: float = EXTERNAL_DOOR_NAVIGATION_ANCHOR_MAX_DISTANCE,
) -> dict[str, Any]:
    """Attach physical campus doors to a routable indoor navmesh component.

    A manually researched street/courtyard marker is intentionally a physical
    point, not proof that the polygon directly below it belongs to the indoor
    route network. Kenneth Myers exposed the failure mode: its MainDoor landed
    on a seven-polygon exterior slab while every room, lift, and stair lived on
    the adjacent interior component. MapsIndoors correctly routed to Level 3,
    but the generated topology started from the isolated slab and blamed the
    room.

    Preserve ``anchor`` as the official campus point and write
    ``navigation_anchor`` on the nearest component containing a vertical
    connector (or, for one-floor buildings, rooms). Both the topology and the
    Godot scene consume the navigation anchor; provenance remains available in
    the manifest and scene metadata.
    """
    doors = [
        record
        for record in manifest.get("external_doors", [])
        if isinstance(record, dict) and _valid_local_anchor(record.get("anchor"))
    ]
    diagnostics: dict[str, Any] = {
        "door_count": len(doors),
        "relocated_count": 0,
        "unchanged_count": 0,
        "failed_count": 0,
        "max_allowed_distance": float(max_distance),
        "records": [],
        "ok": True,
    }
    if not doors:
        return diagnostics

    components_by_floor = _navigation_components_by_floor(
        navigation_meshes,
        manifest.get("floors", []),
    )
    vertical_records_by_floor: dict[int, list[dict[str, Any]]] = {}
    for portal in manifest.get("portals", []):
        if not isinstance(portal, dict):
            continue
        if str(portal.get("kind", "")).lower() not in {"stair", "elevator"}:
            continue
        if not _valid_local_anchor(portal.get("anchor")):
            continue
        vertical_records_by_floor.setdefault(int(portal.get("floor_index", 0)), []).append(portal)

    room_records_by_floor: dict[int, list[dict[str, Any]]] = {}
    for room in manifest.get("rooms", []):
        if not isinstance(room, dict):
            continue
        anchor = room.get("navigation_anchor") or room.get("anchor")
        if not _valid_local_anchor(anchor):
            continue
        room_records_by_floor.setdefault(int(room.get("floor_index", 0)), []).append(room)

    for door in doors:
        floor_index = int(door.get("floor_index", 0))
        components = components_by_floor.get(floor_index, [])
        source_anchor = door["anchor"]
        source_point = Point(float(source_anchor[0]), float(source_anchor[2]))
        record_diagnostic: dict[str, Any] = {
            "external_id": str(door.get("external_id") or door.get("entry_id") or ""),
            "node_name": str(door.get("node_name") or ""),
            "floor_index": floor_index,
            "source_anchor": [float(source_anchor[0]), float(source_anchor[1]), float(source_anchor[2])],
            "component_count": len(components),
            "ok": False,
        }
        if not components:
            record_diagnostic["reason"] = "floor_has_no_navigation_components"
            diagnostics["failed_count"] += 1
            diagnostics["ok"] = False
            diagnostics["records"].append(record_diagnostic)
            continue

        vertical_counts = _component_reference_counts(
            components,
            [
                portal.get("anchor")
                for portal in vertical_records_by_floor.get(floor_index, [])
            ],
        )
        room_counts = _component_reference_counts(
            components,
            [
                room.get("navigation_anchor") or room.get("anchor")
                for room in room_records_by_floor.get(floor_index, [])
            ],
        )
        source_component_index = _nearest_navigation_component_index(source_point, components)

        if any(count > 0 for count in vertical_counts):
            candidate_indexes = [
                index for index, count in enumerate(vertical_counts) if count > 0
            ]
            selection_reason = "vertical_network_component"
        elif any(count > 0 for count in room_counts):
            candidate_indexes = [
                index for index, count in enumerate(room_counts) if count > 0
            ]
            selection_reason = "room_network_component"
        else:
            candidate_indexes = list(range(len(components)))
            selection_reason = "largest_navigation_component"

        target_component_index = min(
            candidate_indexes,
            key=lambda index: (
                source_point.distance(components[index]),
                -vertical_counts[index],
                -room_counts[index],
                -components[index].area,
                index,
            ),
        )
        target_component = components[target_component_index]
        target_point = (
            source_point
            if target_component.covers(source_point)
            else nearest_points(source_point, target_component)[1]
        )
        correction_distance = float(source_point.distance(target_point))
        navigation_anchor = [
            round(float(target_point.x), 6),
            float(source_anchor[1]),
            round(float(target_point.y), 6),
        ]

        record_diagnostic.update(
            {
                "source_component_index": source_component_index,
                "target_component_index": target_component_index,
                "target_vertical_count": vertical_counts[target_component_index],
                "target_room_count": room_counts[target_component_index],
                "selection_reason": selection_reason,
                "navigation_anchor": navigation_anchor,
                "correction_distance": round(correction_distance, 6),
                "component_changed": (
                    source_component_index is not None
                    and source_component_index != target_component_index
                ),
            }
        )
        if correction_distance > max_distance:
            record_diagnostic["reason"] = "navigation_component_too_far"
            diagnostics["failed_count"] += 1
            diagnostics["ok"] = False
            diagnostics["records"].append(record_diagnostic)
            continue

        door["navigation_anchor"] = navigation_anchor
        door["navigation_anchor_source"] = "connected_navigation_component"
        door["navigation_anchor_confidence"] = "high"
        door["navigation_anchor_distance"] = round(correction_distance, 6)
        door["navigation_anchor_component_reason"] = selection_reason
        door["navigation_anchor_component_index"] = target_component_index
        door["navigation_anchor_source_component_index"] = source_component_index
        door["navigation_anchor_relocated"] = correction_distance > 0.05
        record_diagnostic["ok"] = True
        record_diagnostic["reason"] = (
            "relocated_to_routable_component"
            if correction_distance > 0.05
            else "already_on_routable_component"
        )
        if correction_distance > 0.05:
            diagnostics["relocated_count"] += 1
        else:
            diagnostics["unchanged_count"] += 1
        diagnostics["records"].append(record_diagnostic)

    if not diagnostics["ok"]:
        failures = [
            "%s:%s" % (
                record.get("external_id") or record.get("node_name") or "door",
                record.get("reason", "unknown"),
            )
            for record in diagnostics["records"]
            if not bool(record.get("ok", False))
        ]
        # Drop the unusable entries rather than aborting the build. External
        # doors are merged from several sources, so one bad route-derived
        # point should not take the whole building down -- that is what made
        # `group art` and `group music` unrunnable. The guarantee worth keeping
        # is that SOME entrance survives; without one the building really is
        # unpublishable, so that case still raises.
        failed_ids = {
            str(record.get("external_id") or "")
            for record in diagnostics["records"]
            if not bool(record.get("ok", False))
        }
        survivors = [
            record
            for record in manifest.get("external_doors", [])
            if not (isinstance(record, dict)
                    and str(record.get("external_id") or "") in failed_ids)
        ]
        if not survivors:
            raise ValueError(
                "External entrance navigation-anchor validation failed for every "
                "door: %s" % ", ".join(failures)
            )
        manifest["external_doors"] = survivors
        diagnostics["dropped"] = sorted(failed_ids)
        diagnostics["ok"] = True
        print(
            f"  external doors: dropped {len(failed_ids)} unusable entrance(s): "
            + ", ".join(failures)
        )
    return diagnostics


def _navigation_components_by_floor(
    navigation_meshes: list[MeshData],
    floors: list[dict[str, Any]],
) -> dict[int, list[Polygon]]:
    floor_index_by_name = {
        _canonical_floor_name(str(floor.get("floor_name", ""))): int(floor.get("floor_index", 0))
        for floor in floors
        if isinstance(floor, dict)
    }
    polygons_by_floor: dict[int, list[Polygon]] = {}
    for mesh in navigation_meshes:
        if mesh.material != "floor":
            continue
        floor_name = _canonical_floor_name(mesh_floor_name(mesh.name))
        if floor_name not in floor_index_by_name:
            continue
        floor_index = floor_index_by_name[floor_name]
        for face in mesh.faces:
            coordinates = []
            for vertex_index in face:
                if not (0 <= int(vertex_index) < len(mesh.vertices)):
                    continue
                vertex = mesh.vertices[int(vertex_index)]
                if len(vertex) >= 3:
                    coordinates.append((float(vertex[0]), float(vertex[2])))
            if len(coordinates) < 3:
                continue
            polygon = Polygon(coordinates).buffer(0)
            if polygon.is_empty or polygon.area <= 0.0001:
                continue
            polygons_by_floor.setdefault(floor_index, []).extend(
                _iter_route_polygons(polygon)
            )

    components_by_floor: dict[int, list[Polygon]] = {}
    for floor_index, polygons in polygons_by_floor.items():
        merged = unary_union(polygons).buffer(0)
        components_by_floor[floor_index] = _iter_route_polygons(merged)
    return components_by_floor


def _component_reference_counts(
    components: list[Polygon],
    anchors: list[Any],
) -> list[int]:
    counts = [0 for _component in components]
    for anchor in anchors:
        if not _valid_local_anchor(anchor):
            continue
        point = Point(float(anchor[0]), float(anchor[2]))
        component_index = _nearest_navigation_component_index(point, components)
        if component_index is not None:
            counts[component_index] += 1
    return counts


def _nearest_navigation_component_index(
    point: Point,
    components: list[Polygon],
) -> int | None:
    for index, component in enumerate(components):
        if component.covers(point):
            return index
    if not components:
        return None
    return min(
        range(len(components)),
        key=lambda index: (point.distance(components[index]), index),
    )


def _sync_external_door_navigation_links(manifest: dict[str, Any]) -> None:
    doors_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for door in manifest.get("external_doors", []):
        if not isinstance(door, dict) or not _valid_local_anchor(door.get("navigation_anchor")):
            continue
        floor_index = int(door.get("floor_index", 0))
        for key_name, value in (
            ("source_id", door.get("source_id")),
            ("external_id", door.get("external_id") or door.get("entry_id")),
            ("node_name", door.get("node_name")),
        ):
            if value:
                doors_by_key[(key_name, str(value), floor_index)] = door

    links = manifest.get("nav", {}).get("links", [])
    if not isinstance(links, list):
        return
    for link in links:
        if not isinstance(link, dict) or str(link.get("kind", "")) != "walk":
            continue
        for side in ("from", "to"):
            floor_index = int(link.get(f"{side}_floor_index", -999))
            matched = None
            for key_name in ("source_id", "external_id", "node_name"):
                value = link.get(f"{side}_{key_name}")
                if value and (key_name, str(value), floor_index) in doors_by_key:
                    matched = doors_by_key[(key_name, str(value), floor_index)]
                    break
            if matched is not None:
                link[f"{side}_anchor"] = matched["navigation_anchor"]


def _assert_external_door_topology_health(topology: dict[str, Any]) -> None:
    validation = topology.get("validation", {})
    if not isinstance(validation, dict):
        return
    external_door_count = int(validation.get("external_door_terminal_count", 0))
    vertical_edge_count = int(validation.get("vertical_edge_count", 0))
    routable_count = int(validation.get("routable_external_door_count", 0))
    if external_door_count > 0 and vertical_edge_count > 0 and routable_count == 0:
        unreachable = ", ".join(
            str(value)
            for value in validation.get("unreachable_external_door_ids", [])
        )
        raise ValueError(
            "Portal topology has no external entrance connected to its vertical "
            "network. Refusing to publish a topology that would mislabel rooms "
            "as inaccessible. Isolated entrances: %s" % (unreachable or "unknown")
        )


def _required_navigation_floor_names(manifest: dict[str, Any]) -> set[str]:
    floor_names_by_index = {
        int(floor.get("floor_index", 0)): _canonical_floor_name(str(floor.get("floor_name", "")))
        for floor in manifest.get("floors", [])
        if isinstance(floor, dict)
    }
    required_indexes: set[int] = set()
    for key in ("rooms", "portals", "external_doors"):
        for record in manifest.get(key, []):
            if isinstance(record, dict):
                required_indexes.add(int(record.get("floor_index", 0)))
    return {floor_names_by_index[index] for index in required_indexes if index in floor_names_by_index}


def _floor_name_from_route_nav_mesh(name: str) -> str:
    if not name.startswith("floor__"):
        return ""
    return name[len("floor__") :].split("__", 1)[0]


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
            "navigation_anchor": door.get("navigation_anchor", door.get("anchor")),
            "navigation_anchor_source": door.get("navigation_anchor_source", ""),
            "navigation_anchor_distance": door.get("navigation_anchor_distance", 0.0),
            "kind": "door",
            "bidirectional": True,
            "confidence": door.get("confidence", ""),
            "supporting_routes": door.get("supporting_routes", 0),
        }
        for door in manifest.get("external_doors", [])
        if door.get("anchor") and door.get("node_name")
    ]


def _ensure_vertical_route_derivation_summary(manifest: dict[str, Any], stats: dict[str, Any]) -> None:
    nav = manifest.setdefault("nav", {})
    if nav.get("vertical_route_derivation"):
        return
    route_links = [
        link
        for link in nav.get("links", [])
        if isinstance(link, dict) and str(link.get("source", "")) == "mapsindoors_route_graph"
    ]
    if not route_links:
        return
    nav["vertical_route_derivation"] = {
        "graph_id": str(stats.get("graph_id") or DEFAULT_GRAPH_ID),
        "candidates": int(stats.get("candidates", 0)),
        "accepted": len(route_links),
        "rejected": int(stats.get("rejected", 0)),
    }


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


def _write_floor_walkable_path_glbs(
    meshes: list[MeshData],
    floors: list[dict[str, Any]],
    export_dir: Path,
    group_id: str,
) -> dict[int, str]:
    floor_files: dict[int, str] = {}
    if not meshes:
        return floor_files
    floor_height_by_name = {
        str(floor.get("floor_name", "")): float(floor.get("height", 0.0))
        for floor in floors
    }
    debug_meshes = [
        MeshData(
            name=mesh.name,
            vertices=[list(vertex) for vertex in mesh.vertices],
            faces=[list(face) for face in mesh.faces],
            material="walkable_path",
            metadata=dict(mesh.metadata),
        )
        for mesh in meshes
    ]
    for floor in sorted(floors, key=lambda item: int(item.get("floor_index", 0))):
        floor_index = int(floor.get("floor_index", 0))
        floor_name = str(floor.get("floor_name", floor_index))
        floor_height = floor_height_by_name.get(floor_name, float(floor.get("height", 0.0)))
        floor_meshes = [
            _raise_floor_debug_mesh(localize_mesh_to_floor(mesh, floor_height))
            for mesh in debug_meshes
            if mesh_floor_name(mesh.name) == floor_name
        ]
        if not floor_meshes:
            continue
        filename = _floor_walkable_path_glb_name(group_id, floor_index)
        write_glb(floor_meshes, export_dir / filename)
        floor_files[floor_index] = filename
    return floor_files


def _write_floor_route_debug_glbs(
    visual_meshes: list[MeshData],
    route_meshes: list[MeshData],
    floors: list[dict[str, Any]],
    export_dir: Path,
    group_id: str,
) -> dict[int, str]:
    floor_files: dict[int, str] = {}
    if not route_meshes:
        return floor_files
    floor_height_by_name = {
        str(floor.get("floor_name", "")): float(floor.get("height", 0.0))
        for floor in floors
    }
    for floor in sorted(floors, key=lambda item: int(item.get("floor_index", 0))):
        floor_index = int(floor.get("floor_index", 0))
        floor_name = str(floor.get("floor_name", floor_index))
        floor_height = floor_height_by_name.get(floor_name, float(floor.get("height", 0.0)))
        floor_visual_meshes = floor_visual_meshes_from_meshes(visual_meshes, floor_name, floor_height)
        floor_route_meshes = [
            _raise_floor_debug_mesh(localize_mesh_to_floor(mesh, floor_height))
            for mesh in route_meshes
            if mesh_floor_name(mesh.name) == floor_name
        ]
        if not floor_route_meshes:
            continue
        filename = _floor_route_debug_glb_name(group_id, floor_index)
        write_glb([*floor_visual_meshes, *floor_route_meshes], export_dir / filename)
        floor_files[floor_index] = filename
    return floor_files


def _raise_floor_debug_mesh(mesh: MeshData) -> MeshData:
    return MeshData(
        name=mesh.name,
        vertices=[[float(vertex[0]), round(float(vertex[1]) + 0.08, 6), float(vertex[2])] for vertex in mesh.vertices],
        faces=[list(face) for face in mesh.faces],
        material=mesh.material,
        metadata=dict(mesh.metadata),
    )


def _floor_visual_glb_name(group_id: str, floor_index: int) -> str:
    if floor_index < 0:
        return f"{group_id}_floor_neg{abs(floor_index)}_visual.glb"
    return f"{group_id}_floor_{floor_index}_visual.glb"


def _floor_walkable_path_glb_name(group_id: str, floor_index: int) -> str:
    if floor_index < 0:
        return f"{group_id}_floor_neg{abs(floor_index)}_walkable_paths.glb"
    return f"{group_id}_floor_{floor_index}_walkable_paths.glb"


def _floor_route_debug_glb_name(group_id: str, floor_index: int) -> str:
    if floor_index < 0:
        return f"{group_id}_floor_neg{abs(floor_index)}_route_debug.glb"
    return f"{group_id}_floor_{floor_index}_route_debug.glb"


def _route_clip_footprints_by_floor(meshes: list[MeshData], floors: list[dict[str, Any]]) -> dict[str, Any]:
    floor_names = {_canonical_floor_name(str(floor.get("floor_name", ""))) for floor in floors}
    polygons_by_floor: dict[str, list[Polygon]] = {floor_name: [] for floor_name in floor_names}
    for mesh in meshes:
        floor_name = _canonical_floor_name(mesh_floor_name(mesh.name))
        if floor_name not in polygons_by_floor or not mesh.name.startswith("floor__"):
            continue
        polygons_by_floor[floor_name].extend(_mesh_top_footprint_polygons(mesh))
    footprints: dict[str, Any] = {}
    for floor_name, polygons in polygons_by_floor.items():
        valid_polygons = [polygon for polygon in polygons if not polygon.is_empty and polygon.area > 0.01]
        if not valid_polygons:
            continue
        footprint = unary_union(valid_polygons).buffer(0)
        if not footprint.is_empty:
            footprints[floor_name] = footprint
    return footprints


def _mesh_top_footprint_polygons(mesh: MeshData) -> list[Polygon]:
    if not mesh.vertices:
        return []
    top_y = max(float(vertex[1]) for vertex in mesh.vertices)
    polygons: list[Polygon] = []
    for face in mesh.faces:
        face_vertices = [mesh.vertices[index] for index in face if 0 <= index < len(mesh.vertices)]
        if len(face_vertices) < 3:
            continue
        if any(abs(float(vertex[1]) - top_y) > 0.0001 for vertex in face_vertices):
            continue
        polygon = Polygon((float(vertex[0]), float(vertex[2])) for vertex in face_vertices)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty and polygon.area > 0.01:
            polygons.append(polygon)
    return polygons


def _route_wall_blockers_by_floor(meshes: list[MeshData]) -> dict[str, list[LineString]]:
    blockers: dict[str, list[LineString]] = {}
    for mesh in meshes:
        if mesh.material != "wall_low":
            continue
        floor_name = _canonical_floor_name(mesh_floor_name(mesh.name))
        if not floor_name:
            continue
        for face in mesh.faces:
            line = _wall_face_line(mesh, face)
            if line is not None:
                blockers.setdefault(floor_name, []).append(line)
    return blockers


def _wall_face_line(mesh: MeshData, face: list[int]) -> LineString | None:
    points: list[tuple[float, float]] = []
    for index in face:
        if not 0 <= index < len(mesh.vertices):
            continue
        vertex = mesh.vertices[index]
        point = (round(float(vertex[0]), 6), round(float(vertex[2]), 6))
        if point not in points:
            points.append(point)
    if len(points) != 2:
        return None
    line = LineString(points)
    return line if line.length >= 0.05 else None


def _route_wall_blocker_indexes(
    wall_blockers_by_floor: dict[str, list[LineString]],
) -> dict[str, tuple[Any, list[LineString]]]:
    indexes: dict[str, tuple[Any, list[LineString]]] = {}
    for floor_name, blockers in wall_blockers_by_floor.items():
        valid = [blocker for blocker in blockers if isinstance(blocker, LineString) and not blocker.is_empty and blocker.length >= 0.05]
        if valid:
            indexes[_canonical_floor_name(floor_name)] = (STRtree(valid), valid)
    return indexes


def _query_wall_blockers(wall_blocker_index: tuple[Any, list[LineString]], geometry: Any) -> list[LineString]:
    tree, blockers = wall_blocker_index
    matches = list(tree.query(geometry))
    if not matches:
        return []
    first = matches[0]
    if hasattr(first, "geom_type"):
        return [match for match in matches if isinstance(match, LineString)]
    return [blockers[int(index)] for index in matches]


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
- `{group.id}_portal_topology.json`: exact portal terminals plus proven same-floor and vertical portal edges for Godot routing.
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

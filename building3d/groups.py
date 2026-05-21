from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import nearest_points, unary_union

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
from building3d.projection import LocalProjector, project_dataset
from building3d.unimate import write_unimate_scene


ROUTE_NAV_CORRIDOR_RADIUS = 1.8
ROUTE_NAV_POINT_RADIUS = 1.8
ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE = 60.0
ROUTE_NAV_POINT_CONNECTOR_NEIGHBORS = 3
ROUTE_NAV_ROOM_PORTAL_CONNECTOR_NEIGHBORS = 3
ROUTE_NAV_PORTAL_CONNECTOR_NEIGHBORS = 8
ROUTE_NAV_COMPONENT_BRIDGE_MAX_DISTANCE = 125.0
ROUTE_NAV_ANCHOR_ENVELOPE_CELL_SIZE = 20.0
ROUTE_NAV_ANCHOR_ENVELOPE_MARGIN = 2.0
ROUTE_NAV_ANCHOR_ENVELOPE_MIN_CELLS = 4
ROUTE_NAV_GRID_CELL_SIZE = 1.0
ROUTE_NAV_GRID_MIN_CELL_COVERAGE = 0.02
ROUTE_NAV_SIMPLIFY = 0.03
ROUTE_NAV_TRIANGLE_MIN_AREA = 0.001


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
    route_navigation_meshes = _complete_route_navigation_meshes(
        export_dir / "door_route_cache",
        manifest,
        origin_lon,
        origin_lat,
    )
    scene_navigation_meshes = route_navigation_meshes if route_navigation_meshes else meshes
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
    write_glb(navigation_meshes_from_meshes(scene_navigation_meshes), export_dir / names.nav_glb)
    write_manifest(manifest, export_dir / names.manifest)
    scene_path = write_unimate_scene(
        manifest,
        export_dir / f"{group.id}_unimate.tscn",
        asset_base_path=_unimate_asset_base(group),
        navigation_meshes=scene_navigation_meshes,
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
    _apply_room_navigation_anchors(manifest, processed_dir, export_dir, group)
    _dedupe_node_names(manifest)
    _sync_nav_node_names(manifest)
    _add_same_floor_walk_links(manifest)
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
) -> list[MeshData]:
    route_meshes = _route_navigation_meshes_from_cache(
        route_cache_dir,
        manifest.get("floors", []),
        origin_lon,
        origin_lat,
        point_records=_route_navigation_point_records(manifest),
        walk_links=_route_navigation_walk_link_records(manifest),
    )
    if not route_meshes:
        return []

    required_floor_names = _required_navigation_floor_names(manifest)
    route_floor_names = {_floor_name_from_route_nav_mesh(mesh.name) for mesh in route_meshes}
    if required_floor_names and not required_floor_names.issubset(route_floor_names):
        return []
    return route_meshes


def _route_navigation_meshes_from_cache(
    route_cache_dir: Path,
    floors: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    *,
    corridor_radius: float = ROUTE_NAV_CORRIDOR_RADIUS,
    point_records: list[dict[str, Any]] | None = None,
    walk_links: list[dict[str, Any]] | None = None,
) -> list[MeshData]:
    if not route_cache_dir.exists():
        return []

    floor_height_by_name = {
        _canonical_floor_name(str(floor.get("floor_name", ""))): float(floor.get("height", 0.0))
        for floor in floors
    }
    floor_name_by_index = {
        int(floor.get("floor_index", 0)): _canonical_floor_name(str(floor.get("floor_name", "")))
        for floor in floors
    }
    if not floor_height_by_name:
        return []

    projector = LocalProjector(origin_lon, origin_lat)
    geometries_by_floor: dict[str, list[Any]] = {floor_name: [] for floor_name in floor_height_by_name}
    for route_path in sorted(route_cache_dir.glob("route_*.json")):
        try:
            route_data = json.loads(route_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        _collect_route_step_lines(route_data, projector, floor_height_by_name, geometries_by_floor)
    _collect_manifest_walk_link_lines(walk_links or [], floor_name_by_index, geometries_by_floor)
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
        point_records_by_floor[floor_name].append(record)
        geometries_by_floor[floor_name].append(Point(float(anchor[0]), float(anchor[2])).buffer(ROUTE_NAV_POINT_RADIUS, quad_segs=8))

    _append_route_navigation_connectors(geometries_by_floor, point_records_by_floor)

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
            mesh = _route_polygon_to_mesh(floor_name, polygon, height, index)
            if not mesh.faces:
                continue
            floor_meshes.append(mesh)
        meshes.extend(floor_meshes)
    return meshes


def _route_polygon_to_mesh(floor_name: str, polygon: Polygon, height: float, index: int) -> MeshData:
    """Convert a route corridor polygon into edge-connected grid cells."""
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

    for z_index in range(z_count):
        z0 = start_z + z_index * cell_size
        z1 = z0 + cell_size
        for x_index in range(x_count):
            x0 = start_x + x_index * cell_size
            x1 = x0 + cell_size
            cell = box(x0, z0, x1, z1)
            covered_area = coverage_polygon.intersection(cell).area
            if covered_area < min_cell_area:
                continue
            top_left = vertex_index(x0, z0)
            top_right = vertex_index(x1, z0)
            bottom_left = vertex_index(x0, z1)
            bottom_right = vertex_index(x1, z1)
            faces.append([top_left, top_right, bottom_right, bottom_left])

    if faces:
        return MeshData(
            name=f"floor__{floor_name}{suffix}",
            vertices=vertices,
            faces=faces,
            material="floor",
            metadata={"godot_nav_overlay": "route_corridor_grid"},
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
                for start, end in zip(points, points[1:]):
                    if start[0] != end[0]:
                        continue
                    if _distance_2d(start[1], start[2], end[1], end[2]) < 0.05:
                        continue
                    geometries_by_floor[start[0]].append(LineString([(start[1], start[2]), (end[1], end[2])]))


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
            anchor = record.get("anchor")
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
) -> None:
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

        seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        route_lines = [geometry for geometry in geometries if isinstance(geometry, LineString)]
        for _record, point in record_points:
            if not route_lines:
                continue
            nearest_line = min(route_lines, key=lambda line: line.distance(point))
            nearest_point = nearest_line.interpolate(nearest_line.project(point))
            _append_connector_line(geometries, seen, point, nearest_point, ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE)
        if route_lines:
            continue

        _append_nearest_point_connectors(geometries, seen, record_points, ROUTE_NAV_POINT_CONNECTOR_NEIGHBORS)
        portal_points = [(record, point) for record, point in record_points if _is_portal_point_record(record)]
        if portal_points:
            _append_nearest_portal_connectors(geometries, seen, record_points, portal_points)


def _append_nearest_point_connectors(
    geometries: list[Any],
    seen: set[tuple[tuple[float, float], tuple[float, float]]],
    record_points: list[tuple[dict[str, Any], Point]],
    neighbor_count: int,
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
            _append_connector_line(geometries, seen, point, other, ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE)


def _append_nearest_portal_connectors(
    geometries: list[Any],
    seen: set[tuple[tuple[float, float], tuple[float, float]]],
    record_points: list[tuple[dict[str, Any], Point]],
    portal_points: list[tuple[dict[str, Any], Point]],
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
            _append_connector_line(geometries, seen, point, portal_point, ROUTE_NAV_POINT_CONNECTOR_MAX_DISTANCE)


def _append_connector_line(
    geometries: list[Any],
    seen: set[tuple[tuple[float, float], tuple[float, float]]],
    start: Point,
    end: Point,
    max_distance: float,
) -> None:
    distance = start.distance(end)
    if distance <= 0.05 or distance > max_distance:
        return
    key = tuple(sorted((_point_key(start), _point_key(end))))  # type: ignore[assignment]
    if key in seen:
        return
    seen.add(key)
    geometries.append(LineString([(float(start.x), float(start.y)), (float(end.x), float(end.y))]))


def _point_from_anchor(anchor: Any) -> Point | None:
    if not _valid_local_anchor(anchor):
        return None
    return Point(float(anchor[0]), float(anchor[2]))


def _point_key(point: Point) -> tuple[float, float]:
    return (round(float(point.x), 3), round(float(point.y), 3))


def _is_portal_point_record(record: dict[str, Any]) -> bool:
    return str(record.get("kind", "")).lower() in {"stair", "elevator", "door", "portal"}


def _add_same_floor_walk_links(manifest: dict[str, Any]) -> None:
    nav = manifest.setdefault("nav", {})
    links = nav.setdefault("links", [])
    if not isinstance(links, list):
        return

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

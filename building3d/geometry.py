from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


FLOOR_SLAB_PADDING = 0.25
FLOOR_SLAB_THICKNESS = 0.12
FLOOR_SLAB_SIMPLIFY = 0.03
ROOM_PLATE_OFFSET = 0.03
DOOR_OPEN_EDGE_TOLERANCE = 0.75
DOOR_OPEN_EDGE_MAX_LENGTH = 4.0
DOOR_OPEN_CONFIDENCES = {"high"}
DOOR_OPEN_SOURCES = {"route_boundary_intersection"}
WallOpeningMap = dict[str, set[tuple[tuple[float, float], tuple[float, float]]]]


@dataclass
class MeshData:
    name: str
    vertices: list[list[float]]
    faces: list[list[int]]
    material: str = "default"
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def build_room_plate(name: str, ring: list[list[float]], material: str = "room") -> MeshData:
    vertices = _open_ring(ring)
    return MeshData(name=name, vertices=vertices, faces=[list(range(len(vertices)))], material=material)


def build_floor_slab(name: str, ring: list[list[float]], thickness: float = 0.2, material: str = "floor") -> MeshData:
    top = _open_ring(ring)
    bottom = [[x, y - thickness, z] for x, y, z in top]
    vertices = top + bottom
    n = len(top)
    faces = [list(range(n)), list(range(2 * n - 1, n - 1, -1))]
    for index in range(n):
        nxt = (index + 1) % n
        faces.append([index, nxt, nxt + n, index + n])
    return MeshData(name=name, vertices=vertices, faces=faces, material=material)


def build_wall_mesh(
    name: str,
    ring: list[list[float]],
    height: float = 1.5,
    material: str = "wall",
    *,
    open_edges: set[int] | None = None,
) -> MeshData:
    base = _open_ring(ring)
    top = [[x, y + height, z] for x, y, z in base]
    vertices = base + top
    n = len(base)
    faces = []
    open_edges = open_edges or set()
    for index in range(n):
        if index in open_edges:
            continue
        nxt = (index + 1) % n
        faces.append([index, nxt, nxt + n, index + n])
    metadata = {"open_edges": str(len(open_edges))} if open_edges else {}
    return MeshData(name=name, vertices=vertices, faces=faces, material=material, metadata=metadata)


def build_anchor_marker(name: str, position: list[float], size: float = 0.35, material: str = "anchor") -> MeshData:
    x, y, z = position
    s = size
    vertices = [
        [x - s, y, z - s],
        [x + s, y, z - s],
        [x + s, y, z + s],
        [x - s, y, z + s],
    ]
    return MeshData(name=name, vertices=vertices, faces=[[0, 1, 2, 3]], material=material)


def dataset_meshes(
    dataset,
    *,
    include_markers: bool = True,
    door_openings: list[dict[str, Any]] | None = None,
    wall_openings_by_floor: WallOpeningMap | None = None,
) -> list[MeshData]:
    meshes: list[MeshData] = []
    meshes.extend(_floor_slab_meshes(dataset))
    wall_openings = _door_wall_openings(dataset, door_openings or [])
    _merge_wall_openings(wall_openings, wall_openings_by_floor or {})
    for room in dataset.rooms:
        if room.polygon_local:
            ring = _offset_ring_y(room.polygon_local, ROOM_PLATE_OFFSET)
            meshes.append(build_room_plate(_room_mesh_name(room.external_id, room.floor_name), ring, room.category))
            open_edges = _open_edges_for_ring(str(room.floor_name), ring, wall_openings)
            meshes.append(
                build_wall_mesh(
                    f"walls__{room.external_id}__floor_{room.floor_name}",
                    ring,
                    1.2,
                    "wall_low",
                    open_edges=open_edges,
                )
            )
        if include_markers and room.anchor_local:
            meshes.append(build_anchor_marker(f"anchor__{room.external_id}", room.anchor_local))
    for portal in dataset.portals:
        if portal.polygon_local:
            ring = _offset_ring_y(portal.polygon_local, ROOM_PLATE_OFFSET)
            meshes.append(
                build_room_plate(
                    f"portal_area__{portal.kind}__{portal.external_id}__floor_{portal.floor_name}",
                    ring,
                    portal.kind,
                )
            )
            open_edges = _open_edges_for_ring(str(portal.floor_name), ring, wall_openings)
            meshes.append(
                build_wall_mesh(
                    f"portal_walls__{portal.kind}__{portal.external_id}__floor_{portal.floor_name}",
                    ring,
                    0.8,
                    "wall_low",
                    open_edges=open_edges,
                )
            )
        if include_markers and portal.anchor_local:
            meshes.append(build_anchor_marker(f"portal__{portal.kind}__{portal.group_id}__floor_{portal.floor_name}", portal.anchor_local, material=portal.kind))
    return meshes


def visual_meshes_from_meshes(meshes: list[MeshData]) -> list[MeshData]:
    return [
        mesh
        for mesh in meshes
        if not mesh.name.startswith("anchor__") and not mesh.name.startswith("portal__")
    ]


def floor_visual_meshes_from_meshes(meshes: list[MeshData], floor_name: str, floor_height: float) -> list[MeshData]:
    return [
        localize_mesh_to_floor(mesh, floor_height)
        for mesh in visual_meshes_from_meshes(meshes)
        if mesh_floor_name(mesh.name) == str(floor_name)
    ]


def navigation_meshes_from_meshes(meshes: list[MeshData]) -> list[MeshData]:
    return [
        mesh
        for mesh in meshes
        if mesh.material in {"floor", "anchor", "stair", "elevator", "door"}
    ]


def mesh_floor_name(name: str) -> str:
    if name.startswith("floor__"):
        floor_name = name[len("floor__") :]
        return floor_name.split("__", 1)[0]
    if "__floor_" in name:
        return name.rsplit("__floor_", 1)[1]
    return ""


def localize_mesh_to_floor(mesh: MeshData, floor_height: float) -> MeshData:
    vertices = []
    for vertex in mesh.vertices:
        local_y = round(float(vertex[1]) - float(floor_height), 6)
        if abs(local_y) < 0.001:
            local_y = 0.0
        vertices.append([round(float(vertex[0]), 6), local_y, round(float(vertex[2]), 6)])
    return MeshData(
        name=mesh.name,
        vertices=vertices,
        faces=[list(face) for face in mesh.faces],
        material=mesh.material,
        metadata=dict(mesh.metadata),
    )


def _room_mesh_name(external_id: str, floor_name: str) -> str:
    return f"room__{external_id}__floor_{floor_name}"


def _floor_slab_meshes(dataset) -> list[MeshData]:
    meshes: list[MeshData] = []
    floor_by_name = {str(floor.floor_name): floor for floor in dataset.floors}
    explicit_floor_names: set[str] = set()
    for floor in dataset.floors:
        if floor.polygon_local:
            meshes.append(build_floor_slab(f"floor__{floor.floor_name}", floor.polygon_local, FLOOR_SLAB_THICKNESS))
            explicit_floor_names.add(str(floor.floor_name))

    rings_by_floor: dict[str, list[list[list[float]]]] = {}
    for record in [*dataset.rooms, *dataset.portals]:
        floor_name = str(record.floor_name)
        if floor_name in explicit_floor_names or not record.polygon_local:
            continue
        rings_by_floor.setdefault(floor_name, []).append(record.polygon_local)

    for floor_name, rings in rings_by_floor.items():
        floor = floor_by_name.get(floor_name)
        height = float(floor.height if floor else _ring_height(rings[0]))
        polygons = [_ring_to_polygon(ring) for ring in rings]
        polygons = [polygon for polygon in polygons if polygon is not None and not polygon.is_empty]
        if not polygons:
            continue
        merged = unary_union(polygons)
        if FLOOR_SLAB_PADDING:
            merged = merged.buffer(FLOOR_SLAB_PADDING, join_style="mitre")
        if FLOOR_SLAB_SIMPLIFY:
            merged = merged.simplify(FLOOR_SLAB_SIMPLIFY, preserve_topology=True)
        for index, polygon in enumerate(_iter_polygons(merged), start=1):
            ring = _polygon_exterior_to_ring(polygon, height)
            if len(_open_ring(ring)) < 3:
                continue
            suffix = "" if index == 1 else f"__part_{index}"
            meshes.append(build_floor_slab(f"floor__{floor_name}{suffix}", ring, FLOOR_SLAB_THICKNESS))
    return meshes


def _ring_to_polygon(ring: list[list[float]]) -> Polygon | None:
    opened = _open_ring(ring)
    if len(opened) < 3:
        return None
    polygon = Polygon((point[0], point[2]) for point in opened)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area <= 0.001:
        return None
    return polygon


def _iter_polygons(geometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return sorted(list(geometry.geoms), key=lambda polygon: polygon.area, reverse=True)
    return []


def _polygon_exterior_to_ring(polygon: Polygon, height: float) -> list[list[float]]:
    return [[round(float(x), 6), height, round(float(z), 6)] for x, z in polygon.exterior.coords]


def _ring_height(ring: list[list[float]]) -> float:
    for point in ring:
        if len(point) >= 2:
            return float(point[1])
    return 0.0


def _offset_ring_y(ring: list[list[float]], offset: float) -> list[list[float]]:
    return [[point[0], point[1] + offset, point[2]] for point in ring]


def _open_ring(ring: list[list[float]]) -> list[list[float]]:
    if len(ring) > 1 and ring[0] == ring[-1]:
        return [list(point) for point in ring[:-1]]
    return [list(point) for point in ring]


def _door_wall_openings(dataset, door_openings: list[dict[str, Any]]) -> WallOpeningMap:
    if not door_openings:
        return {}

    rooms_by_source_id = {
        str(getattr(room, "source_id", "")): room
        for room in dataset.rooms
        if getattr(room, "source_id", "") and getattr(room, "polygon_local", None)
    }
    rooms_by_external_id = {
        str(getattr(room, "external_id", "")): room
        for room in dataset.rooms
        if getattr(room, "external_id", "") and getattr(room, "polygon_local", None)
    }
    open_edges_by_floor: WallOpeningMap = {}
    records_by_room: dict[int, list[dict[str, Any]]] = {}
    rooms_by_object_id: dict[int, Any] = {}

    for record in door_openings:
        room = rooms_by_source_id.get(str(record.get("source_id", ""))) or rooms_by_external_id.get(str(record.get("external_id", "")))
        if room is None:
            continue
        room_id = id(room)
        records_by_room.setdefault(room_id, []).append(record)
        rooms_by_object_id[room_id] = room
        if not _eligible_door_opening(record):
            continue
        door_local = record.get("door_local")
        edge_index = _nearest_openable_edge_index(getattr(room, "polygon_local", []), door_local)
        if edge_index is None:
            continue
        _add_room_open_edge(open_edges_by_floor, room, edge_index, record.get("floor_name", ""))

    for room_id, records in records_by_room.items():
        room = rooms_by_object_id[room_id]
        floor_name = str(getattr(room, "floor_name", ""))
        if _open_edges_for_ring(floor_name, getattr(room, "polygon_local", []), open_edges_by_floor):
            continue
        edge_index = _nearest_fallback_edge_index(getattr(room, "polygon_local", []), records)
        if edge_index is not None:
            _add_room_open_edge(open_edges_by_floor, room, edge_index, "")

    return open_edges_by_floor


def _eligible_door_opening(record: dict[str, Any]) -> bool:
    confidence = str(record.get("confidence") or "").lower()
    source = str(record.get("door_source") or "")
    return confidence in DOOR_OPEN_CONFIDENCES and source in DOOR_OPEN_SOURCES


def _merge_wall_openings(target: WallOpeningMap, source: WallOpeningMap) -> None:
    for floor_name, edge_keys in source.items():
        if not edge_keys:
            continue
        target.setdefault(str(floor_name), set()).update(edge_keys)


def _nearest_openable_edge_index(ring: list[list[float]], point: Any) -> int | None:
    candidate = _nearest_edge_candidate(ring, point, max_length=DOOR_OPEN_EDGE_MAX_LENGTH)
    return candidate[0] if candidate else None


def _nearest_fallback_edge_index(ring: list[list[float]], records: list[dict[str, Any]]) -> int | None:
    best: tuple[int, float, float, int] | None = None
    for record in records:
        candidate = _nearest_edge_candidate(ring, record.get("door_local"), max_length=None)
        if candidate is None:
            continue
        index, distance, edge_length = candidate
        score = (0 if _eligible_door_opening(record) else 1, distance, edge_length, index)
        if best is None or score < best:
            best = score
    return best[3] if best else None


def _nearest_edge_candidate(ring: list[list[float]], point: Any, *, max_length: float | None) -> tuple[int, float, float] | None:
    if not _valid_point(point):
        return None
    opened = _open_ring(ring)
    if len(opened) < 3:
        return None

    best_index = None
    best_distance = float("inf")
    best_edge_length = float("inf")
    for index, start in enumerate(opened):
        end = opened[(index + 1) % len(opened)]
        edge_length = _edge_length(start, end)
        if edge_length <= 0.001 or (max_length is not None and edge_length > max_length):
            continue
        distance = _point_to_segment_distance(float(point[0]), float(point[2]), start, end)
        if distance < best_distance:
            best_distance = distance
            best_index = index
            best_edge_length = edge_length

    if best_index is None or best_distance > DOOR_OPEN_EDGE_TOLERANCE:
        return None
    return best_index, best_distance, best_edge_length


def _add_room_open_edge(
    open_edges_by_floor: dict[str, set[tuple[tuple[float, float], tuple[float, float]]]],
    room: Any,
    edge_index: int,
    fallback_floor_name: Any,
) -> None:
    ring = _open_ring(getattr(room, "polygon_local", []))
    if len(ring) < 3:
        return
    floor_name = str(getattr(room, "floor_name", fallback_floor_name))
    edge_key = _edge_key(ring[edge_index], ring[(edge_index + 1) % len(ring)])
    open_edges_by_floor.setdefault(floor_name, set()).add(edge_key)


def _open_edges_for_ring(
    floor_name: str,
    ring: list[list[float]],
    open_edges_by_floor: dict[str, set[tuple[tuple[float, float], tuple[float, float]]]],
) -> set[int]:
    floor_open_edges = open_edges_by_floor.get(str(floor_name), set())
    if not floor_open_edges:
        return set()
    opened = _open_ring(ring)
    result: set[int] = set()
    for index, start in enumerate(opened):
        end = opened[(index + 1) % len(opened)]
        if _edge_key(start, end) in floor_open_edges:
            result.add(index)
    return result


def _valid_point(point: Any) -> bool:
    if not isinstance(point, (list, tuple)) or len(point) < 3:
        return False
    try:
        float(point[0])
        float(point[2])
    except (TypeError, ValueError):
        return False
    return True


def _edge_key(left: list[float], right: list[float]) -> tuple[tuple[float, float], tuple[float, float]]:
    a = (round(float(left[0]), 3), round(float(left[2]), 3))
    b = (round(float(right[0]), 3), round(float(right[2]), 3))
    return (a, b) if a <= b else (b, a)


def _edge_length(left: list[float], right: list[float]) -> float:
    return math.hypot(float(right[0]) - float(left[0]), float(right[2]) - float(left[2]))


def _point_to_segment_distance(px: float, pz: float, start: list[float], end: list[float]) -> float:
    ax = float(start[0])
    az = float(start[2])
    bx = float(end[0])
    bz = float(end[2])
    dx = bx - ax
    dz = bz - az
    length_squared = dx * dx + dz * dz
    if length_squared <= 0.000001:
        return math.hypot(px - ax, pz - az)
    t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length_squared))
    nearest_x = ax + t * dx
    nearest_z = az + t * dz
    return math.hypot(px - nearest_x, pz - nearest_z)

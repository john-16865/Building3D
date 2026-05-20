from __future__ import annotations

from dataclasses import asdict, dataclass, field

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


FLOOR_SLAB_PADDING = 0.25
FLOOR_SLAB_THICKNESS = 0.12
FLOOR_SLAB_SIMPLIFY = 0.03
ROOM_PLATE_OFFSET = 0.03


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


def build_wall_mesh(name: str, ring: list[list[float]], height: float = 1.5, material: str = "wall") -> MeshData:
    base = _open_ring(ring)
    top = [[x, y + height, z] for x, y, z in base]
    vertices = base + top
    n = len(base)
    faces = []
    for index in range(n):
        nxt = (index + 1) % n
        faces.append([index, nxt, nxt + n, index + n])
    return MeshData(name=name, vertices=vertices, faces=faces, material=material)


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


def dataset_meshes(dataset, *, include_markers: bool = True) -> list[MeshData]:
    meshes: list[MeshData] = []
    meshes.extend(_floor_slab_meshes(dataset))
    for room in dataset.rooms:
        if room.polygon_local:
            ring = _offset_ring_y(room.polygon_local, ROOM_PLATE_OFFSET)
            meshes.append(build_room_plate(_room_mesh_name(room.external_id, room.floor_name), ring, room.category))
            meshes.append(build_wall_mesh(f"walls__{room.external_id}__floor_{room.floor_name}", ring, 1.2, "wall_low"))
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
            meshes.append(
                build_wall_mesh(
                    f"portal_walls__{portal.kind}__{portal.external_id}__floor_{portal.floor_name}",
                    ring,
                    0.8,
                    "wall_low",
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
        return floor_name.split("__part_", 1)[0]
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

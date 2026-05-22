from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

MATERIAL_COLORS = {
    "floor": (0.72, 0.72, 0.72, 1.0),
    "lecture": (0.24, 0.52, 0.92, 1.0),
    "lab": (0.20, 0.67, 0.55, 1.0),
    "study": (0.85, 0.66, 0.22, 1.0),
    "toilet": (0.62, 0.54, 0.86, 1.0),
    "parking": (0.35, 0.36, 0.38, 1.0),
    "admin": (0.70, 0.48, 0.35, 1.0),
    "other": (0.58, 0.62, 0.66, 1.0),
    "wall_low": (0.88, 0.88, 0.84, 1.0),
    "anchor": (0.05, 0.85, 0.35, 1.0),
    "walkable_path": (0.0, 0.9, 0.85, 1.0),
    "stair": (0.95, 0.45, 0.12, 1.0),
    "elevator": (0.12, 0.65, 0.95, 1.0),
    "door": (0.20, 0.20, 0.20, 1.0),
    "door_point_high": (1.0, 0.05, 0.05, 1.0),
    "door_point_medium": (1.0, 0.7, 0.0, 1.0),
    "door_point_low": (0.55, 0.05, 0.95, 1.0),
    "door_point_unknown": (1.0, 1.0, 1.0, 1.0),
    "default": (0.65, 0.65, 0.65, 1.0),
}


@dataclass
class MeshData:
    name: str
    vertices: list[list[float]]
    faces: list[list[int]]
    material: str = "default"
    metadata: dict[str, str] = field(default_factory=dict)


def write_glb(meshes: list[MeshData], path: Path) -> None:
    mesh_list = [mesh for mesh in meshes if mesh.vertices and mesh.faces]
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    gltf_meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    materials = _materials(mesh_list)
    material_index = {material["name"]: index for index, material in enumerate(materials)}

    for mesh in mesh_list:
        position_accessor = _append_positions(binary, buffer_views, accessors, mesh.vertices)
        indices = _triangulate(mesh.faces, mesh.vertices)
        index_accessor = _append_indices(binary, buffer_views, accessors, indices)
        gltf_meshes.append(
            {
                "name": mesh.name,
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": material_index.get(mesh.material, material_index["default"]),
                    }
                ],
            }
        )
        nodes.append({"name": mesh.name, "mesh": len(gltf_meshes) - 1})

    gltf = {
        "asset": {"version": "2.0", "generator": "Building3D door point debug exporter"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }

    json_bytes = _pad(json.dumps(gltf, separators=(",", ":"), sort_keys=True).encode("utf-8"), b" ")
    bin_bytes = _pad(bytes(binary), b"\x00")
    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    output = bytearray()
    output.extend(struct.pack("<III", GLB_MAGIC, GLB_VERSION, total_length))
    output.extend(struct.pack("<II", len(json_bytes), JSON_CHUNK))
    output.extend(json_bytes)
    output.extend(struct.pack("<II", len(bin_bytes), BIN_CHUNK))
    output.extend(bin_bytes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)


def visual_meshes_from_meshes(meshes: list[MeshData]) -> list[MeshData]:
    return [mesh for mesh in meshes if not mesh.name.startswith("anchor__") and not mesh.name.startswith("portal__")]


def mesh_floor_name(name: str) -> str:
    if name.startswith("floor__"):
        floor_name = name[len("floor__") :]
        return floor_name.split("__", 1)[0]
    if "__floor_" in name:
        return name.rsplit("__floor_", 1)[1]
    return ""


def _append_positions(
    binary: bytearray,
    buffer_views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
    vertices: list[list[float]],
) -> int:
    _align(binary)
    offset = len(binary)
    mins = [min(float(vertex[index]) for vertex in vertices) for index in range(3)]
    maxs = [max(float(vertex[index]) for vertex in vertices) for index in range(3)]
    for vertex in vertices:
        binary.extend(struct.pack("<fff", float(vertex[0]), float(vertex[1]), float(vertex[2])))
    view_index = len(buffer_views)
    buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(binary) - offset, "target": 34962})
    accessor_index = len(accessors)
    accessors.append(
        {
            "bufferView": view_index,
            "byteOffset": 0,
            "componentType": 5126,
            "count": len(vertices),
            "type": "VEC3",
            "min": mins,
            "max": maxs,
        }
    )
    return accessor_index


def _append_indices(
    binary: bytearray,
    buffer_views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
    indices: list[int],
) -> int:
    _align(binary)
    offset = len(binary)
    for index in indices:
        binary.extend(struct.pack("<I", int(index)))
    view_index = len(buffer_views)
    buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(binary) - offset, "target": 34963})
    accessor_index = len(accessors)
    accessors.append(
        {
            "bufferView": view_index,
            "byteOffset": 0,
            "componentType": 5125,
            "count": len(indices),
            "type": "SCALAR",
            "min": [min(indices) if indices else 0],
            "max": [max(indices) if indices else 0],
        }
    )
    return accessor_index


def _triangulate(faces: list[list[int]], vertices: list[list[float]]) -> list[int]:
    indices: list[int] = []
    for face in faces:
        if len(face) < 3:
            continue
        if len(face) == 3:
            indices.extend(face)
            continue
        indices.extend(_ear_clip_face(face, vertices))
    return indices


def _ear_clip_face(face: list[int], vertices: list[list[float]]) -> list[int]:
    polygon = _clean_face(face, vertices)
    if len(polygon) < 3:
        return []
    if len(polygon) == 3:
        return polygon

    points = _project_face_to_2d(polygon, vertices)
    area = _signed_area(points)
    if abs(area) < 1e-9:
        return _fan_triangulate(polygon)
    if area < 0:
        polygon = list(reversed(polygon))
        points = list(reversed(points))

    remaining = list(range(len(polygon)))
    triangles: list[int] = []
    guard = 0
    while len(remaining) > 3 and guard < len(polygon) * len(polygon):
        guard += 1
        clipped = False
        for position, current in enumerate(list(remaining)):
            previous = remaining[position - 1]
            nxt = remaining[(position + 1) % len(remaining)]
            if not _is_convex(points[previous], points[current], points[nxt]):
                continue
            if _triangle_contains_any_point(previous, current, nxt, remaining, points):
                continue
            triangles.extend([polygon[previous], polygon[current], polygon[nxt]])
            remaining.remove(current)
            clipped = True
            break
        if not clipped:
            return _fan_triangulate(polygon)

    if len(remaining) == 3:
        triangles.extend([polygon[remaining[0]], polygon[remaining[1]], polygon[remaining[2]]])
    return triangles


def _clean_face(face: list[int], vertices: list[list[float]]) -> list[int]:
    cleaned: list[int] = []
    for index in face:
        if index < 0 or index >= len(vertices):
            continue
        if cleaned and _same_vertex(vertices[cleaned[-1]], vertices[index]):
            continue
        cleaned.append(index)
    if len(cleaned) > 1 and _same_vertex(vertices[cleaned[0]], vertices[cleaned[-1]]):
        cleaned.pop()
    return cleaned


def _same_vertex(left: list[float], right: list[float]) -> bool:
    return all(abs(float(left[index]) - float(right[index])) < 1e-9 for index in range(3))


def _project_face_to_2d(face: list[int], vertices: list[list[float]]) -> list[tuple[float, float]]:
    normal = _newell_normal(face, vertices)
    axis = max(range(3), key=lambda item: abs(normal[item]))
    if axis == 0:
        return [(float(vertices[index][1]), float(vertices[index][2])) for index in face]
    if axis == 1:
        return [(float(vertices[index][0]), float(vertices[index][2])) for index in face]
    return [(float(vertices[index][0]), float(vertices[index][1])) for index in face]


def _newell_normal(face: list[int], vertices: list[list[float]]) -> tuple[float, float, float]:
    nx = ny = nz = 0.0
    for offset, current in enumerate(face):
        nxt = face[(offset + 1) % len(face)]
        current_vertex = vertices[current]
        next_vertex = vertices[nxt]
        nx += (current_vertex[1] - next_vertex[1]) * (current_vertex[2] + next_vertex[2])
        ny += (current_vertex[2] - next_vertex[2]) * (current_vertex[0] + next_vertex[0])
        nz += (current_vertex[0] - next_vertex[0]) * (current_vertex[1] + next_vertex[1])
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return (0.0, 1.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _signed_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return area / 2.0


def _is_convex(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return _cross(a, b, c) > 1e-10


def _triangle_contains_any_point(
    previous: int,
    current: int,
    nxt: int,
    remaining: list[int],
    points: list[tuple[float, float]],
) -> bool:
    triangle = (points[previous], points[current], points[nxt])
    for candidate in remaining:
        if candidate in {previous, current, nxt}:
            continue
        if _point_in_triangle(points[candidate], triangle):
            return True
    return False


def _point_in_triangle(
    point: tuple[float, float],
    triangle: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> bool:
    a, b, c = triangle
    eps = 1e-10
    crosses = (_cross(a, b, point), _cross(b, c, point), _cross(c, a, point))
    return all(value >= -eps for value in crosses)


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _fan_triangulate(face: list[int]) -> list[int]:
    indices: list[int] = []
    for index in range(1, len(face) - 1):
        indices.extend([face[0], face[index], face[index + 1]])
    return indices


def _materials(meshes: list[MeshData]) -> list[dict[str, Any]]:
    names = ["default"]
    for mesh in meshes:
        if mesh.material not in names:
            names.append(mesh.material)
    return [
        {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": list(MATERIAL_COLORS.get(name, MATERIAL_COLORS["default"])),
                "roughnessFactor": 0.82,
                "metallicFactor": 0.0,
            },
            "doubleSided": True,
        }
        for name in names
    ]


def _align(binary: bytearray) -> None:
    while len(binary) % 4:
        binary.append(0)


def _pad(data: bytes, char: bytes) -> bytes:
    remainder = len(data) % 4
    if remainder == 0:
        return data
    return data + char * (4 - remainder)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export GLB debug markers for route-derived room door points.")
    parser.add_argument("--group", default="science_test")
    parser.add_argument("--processed-dir", default="data/processed/auckland/groups/science_test")
    parser.add_argument("--export-dir", default="exports/auckland/groups/science_test")
    parser.add_argument("--marker-size", type=float, default=0.32)
    args = parser.parse_args()

    group = str(args.group)
    processed_dir = Path(args.processed_dir)
    export_dir = Path(args.export_dir)
    geometry_path = processed_dir / "geometry.json"
    door_path = export_dir / f"{group}_room_door_points_route_derived.json"

    meshes = _load_meshes(geometry_path)
    visual_meshes = visual_meshes_from_meshes(meshes)
    geometry_floors = sorted({mesh_floor_name(mesh.name) for mesh in visual_meshes if mesh_floor_name(mesh.name)})
    door_records = _load_json_list(door_path)

    matched_records = [record for record in door_records if str(record.get("floor_name", "")) in set(geometry_floors)]
    all_markers = [_door_marker(record, index, args.marker_size) for index, record in enumerate(door_records)]
    matched_markers = [_door_marker(record, index, args.marker_size) for index, record in enumerate(matched_records)]

    combined_path = export_dir / f"{group}_door_points_debug.glb"
    matched_markers_path = export_dir / f"{group}_door_points_markers.glb"
    all_markers_path = export_dir / f"{group}_all_door_points_markers.glb"
    summary_path = export_dir / f"{group}_door_points_debug_summary.json"

    write_glb([*visual_meshes, *matched_markers], combined_path)
    write_glb(matched_markers, matched_markers_path)
    write_glb(all_markers, all_markers_path)

    summary = {
        "group": group,
        "geometry_path": str(geometry_path),
        "door_points_path": str(door_path),
        "combined_glb": str(combined_path),
        "matched_markers_glb": str(matched_markers_path),
        "all_markers_glb": str(all_markers_path),
        "geometry_floors": geometry_floors,
        "door_points_total": len(door_records),
        "door_points_matched_to_geometry": len(matched_records),
        "door_points_omitted_from_combined_glb": len(door_records) - len(matched_records),
        "door_points_by_floor": _counter_dict(record.get("floor_name", "") for record in door_records),
        "matched_door_points_by_floor": _counter_dict(record.get("floor_name", "") for record in matched_records),
        "door_points_by_confidence": _counter_dict(record.get("confidence", "unknown") for record in door_records),
        "door_points_by_source": _counter_dict(record.get("door_source", "unknown") for record in door_records),
        "marker_materials": {
            "high": "red",
            "medium": "orange",
            "low": "purple",
            "unknown": "white",
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))


def _load_meshes(path: Path) -> list[MeshData]:
    rows = _load_json_list(path)
    return [
        MeshData(
            name=str(row.get("name", "")),
            vertices=[list(vertex) for vertex in row.get("vertices", [])],
            faces=[list(face) for face in row.get("faces", [])],
            material=str(row.get("material", "default")),
            metadata=dict(row.get("metadata", {})),
        )
        for row in rows
    ]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Expected a JSON list: {path}")
    return [item for item in data if isinstance(item, dict)]


def _door_marker(record: dict[str, Any], index: int, size: float) -> MeshData:
    door_local = record.get("door_local")
    if not isinstance(door_local, list) or len(door_local) < 3:
        return MeshData(name=f"door_point__invalid__{index}", vertices=[], faces=[], material="door_point_unknown")

    x = float(door_local[0])
    y = float(door_local[1])
    z = float(door_local[2])
    post_radius = size * 0.22
    post_height = size * 2.4
    diamond_radius = size
    diamond_height = size * 1.25
    post_top = y + post_height
    center_y = post_top + diamond_height * 0.45

    vertices = [
        [x - post_radius, y + 0.04, z - post_radius],
        [x + post_radius, y + 0.04, z - post_radius],
        [x + post_radius, y + 0.04, z + post_radius],
        [x - post_radius, y + 0.04, z + post_radius],
        [x - post_radius, post_top, z - post_radius],
        [x + post_radius, post_top, z - post_radius],
        [x + post_radius, post_top, z + post_radius],
        [x - post_radius, post_top, z + post_radius],
        [x, center_y + diamond_height, z],
        [x, center_y - diamond_height, z],
        [x - diamond_radius, center_y, z],
        [x + diamond_radius, center_y, z],
        [x, center_y, z - diamond_radius],
        [x, center_y, z + diamond_radius],
    ]
    faces = [
        [0, 1, 2, 3],
        [4, 7, 6, 5],
        [0, 4, 5, 1],
        [1, 5, 6, 2],
        [2, 6, 7, 3],
        [3, 7, 4, 0],
        [8, 10, 12],
        [8, 12, 11],
        [8, 11, 13],
        [8, 13, 10],
        [9, 12, 10],
        [9, 11, 12],
        [9, 13, 11],
        [9, 10, 13],
    ]

    confidence = str(record.get("confidence") or "unknown").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "unknown"
    name_parts = [
        "door_point",
        str(record.get("floor_name") or record.get("floor_index") or "floor"),
        str(record.get("external_id") or index),
        str(record.get("display_name") or "room"),
        confidence,
        str(record.get("door_source") or "source"),
    ]
    return MeshData(
        name="__".join(_slug(part) for part in name_parts),
        vertices=vertices,
        faces=faces,
        material=f"door_point_{confidence}",
        metadata={
            "external_id": str(record.get("external_id") or ""),
            "floor_name": str(record.get("floor_name") or ""),
            "confidence": confidence,
            "door_source": str(record.get("door_source") or ""),
        },
    )


def _counter_dict(values) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:64] or "unknown"


if __name__ == "__main__":
    main()

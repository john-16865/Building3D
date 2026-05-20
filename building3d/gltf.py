from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Iterable

from building3d.blender.materials import material_color
from building3d.geometry import MeshData

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def write_glb(meshes: Iterable[MeshData], path: str | Path) -> None:
    mesh_list = [mesh for mesh in meshes if mesh.vertices and mesh.faces]
    binary = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    gltf_meshes: list[dict] = []
    nodes: list[dict] = []
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
        "asset": {"version": "2.0", "generator": "Building3D native GLB writer"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }

    json_bytes = json.dumps(gltf, separators=(",", ":"), sort_keys=True).encode("utf-8")
    json_bytes = _pad(json_bytes, b" ")
    bin_bytes = _pad(bytes(binary), b"\x00")
    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    output = bytearray()
    output.extend(struct.pack("<III", GLB_MAGIC, GLB_VERSION, total_length))
    output.extend(struct.pack("<II", len(json_bytes), JSON_CHUNK))
    output.extend(json_bytes)
    output.extend(struct.pack("<II", len(bin_bytes), BIN_CHUNK))
    output.extend(bin_bytes)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)


def triangulate_faces(faces: list[list[int]], vertices: list[list[float]]) -> list[list[int]]:
    indices = _triangulate(faces, vertices)
    return [indices[index : index + 3] for index in range(0, len(indices), 3) if len(indices[index : index + 3]) == 3]


def _append_positions(binary: bytearray, buffer_views: list[dict], accessors: list[dict], vertices: list[list[float]]) -> int:
    _align(binary)
    offset = len(binary)
    mins = [min(vertex[i] for vertex in vertices) for i in range(3)]
    maxs = [max(vertex[i] for vertex in vertices) for i in range(3)]
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


def _append_indices(binary: bytearray, buffer_views: list[dict], accessors: list[dict], indices: list[int]) -> int:
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


def _point_in_triangle(point: tuple[float, float], triangle: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]) -> bool:
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


def _materials(meshes: list[MeshData]) -> list[dict]:
    names = ["default"]
    for mesh in meshes:
        if mesh.material not in names:
            names.append(mesh.material)
    return [
        {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": list(material_color(name)),
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

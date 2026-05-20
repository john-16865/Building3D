import json
import struct

from building3d.geometry import MeshData
from building3d.gltf import _triangulate, write_glb


def test_write_glb_creates_valid_binary_gltf_header(tmp_path):
    output = tmp_path / "model.glb"
    meshes = [
        MeshData(
            name="triangle",
            vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            faces=[[0, 1, 2]],
            material="lecture",
        )
    ]

    write_glb(meshes, output)

    data = output.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    json_chunk_length, json_chunk_type = struct.unpack_from("<II", data, 12)
    json_payload = data[20 : 20 + json_chunk_length].rstrip(b" ")
    gltf = json.loads(json_payload.decode("utf-8"))

    assert magic == 0x46546C67
    assert version == 2
    assert length == len(data)
    assert json_chunk_type == 0x4E4F534A
    assert gltf["asset"]["version"] == "2.0"
    assert gltf["meshes"][0]["name"] == "triangle"


def test_triangulate_preserves_concave_polygon_area():
    vertices = [
        [0.0, 0.0, 0.0],
        [4.0, 0.0, 0.0],
        [4.0, 0.0, 4.0],
        [3.0, 0.0, 4.0],
        [3.0, 0.0, 1.0],
        [1.0, 0.0, 1.0],
        [1.0, 0.0, 4.0],
        [0.0, 0.0, 4.0],
    ]
    face = list(range(len(vertices)))

    indices = _triangulate([face], vertices)

    assert len(indices) == (len(vertices) - 2) * 3
    assert _triangle_area(indices, vertices) == _polygon_area(vertices)


def _triangle_area(indices, vertices) -> float:
    area = 0.0
    for offset in range(0, len(indices), 3):
        a, b, c = [vertices[index] for index in indices[offset : offset + 3]]
        area += abs((b[0] - a[0]) * (c[2] - a[2]) - (c[0] - a[0]) * (b[2] - a[2])) / 2.0
    return round(area, 6)


def _polygon_area(vertices) -> float:
    area = 0.0
    for index, point in enumerate(vertices):
        nxt = vertices[(index + 1) % len(vertices)]
        area += point[0] * nxt[2] - nxt[0] * point[2]
    return round(abs(area) / 2.0, 6)

from types import SimpleNamespace

from building3d.geometry import (
    FLOOR_SLAB_THICKNESS,
    ROOM_PLATE_OFFSET,
    MeshData,
    build_room_plate,
    build_wall_mesh,
    dataset_meshes,
    floor_visual_meshes_from_meshes,
    mesh_floor_name,
    navigation_meshes_from_meshes,
    visual_meshes_from_meshes,
)


def test_build_room_plate_uses_polygon_vertices_and_single_face():
    ring = [
        [0.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [2.0, 0.0, 1.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.0, 0.0],
    ]

    mesh = build_room_plate("room__260-115__floor_10", ring)

    assert mesh.name == "room__260-115__floor_10"
    assert len(mesh.vertices) == 4
    assert mesh.faces == [[0, 1, 2, 3]]


def test_build_wall_mesh_extrudes_edges_upward():
    ring = [
        [0.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [2.0, 0.0, 1.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.0, 0.0],
    ]

    mesh = build_wall_mesh("walls__floor_10", ring, height=1.5)

    assert len(mesh.vertices) == 8
    assert len(mesh.faces) == 4
    assert [0.0, 1.5, 0.0] in mesh.vertices


def test_visual_meshes_exclude_debug_anchor_markers_but_keep_portal_areas():
    meshes = [
        MeshData("room__301-001__floor_G", [[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]], "lecture"),
        MeshData("anchor__301-001", [[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]], "anchor"),
        MeshData("portal__elevator__E1__floor_G", [[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]], "elevator"),
        MeshData("portal_area__elevator__301-E1__floor_G", [[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]], "elevator"),
    ]

    visual_names = [mesh.name for mesh in visual_meshes_from_meshes(meshes)]
    nav_names = [mesh.name for mesh in navigation_meshes_from_meshes(meshes)]

    assert visual_names == ["room__301-001__floor_G", "portal_area__elevator__301-E1__floor_G"]
    assert nav_names == ["anchor__301-001", "portal__elevator__E1__floor_G", "portal_area__elevator__301-E1__floor_G"]


def test_floor_visual_meshes_are_split_by_floor_and_localized():
    meshes = [
        MeshData("floor__G", [[0, 0, 0], [1, 0, 0], [0, -0.1, 1]], [[0, 1, 2]], "floor"),
        MeshData("room__301-001__floor_G", [[0, 0.03, 0], [1, 0.03, 0], [0, 0.03, 1]], [[0, 1, 2]], "lecture"),
        MeshData("room__301-101__floor_1", [[0, 4.23, 0], [1, 4.23, 0], [0, 4.23, 1]], [[0, 1, 2]], "lecture"),
        MeshData("anchor__301-001", [[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]], "anchor"),
    ]

    floor_g = floor_visual_meshes_from_meshes(meshes, "G", 0.0)
    floor_1 = floor_visual_meshes_from_meshes(meshes, "1", 4.2)

    assert [mesh.name for mesh in floor_g] == ["floor__G", "room__301-001__floor_G"]
    assert [mesh.name for mesh in floor_1] == ["room__301-101__floor_1"]
    assert floor_1[0].vertices[0][1] == 0.03
    assert mesh_floor_name("floor__G__part_2") == "G"
    assert mesh_floor_name("portal_area__stair__301-S1__floor_B-1") == "B-1"


def test_dataset_meshes_derives_floor_slab_and_offsets_room_detail():
    floor = SimpleNamespace(floor_name="G", height=0.0, polygon_local=[])
    room = SimpleNamespace(
        external_id="301-001",
        floor_name="G",
        category="lecture",
        polygon_local=[
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [2.0, 0.0, 2.0],
            [0.0, 0.0, 2.0],
            [0.0, 0.0, 0.0],
        ],
        anchor_local=[1.0, 0.0, 1.0],
    )
    portal = SimpleNamespace(
        external_id="301-E1",
        floor_name="G",
        kind="elevator",
        group_id="E1",
        polygon_local=[
            [2.0, 0.0, 0.0],
            [3.0, 0.0, 0.0],
            [3.0, 0.0, 1.0],
            [2.0, 0.0, 1.0],
            [2.0, 0.0, 0.0],
        ],
        anchor_local=[2.5, 0.0, 0.5],
    )
    dataset = SimpleNamespace(floors=[floor], rooms=[room], portals=[portal])

    meshes = dataset_meshes(dataset)
    by_name = {mesh.name: mesh for mesh in meshes}

    assert "floor__G" in by_name
    assert min(vertex[1] for vertex in by_name["floor__G"].vertices) == -FLOOR_SLAB_THICKNESS
    assert max(vertex[1] for vertex in by_name["floor__G"].vertices) == 0.0
    assert by_name["room__301-001__floor_G"].vertices[0][1] == ROOM_PLATE_OFFSET
    assert by_name["walls__301-001__floor_G"].vertices[0][1] == ROOM_PLATE_OFFSET
    assert by_name["portal_area__elevator__301-E1__floor_G"].vertices[0][1] == ROOM_PLATE_OFFSET

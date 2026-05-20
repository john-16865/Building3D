import json

from building3d.geometry import MeshData, build_floor_slab, build_room_plate
from building3d.unimate import _navigation_mesh_resources, portal_node_name, room_node_name, write_unimate_scene


def test_room_node_name_preserves_source_prefix_and_display_name():
    assert room_node_name("302-101", "Seminar Room") == "302 101_Seminar Room"
    assert room_node_name("303S-204", "Wet Lab / Clean") == "303S 204_Wet Lab - Clean"


def test_portal_node_name_uses_unimate_set_suffixes():
    assert portal_node_name("302-100E1", "Elevator", "elevator", "E1") == "302 100E1_Elevator_SetE1"
    assert portal_node_name("302-100S2", "Stairs", "stair", "S2") == "302 100S2_Stairs_Set2"


def test_write_unimate_scene_creates_floor_room_and_portal_nodes(tmp_path):
    manifest = {
        "building": {"id": "science", "display_name": "Science Centre"},
        "floors": [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        "rooms": [
            {
                "external_id": "302-101",
                "node_name": "302 101_Seminar Room",
                "floor_index": 0,
                "anchor": [1.0, 0.0, 2.0],
            }
        ],
        "portals": [
            {
                "external_id": "302-100E1",
                "node_name": "302 100E1_Elevator_SetE1",
                "floor_index": 0,
                "anchor": [3.0, 0.0, 4.0],
            }
        ],
        "external_doors": [
            {
                "external_id": "science_entry_001",
                "node_name": "MainDoor",
                "floor_index": 0,
                "anchor": [2.0, 0.0, 1.0],
            }
        ],
    }

    nav_meshes = [
        build_floor_slab(
            "floor__G",
            [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 4.0], [0.0, 0.0, 4.0], [0.0, 0.0, 0.0]],
        )
    ]

    path = write_unimate_scene(
        manifest,
        tmp_path / "science_unimate.tscn",
        navigation_meshes=nav_meshes,
        floor_visual_paths={0: "res://Assets/Buildings/Science/science_floor_0_visual.glb"},
    )
    text = path.read_text(encoding="utf-8")

    assert 'building_name = "science"' in text
    assert 'science_floor_0_visual.glb' in text
    assert '[sub_resource type="NavigationMesh" id="NavigationMesh_floor_0"]' in text
    assert '[sub_resource type="BoxShape3D" id="ClickShape_floor_0"]' in text
    assert '[sub_resource type="BoxShape3D" id="ClickShape_building"]' in text
    assert "vertices = PackedVector3Array(" in text
    assert "polygons = [PackedInt32Array" in text
    assert '[node name="Floor0" type="Node3D" parent="Floors"]' in text
    assert '[node name="test_node" type="Node3D" parent="."]' in text
    assert '[node name="Lid" type="Node3D" parent="BuildingMesh"]' in text
    assert '[node name="Visual" type="Node3D" parent="BuildingMesh"]' in text
    assert '[node name="Visual" parent="BuildingMesh" instance=' not in text
    assert '[node name="NavigationRegion3D" type="NavigationRegion3D" parent="Floors/Floor0"]' in text
    assert 'navigation_mesh = SubResource("NavigationMesh_floor_0")' in text
    assert "navigation_layers = 1" in text
    assert '[node name="FloorMesh" type="Node3D" parent="Floors/Floor0/NavigationRegion3D"]' in text
    assert '[node name="FloorVisual" parent="Floors/Floor0/NavigationRegion3D/FloorMesh" instance=ExtResource("floor_visual_0")]' in text
    assert '[node name="Rooms" type="Node3D" parent="Floors/Floor0"]' in text
    assert '302 101_Seminar Room' in text
    assert '302 100E1_Elevator_SetE1' in text
    assert '[node name="MainDoor" type="Node3D" parent="Floors/Floor0/Rooms"]' in text
    assert "NavigationLinks" in text
    assert '[node name="CollisionShape3D" type="CollisionShape3D" parent="Floors/Floor0/ClickArea3D"]' in text
    assert '[node name="CollisionShape3D" type="CollisionShape3D" parent="ClickArea3D"]' in text

    json.dumps(manifest)


def test_write_unimate_scene_uses_room_plate_navmesh_fallback_when_floor_slab_missing(tmp_path):
    manifest = {
        "building": {"id": "science", "display_name": "Science Centre"},
        "floors": [{"floor_index": 0, "floor_name": "B-2", "height": -6.0}],
        "rooms": [
            {
                "external_id": "303-SB01",
                "node_name": "303 SB01_Storage",
                "floor_index": 0,
                "anchor": [2.0, -6.0, 2.0],
            }
        ],
        "portals": [],
        "external_doors": [],
    }
    nav_meshes = [
        build_room_plate(
            "room__303-SB01__floor_B-2",
            [[0.0, -5.97, 0.0], [4.0, -5.97, 0.0], [4.0, -5.97, 4.0], [0.0, -5.97, 4.0], [0.0, -5.97, 0.0]],
            "other",
        )
    ]

    path = write_unimate_scene(manifest, tmp_path / "science_unimate.tscn", navigation_meshes=nav_meshes)
    text = path.read_text(encoding="utf-8")

    assert '[sub_resource type="NavigationMesh" id="NavigationMesh_floor_0"]' in text
    assert "vertices = PackedVector3Array(" in text
    assert "polygons = [PackedInt32Array" in text
    assert 'navigation_mesh = SubResource("NavigationMesh_floor_0")' in text


def test_write_unimate_scene_snaps_external_doors_to_floor_navmesh(tmp_path):
    manifest = {
        "building": {"id": "science", "display_name": "Science Centre"},
        "floors": [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        "rooms": [],
        "portals": [],
        "external_doors": [
            {
                "external_id": "science_entry_001",
                "node_name": "MainDoor",
                "floor_index": 0,
                "anchor": [20.0, 0.0, 5.0],
            }
        ],
    }
    nav_meshes = [
        build_floor_slab(
            "floor__G",
            [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 0.0, 10.0], [0.0, 0.0, 10.0], [0.0, 0.0, 0.0]],
        )
    ]

    path = write_unimate_scene(manifest, tmp_path / "science_unimate.tscn", navigation_meshes=nav_meshes)
    text = path.read_text(encoding="utf-8")

    assert '[node name="MainDoor" type="Node3D" parent="Floors/Floor0/Rooms"]' in text
    assert "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 0, 5)" in text


def test_write_unimate_scene_errors_when_floor_with_records_has_no_navmesh_source(tmp_path):
    manifest = {
        "building": {"id": "science", "display_name": "Science Centre"},
        "floors": [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        "rooms": [
            {
                "external_id": "302-101",
                "node_name": "302 101_Seminar Room",
                "floor_index": 0,
                "anchor": [1.0, 0.0, 2.0],
            }
        ],
        "portals": [],
        "external_doors": [],
    }

    try:
        write_unimate_scene(manifest, tmp_path / "science_unimate.tscn", navigation_meshes=[MeshData("wall__G", [], [], "wall_low")])
    except ValueError as exc:
        assert "Floor 0" in str(exc)
        assert "no walkable navigation mesh" in str(exc)
    else:
        raise AssertionError("Expected missing navigation mesh source to fail")


def test_write_unimate_scene_assigns_unique_navigation_layer_per_floor(tmp_path):
    manifest = {
        "building": {"id": "science", "display_name": "Science Centre"},
        "floors": [
            {"floor_index": 0, "floor_name": "G", "height": 0.0},
            {"floor_index": 1, "floor_name": "1", "height": 4.2},
        ],
        "rooms": [
            {"external_id": "302-001", "node_name": "302 001", "floor_index": 0, "anchor": [1.0, 0.0, 1.0]},
            {"external_id": "302-101", "node_name": "302 101", "floor_index": 1, "anchor": [1.0, 4.2, 1.0]},
        ],
        "portals": [],
        "external_doors": [],
    }
    nav_meshes = [
        build_floor_slab(
            "floor__G",
            [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 4.0], [0.0, 0.0, 4.0], [0.0, 0.0, 0.0]],
        ),
        build_floor_slab(
            "floor__1",
            [[0.0, 4.2, 0.0], [4.0, 4.2, 0.0], [4.0, 4.2, 4.0], [0.0, 4.2, 4.0], [0.0, 4.2, 0.0]],
        ),
    ]

    path = write_unimate_scene(manifest, tmp_path / "science_unimate.tscn", navigation_meshes=nav_meshes)
    text = path.read_text(encoding="utf-8")

    assert text.count("navigation_layers = 1") == 1
    assert text.count("navigation_layers = 2") == 1


def test_navigation_mesh_resources_add_connected_floor_hull_for_disconnected_plates():
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    meshes = [
        build_room_plate(
            "room__302-A__floor_G",
            [[0.0, 0.03, 0.0], [2.0, 0.03, 0.0], [2.0, 0.03, 2.0], [0.0, 0.03, 2.0], [0.0, 0.03, 0.0]],
            "other",
        ),
        build_room_plate(
            "room__302-B__floor_G",
            [[10.0, 0.03, 0.0], [12.0, 0.03, 0.0], [12.0, 0.03, 2.0], [10.0, 0.03, 2.0], [10.0, 0.03, 0.0]],
            "other",
        ),
    ]

    resources = _navigation_mesh_resources(meshes, floors, {0})

    assert 0 in resources
    assert any(len(polygon) > 3 for polygon in resources[0]["polygons"])


def test_navigation_mesh_resources_emit_godot_map_path_winding():
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    meshes = [
        build_floor_slab(
            "floor__G",
            [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 0.0, 10.0], [0.0, 0.0, 10.0], [0.0, 0.0, 0.0]],
        )
    ]

    resource = _navigation_mesh_resources(meshes, floors, {0})[0]

    for polygon in resource["polygons"]:
        area = 0.0
        for offset, vertex_index in enumerate(polygon):
            vertex = resource["vertices"][vertex_index]
            next_vertex = resource["vertices"][polygon[(offset + 1) % len(polygon)]]
            area += vertex[0] * next_vertex[2] - next_vertex[0] * vertex[2]
        assert area < 0.0

from building3d.unimate_publish import wire_building_main


BUILDING_MAIN = """[gd_scene format=3 uid="uid://yg3klp1gonof"]

[ext_resource type="Script" path="res://Scripts/map/BuildingMainController.gd" id="1_building_main"]
[ext_resource type="PackedScene" path="res://Scene/science_baked_full.tscn" id="4_science_building"]
[ext_resource type="PackedScene" path="res://Scene/business_baked_full.tscn" id="5_business_building"]

[node name="BuildingMain" type="Node3D"]

[node name="Buildings" type="Node3D" parent="GameLayer/SubViewport"]

[node name="Science" parent="GameLayer/SubViewport/Buildings" instance=ExtResource("4_science_building")]
transform = Transform3D(2.85, 0, 0, 0, 2.85, 0, 0, 0, 2.85, -50, 0, 0)
building_name = "science"

[node name="Business" parent="GameLayer/SubViewport/Buildings" instance=ExtResource("5_business_building")]
transform = Transform3D(2.85, 0, 0, 0, 2.85, 0, 0, 0, 2.85, -50, 0, 0)
building_name = "business"

[node name="NavigationNPC" parent="GameLayer/SubViewport" instance=ExtResource("4_navigation_npc")]
"""


def _write(tmp_path):
    scene_dir = tmp_path / "Scene"
    scene_dir.mkdir()
    scene = scene_dir / "BuildingMain.tscn"
    scene.write_text(BUILDING_MAIN, encoding="utf-8")
    return scene


def test_wire_building_main_adds_scaled_child_under_buildings(tmp_path):
    scene = _write(tmp_path)

    result = wire_building_main(tmp_path, "art")
    text = scene.read_text(encoding="utf-8")

    assert result["changed"] is True
    assert result["node_name"] == "Art"
    # ext_resource for the baked scene was added.
    assert '[ext_resource type="PackedScene" path="res://Scene/art_baked_full.tscn"' in text
    # Node lives under the Buildings parent, scaled like the other baked buildings.
    assert '[node name="Art" parent="GameLayer/SubViewport/Buildings" instance=ExtResource(' in text
    assert 'building_name = "art"' in text
    assert "transform = Transform3D(2.85, 0, 0, 0, 2.85, 0, 0, 0, 2.85, -50, 0, 0)" in text
    # Inserted before the non-building sibling, not after it.
    assert text.index('building_name = "art"') < text.index("NavigationNPC")


def test_wire_building_main_is_idempotent(tmp_path):
    scene = _write(tmp_path)

    wire_building_main(tmp_path, "art")
    once = scene.read_text(encoding="utf-8")
    result = wire_building_main(tmp_path, "art")
    twice = scene.read_text(encoding="utf-8")

    assert result["changed"] is False
    assert once == twice
    assert once.count('building_name = "art"') == 1


def test_wire_building_main_allocates_unique_ext_id(tmp_path):
    scene = _write(tmp_path)
    # Pre-seed a clashing id so the allocator must pick another.
    scene.write_text(BUILDING_MAIN.replace('id="5_business_building"', 'id="art_building"'), encoding="utf-8")

    wire_building_main(tmp_path, "art")
    text = scene.read_text(encoding="utf-8")

    assert 'path="res://Scene/art_baked_full.tscn" id="art_building_2"' in text

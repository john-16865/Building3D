import json
from pathlib import Path

from building3d.unimate_publish import publish_group_to_unimate, safe_floor_label


def test_safe_floor_label_matches_unimate_asset_naming():
    assert safe_floor_label("B-2") == "b2"
    assert safe_floor_label("B-1") == "b1"
    assert safe_floor_label("-5") == "b5"
    assert safe_floor_label("-1") == "b1"
    assert safe_floor_label("G") == "g"
    assert safe_floor_label("M8") == "m8"
    assert safe_floor_label("10") == "10"
    assert safe_floor_label("Level 10") == "level_10"


def test_publish_group_to_unimate_generates_source_bake_and_check_files(tmp_path):
    export_dir = tmp_path / "exports" / "business"
    godot_dir = tmp_path / "Godot"
    export_dir.mkdir(parents=True)
    manifest = {
        "building": {"id": "business"},
        "floors": [
            {"floor_index": 0, "floor_name": "B-1", "height": -3.0},
            {"floor_index": 1, "floor_name": "G", "height": 0.0},
        ],
        "rooms": [],
        "portals": [],
        "external_doors": [],
        "nav": {"links": []},
        "assets": {
            "floor_visual_glbs": [
                {"floor_index": 0, "floor_name": "B-1", "filename": "business_floor_0_visual.glb"},
                {"floor_index": 1, "floor_name": "G", "filename": "business_floor_1_visual.glb"},
            ],
            "walkable_path_glbs": [
                {"floor_index": 0, "floor_name": "B-1", "filename": "business_floor_0_walkable_paths.glb"},
                {"floor_index": 1, "floor_name": "G", "filename": "business_floor_1_walkable_paths.glb"},
            ],
        },
    }
    (export_dir / "business_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (export_dir / "business_portal_topology.json").write_text('{"building_id":"business"}\n', encoding="utf-8")
    (export_dir / "business_unimate.tscn").write_text(
        "\n".join(
            [
                '[ext_resource type="PackedScene" path="res://Assets/Buildings/Business/business_floor_0_visual.glb" id="floor_visual_0"]',
                '[ext_resource type="PackedScene" path="res://Assets/Buildings/Business/business_floor_0_walkable_paths.glb" id="walkable_path_visual_0"]',
                '[ext_resource type="PackedScene" path="res://Assets/Buildings/Business/business_floor_1_visual.glb" id="floor_visual_1"]',
                '[ext_resource type="PackedScene" path="res://Assets/Buildings/Business/business_floor_1_walkable_paths.glb" id="walkable_path_visual_1"]',
            ]
        ),
        encoding="utf-8",
    )
    for filename in [
        "business_floor_0_visual.glb",
        "business_floor_0_walkable_paths.glb",
        "business_floor_1_visual.glb",
        "business_floor_1_walkable_paths.glb",
    ]:
        (export_dir / filename).write_bytes(b"glTF-test")

    result = publish_group_to_unimate(export_dir, godot_dir)

    asset_dir = godot_dir / "Assets" / "Buildings" / "Business"
    assert (asset_dir / "business_full_floor_b1_visual.glb").read_bytes() == b"glTF-test"
    assert (asset_dir / "business_full_floor_g_walkable_paths.glb").read_bytes() == b"glTF-test"
    assert (asset_dir / "business_full_manifest.json").exists()
    assert json.loads((asset_dir / "business_full_portal_topology.json").read_text(encoding="utf-8"))["building_id"] == "business"
    assert json.loads((asset_dir / "business_portal_topology.json").read_text(encoding="utf-8"))["building_id"] == "business"
    source_text = (godot_dir / "Scene" / "business_full_source.tscn").read_text(encoding="utf-8")
    assert "business_full_floor_b1_visual.glb" in source_text
    assert "business_floor_0_visual.glb" not in source_text
    bake_text = (godot_dir / "tools" / "generate_business_baked_full_scene.gd").read_text(encoding="utf-8")
    assert 'const SOURCE_SCENE := "res://Scene/business_full_source.tscn"' in bake_text
    assert 'const OUTPUT_SCENE := "res://Scene/business_baked_full.tscn"' in bake_text
    assert 'const EXPECTED_FLOORS := ["B-1", "G"]' in bake_text
    assert 'root.name = "BusinessBakedFull"' in bake_text
    check_text = (godot_dir / "tools" / "check_business_full_scene.gd").read_text(encoding="utf-8")
    assert 'const BAKED_SCENE := "res://Scene/business_baked_full.tscn"' in check_text
    assert 'business_full_floor_b1_visual.glb' in check_text
    assert result["bake_script"] == godot_dir / "tools" / "generate_business_baked_full_scene.gd"


def test_publish_group_to_unimate_generates_campus_placement_for_baked_scene(tmp_path):
    export_dir = tmp_path / "exports" / "business"
    godot_dir = tmp_path / "Godot"
    export_dir.mkdir(parents=True)
    (godot_dir / "Scene").mkdir(parents=True)
    (godot_dir / "Scene" / "business_baked_full.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    manifest = {
        "building": {"id": "business", "display_name": "Business School OGGB"},
        "floors": [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        "rooms": [{"node_name": "Room"}],
        "portals": [],
        "external_doors": [
            {
                "entry_id": "business_entry_001",
                "external_id": "business_entry_001",
                "node_name": "MainDoor",
                "display_name": "Main entrance",
                "anchor": [11.584154, 0.0, 72.913164],
                "lon": 174.77149,
                "lat": -36.8522696,
                "confidence": "high",
                "supporting_routes": 33,
            }
        ],
        "nav": {"links": []},
        "assets": {
            "floor_visual_glbs": [{"floor_index": 0, "floor_name": "G", "filename": "business_floor_0_visual.glb"}],
            "walkable_path_glbs": [
                {"floor_index": 0, "floor_name": "G", "filename": "business_floor_0_walkable_paths.glb"}
            ],
        },
    }
    (export_dir / "business_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (export_dir / "business_unimate.tscn").write_text(
        "\n".join(
            [
                '[ext_resource type="PackedScene" path="res://Assets/Buildings/Business/business_floor_0_visual.glb" id="floor_visual_0"]',
                '[ext_resource type="PackedScene" path="res://Assets/Buildings/Business/business_floor_0_walkable_paths.glb" id="walkable_path_visual_0"]',
            ]
        ),
        encoding="utf-8",
    )
    (export_dir / "business_floor_0_visual.glb").write_bytes(b"glTF-test")
    (export_dir / "business_floor_0_walkable_paths.glb").write_bytes(b"glTF-test")

    result = publish_group_to_unimate(export_dir, godot_dir, apply_campus_placement=True)

    placement_path = godot_dir / "Assets" / "Buildings" / "Business" / "business_campus_placement.json"
    placement = json.loads(placement_path.read_text(encoding="utf-8"))
    assert placement["scene_path"] == "res://Scene/business_baked_full.tscn"
    assert placement["transform"]["origin"] == [48.686271, 0.0, 832.391614]
    apply_script = godot_dir / "tools" / "apply_business_campus_placement.gd"
    apply_text = apply_script.read_text(encoding="utf-8")
    assert 'const PLACEMENT_JSON := "res://Assets/Buildings/Business/business_campus_placement.json"' in apply_text
    assert 'load(str(placement.get("scene_path", ""))) as PackedScene' in apply_text
    assert result["campus_placement"] == placement_path
    assert result["campus_apply_script"] == apply_script


def test_publish_group_to_unimate_rejects_campus_placement_without_baked_scene(tmp_path):
    export_dir = tmp_path / "exports" / "business"
    godot_dir = tmp_path / "Godot"
    export_dir.mkdir(parents=True)
    manifest = {
        "building": {"id": "business"},
        "floors": [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        "rooms": [],
        "portals": [],
        "external_doors": [
            {
                "entry_id": "business_entry_001",
                "node_name": "MainDoor",
                "anchor": [11.584154, 0.0, 72.913164],
                "lon": 174.77149,
                "lat": -36.8522696,
            }
        ],
        "nav": {"links": []},
        "assets": {
            "floor_visual_glbs": [{"floor_index": 0, "floor_name": "G", "filename": "business_floor_0_visual.glb"}],
            "walkable_path_glbs": [
                {"floor_index": 0, "floor_name": "G", "filename": "business_floor_0_walkable_paths.glb"}
            ],
        },
    }
    (export_dir / "business_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (export_dir / "business_unimate.tscn").write_text("", encoding="utf-8")
    (export_dir / "business_floor_0_visual.glb").write_bytes(b"glTF-test")
    (export_dir / "business_floor_0_walkable_paths.glb").write_bytes(b"glTF-test")

    try:
        publish_group_to_unimate(export_dir, godot_dir, apply_campus_placement=True)
    except FileNotFoundError as exc:
        assert "business_baked_full.tscn" in str(exc)
    else:
        raise AssertionError("Expected missing baked scene to reject campus placement")

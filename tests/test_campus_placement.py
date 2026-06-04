import json

from building3d.campus_placement import CampusPlacementConfig, CampusPlacementReference, build_campus_placement


def test_build_campus_placement_computes_business_origin_from_science_reference():
    manifest = {
        "building": {"id": "business", "display_name": "Business School OGGB"},
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
    }
    config = CampusPlacementConfig(
        reference=CampusPlacementReference(
            building_id="science",
            lon=174.76818825,
            lat=-36.852228925,
            local_anchor=[-18.06752, 0.0, 119.648002],
            origin=[-187.370620, 0.0, 870.065906],
            scale=0.89260984,
        )
    )

    placement = build_campus_placement(manifest, config)

    assert placement["building_id"] == "business"
    assert placement["scene_path"] == "res://Scene/business_baked_full.tscn"
    assert placement["entrance"]["entry_id"] == "business_entry_001"
    assert placement["entrance"]["anchor"] == [11.584154, 0.0, 72.913164]
    assert placement["transform"]["origin"] == [48.686271, 0.0, 832.391614]
    assert placement["transform"]["godot"] == (
        "Transform3D(0.89260984, 0, 0, 0, 0.89260984, 0, "
        "0, 0, -0.89260984, 48.686271, 0, 832.391614)"
    )


def test_build_campus_placement_prefers_most_supported_high_confidence_door():
    manifest = {
        "building": {"id": "business"},
        "external_doors": [
            {
                "entry_id": "business_entry_001",
                "node_name": "MainDoor",
                "anchor": [0.0, 0.0, 0.0],
                "lon": 174.0,
                "lat": -36.0,
                "confidence": "medium",
                "supporting_routes": 100,
            },
            {
                "entry_id": "business_entry_002",
                "node_name": "Door2",
                "anchor": [5.0, 0.0, 10.0],
                "lon": 174.1,
                "lat": -36.1,
                "confidence": "high",
                "supporting_routes": 2,
            },
        ],
    }

    placement = build_campus_placement(manifest, CampusPlacementConfig())

    assert placement["entrance"]["entry_id"] == "business_entry_002"


def test_build_campus_placement_carries_reference_y_offset():
    manifest = {
        "building": {"id": "business"},
        "external_doors": [
            {
                "entry_id": "business_entry_001",
                "node_name": "MainDoor",
                "anchor": [11.584154, 0.0, 72.913164],
                "lon": 174.77149,
                "lat": -36.8522696,
            }
        ],
    }
    config = CampusPlacementConfig(
        reference=CampusPlacementReference(
            lon=174.76818825,
            lat=-36.852228925,
            local_anchor=[-18.06752, 0.0, 119.648],
            origin=[-162.63676, 0.45404053, 860.31226],
            scale=0.89260983,
        )
    )

    placement = build_campus_placement(manifest, config)

    assert placement["transform"]["origin"] == [73.420128, 0.454041, 822.637971]


def test_build_campus_placement_writes_json_safe_values(tmp_path):
    manifest = {
        "building": {"id": "science"},
        "external_doors": [
            {
                "entry_id": "science_entry_001",
                "node_name": "MainDoor",
                "anchor": [-18.06752, 0.0, 119.648002],
                "lon": 174.76818825,
                "lat": -36.852228925,
                "confidence": "high",
                "supporting_routes": 76,
            }
        ],
    }

    placement = build_campus_placement(manifest, CampusPlacementConfig())
    path = tmp_path / "science_campus_placement.json"
    path.write_text(json.dumps(placement, indent=2, sort_keys=True), encoding="utf-8")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == 1
    assert loaded["node_name"] == "Science"
    assert loaded["buildings_parent"] == "GameLayer/HBox/VBoxContainer/ViewportContainer/SubViewport/Buildings"

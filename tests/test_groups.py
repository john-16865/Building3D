import json

from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.discovery import BuildingInventoryRecord
from building3d.groups import _dedupe_node_names, _sync_nav_node_names, generate_group


def test_generate_group_builds_science_package_with_unimate_scene(tmp_path):
    solution = _solution_config(tmp_path)
    records = [
        _record("301-science", "301", [174.0, -36.0]),
        _record("302-science", "302", [174.0002, -36.0002]),
    ]
    _write_locations(solution.raw_root / "buildings" / "301-science", "301", "0", "301-001", "Teaching Lab")
    _write_locations(solution.raw_root / "buildings" / "302-science", "302", "G", "302-100E1", "Elevator")
    _write_external_doors(solution.processed_root / "groups" / "science")

    group = BuildingGroupConfig(
        id="science",
        display_name="Science Centre",
        members=["301", "302"],
        aliases=["science", "science centre", "301", "302"],
        primary_member="302",
    )

    result = generate_group(solution, group, records=records, fetch_missing=False)

    export_dir = tmp_path / "exports" / "groups" / "science"
    manifest = json.loads((export_dir / "science_manifest.json").read_text(encoding="utf-8"))
    scene_text = (export_dir / "science_unimate.tscn").read_text(encoding="utf-8")

    assert result["rooms"] == 1
    assert result["portals"] == 1
    assert result["external_doors"] == 1
    assert (export_dir / "science_visual.glb").read_bytes()[:4] == b"glTF"
    assert (export_dir / "science_nav.glb").read_bytes()[:4] == b"glTF"
    assert (export_dir / "science_floor_0_visual.glb").read_bytes()[:4] == b"glTF"
    assert manifest["schema_version"] == 2
    assert manifest["building"]["id"] == "science"
    assert manifest["building"]["members"] == ["301", "302"]
    assert manifest["assets"]["floor_visual_glbs"][0]["filename"] == "science_floor_0_visual.glb"
    assert manifest["building_aliases"]["302"] == "science"
    assert manifest["rooms"][0]["logical_building_id"] == "science"
    assert manifest["rooms"][0]["source_building_admin_id"] == "301"
    assert manifest["rooms"][0]["node_name"] == "301 001_Teaching Lab"
    assert manifest["portals"][0]["node_name"] == "302 100E1_Elevator_SetE1"
    assert manifest["external_doors"][0]["node_name"] == "MainDoor"
    assert manifest["external_doors"][0]["kind"] == "door"
    assert manifest["external_doors"][0]["anchor"] == [1.0, 0.0, 2.0]
    assert manifest["nav"]["building_entries"][0]["node_name"] == "MainDoor"
    assert manifest["nav"]["room_targets"][0]["logical_building_id"] == "science"
    assert manifest["nav"]["room_targets"][0]["node_name"] == "301 001_Teaching Lab"
    assert 'building_name = "science"' in scene_text
    assert '[node name="test_node" type="Node3D" parent="."]' in scene_text
    assert '[node name="Lid" type="Node3D" parent="BuildingMesh"]' in scene_text
    assert '[node name="NavigationRegion3D" type="NavigationRegion3D"' in scene_text
    assert '[node name="FloorMesh" type="Node3D" parent="Floors/Floor0/NavigationRegion3D"]' in scene_text
    assert 'science_floor_0_visual.glb' in scene_text
    assert '[node name="CollisionShape3D" type="CollisionShape3D" parent="Floors/Floor0/ClickArea3D"]' in scene_text
    assert '[sub_resource type="NavigationMesh" id="NavigationMesh_floor_' in scene_text
    assert "navigation_mesh = SubResource" in scene_text
    assert "301 001_Teaching Lab" in scene_text
    assert "302 100E1_Elevator_SetE1" in scene_text
    assert '[node name="MainDoor" type="Node3D" parent="Floors/Floor0/Rooms"]' in scene_text
    assert json.loads((export_dir / "external_doors.json").read_text(encoding="utf-8"))[0]["node_name"] == "MainDoor"


def test_group_node_names_are_deduped_per_floor():
    manifest = {
        "rooms": [
            {"floor_index": 0, "node_name": "302 615_Office Space", "source_id": "aaaa1111"},
            {"floor_index": 0, "node_name": "302 615_Office Space", "source_id": "bbbb2222"},
        ],
        "portals": [],
        "nav": {
            "room_targets": [
                {"source_id": "aaaa1111", "node_name": "302 615_Office Space"},
                {"source_id": "bbbb2222", "node_name": "302 615_Office Space"},
            ],
            "links": [],
        },
    }

    _dedupe_node_names(manifest)
    _sync_nav_node_names(manifest)

    assert manifest["rooms"][0]["node_name"] == "302 615_Office Space"
    assert manifest["rooms"][1]["node_name"] == "302 615_Office Space__bbbb2222"
    assert manifest["nav"]["room_targets"][0]["node_name"] == "302 615_Office Space"
    assert manifest["nav"]["room_targets"][1]["node_name"] == "302 615_Office Space__bbbb2222"


def test_group_node_names_are_globally_deduped_but_keep_portal_set_suffix():
    manifest = {
        "rooms": [
            {"floor_index": 1, "node_name": "302 200C3_Unclassified Facilities", "source_id": "room1111"},
            {"floor_index": 2, "node_name": "302 200C3_Unclassified Facilities", "source_id": "room2222"},
        ],
        "portals": [
            {"floor_index": 1, "node_name": "302 100S2_Stairs_Set2", "source_id": "portal1111"},
            {"floor_index": 2, "node_name": "302 100S2_Stairs_Set2", "source_id": "portal2222"},
        ],
        "nav": {
            "room_targets": [
                {"source_id": "room1111", "node_name": "302 200C3_Unclassified Facilities"},
                {"source_id": "room2222", "node_name": "302 200C3_Unclassified Facilities"},
            ],
            "links": [
                {"from_source_id": "portal1111", "to_source_id": "portal2222"},
            ],
        },
    }

    _dedupe_node_names(manifest)
    _sync_nav_node_names(manifest)

    assert manifest["rooms"][0]["node_name"] == "302 200C3_Unclassified Facilities"
    assert manifest["rooms"][1]["node_name"] == "302 200C3_Unclassified Facilities__room2222"
    assert manifest["portals"][0]["node_name"] == "302 100S2_Stairs_Set2"
    assert manifest["portals"][1]["node_name"] == "302 100S2_Stairs__portal22_Set2"
    assert manifest["nav"]["room_targets"][1]["node_name"] == "302 200C3_Unclassified Facilities__room2222"
    assert manifest["nav"]["links"][0]["to_node_name"] == "302 100S2_Stairs__portal22_Set2"


def _solution_config(tmp_path):
    return SolutionConfig(
        project_root=tmp_path,
        solution_id="auckland",
        raw_root=tmp_path / "raw",
        processed_root=tmp_path / "processed",
        export_root=tmp_path / "exports",
        buildings_sync_url="https://example.test/buildings",
        venues_sync_url="https://example.test/venues",
        locations_url="https://example.test/locations",
        building_details_url_template="https://example.test/buildings/{building_id}",
        take=1000,
        default_floor_spacing=4.2,
        basement_floor_spacing=3.0,
        failure_policy="continue",
        building_admin_ids=[],
        venue_ids=[],
    )


def _record(slug, admin_id, origin):
    return BuildingInventoryRecord(
        slug=slug,
        mapsindoors_id=f"building-{admin_id}",
        admin_id=admin_id,
        external_id=f"B{admin_id}",
        display_name="Science Centre",
        venue_id="venue-city",
        venue_name="City Campus",
        origin=origin,
        bbox=[],
        default_floor="0",
        floor_keys=["0"],
        source_urls=[f"https://example.test/locations?building={admin_id}"],
    )


def _write_locations(raw_dir, building, floor_name, external_id, name):
    raw_dir.mkdir(parents=True)
    lon = 174.0 + int(building[:3]) * 0.000001
    lat = -36.0
    raw = [
        {
            "id": f"feature-{external_id}",
            "properties": {
                "externalId": external_id,
                "name": name,
                "building": building,
                "floorName": floor_name,
                "type": name,
                "anchor": {"coordinates": [lon + 0.00001, lat + 0.00001]},
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [lon, lat],
                        [lon + 0.00002, lat],
                        [lon + 0.00002, lat + 0.00002],
                        [lon, lat + 0.00002],
                        [lon, lat],
                    ]
                ],
            },
        }
    ]
    (raw_dir / "locations_0000.json").write_text(json.dumps(raw), encoding="utf-8")


def _write_external_doors(processed_group_dir):
    processed_group_dir.mkdir(parents=True)
    rows = [
        {
            "entry_id": "science_entry_001",
            "floor_name": "G",
            "floor_index": 0,
            "local": [1.0, 0.0, 2.0],
            "source": "route_abutters_outside_to_inside",
            "confidence": "high",
            "supporting_routes": 12,
            "target_building_admin_ids": ["301", "302"],
            "target_external_ids": ["301-001"],
        }
    ]
    (processed_group_dir / "external_doors.json").write_text(json.dumps(rows), encoding="utf-8")

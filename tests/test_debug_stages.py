import json

from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.debug_stages import door_point_marker_meshes, export_group_debug_stages, wall_opening_marker_meshes
from building3d.discovery import BuildingInventoryRecord


def test_door_point_marker_meshes_use_confidence_materials():
    meshes = door_point_marker_meshes(
        [
            {"external_id": "301-001", "door_local": [1.0, 2.0, 3.0], "confidence": "high"},
            {"external_id": "301-002", "door_local": [4.0, 5.0, 6.0], "confidence": "medium"},
            {"external_id": "301-003", "door_local": [7.0, 8.0, 9.0], "confidence": "unexpected"},
            {"external_id": "301-004", "door_local": ["bad"], "confidence": "high"},
        ]
    )

    assert [mesh.material for mesh in meshes] == ["door_point_high", "door_point_medium", "door_point_unknown"]
    assert meshes[0].metadata["debug_overlay"] == "door_point"


def test_wall_opening_marker_meshes_create_vertical_debug_panels():
    meshes = wall_opening_marker_meshes(
        {"G": {((0.0, 0.0), (2.0, 0.0))}},
        {"G": 4.2},
        "route",
    )

    assert len(meshes) == 1
    assert meshes[0].material == "wall_open_route"
    assert meshes[0].vertices[0] == [0.0, 4.36, 0.0]
    assert meshes[0].vertices[2] == [2.0, 5.91, 0.0]
    assert meshes[0].metadata["debug_overlay"] == "wall_opening_route"


def test_export_group_debug_stages_writes_all_stage_files(tmp_path):
    solution = _solution_config(tmp_path)
    records = [
        _record("301-science", "301", [174.0, -36.0]),
        _record("302-science", "302", [174.0002, -36.0002]),
    ]
    _write_locations(solution.raw_root / "buildings" / "301-science", "301", "0", "301-001", "Teaching Lab")
    _write_locations(solution.raw_root / "buildings" / "302-science", "302", "G", "302-100E1", "Elevator")
    _write_room_door_points(solution.processed_root / "groups" / "science")

    group = BuildingGroupConfig(
        id="science",
        display_name="Science Centre",
        members=["301", "302"],
        aliases=["science", "science centre", "301", "302"],
        primary_member="302",
    )

    result = export_group_debug_stages(solution, group, records=records, fetch_missing=False)
    debug_dir = tmp_path / "exports" / "groups" / "science" / "debug"
    summary = json.loads((debug_dir / "stage_summary.json").read_text(encoding="utf-8"))

    for filename in (
        "stage_01_raw_visual.glb",
        "stage_02_door_points.glb",
        "stage_03_route_lines.glb",
        "stage_04_wall_opening_candidates.glb",
        "stage_05_wall_opened_visual.glb",
        "stage_06_combined_overlay.glb",
    ):
        assert (debug_dir / filename).read_bytes()[:4] == b"glTF"

    assert result["debug_dir"] == str(debug_dir)
    assert summary["rooms"] == 1
    assert summary["portals"] == 1
    assert summary["door_points"] == 1
    assert "raw_visual" in summary["files"]


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


def _write_room_door_points(processed_group_dir):
    processed_group_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "external_id": "301-001",
            "source_id": "feature-301-001",
            "floor_name": "G",
            "floor_index": 0,
            "door_local": [2.0, 0.0, 3.0],
            "door_source": "route_boundary_intersection",
            "confidence": "high",
        }
    ]
    (processed_group_dir / "science_room_door_points_route_derived.json").write_text(json.dumps(rows), encoding="utf-8")

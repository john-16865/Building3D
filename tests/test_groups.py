import json
from math import cos, pi

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

import building3d.groups as groups
from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.discovery import BuildingInventoryRecord
from building3d.groups import (
    _dedupe_node_names,
    _route_anchor_envelope_mesh,
    _route_navigation_meshes_with_stats_from_cache,
    _route_navigation_meshes_from_cache,
    _sync_nav_node_names,
    generate_group,
)
from building3d.normalize import FloorRecord, NormalizedDataset, PortalRecord
from building3d.unimate import _navigation_mesh_resources


def test_generate_group_builds_science_package_with_unimate_scene(tmp_path):
    solution = _solution_config(tmp_path)
    records = [
        _record("301-science", "301", [174.0, -36.0]),
        _record("302-science", "302", [174.0002, -36.0002]),
    ]
    _write_locations(solution.raw_root / "buildings" / "301-science", "301", "0", "301-001", "Teaching Lab")
    _write_locations(solution.raw_root / "buildings" / "302-science", "302", "G", "302-100E1", "Elevator")
    _write_external_doors(solution.processed_root / "groups" / "science")
    _write_room_door_points(solution.processed_root / "groups" / "science")

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
    assert manifest["rooms"][0]["navigation_anchor"] == [2.0, 0.0, 3.0]
    assert manifest["portals"][0]["node_name"] == "302 100E1_Elevator_Set302E1"
    assert manifest["external_doors"][0]["node_name"] == "MainDoor"
    assert manifest["external_doors"][0]["kind"] == "door"
    assert manifest["external_doors"][0]["anchor"] == [1.0, 0.0, 2.0]
    topology = json.loads((export_dir / "science_portal_topology.json").read_text(encoding="utf-8"))
    assert topology["building_id"] == "science"
    assert topology["validation"]["terminal_count"] == 2
    assert topology["terminals"][0]["portal_name"] == "MainDoor"
    assert topology["terminals"][1]["portal_name"] == "302 100E1_Elevator_Set302E1"
    assert manifest["assets"]["portal_topology"] == "science_portal_topology.json"
    assert result["artifacts"]["portal_topology"] == str(export_dir / "science_portal_topology.json")
    assert manifest["nav"]["building_entries"][0]["node_name"] == "MainDoor"
    assert any(link["kind"] == "walk" for link in manifest["nav"]["links"])
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
    assert '[node name="NavTarget" type="Node3D" parent="Floors/Floor0/Rooms/301 001_Teaching Lab"]' in scene_text
    assert "302 100E1_Elevator_Set302E1" in scene_text
    assert '[node name="MainDoor" type="Node3D" parent="Floors/Floor0/Rooms"]' in scene_text
    assert json.loads((export_dir / "external_doors.json").read_text(encoding="utf-8"))[0]["node_name"] == "MainDoor"


def test_external_door_navigation_anchor_moves_off_isolated_exterior_component():
    manifest = {
        "building": {"id": "kenneth_myers"},
        "floors": [
            {"floor_index": 0, "floor_name": "G", "height": 0.0},
            {"floor_index": 1, "floor_name": "1", "height": 4.2},
        ],
        "rooms": [
            {
                "external_id": "820-101",
                "floor_index": 0,
                "floor_name": "G",
                "anchor": [8.0, 0.0, 5.0],
            }
        ],
        "portals": [
            {
                "external_id": "820-100E1",
                "node_name": "820 100E1_Elevator_Set820E1",
                "floor_index": 0,
                "floor_name": "G",
                "kind": "elevator",
                "anchor": [6.0, 0.0, 1.0],
            },
            {
                "external_id": "820-200E1",
                "node_name": "820 200E1_Elevator_Set820E1",
                "floor_index": 1,
                "floor_name": "1",
                "kind": "elevator",
                "anchor": [6.0, 4.2, 1.0],
            },
        ],
        "external_doors": [
            {
                "external_id": "kenneth_myers_entry_001",
                "entry_id": "kenneth_myers_entry_001",
                "node_name": "MainDoor",
                "floor_index": 0,
                "floor_name": "G",
                "kind": "door",
                "anchor": [1.0, 0.0, 1.0],
            }
        ],
        "nav": {"links": []},
    }
    navigation_meshes = [
        groups.MeshData(
            name="floor__G__interior",
            vertices=[
                [5.0, 0.0, 0.0],
                [15.0, 0.0, 0.0],
                [15.0, 0.0, 10.0],
                [5.0, 0.0, 10.0],
            ],
            faces=[[0, 1, 2, 3]],
            material="floor",
        ),
        groups.MeshData(
            name="floor__G__exterior_stub",
            vertices=[
                [0.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [2.0, 0.0, 2.0],
                [0.0, 0.0, 2.0],
            ],
            faces=[[0, 1, 2, 3]],
            material="floor",
        ),
    ]

    diagnostics = groups._assign_external_door_navigation_anchors(
        manifest,
        navigation_meshes,
    )

    door = manifest["external_doors"][0]
    assert door["anchor"] == [1.0, 0.0, 1.0]
    assert door["navigation_anchor"] == [5.0, 0.0, 1.0]
    assert door["navigation_anchor_relocated"] is True
    assert door["navigation_anchor_component_reason"] == "vertical_network_component"
    assert diagnostics["ok"] is True
    assert diagnostics["relocated_count"] == 1
    assert diagnostics["records"][0]["component_changed"] is True

    route_door = next(
        record
        for record in groups._route_navigation_point_records(manifest)
        if record["kind"] == "door"
    )
    assert route_door["anchor"] == [5.0, 0.0, 1.0]


def test_external_door_ids_are_normalized_to_group_id():
    group = BuildingGroupConfig(
        id="business",
        display_name="Business School OGGB",
        members=["260"],
        aliases=["business", "260"],
        primary_member="260",
    )

    door = groups._normalise_external_door(
        {
            "entry_id": "science_entry_001",
            "external_id": "science_entry_001",
            "floor_name": "G",
            "local": [11.584154, 0.0, 72.913164],
            "source": "route_abutters_outside_to_inside",
            "confidence": "high",
            "supporting_routes": 33,
        },
        1,
        group,
        {"G": 5},
    )

    assert door is not None
    assert door["entry_id"] == "business_entry_001"
    assert door["external_id"] == "business_entry_001"
    assert door["source_id"] == "business_entry_001"
    assert door["source_entry_id"] == "science_entry_001"
    assert door["source_external_id"] == "science_entry_001"
    assert door["aliases"][0] == "business_entry_001"


def test_generate_group_can_filter_to_one_member_and_one_floor_from_source(tmp_path):
    solution = _solution_config(tmp_path)
    records = [
        _record("301-science", "301", [174.0, -36.0]),
        _record("302-science", "302", [174.0002, -36.0002]),
    ]
    _write_locations(solution.raw_root / "buildings" / "301-science", "301", "0", "301-001", "Teaching Lab")
    _write_locations(solution.raw_root / "buildings" / "302-science", "302", "G", "302-100E1", "Elevator")
    _write_external_doors(solution.processed_root / "groups" / "science")
    _write_room_door_points(solution.processed_root / "groups" / "science")

    group = BuildingGroupConfig(
        id="science",
        display_name="Science Centre",
        members=["301", "302"],
        aliases=["science", "science centre", "301", "302"],
        primary_member="302",
    )

    result = generate_group(
        solution,
        group,
        records=records,
        fetch_missing=False,
        only_members=["301"],
        only_floors=["G"],
    )

    export_dir = tmp_path / "exports" / "groups" / "science"
    manifest = json.loads((export_dir / "science_manifest.json").read_text(encoding="utf-8"))
    scene_text = (export_dir / "science_unimate.tscn").read_text(encoding="utf-8")

    assert result["rooms"] == 1
    assert result["portals"] == 0
    assert result["floors"] == 1
    assert manifest["building"]["id"] == "science"
    assert manifest["building"]["members"] == ["301"]
    assert manifest["floors"] == [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    assert manifest["rooms"][0]["source_building_admin_id"] == "301"
    assert manifest["rooms"][0]["floor_name"] == "G"
    assert manifest["rooms"][0]["floor_index"] == 0
    assert manifest["assets"]["floor_visual_glbs"] == [
        {"floor_index": 0, "floor_name": "G", "filename": "science_floor_0_visual.glb"}
    ]
    assert 'building_name = "science"' in scene_text
    assert 'floor_name = "G"' in scene_text
    assert "301 001_Teaching Lab" in scene_text
    assert "302 100E1_Elevator_Set302E1" not in scene_text


def test_group_manifest_derives_unknown_vertical_links_for_godot(monkeypatch, tmp_path):
    dataset = NormalizedDataset(
        building_id="science",
        building_admin_id="303",
        building_name="Science Centre",
        floors=[
            FloorRecord("8", 10, 33.6),
            FloorRecord("M8", 11, 35.7),
        ],
        portals=[
            _unknown_elevator(
                "303-802",
                "feature-802",
                "8",
                10,
                80,
                [174.767831, -36.852784],
                [0.0, 33.6, 0.0],
            ),
            _unknown_elevator(
                "303-8U02",
                "feature-8U02",
                "M8",
                11,
                85,
                [174.7678313, -36.8527782],
                [0.1, 35.7, 0.1],
            ),
        ],
    )
    group = BuildingGroupConfig(
        id="science",
        display_name="Science Centre",
        members=["303"],
        aliases=["science", "science centre"],
        primary_member="303",
    )
    processed_dir = tmp_path / "processed" / "groups" / "science"
    export_dir = tmp_path / "exports" / "groups" / "science"
    processed_dir.mkdir(parents=True)
    export_dir.mkdir(parents=True)
    monkeypatch.setattr(groups, "MapsIndoorsRouteClient", _FakeMapsIndoorsRouteClient)

    manifest = groups._build_group_manifest(
        dataset,
        group,
        [_record("303-science", "303", [174.0, -36.0])],
        processed_dir,
        export_dir,
    )

    route_links = [
        link
        for link in manifest["nav"]["links"]
        if link.get("source") == "mapsindoors_route_graph"
    ]
    assert [(link["from_external_id"], link["to_external_id"]) for link in route_links] == [
        ("303-802", "303-8U02")
    ]
    assert route_links[0]["group_id"] == "MI_303_ELEV_001"
    assert manifest["portals"][0]["node_name"] == "303 802_Elevator_Set303MI_303_ELEV_001"
    assert manifest["portals"][1]["node_name"] == "303 8U02_Elevator_Set303MI_303_ELEV_001"
    assert manifest["nav"]["vertical_route_derivation"] == {
        "accepted": 1,
        "candidates": 1,
        "graph_id": "CITY_CAMPUS_Graph",
        "rejected": 0,
    }
    derived = json.loads((export_dir / "science_vertical_links_route_derived.json").read_text(encoding="utf-8"))
    assert derived["accepted"] == 1


def test_group_manifest_keeps_route_derivation_summary_after_rebuild(monkeypatch, tmp_path):
    portals = [
        _unknown_elevator(
            "303-802",
            "feature-802",
            "8",
            10,
            80,
            [174.767831, -36.852784],
            [0.0, 33.6, 0.0],
        ),
        _unknown_elevator(
            "303-8U02",
            "feature-8U02",
            "M8",
            11,
            85,
            [174.7678313, -36.8527782],
            [0.1, 35.7, 0.1],
        ),
    ]
    for portal in portals:
        portal.group_id = "MI_303_ELEV_001"
        portal.source_properties["vertical_group_source"] = "mapsindoors_route_graph"
        portal.source_properties["vertical_group_confidence"] = "high"
    dataset = NormalizedDataset(
        building_id="science",
        building_admin_id="303",
        building_name="Science Centre",
        floors=[
            FloorRecord("8", 10, 33.6),
            FloorRecord("M8", 11, 35.7),
        ],
        portals=portals,
    )
    group = BuildingGroupConfig(
        id="science",
        display_name="Science Centre",
        members=["303"],
        aliases=["science", "science centre"],
        primary_member="303",
    )
    processed_dir = tmp_path / "processed" / "groups" / "science"
    export_dir = tmp_path / "exports" / "groups" / "science"
    processed_dir.mkdir(parents=True)
    export_dir.mkdir(parents=True)

    class NoRouteCalls:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, origin, destination):
            raise AssertionError("route client should not be called for already grouped MI portals")

    monkeypatch.setattr(groups, "MapsIndoorsRouteClient", NoRouteCalls)

    manifest = groups._build_group_manifest(
        dataset,
        group,
        [_record("303-science", "303", [174.0, -36.0])],
        processed_dir,
        export_dir,
    )

    route_links = [
        link
        for link in manifest["nav"]["links"]
        if link.get("source") == "mapsindoors_route_graph"
    ]
    assert [(link["from_external_id"], link["to_external_id"]) for link in route_links] == [
        ("303-802", "303-8U02")
    ]
    assert manifest["nav"]["vertical_route_derivation"] == {
        "accepted": 1,
        "candidates": 0,
        "graph_id": "CITY_CAMPUS_Graph",
        "rejected": 0,
    }
    assert not (export_dir / "science_vertical_links_route_derived.json").exists()


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
            {"floor_index": 1, "node_name": "302 100S2_Stairs_Set302S2", "source_id": "portal1111"},
            {"floor_index": 2, "node_name": "302 100S2_Stairs_Set302S2", "source_id": "portal2222"},
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
    assert manifest["portals"][0]["node_name"] == "302 100S2_Stairs_Set302S2"
    assert manifest["portals"][1]["node_name"] == "302 100S2_Stairs__portal22_Set302S2"
    assert manifest["nav"]["room_targets"][1]["node_name"] == "302 200C3_Unclassified Facilities__room2222"
    assert manifest["nav"]["links"][0]["to_node_name"] == "302 100S2_Stairs__portal22_Set302S2"


def test_route_navigation_meshes_follow_cached_route_corridors_not_floor_union(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 8.0), point(8.0, 8.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_test.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)

    assert meshes
    assert all(mesh.name == "floor__G" or mesh.name.startswith("floor__G__part_") for mesh in meshes)
    assert all(mesh.material == "floor" for mesh in meshes)
    xs = [vertex[0] for mesh in meshes for vertex in mesh.vertices]
    zs = [vertex[2] for mesh in meshes for vertex in mesh.vertices]
    assert min(xs) < -0.5
    assert max(xs) < 10.5
    assert min(zs) < -0.5
    assert max(zs) < 10.5


def test_route_navigation_meshes_clip_stale_cached_routes_to_floor_footprint(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    inside_route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(1.0, 1.0), point(8.0, 1.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    stale_route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(40.0, -100.0), point(45.0, -100.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_inside.json").write_text(json.dumps(inside_route), encoding="utf-8")
    (route_cache_dir / "route_stale.json").write_text(json.dumps(stale_route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    footprint = Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])

    meshes, stats = _route_navigation_meshes_with_stats_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        clip_footprints_by_floor={"G": footprint},
    )

    assert meshes
    xs = [vertex[0] for mesh in meshes for vertex in mesh.vertices]
    zs = [vertex[2] for mesh in meshes for vertex in mesh.vertices]
    assert min(xs) >= -2.1
    assert max(xs) <= 12.1
    assert min(zs) >= -2.1
    assert max(zs) <= 12.1
    assert stats["route_cache"]["files_total"] == 2
    assert stats["route_cache"]["files_used"] == 1
    assert stats["route_cache"]["files_rejected"] == 1
    assert stats["route_cache"]["segments_rejected"] >= 1


def test_route_debug_centerlines_show_thin_cached_routes_without_navmesh_expansion(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 8.0), point(8.0, 8.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_test.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    assert hasattr(groups, "_route_debug_centerline_meshes_from_cache")
    meshes = groups._route_debug_centerline_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)

    assert meshes
    assert all(mesh.material == "route_centerline" for mesh in meshes)
    xs = [vertex[0] for mesh in meshes for vertex in mesh.vertices]
    zs = [vertex[2] for mesh in meshes for vertex in mesh.vertices]
    assert min(xs) >= -0.2
    assert max(xs) <= 8.2
    assert min(zs) >= -0.2
    assert max(zs) <= 8.2
    assert _mesh_surface_area(meshes) < 4.0


def test_route_debug_centerlines_draw_turns_as_separate_thin_segments(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 4.0)],
                            },
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 4.0), point(4.0, 4.0)],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_turn.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = groups._route_debug_centerline_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)

    assert len(meshes) == 2
    assert all(mesh.material == "route_centerline" for mesh in meshes)
    assert all(mesh.metadata["debug_overlay"] == "route_centerline" for mesh in meshes)
    assert all(len(mesh.vertices) == 4 for mesh in meshes)
    assert all(mesh.faces == [[0, 1, 2, 3]] for mesh in meshes)
    assert _mesh_surface_area(meshes) > 1.4
    assert _mesh_surface_area(meshes) < 1.8


def test_route_debug_centerlines_ignore_manifest_walk_links_and_anchor_points(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(5.0, 0.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_test.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    assert hasattr(groups, "_route_debug_centerline_meshes_from_cache")
    meshes = groups._route_debug_centerline_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        point_records=[{"floor_name": "G", "anchor": [200.0, 0.0, 0.0]}],
        walk_links=[
            {
                "kind": "walk",
                "from_floor_index": 0,
                "to_floor_index": 0,
                "from_anchor": [0.0, 0.0, 0.0],
                "to_anchor": [80.0, 0.0, 0.0],
            }
        ],
    )

    assert meshes
    assert max(vertex[0] for mesh in meshes for vertex in mesh.vertices) <= 5.2


def test_route_debug_centerlines_bridge_gaps_between_disconnected_floor_islands(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(-5.0, -5.0), point(15.0, -5.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_bridge.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    disconnected_footprint = MultiPolygon(
        [
            Polygon([(-10.0, -10.0), (0.0, -10.0), (0.0, 0.0), (-10.0, 0.0)]),
            Polygon([(10.0, -10.0), (20.0, -10.0), (20.0, 0.0), (10.0, 0.0)]),
        ]
    )

    meshes = groups._route_debug_centerline_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        clip_footprints_by_floor={"G": disconnected_footprint},
    )

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1
    assert min(vertex[0] for mesh in meshes for vertex in mesh.vertices) < -4.9
    assert max(vertex[0] for mesh in meshes for vertex in mesh.vertices) > 14.9


def test_route_debug_centerlines_reject_routes_outside_floor_hull(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(100.0, 100.0), point(110.0, 100.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_outside.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    footprint = Polygon([(-10.0, -10.0), (0.0, -10.0), (0.0, 0.0), (-10.0, 0.0)])

    meshes = groups._route_debug_centerline_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        clip_footprints_by_floor={"G": footprint},
    )

    assert meshes == []


def test_route_debug_centerlines_ignore_cached_routes_for_other_floor_endpoints(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float, floor_name: str = "2") -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": floor_name,
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": float(floor_name) * 10.0,
        }

    current_route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "start_location": point(0.0, 0.0),
                        "end_location": point(5.0, 0.0),
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(5.0, 0.0)],
                            }
                        ],
                    }
                ]
            }
        ],
    }
    stale_interfloor_route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "start_location": point(0.0, 50.0, "3"),
                        "end_location": point(100.0, 0.0, "2"),
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(100.0, 0.0), point(110.0, 0.0)],
                            }
                        ],
                    },
                    {
                        "start_location": point(100.0, 0.0, "2"),
                        "end_location": point(0.0, 60.0, "3"),
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 60.0, "3"), point(5.0, 60.0, "3")],
                            }
                        ],
                    },
                ]
            }
        ],
    }
    (route_cache_dir / "route_current.json").write_text(json.dumps(current_route), encoding="utf-8")
    (route_cache_dir / "route_stale_interfloor.json").write_text(json.dumps(stale_interfloor_route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "2", "height": 8.4}]

    meshes = groups._route_debug_centerline_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        route_endpoint_scope=[{"floor_name": "2", "anchor": [5.0, 8.4, 0.0]}],
    )

    assert meshes
    assert max(vertex[0] for mesh in meshes for vertex in mesh.vertices) <= 5.2


def test_route_navigation_meshes_buffer_multiline_clipped_routes_between_floor_islands(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(-5.0, -5.0), point(15.0, -5.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_bridge.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    disconnected_footprint = MultiPolygon(
        [
            Polygon([(-10.0, -10.0), (0.0, -10.0), (0.0, 0.0), (-10.0, 0.0)]),
            Polygon([(10.0, -10.0), (20.0, -10.0), (20.0, 0.0), (10.0, 0.0)]),
        ]
    )

    meshes = _route_navigation_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        clip_footprints_by_floor={"G": disconnected_footprint},
    )

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_generate_group_writes_walkable_path_visual_when_route_cache_is_used(tmp_path):
    solution = _solution_config(tmp_path)
    records = [_record("301-science", "301", [174.0, -36.0])]
    _write_locations(solution.raw_root / "buildings" / "301-science", "301", "0", "301-001", "Teaching Lab")
    _write_room_door_points(solution.processed_root / "groups" / "science")

    route_cache_dir = solution.export_root / "groups" / "science" / "door_route_cache"
    route_cache_dir.mkdir(parents=True)
    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    {"floor_name": "G", "lng": 174.00031, "lat": -35.99999, "zLevel": 0.0},
                                    {"floor_name": "G", "lng": 174.00032, "lat": -35.99999, "zLevel": 0.0},
                                ],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_test.json").write_text(json.dumps(route), encoding="utf-8")

    group = BuildingGroupConfig(
        id="science",
        display_name="Science Centre",
        members=["301"],
        aliases=["science", "301"],
        primary_member="301",
    )

    generate_group(solution, group, records=records, fetch_missing=False)

    export_dir = tmp_path / "exports" / "groups" / "science"
    manifest = json.loads((export_dir / "science_manifest.json").read_text(encoding="utf-8"))
    scene_text = (export_dir / "science_unimate.tscn").read_text(encoding="utf-8")

    assert (export_dir / "science_floor_0_walkable_paths.glb").read_bytes()[:4] == b"glTF"
    assert (export_dir / "science_floor_0_route_debug.glb").read_bytes()[:4] == b"glTF"
    assert manifest["assets"]["walkable_path_glbs"] == [
        {"floor_index": 0, "floor_name": "G", "filename": "science_floor_0_walkable_paths.glb"}
    ]
    assert manifest["assets"]["route_debug_glbs"] == [
        {"floor_index": 0, "floor_name": "G", "filename": "science_floor_0_route_debug.glb"}
    ]
    assert "WalkablePathVisual" in scene_text
    assert "science_floor_0_walkable_paths.glb" in scene_text
    assert manifest["nav"]["validation"]["route_cache"]["files_used"] == 1


def test_generate_group_filters_route_cache_to_current_manifest_endpoints(tmp_path):
    solution = _solution_config(tmp_path)
    records = [_record("303-science", "303", [174.0, -36.0])]
    _write_locations(solution.raw_root / "buildings" / "303-science", "303", "2", "303-201", "Teaching Lab")

    route_cache_dir = solution.export_root / "groups" / "science_test" / "door_route_cache"
    route_cache_dir.mkdir(parents=True)

    current_route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "start_location": {"floor_name": "2", "lng": 174.00031, "lat": -35.99999, "zLevel": 20.0},
                        "end_location": {"floor_name": "2", "lng": 174.000313, "lat": -35.99999, "zLevel": 20.0},
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    {"floor_name": "2", "lng": 174.00031, "lat": -35.99999, "zLevel": 20.0},
                                    {"floor_name": "2", "lng": 174.000313, "lat": -35.99999, "zLevel": 20.0},
                                ],
                            }
                        ],
                    }
                ]
            }
        ],
    }
    stale_route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "start_location": {"floor_name": "3", "lng": 174.0009, "lat": -35.9995, "zLevel": 30.0},
                        "end_location": {"floor_name": "3", "lng": 174.00095, "lat": -35.9995, "zLevel": 30.0},
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    {"floor_name": "2", "lng": 174.002, "lat": -35.99999, "zLevel": 20.0},
                                    {"floor_name": "2", "lng": 174.0021, "lat": -35.99999, "zLevel": 20.0},
                                ],
                            }
                        ],
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_current.json").write_text(json.dumps(current_route), encoding="utf-8")
    (route_cache_dir / "route_stale_interfloor.json").write_text(json.dumps(stale_route), encoding="utf-8")

    group = BuildingGroupConfig(
        id="science_test",
        display_name="Science Test",
        members=["303"],
        aliases=["science_test", "303"],
        primary_member="303",
    )

    generate_group(solution, group, records=records, fetch_missing=False, only_floors=["2"])

    export_dir = tmp_path / "exports" / "groups" / "science_test"
    manifest = json.loads((export_dir / "science_test_manifest.json").read_text(encoding="utf-8"))

    assert manifest["nav"]["validation"]["route_cache"]["files_total"] == 2
    assert manifest["nav"]["validation"]["route_cache"]["files_used"] == 1
    assert manifest["nav"]["validation"]["route_cache"]["files_out_of_scope"] == 1


def test_route_navigation_meshes_preserve_corridor_holes(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    point(-5.0, -5.0),
                                    point(5.0, -5.0),
                                    point(5.0, 5.0),
                                    point(-5.0, 5.0),
                                    point(-5.0, -5.0),
                                ],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_loop.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)

    assert meshes
    assert all(len(face) >= 3 for mesh in meshes for face in mesh.faces)
    for mesh in meshes:
        for face in mesh.faces:
            polygon = Polygon([(mesh.vertices[index][0], mesh.vertices[index][2]) for index in face])
            assert not polygon.covers(Point(0.0, 0.0))


def test_route_navigation_meshes_do_not_replace_cached_corridors_with_anchor_envelope(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    point(-5.0, -5.0),
                                    point(5.0, -5.0),
                                    point(5.0, 5.0),
                                    point(-5.0, 5.0),
                                    point(-5.0, -5.0),
                                ],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_loop.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"floor_name": "G", "anchor": [-5.0, 0.0, -5.0]},
        {"floor_name": "G", "anchor": [5.0, 0.0, 5.0]},
    ]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat, point_records=point_records)

    assert meshes
    assert all(mesh.metadata.get("godot_nav_overlay") != "anchor_envelope_grid" for mesh in meshes)
    for mesh in meshes:
        for face in mesh.faces:
            triangle = [(mesh.vertices[index][0], mesh.vertices[index][2]) for index in face]
            assert not _point_in_triangle_2d((0.0, 0.0), triangle)


def test_route_navigation_meshes_connect_nearby_anchor_points(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"floor_name": "G", "anchor": [0.0, 0.0, 0.0]},
        {"floor_name": "G", "anchor": [0.0, 0.0, 8.0]},
        {"floor_name": "G", "anchor": [8.0, 0.0, 8.0]},
    ]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, 174.0, -36.0, point_records=point_records)

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_route_navigation_meshes_connect_large_room_to_portal_gaps(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"floor_name": "G", "anchor": [0.0, 0.0, 0.0]},
        {"floor_name": "G", "anchor": [50.0, 0.0, 0.0]},
    ]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, 174.0, -36.0, point_records=point_records)

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_route_navigation_meshes_connect_anchor_points_to_cached_routes(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(10.0, 0.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_test.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [{"floor_name": "G", "anchor": [10.0, 0.0, 3.0]}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat, point_records=point_records)

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_route_navigation_meshes_include_manifest_walk_links(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    walk_links = [
        {
            "kind": "walk",
            "from_floor_index": 0,
            "to_floor_index": 0,
            "from_anchor": [0.0, 0.0, 0.0],
            "to_anchor": [40.0, 0.0, 0.0],
        }
    ]

    meshes = _route_navigation_meshes_from_cache(
        route_cache_dir,
        floors,
        174.0,
        -36.0,
        walk_links=walk_links,
    )
    resources = _navigation_mesh_resources(meshes, floors, {0})

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1
    assert _nav_resource_edge_component_count(resources[0]) == 1
    assert max(vertex[0] for mesh in meshes for vertex in mesh.vertices) >= 39.0


def test_route_navigation_meshes_reject_walk_links_crossing_closed_walls(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    walk_links = [
        {
            "kind": "walk",
            "from_floor_index": 0,
            "to_floor_index": 0,
            "from_anchor": [0.0, 0.0, 0.0],
            "to_anchor": [10.0, 0.0, 0.0],
        }
    ]
    wall_blockers = {"G": [LineString([(5.0, -2.0), (5.0, 2.0)])]}

    meshes, stats = _route_navigation_meshes_with_stats_from_cache(
        route_cache_dir,
        floors,
        174.0,
        -36.0,
        walk_links=walk_links,
        wall_blockers_by_floor=wall_blockers,
    )

    assert meshes == []
    assert stats["walk_links"]["segments_rejected"] == 1
    assert stats["wall_filter"]["walk_links_rejected"] == 1


def test_route_navigation_meshes_keep_authoritative_cached_routes_crossing_wall_lines(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(10.0, 0.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_authoritative.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    wall_blockers = {"G": [LineString([(5.0, -2.0), (5.0, 2.0)])]}
    openings = groups._route_wall_openings_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        wall_blockers_by_floor=wall_blockers,
    )

    meshes, stats = _route_navigation_meshes_with_stats_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
    )

    assert openings == {"G": {((5.0, -2.0), (5.0, 2.0))}}
    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1
    assert stats["route_cache"]["segments_used"] == 1
    assert stats["wall_filter"]["route_segments_rejected"] == 0


def test_route_navigation_meshes_use_narrow_corridor_grid(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(10.0, 0.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_narrow.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)
    z_values = [float(vertex[2]) for mesh in meshes for vertex in mesh.vertices]

    assert meshes
    assert max(z_values) - min(z_values) <= 1.5


def test_route_navigation_meshes_merge_targeted_connectors_without_overlapping_edges(tmp_path, monkeypatch):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(20.0, 0.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_base.json").write_text(json.dumps(route), encoding="utf-8")
    monkeypatch.setattr(groups, "ROUTE_NAV_TARGETED_POINT_CONNECTORS", {frozenset(("A", "B"))})
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"floor_name": "G", "external_id": "A", "anchor": [5.0, 0.0, 0.0]},
        {"floor_name": "G", "external_id": "B", "anchor": [15.0, 0.0, 0.0]},
    ]

    meshes = _route_navigation_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        point_records=point_records,
    )
    resources = _navigation_mesh_resources(meshes, floors, {0})

    assert meshes
    assert _nav_resource_edge_component_count(resources[0]) == 1
    assert _nav_resource_max_edge_occupancy(resources[0]) <= 2


def test_route_navigation_targeted_connectors_survive_multi_footprint_clip(tmp_path, monkeypatch):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(3.0, 0.0)],
                            },
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(67.0, 0.0), point(70.0, 0.0)],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_base.json").write_text(json.dumps(route), encoding="utf-8")

    monkeypatch.setattr(groups, "ROUTE_NAV_TARGETED_POINT_CONNECTORS", {frozenset(("A", "B"))})
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"floor_name": "G", "external_id": "A", "anchor": [0.0, 0.0, 0.0]},
        {"floor_name": "G", "external_id": "B", "anchor": [70.0, 0.0, 0.0]},
    ]
    clip_footprints = {
        "G": MultiPolygon(
            [
                box(-5.0, -5.0, 5.0, 5.0),
                box(65.0, -5.0, 75.0, 5.0),
            ]
        )
    }

    meshes = _route_navigation_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        point_records=point_records,
        clip_footprints_by_floor=clip_footprints,
    )
    resources = _navigation_mesh_resources(meshes, floors, {0})

    assert meshes
    assert _nav_resource_edge_component_count(resources[0]) == 1
    assert _nav_resource_max_edge_occupancy(resources[0]) <= 2


def test_route_wall_openings_from_cache_open_crossed_wall_edges(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "G",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(10.0, 0.0)],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_authoritative.json").write_text(json.dumps(route), encoding="utf-8")

    openings = groups._route_wall_openings_from_cache(
        route_cache_dir,
        [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        origin_lon,
        origin_lat,
        wall_blockers_by_floor={"G": [LineString([(5.0, -2.0), (5.0, 2.0)])]},
    )

    assert openings == {"G": {((5.0, -2.0), (5.0, 2.0))}}


def test_route_navigation_grid_omits_cells_touching_closed_walls():
    wall_blocker_index = groups._route_wall_blocker_indexes({"G": [LineString([(1.0, -2.0), (1.0, 2.0)])]})["G"]
    wall = LineString([(1.0, -2.0), (1.0, 2.0)])

    mesh = groups._route_polygon_to_mesh(
        "G",
        Polygon([(0.0, -1.0), (2.0, -1.0), (2.0, 1.0), (0.0, 1.0)]),
        0.0,
        1,
        wall_blocker_index=wall_blocker_index,
    )

    assert mesh.faces
    assert _mesh_coverage_component_count([mesh]) == 2
    assert all(
        not Polygon([(mesh.vertices[index][0], mesh.vertices[index][2]) for index in face]).intersects(wall)
        for face in mesh.faces
    )


def test_route_navigation_meshes_bridge_nearby_route_fragments(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 6.0)],
                            },
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(10.0, 6.0), point(16.0, 6.0)],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_fragmented.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_route_navigation_meshes_bridge_science_cross_building_span(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 8.0)],
                            },
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(100.0, 8.0), point(108.0, 8.0)],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_cross_building.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_route_navigation_meshes_force_targeted_science_connector_through_blocked_gap(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "G",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 1.0)],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_near_portal.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"kind": "elevator", "floor_name": "G", "floor_index": 0, "external_id": "303S-400E4", "anchor": [0.0, 0.0, 0.0]},
        {"kind": "room", "floor_name": "G", "floor_index": 0, "external_id": "305-400C1", "anchor": [10.0, 0.0, 0.0]},
    ]

    meshes = _route_navigation_meshes_from_cache(
        route_cache_dir,
        floors,
        origin_lon,
        origin_lat,
        point_records=point_records,
        wall_blockers_by_floor={"G": [LineString([(5.0, -2.0), (5.0, 2.0)])]},
    )

    assert meshes
    assert _mesh_coverage_component_count(meshes) == 1


def test_science_shaped_route_corridor_exports_single_godot_edge_component(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    point(-102.0, 200.0),
                                    point(-106.0, 271.0),
                                    point(-45.0, 67.0),
                                    point(-3.0, 68.0),
                                    point(8.0, 23.0),
                                ],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_science_shape.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)
    resources = _navigation_mesh_resources(meshes, floors, {0})

    assert _mesh_coverage_component_count(meshes) == 1
    assert _nav_resource_edge_component_count(resources[0]) == 1


def test_route_navigation_grid_merges_straight_corridors_for_stable_godot_queries(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [
                                    point(-125.0, 0.0),
                                    point(125.0, 0.0),
                                ],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_long_corridor.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat)
    resources = _navigation_mesh_resources(meshes, floors, {0})
    resource_polygon_count = len(resources[0]["polygons"])
    source_grid_cells = sum(int(mesh.metadata.get("source_grid_cells", 0)) for mesh in meshes)

    assert _mesh_coverage_component_count(meshes) == 1
    assert _nav_resource_edge_component_count(resources[0]) == 1
    assert source_grid_cells > 500
    assert resource_polygon_count < 50
    assert resource_polygon_count < source_grid_cells / 10
    assert {mesh.metadata.get("route_nav_meshing") for mesh in meshes} == {"constrained_delaunay"}


def test_route_anchor_envelope_exports_edge_connected_godot_nav_grid():
    point_records = [
        {"floor_name": "G", "anchor": [-70.0, 0.0, -20.0]},
        {"floor_name": "G", "anchor": [-65.0, 0.0, 120.0]},
        {"floor_name": "G", "anchor": [-45.0, 0.0, 70.0]},
        {"floor_name": "G", "anchor": [-88.0, 0.0, -18.0]},
        {"floor_name": "G", "anchor": [-20.0, 0.0, 100.0]},
    ]

    envelope = _route_anchor_envelope_mesh("G", point_records, 0.0)
    resources = _navigation_mesh_resources(
        [envelope],
        [{"floor_index": 0, "floor_name": "G", "height": 0.0}],
        {0},
    )

    assert envelope is not None
    assert len(resources[0]["polygons"]) > 1
    assert _nav_resource_edge_component_count(resources[0]) == 1


def test_route_anchor_envelope_subdivides_two_point_floor_for_stable_godot_queries():
    point_records = [
        {"floor_name": "B-2", "anchor": [-69.433, 0.0, 60.907]},
        {"floor_name": "B-2", "anchor": [-63.573, 0.0, 70.312]},
    ]

    envelope = _route_anchor_envelope_mesh("B-2", point_records, -17.1)
    resources = _navigation_mesh_resources(
        [envelope],
        [{"floor_index": 0, "floor_name": "B-2", "height": -17.1}],
        {0},
    )

    assert envelope is not None
    assert len(resources[0]["vertices"]) >= 25
    assert len(resources[0]["polygons"]) >= 32
    assert _nav_resource_edge_component_count(resources[0]) == 1


def test_anchor_grid_route_export_stays_one_godot_edge_component_with_route_fragments(tmp_path):
    route_cache_dir = tmp_path / "door_route_cache"
    route_cache_dir.mkdir()
    origin_lon = 174.0
    origin_lat = -36.0

    def point(x: float, z: float) -> dict:
        metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)
        return {
            "floor_name": "0",
            "lng": origin_lon + x / metres_per_degree_lon,
            "lat": origin_lat + z / 111_320.0,
            "zLevel": 0.0,
        }

    route = {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(0.0, 0.0), point(0.0, 12.0)],
                            },
                            {
                                "abutters": "InsideBuilding",
                                "geometry": [point(80.0, 80.0), point(92.0, 80.0)],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    (route_cache_dir / "route_fragmented.json").write_text(json.dumps(route), encoding="utf-8")
    floors = [{"floor_index": 0, "floor_name": "G", "height": 0.0}]
    point_records = [
        {"floor_name": "G", "anchor": [0.0, 0.0, 0.0]},
        {"floor_name": "G", "anchor": [0.0, 0.0, 92.0]},
        {"floor_name": "G", "anchor": [92.0, 0.0, 0.0]},
        {"floor_name": "G", "anchor": [92.0, 0.0, 92.0]},
    ]

    meshes = _route_navigation_meshes_from_cache(route_cache_dir, floors, origin_lon, origin_lat, point_records=point_records)
    resources = _navigation_mesh_resources(meshes, floors, {0})

    assert _nav_resource_edge_component_count(resources[0]) == 1


def _mesh_coverage_component_count(meshes) -> int:
    triangles = []
    for mesh in meshes:
        for face in mesh.faces:
            triangles.append(Polygon([(mesh.vertices[index][0], mesh.vertices[index][2]) for index in face]))
    coverage = unary_union(triangles)
    if isinstance(coverage, MultiPolygon):
        return len(coverage.geoms)
    return 0 if coverage.is_empty else 1


def _mesh_surface_area(meshes) -> float:
    faces = []
    for mesh in meshes:
        for face in mesh.faces:
            faces.append(Polygon([(mesh.vertices[index][0], mesh.vertices[index][2]) for index in face]))
    return float(unary_union(faces).area)


def _nav_resource_edge_component_count(resource: dict) -> int:
    polygons = resource.get("polygons", [])
    if not polygons:
        return 0
    polygon_indexes_by_edge: dict[tuple[int, int], list[int]] = {}
    for polygon_index, polygon in enumerate(polygons):
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            edge = tuple(sorted((int(start), int(end))))
            polygon_indexes_by_edge.setdefault(edge, []).append(polygon_index)

    graph = {index: set() for index in range(len(polygons))}
    for polygon_indexes in polygon_indexes_by_edge.values():
        if len(polygon_indexes) < 2:
            continue
        for start in polygon_indexes:
            for end in polygon_indexes:
                if start != end:
                    graph[start].add(end)

    remaining = set(graph)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            for nxt in graph[current]:
                if nxt in remaining:
                    remaining.remove(nxt)
                    stack.append(nxt)
    return components


def _nav_resource_max_edge_occupancy(resource: dict) -> int:
    edge_occupancy: dict[tuple[int, int], int] = {}
    for polygon in resource.get("polygons", []):
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            edge = tuple(sorted((int(start), int(end))))
            edge_occupancy[edge] = edge_occupancy.get(edge, 0) + 1
    return max(edge_occupancy.values(), default=0)


def _point_in_triangle_2d(point, triangle) -> bool:
    def sign(a, b, c) -> float:
        return (a[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (a[1] - c[1])

    d1 = sign(point, triangle[0], triangle[1])
    d2 = sign(point, triangle[1], triangle[2])
    d3 = sign(point, triangle[2], triangle[0])
    has_negative = d1 < 0 or d2 < 0 or d3 < 0
    has_positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_negative and has_positive)


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


class _FakeMapsIndoorsRouteClient:
    def __init__(self, **_kwargs):
        pass

    def route(self, origin, destination):
        return {
            "status": "OK",
            "routes": [
                {
                    "legs": [
                        {
                            "steps": [
                                {
                                    "geometry": [
                                        {
                                            "lng": 174.7678329,
                                            "lat": -36.8527891,
                                            "zLevel": origin.zlevel,
                                            "floor_name": origin.floor_name,
                                        },
                                        {
                                            "lng": 174.7678329,
                                            "lat": -36.8527891,
                                            "zLevel": destination.zlevel,
                                            "floor_name": destination.floor_name,
                                        },
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ],
        }


def _unknown_elevator(
    external_id,
    source_id,
    floor_name,
    floor_index,
    source_floor,
    anchor_lonlat,
    anchor_local,
):
    lon, lat = anchor_lonlat
    return PortalRecord(
        source_id=source_id,
        external_id=external_id,
        display_name="Elevator",
        building_admin_id="303",
        floor_name=floor_name,
        floor_index=floor_index,
        kind="elevator",
        group_id="",
        anchor_lonlat=anchor_lonlat,
        anchor_local=anchor_local,
        polygon_lonlat=[
            [lon - 0.00002, lat - 0.00002],
            [lon + 0.00002, lat - 0.00002],
            [lon + 0.00002, lat + 0.00002],
            [lon - 0.00002, lat + 0.00002],
            [lon - 0.00002, lat - 0.00002],
        ],
        polygon_local=[],
        source_properties={"floor": str(source_floor)},
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


def test_floor_sort_key_orders_mezzanine_and_subbasement_labels():
    # "1M" (digit-first mezzanine) and "SB" (sub-basement) used to fall
    # through to the 10_000 sentinel, which _floor_heights turned into a
    # 42000-unit floor height (a kilometre-tall campus prop).
    assert groups._floor_sort_key("1M")[0] == 1.5
    assert groups._floor_sort_key("M8")[0] == 8.5
    assert groups._floor_sort_key("SB")[0] == -1.5
    assert groups._floor_sort_key("B-2")[0] == -2.0
    assert groups._floor_sort_key("G")[0] == 0.0
    assert groups._floor_sort_key("???")[0] == 10_000.0


def test_floor_heights_place_mezzanine_between_floors_and_sb_below_b1():
    floors = [
        FloorRecord(floor_name="B-1", floor_index=0),
        FloorRecord(floor_name="SB", floor_index=1),
        FloorRecord(floor_name="1", floor_index=2),
        FloorRecord(floor_name="1M", floor_index=3),
        FloorRecord(floor_name="2", floor_index=4),
    ]
    heights = groups._floor_heights(floors, default_spacing=4.2, basement_spacing=3.0)
    assert heights == {"B-1": -3.0, "SB": -4.5, "1": 4.2, "1M": 6.3, "2": 8.4}

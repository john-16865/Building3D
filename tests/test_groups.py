import json
from math import cos, pi

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
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
    assert manifest["portals"][0]["node_name"] == "302 100E1_Elevator_SetE1"
    assert manifest["external_doors"][0]["node_name"] == "MainDoor"
    assert manifest["external_doors"][0]["kind"] == "door"
    assert manifest["external_doors"][0]["anchor"] == [1.0, 0.0, 2.0]
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
    assert "302 100E1_Elevator_SetE1" in scene_text
    assert '[node name="MainDoor" type="Node3D" parent="Floors/Floor0/Rooms"]' in scene_text
    assert json.loads((export_dir / "external_doors.json").read_text(encoding="utf-8"))[0]["node_name"] == "MainDoor"


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
    assert "302 100E1_Elevator_SetE1" not in scene_text


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


def test_route_debug_centerlines_buffer_turns_as_one_continuous_mesh(tmp_path):
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

    assert len(meshes) == 1
    assert meshes[0].material == "route_centerline"
    assert meshes[0].metadata["debug_overlay"] == "route_centerline"
    assert _mesh_surface_area(meshes) > 1.4
    assert _mesh_surface_area(meshes) < 2.4


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

    assert _mesh_coverage_component_count(meshes) == 1
    assert _nav_resource_edge_component_count(resources[0]) == 1
    assert resource_polygon_count < 7000


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

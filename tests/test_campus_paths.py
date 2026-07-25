import math

from building3d.campus_paths import (
    CampusPathsConfig,
    _bridge_components,
    _connected_components,
    add_entrance_spurs,
    build_network,
    extract_outdoor_segments,
    graph_to_meshes,
    load_campus_paths_config,
    placement_to_lonlat,
    project_to_campus,
    sample_campus_routes,
)


def _route(points, highway="residential", abutters="OutsideOnVenue"):
    return {
        "status": "OK",
        "routes": [{"legs": [{"steps": [{
            "abutters": abutters,
            "highway": highway,
            "geometry": [{"lng": p[0], "lat": p[1], "zLevel": 0.0, "floor_name": "0"} for p in points],
        }]}]}],
    }


def test_projection_matches_reference_anchor():
    cfg = CampusPathsConfig()
    ref = cfg.reference
    x, z = project_to_campus(ref.lon, ref.lat, ref)
    assert math.isclose(x, ref.origin[0] + ref.scale * ref.local_anchor[0], abs_tol=1e-6)
    assert math.isclose(z, ref.origin[2] - ref.scale * ref.local_anchor[2], abs_tol=1e-6)


def test_extract_outdoor_segments_drops_inside_steps():
    routes = [
        _route([(174.767, -36.852), (174.7675, -36.8525)], abutters="OutsideOnVenue"),
        _route([(174.768, -36.853), (174.7685, -36.8535)], abutters="InsideBuilding"),
    ]
    segments, highways = extract_outdoor_segments(routes)
    # Only the outdoor route's single segment survives.
    assert len(segments) == 1
    assert highways == ["residential"]


def test_build_network_is_deterministic_and_merges_shared_nodes():
    cfg = CampusPathsConfig()
    # Two routes sharing a midpoint -> the midpoint must merge to one node.
    routes = [
        _route([(174.7670, -36.8520), (174.7675, -36.8525)]),
        _route([(174.7675, -36.8525), (174.7680, -36.8530)]),
    ]
    segments, highways = extract_outdoor_segments(routes)
    graph_a = build_network(segments, highways, cfg)
    graph_b = build_network(segments, highways, cfg)
    assert graph_a["edges"] == graph_b["edges"]
    assert graph_a["nodes"] == graph_b["nodes"]
    # 3 distinct points -> 3 nodes, 2 edges, shared middle node.
    assert len(graph_a["nodes"]) == 3
    assert len(graph_a["edges"]) == 2


def test_build_network_prunes_short_stubs():
    cfg = CampusPathsConfig()
    # A trunk plus a tiny (~1 m) dangling spike off the end -> spike pruned.
    trunk = _route([(174.7670, -36.8520), (174.7680, -36.8530), (174.7690, -36.8540)])
    spike = _route([(174.7690, -36.8540), (174.76901, -36.85401)])
    segments, highways = extract_outdoor_segments([trunk, spike])
    graph = build_network(segments, highways, cfg)
    # The trunk keeps its 2 edges; the sub-min_stub spike is dropped.
    assert len(graph["edges"]) == 2


def test_entrance_spur_connects_within_threshold_and_skips_beyond():
    cfg = CampusPathsConfig()
    routes = [_route([(174.7670, -36.8520), (174.7680, -36.8530)])]
    segments, highways = extract_outdoor_segments(routes)
    graph = build_network(segments, highways, cfg)
    near_node = next(iter(graph["nodes"].values()))
    entrances = [
        {"building_id": "near", "x": near_node[0] + 1.0, "z": near_node[1] + 1.0},
        {"building_id": "far", "x": near_node[0] + 9000.0, "z": near_node[1]},
    ]
    stats = add_entrance_spurs(graph, entrances, cfg)
    by_id = {s["building_id"]: s for s in stats}
    assert by_id["near"]["connected"] is True
    assert by_id["far"]["connected"] is False
    assert any(e["highway"] == "spur" for e in graph["edges"])


def test_graph_to_meshes_emits_quads_per_class_at_road_y():
    cfg = CampusPathsConfig()
    routes = [
        _route([(174.7670, -36.8520), (174.7680, -36.8530)], highway="residential"),
        _route([(174.7680, -36.8530), (174.7690, -36.8540)], highway="footway"),
    ]
    segments, highways = extract_outdoor_segments(routes)
    graph = build_network(segments, highways, cfg)
    meshes = graph_to_meshes(graph, cfg)
    materials = {m.material for m in meshes}
    assert "campus_road" in materials
    assert "campus_footpath" in materials
    for mesh in meshes:
        assert all(len(face) == 4 for face in mesh.faces)  # quad strips
        assert all(abs(v[1] - cfg.road_y) < 1e-6 for v in mesh.vertices)  # flat at road_y


def test_build_network_bridges_disconnected_islands():
    cfg = CampusPathsConfig()
    # Two separate route clusters that do not share any snapped node.
    main = _route([(174.7670, -36.8520), (174.7680, -36.8530), (174.7690, -36.8540)])
    island = _route([(174.7800, -36.8600), (174.7810, -36.8610), (174.7820, -36.8620)])
    segments, highways = extract_outdoor_segments([main, island])
    graph = build_network(segments, highways, cfg)
    # After bridging, the whole graph is a single connected component.
    assert len(_connected_components(graph)) == 1


def test_bridge_components_is_noop_when_already_connected():
    graph = {
        "nodes": {0: [0.0, 0.0], 1: [1.0, 0.0], 2: [2.0, 0.0]},
        "edges": [{"a": 0, "b": 1, "highway": "residential"}, {"a": 1, "b": 2, "highway": "residential"}],
    }
    before = [dict(e) for e in graph["edges"]]
    _bridge_components(graph)
    assert graph["edges"] == before


def test_entrance_point_flips_anchor_z_for_building_basis():
    from building3d.campus_paths import _entrance_placement_point

    placement = {
        "scale": 0.9,
        "transform": {"origin": [100.0, 0.0, 200.0]},
        "entrance": {"anchor": [10.0, 0.0, 30.0]},
    }
    x, z = _entrance_placement_point(placement)
    # X keeps the anchor sign, Z is negated (building node has a -Z flip basis).
    assert math.isclose(x, 100.0 + 0.9 * 10.0)
    assert math.isclose(z, 200.0 - 0.9 * 30.0)


def _multistep_route(steps):
    # steps: list of (abutters, highway, [(lng,lat), ...])
    return {
        "status": "OK",
        "routes": [{"legs": [{"steps": [
            {
                "abutters": ab,
                "highway": hw,
                "geometry": [{"lng": p[0], "lat": p[1], "zLevel": 0.0, "floor_name": "0"} for p in pts],
            }
            for ab, hw, pts in steps
        ]}]}],
    }


def test_extract_bridges_indoor_gap_within_a_leg():
    # One leg: outdoor -> indoor -> outdoor. The indoor stretch must not just be
    # dropped (that shatters the route); it becomes one connector spanning it.
    route = _multistep_route([
        ("OutsideOnVenue", "footway", [(0.0, 0.0), (0.001, 0.0)]),
        ("InsideBuilding", "footway", [(0.001, 0.0), (0.002, 0.0)]),
        ("OutsideOnVenue", "footway", [(0.002, 0.0), (0.003, 0.0)]),
    ])
    segments, highways = extract_outdoor_segments([route])
    assert highways.count("footway") == 2
    assert highways.count("connector") == 1
    conn = segments[highways.index("connector")]
    assert conn == ((0.001, 0.0), (0.002, 0.0))  # spans exactly the indoor step


def test_extract_drops_trailing_indoor_stretch_without_connector():
    # A route that ENDS inside the destination building must not leave a
    # dangling connector into it: the entrance spur owns the road->door link.
    route = _multistep_route([
        ("OutsideOnVenue", "footway", [(0.0, 0.0), (0.001, 0.0)]),
        ("InsideBuilding", "footway", [(0.001, 0.0), (0.002, 0.0)]),
    ])
    segments, highways = extract_outdoor_segments([route])
    assert highways.count("connector") == 0
    assert highways.count("footway") == 1


def test_placement_to_lonlat_inverts_projection():
    cfg = CampusPathsConfig()
    ref = cfg.reference
    for x, z in [(5.2, 863.4), (-178.77, 753.52), (0.0, 0.0), (83.8, 757.6)]:
        lon, lat = placement_to_lonlat(x, z, ref)
        bx, bz = project_to_campus(lon, lat, ref)
        assert math.isclose(bx, x, abs_tol=1e-3)
        assert math.isclose(bz, z, abs_tol=1e-3)


def test_sample_routes_covers_every_building_pair():
    cfg = CampusPathsConfig()
    seen: list[tuple] = []

    class _FakeClient:
        def route(self, origin, destination):
            seen.append(tuple(sorted((
                (round(origin.lon, 6), round(origin.lat, 6)),
                (round(destination.lon, 6), round(destination.lat, 6)),
            ))))
            return {"status": "OK", "routes": []}

    bpts = [(174.0, -36.0), (174.001, -36.0), (174.0, -36.001), (174.001, -36.001)]
    sample_campus_routes(cfg, targets=[], client=_FakeClient(), entry_points=[], building_points=bpts)
    sampled = set(seen)
    for i in range(len(bpts)):
        for j in range(i + 1, len(bpts)):
            assert tuple(sorted((bpts[i], bpts[j]))) in sampled  # all C(n,2) direct routes


def test_load_config_round_trip(tmp_path):
    yaml_text = """
graph_id: TEST_Graph
hub_count: 3
road_y: 0.5
widths_m:
  residential: 7.0
extra_entrances:
  - building_id: kate
    x: -74.74
    z: 750.90
"""
    path = tmp_path / "campus_paths.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    cfg = load_campus_paths_config(path)
    assert cfg.graph_id == "TEST_Graph"
    assert cfg.hub_count == 3
    assert cfg.road_y == 0.5
    assert cfg.widths_m["residential"] == 7.0
    assert cfg.extra_entrances[0]["building_id"] == "kate"


def test_extra_edge_segments_from_lonlat_and_placement_points():
    from building3d.campus_paths import extra_edge_segments

    cfg_lonlat = CampusPathsConfig(extra_edges=[{
        "highway": "underpass",
        "points_lonlat": [[174.7693511, -36.8525145], [174.7695195, -36.8529999]],
    }])
    segments, highways = extra_edge_segments(cfg_lonlat)
    assert highways == ["underpass"]
    assert segments[0][0] == (174.7693511, -36.8525145)
    assert segments[0][1] == (174.7695195, -36.8529999)

    # Placement-space points convert through the same reference the frame uses:
    # project(placement_to_lonlat(p)) must land back on p.
    ref = CampusPathsConfig().reference
    placement_start = [-104.3, 815.8]
    cfg_placement = CampusPathsConfig(extra_edges=[{
        "points": [placement_start, [-97.6, 839.9]],
    }])
    segments_p, highways_p = extra_edge_segments(cfg_placement)
    assert highways_p == ["underpass"]  # class defaults to underpass
    x, z = project_to_campus(segments_p[0][0][0], segments_p[0][0][1], ref)
    assert math.isclose(x, placement_start[0], abs_tol=1e-6)
    assert math.isclose(z, placement_start[1], abs_tol=1e-6)


def test_extra_edges_join_network_bridged_with_class_and_material():
    from building3d.campus_paths import extra_edge_segments

    cfg = CampusPathsConfig(extra_edges=[{
        "highway": "underpass",
        "points_lonlat": [[174.7693511, -36.8525145], [174.7695195, -36.8529999]],
    }])
    # Sampled network far from the tunnel -> the tunnel starts as an island.
    routes = [_route([(174.7670, -36.8520), (174.7680, -36.8530)])]
    segments, highways = extract_outdoor_segments(routes)
    extra_segments, extra_highways = extra_edge_segments(cfg)
    graph = build_network(segments + extra_segments, highways + extra_highways, cfg)
    # Bridged into one component; the hand edge keeps its class (56 m is far
    # above the stub-prune floor, so degree-1 endpoints do not delete it).
    assert len(_connected_components(graph)) == 1
    assert any(e["highway"] == "underpass" for e in graph["edges"])
    materials = {m.material for m in graph_to_meshes(graph, cfg)}
    assert "campus_underpass" in materials


def test_load_config_parses_extra_edges(tmp_path):
    yaml_text = """
extra_edges:
  - highway: underpass
    points_lonlat: [[174.7693511, -36.8525145], [174.7695195, -36.8529999]]
"""
    path = tmp_path / "campus_paths_extra.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    cfg = load_campus_paths_config(path)
    assert cfg.extra_edges == [{
        "highway": "underpass",
        "points_lonlat": [[174.7693511, -36.8525145], [174.7695195, -36.8529999]],
    }]

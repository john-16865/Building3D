from building3d.geometry import MeshData
from building3d.portal_topology import build_portal_topology


def test_portal_topology_uses_exact_terminals_and_component_walk_edges():
    manifest = _science_manifest()
    route_meshes = [
        _route_polygon("8", [(-1.0, -1.0), (10.0, -1.0), (10.0, 1.0), (-1.0, 1.0)], height=33.6),
        _route_polygon("8", [(90.0, 90.0), (110.0, 90.0), (110.0, 110.0), (90.0, 110.0)], height=33.6),
        _route_polygon("M8", [(-1.0, -1.0), (10.0, -1.0), (10.0, 1.0), (-1.0, 1.0)], height=35.7),
    ]

    topology = build_portal_topology(manifest, route_meshes, generated_at="2026-06-04T00:00:00Z")

    terminals = {terminal["id"]: terminal for terminal in topology["terminals"]}
    lift_303_f10 = "science:F10:303:lift:303MI_303_ELEV_001:303_802"
    stair_303_f10 = "science:F10:303:stair:303S3:303_800S3"
    lift_303_f11 = "science:F11:303:lift:303MI_303_ELEV_001:303_8U02"
    lift_302_f10 = "science:F10:302:lift:302MI_302_ELEV_001:302_802"

    assert terminals[lift_303_f10] == {
        "id": lift_303_f10,
        "building_id": "science",
        "floor_index": 10,
        "floor_number": 8,
        "section": "303",
        "portal_name": "303 802_Elevator_Set303MI_303_ELEV_001",
        "portal_type": "lift",
        "group_id": "303MI_303_ELEV_001",
        "position_local": [5.0, 33.6, 0.0],
    }

    same_floor_edges = {
        (edge["from"], edge["to"]): edge
        for edge in topology["same_floor_transfer_edges"]
    }
    assert topology["transfer_edges"] == topology["same_floor_transfer_edges"]
    assert (stair_303_f10, lift_303_f10) in same_floor_edges
    assert (lift_303_f10, stair_303_f10) in same_floor_edges
    assert same_floor_edges[(stair_303_f10, lift_303_f10)]["reason"] == "same_navmesh_component"
    assert same_floor_edges[(stair_303_f10, lift_303_f10)]["cost"] == 5.0
    assert (lift_302_f10, lift_303_f10) not in same_floor_edges
    assert (lift_303_f10, lift_302_f10) not in same_floor_edges

    vertical_edges = {
        (edge["from"], edge["to"]): edge
        for edge in topology["vertical_edges"]
    }
    assert vertical_edges[(lift_303_f10, lift_303_f11)]["mode"] == "lift"
    assert vertical_edges[(lift_303_f10, lift_303_f11)]["group_id"] == "303MI_303_ELEV_001"
    assert vertical_edges[(lift_303_f10, lift_303_f11)]["cost"] == 20.0
    assert (lift_303_f11, lift_303_f10) in vertical_edges
    assert all("302" not in edge["to"] for edge in vertical_edges.values() if edge["from"] == lift_303_f10)

    assert topology["validation"]["building_id"] == "science"
    assert topology["validation"]["terminal_count"] == 4
    assert topology["validation"]["same_floor_transfer_edge_count"] == 2
    assert topology["validation"]["vertical_edge_count"] == 2
    assert topology["validation"]["edge_count"] == 4
    assert len(topology["validation"]["source_hash"]) == 64


def test_portal_topology_does_not_emit_same_floor_edges_without_component_proof():
    manifest = _science_manifest()

    topology = build_portal_topology(manifest, [], generated_at="2026-06-04T00:00:00Z")

    assert topology["same_floor_transfer_edges"] == []
    assert topology["transfer_edges"] == []
    assert topology["validation"]["same_floor_transfer_edge_count"] == 0


def test_portal_topology_skips_ungrouped_vertical_markers_godot_does_not_scan():
    manifest = _science_manifest()
    manifest["portals"].append(
        {
            "external_id": "303-803A",
            "source_id": "feature-303-803A",
            "node_name": "303 803A_Elevator",
            "floor_index": 10,
            "floor_name": "8",
            "kind": "elevator",
            "group_id": "",
            "anchor": [6.0, 33.6, 0.0],
            "source_building_admin_id": "303",
        }
    )

    topology = build_portal_topology(manifest, [], generated_at="2026-06-04T00:00:00Z")

    assert all(terminal["portal_name"] != "303 803A_Elevator" for terminal in topology["terminals"])
    assert topology["validation"]["terminal_count"] == 4


def test_portal_topology_can_reach_science_floor_11_mi_lift_from_floor_2():
    manifest = _science_manifest_with_floor_2_route_to_mi_lift()
    route_meshes = [
        _route_polygon("7", [(-54.0, 54.0), (-48.0, 54.0), (-48.0, 63.0), (-54.0, 63.0)], height=29.4),
        _route_polygon("8", [(-52.0, 56.7), (-48.0, 56.7), (-48.0, 60.0), (-52.0, 60.0)], height=33.6),
        _route_polygon("8", [(-53.5, 58.5), (-52.5, 58.5), (-52.5, 61.0), (-53.5, 61.0)], height=33.6),
    ]

    topology = build_portal_topology(manifest, route_meshes, generated_at="2026-06-04T00:00:00Z")

    target = "science:F11:303:lift:303MI_303_ELEV_001:303_8U02"
    assert _is_reachable(topology, start_floor_index=2, target_terminal_id=target)

    bridge_edges = {
        (edge["from"], edge["to"]): edge
        for edge in topology["transfer_edges"]
        if edge.get("reason") == "adjacent_route_navmesh_components"
    }
    assert (
        "science:F10:303:stair:303S3:303_800S3",
        "science:F10:303:lift:303MI_303_ELEV_001:303_802",
    ) in bridge_edges


def _science_manifest():
    return {
        "schema_version": 2,
        "building": {"id": "science"},
        "floors": [
            {"floor_index": 10, "floor_name": "8", "height": 33.6},
            {"floor_index": 11, "floor_name": "M8", "height": 35.7},
        ],
        "portals": [
            {
                "external_id": "303-800S3",
                "source_id": "feature-303-800S3",
                "node_name": "303 800S3_Stairs_Set303S3",
                "floor_index": 10,
                "floor_name": "8",
                "kind": "stair",
                "group_id": "S3",
                "anchor": [0.0, 33.6, 0.0],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-802",
                "source_id": "feature-303-802",
                "node_name": "303 802_Elevator_Set303MI_303_ELEV_001",
                "floor_index": 10,
                "floor_name": "8",
                "kind": "elevator",
                "group_id": "MI_303_ELEV_001",
                "anchor": [5.0, 33.6, 0.0],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-8U02",
                "source_id": "feature-303-8U02",
                "node_name": "303 8U02_Elevator_Set303MI_303_ELEV_001",
                "floor_index": 11,
                "floor_name": "M8",
                "kind": "elevator",
                "group_id": "MI_303_ELEV_001",
                "anchor": [5.0, 35.7, 0.0],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "302-802",
                "source_id": "feature-302-802",
                "node_name": "302 802_Elevator_Set302MI_302_ELEV_001",
                "floor_index": 10,
                "floor_name": "8",
                "kind": "elevator",
                "group_id": "MI_302_ELEV_001",
                "anchor": [100.0, 33.6, 100.0],
                "source_building_admin_id": "302",
            },
        ],
        "external_doors": [],
        "nav": {
            "links": [
                {
                    "kind": "elevator",
                    "group_id": "MI_303_ELEV_001",
                    "source_building_admin_id": "303",
                    "from_source_id": "feature-303-802",
                    "to_source_id": "feature-303-8U02",
                    "from_external_id": "303-802",
                    "to_external_id": "303-8U02",
                    "from_node_name": "303 802_Elevator_Set303MI_303_ELEV_001",
                    "to_node_name": "303 8U02_Elevator_Set303MI_303_ELEV_001",
                    "from_floor_index": 10,
                    "to_floor_index": 11,
                    "from_anchor": [5.0, 33.6, 0.0],
                    "to_anchor": [5.0, 35.7, 0.0],
                    "bidirectional": True,
                }
            ]
        },
    }


def _science_manifest_with_floor_2_route_to_mi_lift():
    return {
        "schema_version": 2,
        "building": {"id": "science"},
        "floors": [
            {"floor_index": 2, "floor_name": "G", "height": 0.0},
            {"floor_index": 9, "floor_name": "7", "height": 29.4},
            {"floor_index": 10, "floor_name": "8", "height": 33.6},
            {"floor_index": 11, "floor_name": "M8", "height": 35.7},
        ],
        "portals": [
            {
                "external_id": "303-G00S2",
                "source_id": "feature-303-G00S2",
                "node_name": "303 G00S2_Stairs_Set303S2",
                "floor_index": 2,
                "floor_name": "G",
                "kind": "stair",
                "group_id": "S2",
                "anchor": [-50.0, 0.0, 58.0],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-700S2",
                "source_id": "feature-303-700S2",
                "node_name": "303 700S2_Stairs_Set303S2",
                "floor_index": 9,
                "floor_name": "7",
                "kind": "stair",
                "group_id": "S2",
                "anchor": [-50.0, 29.4, 58.0],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-700S3",
                "source_id": "feature-303-700S3",
                "node_name": "303 700S3_Stairs_Set303S3",
                "floor_index": 9,
                "floor_name": "7",
                "kind": "stair",
                "group_id": "S3",
                "anchor": [-52.8, 29.4, 60.3],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-800S3",
                "source_id": "feature-303-800S3",
                "node_name": "303 800S3_Stairs_Set303S3",
                "floor_index": 10,
                "floor_name": "8",
                "kind": "stair",
                "group_id": "S3",
                "anchor": [-53.0, 33.6, 59.9],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-802",
                "source_id": "feature-303-802",
                "node_name": "303 802_Elevator_Set303MI_303_ELEV_001",
                "floor_index": 10,
                "floor_name": "8",
                "kind": "elevator",
                "group_id": "MI_303_ELEV_001",
                "anchor": [-49.9, 33.6, 57.9],
                "source_building_admin_id": "303",
            },
            {
                "external_id": "303-8U02",
                "source_id": "feature-303-8U02",
                "node_name": "303 8U02_Elevator_Set303MI_303_ELEV_001",
                "floor_index": 11,
                "floor_name": "M8",
                "kind": "elevator",
                "group_id": "MI_303_ELEV_001",
                "anchor": [-49.9, 35.7, 58.5],
                "source_building_admin_id": "303",
            },
        ],
        "external_doors": [],
        "nav": {
            "links": [
                _vertical_link(
                    "stair",
                    "S2",
                    "303",
                    "feature-303-G00S2",
                    "feature-303-700S2",
                    "303-G00S2",
                    "303-700S2",
                    "303 G00S2_Stairs_Set303S2",
                    "303 700S2_Stairs_Set303S2",
                    2,
                    9,
                ),
                _vertical_link(
                    "stair",
                    "S3",
                    "303",
                    "feature-303-700S3",
                    "feature-303-800S3",
                    "303-700S3",
                    "303-800S3",
                    "303 700S3_Stairs_Set303S3",
                    "303 800S3_Stairs_Set303S3",
                    9,
                    10,
                ),
                _vertical_link(
                    "elevator",
                    "MI_303_ELEV_001",
                    "303",
                    "feature-303-802",
                    "feature-303-8U02",
                    "303-802",
                    "303-8U02",
                    "303 802_Elevator_Set303MI_303_ELEV_001",
                    "303 8U02_Elevator_Set303MI_303_ELEV_001",
                    10,
                    11,
                ),
            ]
        },
    }


def _vertical_link(
    kind,
    group_id,
    section,
    from_source_id,
    to_source_id,
    from_external_id,
    to_external_id,
    from_node_name,
    to_node_name,
    from_floor_index,
    to_floor_index,
):
    return {
        "kind": kind,
        "group_id": group_id,
        "source_building_admin_id": section,
        "from_source_id": from_source_id,
        "to_source_id": to_source_id,
        "from_external_id": from_external_id,
        "to_external_id": to_external_id,
        "from_node_name": from_node_name,
        "to_node_name": to_node_name,
        "from_floor_index": from_floor_index,
        "to_floor_index": to_floor_index,
        "from_anchor": [0.0, 0.0, 0.0],
        "to_anchor": [0.0, 0.0, 0.0],
        "bidirectional": True,
    }


def _is_reachable(topology, *, start_floor_index: int, target_terminal_id: str) -> bool:
    adjacency = {}
    for terminal in topology["terminals"]:
        adjacency.setdefault(terminal["id"], [])
    for key in ("transfer_edges", "vertical_edges"):
        for edge in topology.get(key, []):
            adjacency.setdefault(edge["from"], []).append(edge["to"])
    queue = [
        terminal["id"]
        for terminal in topology["terminals"]
        if terminal["floor_index"] == start_floor_index
    ]
    seen = set(queue)
    while queue:
        current = queue.pop(0)
        if current == target_terminal_id:
            return True
        for next_id in adjacency.get(current, []):
            if next_id in seen:
                continue
            seen.add(next_id)
            queue.append(next_id)
    return False


def _route_polygon(floor_name: str, points: list[tuple[float, float]], *, height: float) -> MeshData:
    return MeshData(
        name=f"floor__{floor_name}__route_nav_001",
        vertices=[[x, height, z] for x, z in points],
        faces=[list(range(len(points)))],
        material="floor",
    )

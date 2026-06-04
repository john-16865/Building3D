from building3d.manifest import build_manifest
from building3d.normalize import FloorRecord, NormalizedDataset, PortalRecord
from building3d.vertical_links import apply_route_derived_vertical_links


def test_route_derived_vertical_links_accept_direct_mapsindoors_transition():
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
    manifest = build_manifest(dataset, source_urls=[])
    route_client = _FakeRouteClient(
        {
            ("303-802", "303-8U02"): _route(
                [
                    _point(174.7678329, -36.8527891, 80, "8"),
                    _point(174.7678329, -36.8527891, 85, "M8"),
                ]
            )
        }
    )

    stats = apply_route_derived_vertical_links(dataset, manifest, route_client=route_client)

    route_links = [
        link
        for link in manifest["nav"]["links"]
        if link.get("source") == "mapsindoors_route_graph"
    ]
    assert stats["accepted"] == 1
    assert [(link["from_external_id"], link["to_external_id"]) for link in route_links] == [
        ("303-802", "303-8U02")
    ]
    assert route_links[0]["group_id"] == "MI_303_ELEV_001"
    assert route_links[0]["confidence"] == "high"
    assert manifest["portals"][0]["group_id"] == "MI_303_ELEV_001"
    assert manifest["portals"][1]["group_id"] == "MI_303_ELEV_001"
    assert manifest["portals"][0]["node_name"] == "303 802_Elevator_SetMI_303_ELEV_001"
    assert manifest["portals"][1]["node_name"] == "303 8U02_Elevator_SetMI_303_ELEV_001"
    assert dataset.portals[0].group_id == "MI_303_ELEV_001"
    assert dataset.portals[1].group_id == "MI_303_ELEV_001"


def test_route_derived_vertical_links_reject_transition_away_from_origin_portal():
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
                "303-803A",
                "feature-803A",
                "8",
                10,
                80,
                [174.7678531, -36.8527142],
                [7.0, 33.6, 7.0],
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
    manifest = build_manifest(dataset, source_urls=[])
    route_client = _FakeRouteClient(
        {
            ("303-803A", "303-8U02"): _route(
                [
                    _point(174.7678028, -36.8527648, 80, "8"),
                    _point(174.7678028, -36.8527648, 85, "M8"),
                ]
            )
        }
    )

    stats = apply_route_derived_vertical_links(dataset, manifest, route_client=route_client)

    assert stats["accepted"] == 0
    assert [
        link
        for link in manifest["nav"]["links"]
        if link.get("source") == "mapsindoors_route_graph"
    ] == []
    assert manifest["portals"][0]["group_id"] == ""
    assert manifest["portals"][1]["group_id"] == ""
    assert dataset.portals[0].group_id == ""
    assert dataset.portals[1].group_id == ""


class _FakeRouteClient:
    def __init__(self, routes):
        self.routes = routes

    def route(self, origin, destination):
        return self.routes.get((origin.external_id, destination.external_id), {"status": "NOT_FOUND"})


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


def _route(points):
    return {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "steps": [
                            {
                                "geometry": points,
                                "start_location": points[0],
                                "end_location": points[-1],
                            }
                        ]
                    }
                ]
            }
        ],
    }


def _point(lon, lat, zlevel, floor_name):
    return {"lng": lon, "lat": lat, "zLevel": zlevel, "floor_name": floor_name}

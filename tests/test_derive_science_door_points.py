from tools.derive_science_door_points import (
    _derive_one_room_door,
    _outside_to_inside_points,
)
from building3d.projection import LocalProjector


class FakeRouteClient:
    def route(self, origin, destination):
        return {
            "status": "OK",
            "routes": [
                {
                    "legs": [
                        {
                            "end_location": {
                                "lng": 0.9,
                                "lat": 0.5,
                                "zLevel": 40.0,
                                "floor_name": "4",
                            },
                            "steps": [
                                {
                                    "geometry": [
                                        {"lng": 0.0, "lat": 0.5, "zLevel": 40.0, "floor_name": "4"},
                                        {"lng": 0.6, "lat": 0.5, "zLevel": 40.0, "floor_name": "4"},
                                        {"lng": 0.9, "lat": 0.5, "zLevel": 40.0, "floor_name": "4"},
                                    ]
                                }
                            ],
                        }
                    ]
                }
            ],
        }


def test_science_corridor_305_400c1_uses_route_endpoint_for_303s_400e4_override():
    room = {
        "external_id": "305-400C1",
        "source_id": "room-source",
        "display_name": "corridor",
        "category": "other",
        "building_admin_id": "305",
        "floor_name": "4",
        "floor_index": 0,
        "source_properties": {"floor": 40.0},
        "anchor_lonlat": [0.9, 0.5],
        "anchor_local": [90.0, 16.8, 50.0],
        "polygon_lonlat": [
            [0.6, 0.4],
            [1.0, 0.4],
            [1.0, 0.6],
            [0.6, 0.6],
            [0.6, 0.4],
        ],
    }
    origins = [
        {
            "external_id": "303S-400E4",
            "source_id": "portal-source",
            "display_name": "Elevator",
            "origin_kind": "portal:elevator",
            "floor_name": "4",
            "floor_index": 0,
            "source_properties": {"floor": 40.0},
            "anchor_lonlat": [0.0, 0.5],
            "anchor_local": [0.0, 16.8, 50.0],
        },
        {
            "external_id": "303-400E2",
            "source_id": "other-portal-source",
            "display_name": "Other Elevator",
            "origin_kind": "portal:elevator",
            "floor_name": "4",
            "floor_index": 0,
            "source_properties": {"floor": 40.0},
            "anchor_lonlat": [-0.1, 0.5],
            "anchor_local": [-10.0, 16.8, 50.0],
        }
    ]

    row = _derive_one_room_door(
        room=room,
        origins=origins,
        client=FakeRouteClient(),
        projector=LocalProjector(0.0, 0.0),
        origin_tries=2,
    )

    assert row["origin_external_id"] == "303S-400E4"
    assert row["door_source"] == "targeted_route_endpoint_override"
    assert row["door_lon"] == 0.9
    assert row["door_lat"] == 0.5
    assert row["distance_door_to_route_end_m"] == 0.0


def _route_step(abutters, lon):
    return {
        "abutters": abutters,
        "start_location": {
            "lng": lon,
            "lat": -36.85,
            "zLevel": 0.0,
            "floor_name": "0",
        },
    }


def test_external_entry_uses_only_final_destination_building_transition():
    route = {
        "legs": [
            {"steps": [_route_step("OutsideOnVenue", 174.70)]},
            {"steps": [_route_step("InsideBuilding", 174.71)]},
            {"steps": [_route_step("OutsideOnVenue", 174.72)]},
            {"steps": [_route_step("InsideBuilding", 174.73)]},
        ]
    }

    points = _outside_to_inside_points(route)

    assert len(points) == 1
    assert points[0].lon == 174.73


def test_external_entry_rejects_intermediate_building_when_route_finishes_outside():
    route = {
        "legs": [
            {"steps": [_route_step("OutsideOnVenue", 174.70)]},
            {"steps": [_route_step("InsideBuilding", 174.71)]},
            {"steps": [_route_step("OutsideOnVenue", 174.72)]},
        ]
    }

    assert _outside_to_inside_points(route) == []

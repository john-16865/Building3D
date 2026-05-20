from building3d.normalize import normalize_locations


def test_normalize_locations_preserves_rooms_aliases_and_portals():
    raw_locations = [
        {
            "id": "room-115",
            "properties": {
                "externalId": "260-115",
                "roomId": "260-115",
                "name": "Fisher & Paykel Appliances Auditorium",
                "building": "260",
                "floorName": "10",
                "locationType": "room",
                "type": "Lecture Theatre",
                "anchor": {"coordinates": [174.7714, -36.8529]},
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[174.7714, -36.8529], [174.7715, -36.8529], [174.7714, -36.8528], [174.7714, -36.8529]]],
            },
        },
        {
            "id": "stair-1",
            "properties": {
                "externalId": "260-100S1",
                "name": "Stair S1",
                "building": "260",
                "floorName": "10",
                "locationType": "room",
                "type": "Stair",
                "anchor": {"coordinates": [174.77142, -36.85288]},
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[174.77142, -36.85288], [174.77143, -36.85288], [174.77142, -36.85287], [174.77142, -36.85288]]],
            },
        },
    ]

    dataset = normalize_locations(raw_locations, building_admin_id="260", building_id="oggb")

    assert len(dataset.rooms) == 1
    assert dataset.rooms[0].external_id == "260-115"
    assert dataset.rooms[0].aliases == ["260-115", "115", "260 115", "OGGB 115"]
    assert dataset.rooms[0].category == "lecture"
    assert len(dataset.portals) == 1
    assert dataset.portals[0].kind == "stair"
    assert dataset.portals[0].group_id == "S1"


def test_normalize_skips_locations_without_polygon_or_external_id():
    raw_locations = [
        {"id": "bad", "properties": {"name": "Unnamed", "building": "260"}, "geometry": None},
        {
            "id": "point",
            "properties": {"externalId": "260-X", "building": "260"},
            "geometry": {"type": "Point", "coordinates": [174.0, -36.0]},
        },
    ]

    dataset = normalize_locations(raw_locations, building_admin_id="260", building_id="oggb")

    assert dataset.rooms == []
    assert len(dataset.warnings) == 2

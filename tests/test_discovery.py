from building3d.discovery import building_slug, parse_building_inventory, parse_venue_inventory


def test_building_slug_uses_admin_id_and_sanitized_name():
    assert building_slug("423", "Conference Centre") == "423-conference-centre"
    assert building_slug("260", "Sir Owen G Glenn Building OGGB") == "260-sir-owen-g-glenn-building-oggb"
    assert building_slug("105S", "The Clock Tower South Wing") == "105s-the-clock-tower-south-wing"


def test_parse_building_inventory_extracts_structured_records():
    venues = parse_venue_inventory(
        [
            {
                "id": "venue-city",
                "name": "CITY_CAMPUS",
                "venueInfo": {"name": "City Campus"},
            }
        ]
    )
    records = parse_building_inventory(
        [
            {
                "id": "building-423",
                "administrativeId": "423",
                "externalId": "B423 ",
                "venueId": "venue-city",
                "buildingInfo": {"name": "Conference Centre"},
                "defaultFloor": 0,
                "anchor": {"coordinates": [174.7693, -36.8537]},
                "geometry": {"type": "Polygon", "bbox": [174.7690, -36.8540, 174.7697, -36.8534]},
                "floors": {"0": {"name": "3"}, "-10": {"name": "2"}},
            }
        ],
        venues_by_id=venues,
    )

    assert len(records) == 1
    record = records[0]
    assert record.slug == "423-conference-centre"
    assert record.mapsindoors_id == "building-423"
    assert record.admin_id == "423"
    assert record.external_id == "B423"
    assert record.venue_name == "City Campus"
    assert record.origin == [174.7693, -36.8537]
    assert record.floor_keys == ["-10", "0"]

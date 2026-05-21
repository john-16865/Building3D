from building3d.manifest import build_manifest
from building3d.normalize import FloorRecord, NormalizedDataset, PortalRecord, RoomRecord


def test_build_manifest_contains_rooms_aliases_and_hash():
    dataset = NormalizedDataset(
        building_id="oggb",
        building_admin_id="260",
        building_name="Sir Owen G Glenn Building OGGB",
        rooms=[
            RoomRecord(
                source_id="room-115",
                external_id="260-115",
                display_name="Fisher & Paykel Appliances Auditorium",
                building_admin_id="260",
                floor_name="10",
                floor_index=1,
                category="lecture",
                aliases=["260-115", "115", "260 115", "OGGB 115"],
                anchor_lonlat=[174.7714, -36.8529],
                anchor_local=[1.0, 4.2, 2.0],
                polygon_lonlat=[],
                polygon_local=[],
                source_properties={},
            )
        ],
    )

    manifest = build_manifest(dataset, source_urls=["https://example.test/source"])

    assert manifest["schema_version"] == 1
    assert manifest["building"]["id"] == "oggb"
    assert manifest["rooms"][0]["external_id"] == "260-115"
    assert manifest["rooms"][0]["aliases"] == ["260-115", "115", "260 115", "OGGB 115"]
    assert len(manifest["generation_hash"]) == 64


def test_manifest_hash_is_stable_for_same_input():
    dataset = NormalizedDataset(building_id="oggb", building_admin_id="260", building_name="OGGB")

    first = build_manifest(dataset, source_urls=[])
    second = build_manifest(dataset, source_urls=[])

    assert first["generation_hash"] == second["generation_hash"]


def test_vertical_links_keep_repeated_portal_sets_in_source_building_shafts():
    dataset = NormalizedDataset(
        building_id="science",
        building_admin_id="302,303",
        building_name="Science Centre",
        floors=[
            FloorRecord("B-2", 0, -6.0),
            FloorRecord("B-1", 1, -3.0),
        ],
        portals=[
            _portal("303-SB00S4", "303", 0, [0.0, -6.0, 0.0]),
            _portal("302-SB00S4", "302", 0, [100.0, -6.0, 0.0]),
            _portal("303-B00S4", "303", 1, [0.0, -3.0, 0.0]),
            _portal("302-B00S4", "302", 1, [100.0, -3.0, 0.0]),
        ],
    )

    manifest = build_manifest(dataset, source_urls=[])
    vertical_links = [link for link in manifest["nav"]["links"] if link["kind"] == "stair"]

    assert [
        (link["from_external_id"], link["to_external_id"])
        for link in vertical_links
    ] == [
        ("303-SB00S4", "303-B00S4"),
        ("302-SB00S4", "302-B00S4"),
    ]


def _portal(external_id: str, building_admin_id: str, floor_index: int, anchor: list[float]) -> PortalRecord:
    return PortalRecord(
        source_id=f"feature-{external_id}",
        external_id=external_id,
        display_name="Stairs",
        building_admin_id=building_admin_id,
        floor_name=str(floor_index),
        floor_index=floor_index,
        kind="stair",
        group_id="S4",
        anchor_lonlat=None,
        anchor_local=anchor,
        polygon_lonlat=[],
        polygon_local=[],
        source_properties={},
    )

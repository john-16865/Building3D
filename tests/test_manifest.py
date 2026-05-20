from building3d.manifest import build_manifest
from building3d.normalize import NormalizedDataset, RoomRecord


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

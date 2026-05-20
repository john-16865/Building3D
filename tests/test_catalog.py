import json

from building3d.catalog import write_building_catalog


def test_write_building_catalog_creates_navigable_markdown(tmp_path):
    index_path = tmp_path / "exports" / "auckland" / "index.json"
    output_path = tmp_path / "docs" / "auckland-building-catalog.md"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "summary": {"total": 2, "generated": 1, "skipped": 1, "failed": 0},
                "buildings": [
                    {
                        "admin_id": "260",
                        "display_name": "Sir Owen G Glenn Building OGGB",
                        "slug": "260-sir-owen-g-glenn-building-oggb",
                        "status": "generated",
                        "venue_name": "City Campus",
                        "rooms": 932,
                        "floors": 12,
                        "portals": 124,
                        "warnings": ["Skipped point-only feature"],
                        "errors": [],
                        "mapsindoors_id": "b2fc3c66e2ca44a2b5c924f4",
                        "external_id": "B260",
                        "artifacts": {
                            "manifest": "buildings/260-sir-owen-g-glenn-building-oggb/260-sir-owen-g-glenn-building-oggb_manifest.json",
                            "visual_glb": "buildings/260-sir-owen-g-glenn-building-oggb/260-sir-owen-g-glenn-building-oggb_visual.glb",
                            "nav_glb": "buildings/260-sir-owen-g-glenn-building-oggb/260-sir-owen-g-glenn-building-oggb_nav.glb",
                        },
                        "source_urls": ["https://example.test/building/260"],
                    },
                    {
                        "admin_id": "104",
                        "display_name": "Old Choral Hall",
                        "slug": "104-old-choral-hall",
                        "status": "skipped",
                        "venue_name": "City Campus",
                        "rooms": 0,
                        "floors": 0,
                        "portals": 0,
                        "warnings": ["No usable indoor geometry"],
                        "errors": [],
                        "mapsindoors_id": "building-104",
                        "external_id": "B104",
                        "artifacts": {},
                        "source_urls": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = write_building_catalog(index_path, output_path)

    text = result.read_text(encoding="utf-8")
    assert "# Auckland Building Catalog" in text
    assert "- [260 - Sir Owen G Glenn Building OGGB](#260-sir-owen-g-glenn-building-oggb)" in text
    assert "### 260 - Sir Owen G Glenn Building OGGB" in text
    assert "[manifest](../exports/auckland/buildings/260-sir-owen-g-glenn-building-oggb/260-sir-owen-g-glenn-building-oggb_manifest.json)" in text
    assert "No usable indoor geometry" in text

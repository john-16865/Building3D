import json

from building3d.batch import SkippedBuilding, derive_building_config, generate_all_for_records
from building3d.config import SolutionConfig
from building3d.discovery import BuildingInventoryRecord


def test_derive_building_config_uses_inventory_paths_and_artifact_slug(tmp_path):
    solution = _solution_config(tmp_path)
    record = _record(slug="423-conference-centre", admin_id="423", floor_keys=["-10", "0", "10"])

    config = derive_building_config(solution, record)

    assert config.building_id == "423-conference-centre"
    assert config.building_admin_id == "423"
    assert config.display_name == "Conference Centre"
    assert config.origin_lon == 174.7693
    assert config.origin_lat == -36.8537
    assert config.raw_dir == tmp_path / "raw" / "buildings" / "423-conference-centre"
    assert config.processed_dir == tmp_path / "processed" / "buildings" / "423-conference-centre"
    assert config.export_dir == tmp_path / "exports" / "buildings" / "423-conference-centre"
    assert config.floor_heights == {"-10": -3.0, "0": 0.0, "10": 4.2}


def test_generate_all_for_records_continues_after_building_failure(tmp_path):
    solution = _solution_config(tmp_path)
    good = _record(slug="260-oggb", admin_id="260")
    bad = _record(slug="423-conference-centre", admin_id="423")

    def runner(config):
        if config.building_admin_id == "423":
            raise RuntimeError("No raw locations")
        return {
            "rooms": 3,
            "floors": 2,
            "portals": 1,
            "warnings": [],
            "artifacts": {"manifest": f"buildings/{config.building_id}/{config.building_id}_manifest.json"},
            "generation_hash": "abc",
        }

    index_path = generate_all_for_records(solution, [good, bad], runner=runner)

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["summary"]["generated"] == 1
    assert index["summary"]["failed"] == 1
    statuses = {record["slug"]: record["status"] for record in index["buildings"]}
    assert statuses == {"260-oggb": "generated", "423-conference-centre": "failed"}


def test_generate_all_for_records_records_skipped_buildings(tmp_path):
    solution = _solution_config(tmp_path)
    record = _record(slug="104-old-choral-hall", admin_id="104")
    stale_manifest = solution.export_root / "buildings" / record.slug / f"{record.slug}_manifest.json"
    stale_manifest.parent.mkdir(parents=True)
    stale_manifest.write_text("stale", encoding="utf-8")

    def runner(config):
        raise SkippedBuilding("No usable indoor geometry")

    index_path = generate_all_for_records(solution, [record], runner=runner)

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["summary"]["skipped"] == 1
    assert index["buildings"][0]["status"] == "skipped"
    assert index["buildings"][0]["warnings"] == ["No usable indoor geometry"]
    assert not stale_manifest.exists()


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


def _record(slug, admin_id, floor_keys=None):
    return BuildingInventoryRecord(
        slug=slug,
        mapsindoors_id=f"building-{admin_id}",
        admin_id=admin_id,
        external_id=f"B{admin_id}",
        display_name="Conference Centre" if admin_id == "423" else "OGGB",
        venue_id="venue-city",
        venue_name="City Campus",
        origin=[174.7693, -36.8537],
        bbox=[],
        default_floor="0",
        floor_keys=floor_keys or ["0"],
    )

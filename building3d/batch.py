from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import requests

from building3d.artifacts import artifact_names, write_campus_index
from building3d.config import GeneratorConfig, SolutionConfig
from building3d.discovery import BuildingInventoryRecord, parse_building_inventory, parse_venue_inventory
from building3d.export_package import package_export
from building3d.geometry import MeshData, dataset_meshes, navigation_meshes_from_meshes, visual_meshes_from_meshes
from building3d.gltf import write_glb
from building3d.manifest import build_manifest, write_manifest
from building3d.mapsindoors import fetch_source_data, load_building_name, load_raw_locations, source_urls
from building3d.normalize import normalize_locations
from building3d.projection import project_dataset
from building3d.validate import validate_dataset, validate_export_package


Runner = Callable[[GeneratorConfig], dict[str, Any]]


class SkippedBuilding(RuntimeError):
    """Raised when a building is valid inventory but has no useful indoor geometry."""


def discover_inventory(config: SolutionConfig) -> list[BuildingInventoryRecord]:
    config.raw_root.mkdir(parents=True, exist_ok=True)
    buildings = _get_json(config.buildings_sync_url, {"solutionId": config.solution_id, "v": 5})
    venues = _get_json(config.venues_sync_url, {"solutionId": config.solution_id, "v": 5})
    _write_json(config.raw_root / "buildings.json", buildings)
    _write_json(config.raw_root / "venues.json", venues)
    records = parse_building_inventory(buildings, venues_by_id=parse_venue_inventory(venues))
    records = _filtered_records(config, records)
    records = [_with_source_urls(config, record) for record in records]
    write_inventory(config, records)
    return records


def write_inventory(config: SolutionConfig, records: list[BuildingInventoryRecord]) -> Path:
    config.processed_root.mkdir(parents=True, exist_ok=True)
    path = config.processed_root / "inventory.json"
    _write_json(path, [record.to_dict() for record in records])
    return path


def load_inventory(config: SolutionConfig) -> list[BuildingInventoryRecord]:
    path = config.processed_root / "inventory.json"
    if not path.exists():
        return discover_inventory(config)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [BuildingInventoryRecord(**item) for item in data]


def derive_building_config(config: SolutionConfig, record: BuildingInventoryRecord) -> GeneratorConfig:
    origin = record.origin if len(record.origin) >= 2 else [0.0, 0.0]
    return GeneratorConfig(
        project_root=config.project_root,
        building_id=record.slug,
        building_admin_id=record.admin_id,
        display_name=record.display_name,
        origin_lon=float(origin[0]),
        origin_lat=float(origin[1]),
        raw_dir=config.raw_root / "buildings" / record.slug,
        processed_dir=config.processed_root / "buildings" / record.slug,
        export_dir=config.export_root / "buildings" / record.slug,
        building_details_url=config.building_details_url_template.format(building_id=record.mapsindoors_id),
        locations_url=config.locations_url,
        take=config.take,
        floor_heights=_derived_floor_heights(record.floor_keys, config.default_floor_spacing, config.basement_floor_spacing),
    )


def generate_all(config: SolutionConfig) -> Path:
    return generate_all_for_records(config, load_inventory(config))


def generate_all_for_records(config: SolutionConfig, records: list[BuildingInventoryRecord], *, runner: Runner | None = None) -> Path:
    runner = runner or generate_one_building
    index_records: list[dict[str, Any]] = []
    for record in records:
        building_config = derive_building_config(config, record)
        _clear_generated_artifacts(building_config)
        try:
            result = runner(building_config)
            index_records.append(_index_record(record, "generated", result=result))
        except SkippedBuilding as exc:
            index_records.append(_index_record(record, "skipped", warnings=[str(exc)]))
        except Exception as exc:  # noqa: BLE001 - batch generation must report per-building failures.
            index_records.append(_index_record(record, "failed", errors=[str(exc)]))
            if config.failure_policy == "fail_fast":
                break
    return write_campus_index(config.export_root, solution_id=config.solution_id, records=index_records)


def generate_one_building(config: GeneratorConfig) -> dict[str, Any]:
    fetch_source_data(config)
    dataset = _process_building(config)
    if not dataset.rooms and not dataset.portals:
        raise SkippedBuilding("No usable indoor geometry")
    validation = validate_dataset(dataset)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))
    _build_glbs(config)
    package_export(config)
    package_validation = validate_export_package(config.export_dir, config.building_id)
    if not package_validation.ok:
        raise ValueError("; ".join(package_validation.errors))
    names = artifact_names(config.building_id)
    manifest_path = config.export_dir / names.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "rooms": len(manifest.get("rooms", [])),
        "floors": len(manifest.get("floors", [])),
        "portals": len(manifest.get("portals", [])),
        "warnings": list(manifest.get("warnings", [])),
        "artifacts": {
            "visual_glb": _relative(config.export_dir.parent.parent, config.export_dir / names.visual_glb),
            "nav_glb": _relative(config.export_dir.parent.parent, config.export_dir / names.nav_glb),
            "manifest": _relative(config.export_dir.parent.parent, manifest_path),
            "readme": _relative(config.export_dir.parent.parent, config.export_dir / names.readme),
        },
        "generation_hash": str(manifest.get("generation_hash", "")),
    }


def _process_building(config: GeneratorConfig):
    raw_locations = load_raw_locations(config.raw_dir)
    if not raw_locations:
        raise ValueError(f"No raw locations found in {config.raw_dir}")
    building_name = load_building_name(config.raw_dir, config.display_name)
    dataset = normalize_locations(
        raw_locations,
        building_admin_id=config.building_admin_id,
        building_id=config.building_id,
        building_name=building_name,
    )
    projected = project_dataset(dataset, config.origin_lon, config.origin_lat, config.floor_heights)
    config.processed_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.processed_dir / "dataset.json", projected.to_dict())
    _write_json(config.processed_dir / "geometry.json", [mesh.to_dict() for mesh in dataset_meshes(projected)])
    manifest = build_manifest(projected, source_urls(config))
    names = artifact_names(config.building_id)
    write_manifest(manifest, config.processed_dir / names.manifest)
    return projected


def _build_glbs(config: GeneratorConfig) -> None:
    geometry_path = config.processed_dir / "geometry.json"
    with geometry_path.open("r", encoding="utf-8") as handle:
        meshes = [MeshData(**item) for item in json.load(handle)]
    config.export_dir.mkdir(parents=True, exist_ok=True)
    names = artifact_names(config.building_id)
    write_glb(visual_meshes_from_meshes(meshes), config.export_dir / names.visual_glb)
    write_glb(navigation_meshes_from_meshes(meshes), config.export_dir / names.nav_glb)


def _index_record(
    record: BuildingInventoryRecord,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    result = result or {}
    return {
        "slug": record.slug,
        "admin_id": record.admin_id,
        "mapsindoors_id": record.mapsindoors_id,
        "external_id": record.external_id,
        "display_name": record.display_name,
        "venue_id": record.venue_id,
        "venue_name": record.venue_name,
        "status": status,
        "rooms": int(result.get("rooms", 0)),
        "floors": int(result.get("floors", 0)),
        "portals": int(result.get("portals", 0)),
        "warnings": warnings or list(result.get("warnings", [])),
        "errors": errors or [],
        "artifacts": dict(result.get("artifacts", {})),
        "generation_hash": str(result.get("generation_hash", "")),
        "source_urls": list(record.source_urls),
    }


def _clear_generated_artifacts(config: GeneratorConfig) -> None:
    names = artifact_names(config.building_id)
    for filename in (names.visual_glb, names.nav_glb, names.manifest, names.readme):
        path = config.export_dir / filename
        if path.exists() and path.is_file():
            path.unlink()


def _filtered_records(config: SolutionConfig, records: list[BuildingInventoryRecord]) -> list[BuildingInventoryRecord]:
    result = records
    if config.building_admin_ids:
        allowed = set(config.building_admin_ids)
        result = [record for record in result if record.admin_id in allowed]
    if config.venue_ids:
        allowed = set(config.venue_ids)
        result = [record for record in result if record.venue_id in allowed]
    return result


def _with_source_urls(config: SolutionConfig, record: BuildingInventoryRecord) -> BuildingInventoryRecord:
    return replace(
        record,
        source_urls=[
            f"{config.buildings_sync_url}?solutionId={config.solution_id}&v=5",
            f"{config.venues_sync_url}?solutionId={config.solution_id}&v=5",
            f"{config.locations_url}?building={record.admin_id}&take={config.take}",
            config.building_details_url_template.format(building_id=record.mapsindoors_id),
        ],
    )


def _derived_floor_heights(floor_keys: list[str], default_spacing: float, basement_spacing: float) -> dict[str, float]:
    heights: dict[str, float] = {}
    negative = [key for key in floor_keys if _as_int(key) is not None and _as_int(key) < 0]
    nonnegative = [key for key in floor_keys if key not in negative]
    for offset, key in enumerate(reversed(negative), start=1):
        heights[key] = round(-basement_spacing * offset, 6)
    for index, key in enumerate(nonnegative):
        heights[key] = round(default_spacing * index, 6)
    return heights


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _relative(root: Path, path: Path) -> str:
    return str(path.relative_to(root))


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, timeout=120, headers={"User-Agent": "Building3D/0.1"})
    response.raise_for_status()
    return response.json()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")

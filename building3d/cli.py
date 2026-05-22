from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from building3d.artifacts import artifact_names
from building3d.batch import discover_inventory, generate_all
from building3d.catalog import write_building_catalog
from building3d.config import load_config, load_group_config, load_solution_config
from building3d.export_package import package_export
from building3d.geometry import MeshData, dataset_meshes, navigation_meshes_from_meshes, visual_meshes_from_meshes
from building3d.gltf import write_glb
from building3d.groups import generate_group
from building3d.manifest import build_manifest, write_manifest
from building3d.mapsindoors import fetch_source_data, load_building_name, load_raw_locations, source_urls
from building3d.normalize import dataset_from_dict, normalize_locations
from building3d.projection import project_dataset
from building3d.validate import validate_dataset, validate_export_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="building3d")
    subparsers = parser.add_subparsers(dest="command", required=True)
    single_building_commands = ("fetch", "process", "validate", "build", "package", "all")
    batch_commands = ("discover", "generate-all", "catalog")
    for command in single_building_commands:
        sub = subparsers.add_parser(command)
        sub.add_argument("--config", default="configs/oggb.yaml")
    for command in batch_commands:
        sub = subparsers.add_parser(command)
        sub.add_argument("--config", default="configs/auckland.yaml")
    group_parser = subparsers.add_parser("group")
    group_parser.add_argument("group_id")
    group_parser.add_argument("--config", default="configs/auckland.yaml")
    group_parser.add_argument("--groups-config", default="configs/auckland_building_groups.yaml")
    group_parser.add_argument("--no-fetch", action="store_true", help="Fail if cached raw member data is missing")
    group_parser.add_argument(
        "--only-member",
        action="append",
        default=[],
        help="Generate only this group member admin id. Repeat or comma-separate for multiple members.",
    )
    group_parser.add_argument(
        "--only-floor",
        action="append",
        default=[],
        help="Generate only this floor label after canonicalization, for example G or 2. Repeat or comma-separate for multiple floors.",
    )
    args = parser.parse_args(argv)
    if args.command == "group":
        solution_config = load_solution_config(args.config)
        group_config = load_group_config(args.groups_config).get(args.group_id)
        result = generate_group(
            solution_config,
            group_config,
            fetch_missing=not args.no_fetch,
            only_members=_split_values(args.only_member),
            only_floors=_split_values(args.only_floor),
        )
        print(f"Wrote {result['export_dir']}")
        print(
            f"Rooms: {result['rooms']}, floors: {result['floors']}, "
            f"portals: {result['portals']}, external doors: {result.get('external_doors', 0)}"
        )
        for label, path in result["artifacts"].items():
            print(f"{label}: {path}")
        for warning in result.get("warnings", [])[:20]:
            print(f"warning: {warning}")
        return 0
    if args.command in batch_commands:
        solution_config = load_solution_config(args.config)
        if args.command == "discover":
            records = discover_inventory(solution_config)
            print(f"Discovered {len(records)} buildings")
            print(f"Wrote {solution_config.processed_root / 'inventory.json'}")
            return 0
        if args.command == "generate-all":
            index_path = generate_all(solution_config)
            print(f"Wrote {index_path}")
            return 0
        if args.command == "catalog":
            output_path = solution_config.project_root / "docs" / f"{solution_config.solution_id}-building-catalog.md"
            catalog_path = write_building_catalog(solution_config.export_root / "index.json", output_path)
            print(f"Wrote {catalog_path}")
            return 0
        return 1

    config = load_config(args.config)

    if args.command == "fetch":
        outputs = fetch_source_data(config)
        for label, path in outputs.items():
            print(f"{label}: {path}")
        return 0
    if args.command == "process":
        return _process(config)
    if args.command == "validate":
        return _validate(config)
    if args.command == "build":
        return _build(config)
    if args.command == "package":
        package_export(config)
        result = validate_export_package(config.export_dir, config.building_id)
        _print_result(result)
        return 0 if result.ok else 1
    if args.command == "all":
        fetch_source_data(config)
        process_code = _process(config)
        if process_code != 0:
            return process_code
        validate_code = _validate(config)
        if validate_code != 0:
            return validate_code
        build_code = _build(config)
        if build_code != 0:
            return build_code
        package_export(config)
        return 0
    return 1


def _process(config) -> int:
    raw_locations = load_raw_locations(config.raw_dir)
    if not raw_locations:
        print(f"No raw locations found in {config.raw_dir}. Run fetch first.", file=sys.stderr)
        return 1
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
    print(f"Processed {len(projected.rooms)} rooms, {len(projected.portals)} portals, {len(projected.floors)} floors")
    for warning in projected.warnings[:20]:
        print(f"warning: {warning}")
    return 0


def _validate(config) -> int:
    dataset_path = config.processed_dir / "dataset.json"
    if not dataset_path.exists():
        print(f"No processed dataset found at {dataset_path}. Run process first.", file=sys.stderr)
        return 1
    with dataset_path.open("r", encoding="utf-8") as handle:
        dataset = dataset_from_dict(json.load(handle))
    result = validate_dataset(dataset)
    _print_result(result)
    return 0 if result.ok else 1


def _build(config) -> int:
    geometry_path = config.processed_dir / "geometry.json"
    if not geometry_path.exists():
        print(f"No geometry found at {geometry_path}. Run process first.", file=sys.stderr)
        return 1
    with geometry_path.open("r", encoding="utf-8") as handle:
        meshes = [MeshData(**item) for item in json.load(handle)]
    config.export_dir.mkdir(parents=True, exist_ok=True)
    names = artifact_names(config.building_id)
    write_glb(visual_meshes_from_meshes(meshes), config.export_dir / names.visual_glb)
    write_glb(navigation_meshes_from_meshes(meshes), config.export_dir / names.nav_glb)
    print(f"Wrote {config.export_dir / names.visual_glb}")
    print(f"Wrote {config.export_dir / names.nav_glb}")
    return 0


def _print_result(result) -> None:
    for warning in result.warnings:
        print(f"warning: {warning}")
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    print("ok" if result.ok else "failed")


def _write_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _split_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in str(value).split(",") if part.strip())
    return result


if __name__ == "__main__":
    raise SystemExit(main())

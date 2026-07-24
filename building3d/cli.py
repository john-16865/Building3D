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
    debug_parser = subparsers.add_parser("debug-stages")
    debug_parser.add_argument("group_id")
    debug_parser.add_argument("--config", default="configs/auckland.yaml")
    debug_parser.add_argument("--groups-config", default="configs/auckland_building_groups.yaml")
    debug_parser.add_argument("--no-fetch", action="store_true", help="Fail if cached raw member data is missing")
    debug_parser.add_argument("--output-dir", help="Directory for stage GLBs. Defaults to the group export debug folder.")
    debug_parser.add_argument(
        "--only-member",
        action="append",
        default=[],
        help="Export debug stages for only this group member admin id. Repeat or comma-separate for multiple members.",
    )
    debug_parser.add_argument(
        "--only-floor",
        action="append",
        default=[],
        help="Export debug stages for only this floor label after canonicalization. Repeat or comma-separate for multiple floors.",
    )
    unimate_parser = subparsers.add_parser(
        "unimate",
        help="Generate a group and publish it into a UNIMATE Godot project end to end.",
    )
    unimate_parser.add_argument("group_id")
    unimate_parser.add_argument("--config", default="configs/auckland.yaml")
    unimate_parser.add_argument("--groups-config", default="configs/auckland_building_groups.yaml")
    unimate_parser.add_argument("--godot-dir", required=True, help="UNIMATE Godot project directory.")
    unimate_parser.add_argument("--godot-bin", default="godot", help="Godot 4.6+ executable for bake/apply scripts.")
    unimate_parser.add_argument("--graph-id", default="CITY_CAMPUS_Graph")
    unimate_parser.add_argument("--no-derive-doors", action="store_true", help="Skip route-derived door points (rooms stay sealed).")
    unimate_parser.add_argument("--force-derive", action="store_true", help="Re-derive door points even if cached.")
    unimate_parser.add_argument("--no-bake", action="store_true", help="Write the bake script but do not run Godot.")
    unimate_parser.add_argument("--no-wire-building-main", action="store_true", help="Do not add the baked scene to BuildingMain.tscn.")
    unimate_parser.add_argument("--no-campus-placement", action="store_true", help="Do not place the building on CampusMain.")
    unimate_parser.add_argument("--campus-placement-config", default="configs/unimate_campus_placement.yaml")
    unimate_parser.add_argument("--sync-backend", action="store_true", help="Run the backend sync_mapsindoors command.")
    unimate_parser.add_argument("--backend-dir", default=None, help="UNIMATE backend directory containing manage.py.")
    unimate_parser.add_argument("--no-fetch", action="store_true", help="Fail if cached raw member data is missing.")
    unimate_parser.add_argument(
        "--no-campus-paths",
        action="store_true",
        help="Skip refreshing the campus road network (the new building will have no road spur until campus-paths runs).",
    )
    unimate_parser.add_argument("--campus-paths-config", default="configs/unimate_campus_paths.yaml")
    campus_paths_parser = subparsers.add_parser(
        "campus-paths",
        help="Generate CampusMain roads/paths from MapsIndoors and publish into Godot.",
    )
    campus_paths_parser.add_argument("--config", default="configs/auckland.yaml")
    campus_paths_parser.add_argument("--paths-config", default="configs/unimate_campus_paths.yaml")
    campus_paths_parser.add_argument("--godot-dir", default=None, help="UNIMATE Godot project directory (required to publish).")
    campus_paths_parser.add_argument("--godot-bin", default="godot", help="Godot 4.6+ executable for bake/apply scripts.")
    campus_paths_parser.add_argument("--no-publish", action="store_true", help="Generate artifacts only; do not copy into Godot.")
    campus_paths_parser.add_argument("--no-bake", action="store_true", help="Publish files but do not bake the nav mesh.")
    campus_paths_parser.add_argument("--no-apply", action="store_true", help="Do not upsert the roads into CampusMain.")
    campus_paths_parser.add_argument("--no-probe", action="store_true", help="Skip the headless campus nav pathfinding probe.")
    campus_paths_parser.add_argument("--force-sample", action="store_true", help="Ignore the route cache and re-sample.")
    campus_paths_parser.add_argument("--max-targets", type=int, default=None, help="Cap building routing targets.")
    campus_context_parser = subparsers.add_parser(
        "campus-context",
        help="Generate the all-campus building context GLB and publish it into CampusMain.",
    )
    campus_context_parser.add_argument("--config", default="configs/auckland.yaml")
    campus_context_parser.add_argument("--paths-config", default="configs/unimate_campus_paths.yaml")
    campus_context_parser.add_argument("--groups-config", default="configs/auckland_building_groups.yaml")
    campus_context_parser.add_argument("--godot-dir", default=None, help="UNIMATE Godot project directory (required to publish).")
    campus_context_parser.add_argument("--no-publish", action="store_true", help="Generate artifacts only; do not copy into Godot.")
    args = parser.parse_args(argv)
    if args.command == "campus-context":
        from building3d.campus_context import generate_campus_context, publish_campus_context_to_unimate
        from building3d.campus_paths import load_campus_paths_config

        solution_config = load_solution_config(args.config)
        cfg = load_campus_paths_config(args.paths_config)
        groups = load_group_config(args.groups_config)
        stats = generate_campus_context(solution_config, cfg, groups)
        print(
            f"Campus context: {stats['buildings']} buildings, {stats['meshes']} meshes "
            f"({stats['skipped_placed']} placed buildings skipped, {stats['skipped_geometry']} without geometry)"
        )
        if not args.no_publish:
            if args.godot_dir is None:
                print("  (no --godot-dir given; artifacts written to export dir only)")
            else:
                publish = publish_campus_context_to_unimate(stats["export_dir"], args.godot_dir)
                print(f"  published -> {publish['campus_main']} (changed={publish['changed']})")
        return 0
    if args.command == "campus-paths":
        from building3d.campus_paths import generate_campus_paths, load_campus_paths_config
        from building3d.unimate_publish import publish_campus_paths_to_unimate

        solution_config = load_solution_config(args.config)
        cfg = load_campus_paths_config(args.paths_config)
        if args.max_targets is not None:
            from dataclasses import replace as _replace

            cfg = _replace(cfg, max_targets=args.max_targets)
        godot_dir = Path(args.godot_dir) if args.godot_dir else None
        stats = generate_campus_paths(solution_config, cfg, godot_dir=godot_dir, force_sample=args.force_sample)
        print(
            f"Campus paths: {stats['edges']} edges, {stats['nodes']} nodes, "
            f"~{stats['total_length_m']:.0f} m ({stats['routes_ok']}/{stats['routes_total']} routes)"
        )
        for spur in stats.get("entrance_spurs", []):
            flag = "ok" if spur.get("connected") else "UNCONNECTED"
            print(f"  - spur {spur.get('building_id')}: {spur.get('snap_m')} m [{flag}]")
        if not args.no_publish:
            if godot_dir is None:
                print("  (no --godot-dir given; artifacts written to export dir only)")
            else:
                publish = publish_campus_paths_to_unimate(
                    Path(stats["export_dir"]),
                    godot_dir,
                    cfg,
                    run_bake=not args.no_bake,
                    apply_to_campus=not args.no_apply,
                    run_probe=not args.no_probe,
                    godot_bin=args.godot_bin,
                )
                print(f"  published -> {publish['asset_dir']}")
                print(f"  baked scene -> {publish['baked_scene']}")
        return 0
    if args.command == "unimate":
        from building3d.unimate_pipeline import build_unimate_building

        solution_config = load_solution_config(args.config)
        group_config = load_group_config(args.groups_config).get(args.group_id)
        result = build_unimate_building(
            solution_config,
            group_config,
            args.godot_dir,
            godot_bin=args.godot_bin,
            graph_id=args.graph_id,
            derive_doors=not args.no_derive_doors,
            force_derive=args.force_derive,
            run_bake=not args.no_bake,
            wire_main=not args.no_wire_building_main,
            apply_campus_placement=not args.no_campus_placement,
            campus_placement_config=args.campus_placement_config if not args.no_campus_placement else None,
            sync_backend=args.sync_backend,
            backend_dir=args.backend_dir,
            fetch_missing=not args.no_fetch,
            refresh_campus_paths=not args.no_campus_paths and not args.no_campus_placement,
            campus_paths_config=args.campus_paths_config,
        )
        print(f"Published UNIMATE building '{result['group_id']}' from {result['export_dir']}")
        for step in result["steps"]:
            print(f"  - {step.get('step')}: {json.dumps({k: v for k, v in step.items() if k != 'step'}, default=str)}")
        return 0
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
    if args.command == "debug-stages":
        # debug_stages.py is not tracked in the repo; import lazily so the
        # missing module only affects this command, not the whole CLI.
        from building3d.debug_stages import export_group_debug_stages

        solution_config = load_solution_config(args.config)
        group_config = load_group_config(args.groups_config).get(args.group_id)
        result = export_group_debug_stages(
            solution_config,
            group_config,
            fetch_missing=not args.no_fetch,
            only_members=_split_values(args.only_member),
            only_floors=_split_values(args.only_floor),
            output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
        )
        print(f"Wrote {result['debug_dir']}")
        print(
            f"Rooms: {result['rooms']}, floors: {result['floors']}, "
            f"door points: {result['door_points']}, route line meshes: {result['route_line_meshes']}"
        )
        print(f"Door wall openings: {result['door_wall_openings']['total_edges']}")
        print(f"Route wall openings: {result['route_wall_openings']['total_edges']}")
        for label, path in result["artifacts"].items():
            print(f"{label}: {path}")
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

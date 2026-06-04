#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from building3d.unimate_publish import publish_group_to_unimate


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a Building3D group export into a UNIMATE Godot project.")
    parser.add_argument("--export-dir", required=True, help="Building3D group export directory.")
    parser.add_argument("--godot-dir", required=True, help="UNIMATE Godot project directory.")
    parser.add_argument("--run-bake", action="store_true", help="Run the generated Godot bake script before returning.")
    parser.add_argument(
        "--apply-campus-placement",
        action="store_true",
        help="Generate and run the CampusMain placement script for the baked full scene.",
    )
    parser.add_argument(
        "--campus-placement-config",
        default="configs/unimate_campus_placement.yaml",
        help="Campus placement calibration YAML.",
    )
    parser.add_argument("--godot-bin", default="godot", help="Godot executable to use for bake/apply scripts.")
    args = parser.parse_args()

    result = publish_group_to_unimate(
        Path(args.export_dir),
        Path(args.godot_dir),
        run_bake=args.run_bake,
        apply_campus_placement=args.apply_campus_placement,
        run_campus_apply=args.apply_campus_placement,
        campus_placement_config=Path(args.campus_placement_config) if args.apply_campus_placement else None,
        godot_bin=args.godot_bin,
    )
    printable = {key: _jsonable(value) for key, value in result.items()}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())

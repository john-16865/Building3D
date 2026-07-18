"""One-command Building3D -> UNIMATE integration for a building group.

Chains the pieces that were previously run by hand:

    ensure inventory
      -> generate_group (pass 1, produces dataset.json)
      -> derive route-based room door points + external entries
      -> generate_group (pass 2, cuts doorways, builds route nav + portal topology)
      -> publish into the UNIMATE Godot project (assets, scenes, headless bake)
      -> wire the baked scene into BuildingMain
      -> apply CampusMain placement
      -> (optional) sync backend rooms from MapsIndoors

Re-runs are cheap: when the room door evidence already exists the two-pass
generate collapses to a single generate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from building3d.batch import discover_inventory
from building3d.config import BuildingGroupConfig, SolutionConfig
from building3d.groups import generate_group
from building3d.room_door_derivation import derive_group_door_points
from building3d.unimate_publish import publish_group_to_unimate, wire_building_main, wire_campus_main
from building3d.vertical_links import DEFAULT_GRAPH_ID


def build_unimate_building(
    solution_config: SolutionConfig,
    group: BuildingGroupConfig,
    godot_dir: str | Path,
    *,
    godot_bin: str = "godot",
    graph_id: str = DEFAULT_GRAPH_ID,
    derive_doors: bool = True,
    force_derive: bool = False,
    run_bake: bool = True,
    wire_main: bool = True,
    apply_campus_placement: bool = True,
    campus_placement_config: str | Path | None = None,
    sync_backend: bool = False,
    backend_dir: str | Path | None = None,
    fetch_missing: bool = True,
    door_workers: int = 4,
    door_delay: float = 0.03,
    refresh_campus_paths: bool = False,
    campus_paths_config: str | Path | None = None,
) -> dict[str, Any]:
    godot_dir = Path(godot_dir)
    processed_dir = solution_config.processed_root / "groups" / group.id
    export_dir = solution_config.export_root / "groups" / group.id
    inventory_path = solution_config.processed_root / "inventory.json"
    steps: list[dict[str, Any]] = []

    if not inventory_path.exists():
        discover_inventory(solution_config)
        steps.append({"step": "discover_inventory", "inventory": str(inventory_path)})

    room_door_json = export_dir / f"{group.id}_room_door_points_route_derived.json"
    need_two_pass = derive_doors and (force_derive or not room_door_json.exists())

    if need_two_pass:
        first = generate_group(solution_config, group, fetch_missing=fetch_missing)
        steps.append({"step": "generate_pass_1", "rooms": first.get("rooms"), "floors": first.get("floors")})
        door_stats = derive_group_door_points(
            dataset_path=processed_dir / "dataset.json",
            inventory_path=inventory_path,
            output_dir=export_dir,
            group_id=group.id,
            display_name=group.display_name,
            graph_id=graph_id,
            primary_member=group.primary_member or (group.members[0] if group.members else ""),
            workers=door_workers,
            delay=door_delay,
        )
        steps.append({"step": "derive_doors", **door_stats})
        generated = generate_group(solution_config, group, fetch_missing=False)
        steps.append({"step": "generate_pass_2", "rooms": generated.get("rooms"), "floors": generated.get("floors")})
    else:
        generated = generate_group(solution_config, group, fetch_missing=fetch_missing)
        steps.append({"step": "generate", "rooms": generated.get("rooms"), "floors": generated.get("floors")})

    publish = publish_group_to_unimate(
        export_dir,
        godot_dir,
        run_bake=run_bake,
        apply_campus_placement=apply_campus_placement,
        # CampusMain is wired by text insertion below (wire_campus_main); the
        # generated apply script re-packs the whole hand-made scene and can
        # reformat it when the local Godot build differs from the editor's.
        run_campus_apply=False,
        campus_placement_config=campus_placement_config if apply_campus_placement else None,
        godot_bin=godot_bin,
    )
    steps.append({"step": "publish", "source_scene": str(publish.get("source_scene")), "asset_dir": str(publish.get("asset_dir"))})

    if apply_campus_placement:
        campus_wiring = wire_campus_main(godot_dir, group.id)
        steps.append({"step": "wire_campus_main", **{k: str(v) for k, v in campus_wiring.items()}})

    wiring: dict[str, Any] = {}
    if wire_main:
        # BuildingMain no longer hard-wires published buildings: UNIMATE's
        # BuildingRegistry lazy-loads them from building_registry.json, so
        # "wiring" now means regenerating that registry (and the per-building
        # rooms indexes) from the published manifests + baked scenes.
        registry_script = Path(godot_dir) / "tools" / "generate_building_registry.gd"
        if registry_script.exists() and run_bake:
            _run_registry_generator(Path(godot_dir), godot_bin)
            wiring = {"registry": str(Path(godot_dir) / "Assets" / "Buildings" / "building_registry.json")}
            steps.append({"step": "generate_building_registry", **wiring})
        else:
            wiring = wire_building_main(godot_dir, group.id)
            steps.append({"step": "wire_building_main", **{k: str(v) for k, v in wiring.items()}})

    backend: dict[str, Any] = {}
    if sync_backend:
        backend = _sync_backend_rooms(backend_dir)
        steps.append({"step": "sync_backend", **backend})

    campus_paths: dict[str, Any] = {}
    if refresh_campus_paths:
        # Re-derive the campus road network so this newly published building
        # (its *_campus_placement.json) becomes a routing target + entrance spur.
        from building3d.campus_paths import generate_campus_paths, load_campus_paths_config
        from building3d.unimate_publish import publish_campus_paths_to_unimate

        cfg = load_campus_paths_config(campus_paths_config)
        campus_stats = generate_campus_paths(solution_config, cfg, godot_dir=godot_dir)
        publish_campus_paths_to_unimate(
            Path(campus_stats["export_dir"]),
            godot_dir,
            cfg,
            run_bake=run_bake,
            apply_to_campus=apply_campus_placement,
            run_probe=False,
            godot_bin=godot_bin,
        )
        campus_paths = {"edges": campus_stats["edges"], "nodes": campus_stats["nodes"], "length_m": campus_stats["total_length_m"]}
        steps.append({"step": "refresh_campus_paths", **campus_paths})

    return {
        "group_id": group.id,
        "export_dir": str(export_dir),
        "generated": generated,
        "publish": {key: str(value) if isinstance(value, Path) else value for key, value in publish.items()},
        "wiring": wiring,
        "backend": backend,
        "campus_paths": campus_paths,
        "steps": steps,
    }


def _run_registry_generator(godot_dir: Path, godot_bin: str) -> None:
    subprocess.run(
        [
            godot_bin,
            "--headless",
            "--path",
            str(godot_dir),
            "--script",
            "res://tools/generate_building_registry.gd",
        ],
        check=True,
    )


def _sync_backend_rooms(backend_dir: str | Path | None) -> dict[str, Any]:
    """Run the UNIMATE ``sync_mapsindoors`` management command if possible.

    When the backend directory or its Python is unavailable, returns the exact
    command to run instead of failing the whole pipeline.
    """
    command_hint = "python manage.py sync_mapsindoors"
    if not backend_dir:
        return {"ran": False, "reason": "no backend_dir provided", "command": command_hint}
    backend_path = Path(backend_dir)
    if not (backend_path / "manage.py").exists():
        return {"ran": False, "reason": f"manage.py not found in {backend_path}", "command": command_hint}
    python = _backend_python(backend_path)
    try:
        result = subprocess.run(
            [python, "manage.py", "sync_mapsindoors"],
            cwd=str(backend_path),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return {"ran": False, "reason": str(exc), "command": f"{python} manage.py sync_mapsindoors"}
    return {
        "ran": result.returncode == 0,
        "returncode": result.returncode,
        "command": f"{python} manage.py sync_mapsindoors",
        "stdout_tail": "\n".join(result.stdout.splitlines()[-8:]),
        "stderr_tail": "\n".join(result.stderr.splitlines()[-8:]),
    }


def _backend_python(backend_path: Path) -> str:
    for candidate in (
        backend_path / ".venv" / "Scripts" / "python.exe",
        backend_path / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable

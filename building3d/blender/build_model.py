from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

try:
    import bpy
except ImportError:  # pragma: no cover - only runs inside Blender.
    bpy = None

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from building3d.blender.materials import material_color


def main(argv: list[str] | None = None) -> int:
    if bpy is None:
        print("This script must run inside Blender.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--slug", default="oggb")
    args = parser.parse_args(_after_double_dash(argv or sys.argv))

    processed = Path(args.processed)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    with (processed / "geometry.json").open("r", encoding="utf-8") as handle:
        meshes = json.load(handle)

    _clear_scene()
    materials = {}
    for mesh_data in meshes:
        obj = _create_mesh_object(mesh_data)
        mat_name = mesh_data.get("material", "default")
        if mat_name not in materials:
            materials[mat_name] = _create_material(mat_name)
        obj.data.materials.append(materials[mat_name])

    _export(output / f"{args.slug}_visual.glb")

    _clear_scene()
    for mesh_data in meshes:
        mat = str(mesh_data.get("material", ""))
        if mat in {"floor", "anchor", "stair", "elevator", "door"}:
            obj = _create_mesh_object(mesh_data)
            if mat not in materials:
                materials[mat] = _create_material(mat)
            obj.data.materials.append(materials[mat])
    _export(output / f"{args.slug}_nav.glb")
    return 0


def _after_double_dash(argv: list[str]) -> list[str]:
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv[1:]


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _create_mesh_object(mesh_data: dict) -> object:
    mesh = bpy.data.meshes.new(mesh_data["name"])
    mesh.from_pydata(mesh_data["vertices"], [], mesh_data["faces"])
    mesh.validate()
    mesh.update()
    obj = bpy.data.objects.new(mesh_data["name"], mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _create_material(name: str):
    material = bpy.data.materials.new(name)
    material.diffuse_color = material_color(name)
    return material


def _export(path: Path) -> None:
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB")


if __name__ == "__main__":
    raise SystemExit(main())

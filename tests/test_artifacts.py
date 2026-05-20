import json

from building3d.artifacts import artifact_names, write_campus_index
from building3d.geometry import MeshData
from building3d.gltf import write_glb
from building3d.validate import validate_export_package


def test_artifact_names_are_derived_from_building_slug():
    names = artifact_names("423-conference-centre")

    assert names.visual_glb == "423-conference-centre_visual.glb"
    assert names.nav_glb == "423-conference-centre_nav.glb"
    assert names.manifest == "423-conference-centre_manifest.json"


def test_validate_export_package_uses_generic_artifact_names(tmp_path):
    names = artifact_names("423-conference-centre")
    write_glb([_triangle_mesh()], tmp_path / names.visual_glb)
    write_glb([_triangle_mesh()], tmp_path / names.nav_glb)
    for filename in (names.manifest, "README.md"):
        (tmp_path / filename).write_text("x", encoding="utf-8")

    result = validate_export_package(tmp_path, "423-conference-centre")

    assert result.ok


def _triangle_mesh() -> MeshData:
    return MeshData(
        name="triangle",
        vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        faces=[[0, 1, 2]],
    )


def test_write_campus_index_records_generated_and_failed_buildings(tmp_path):
    index_path = write_campus_index(
        tmp_path,
        solution_id="auckland",
        records=[
            {
                "slug": "260-oggb",
                "admin_id": "260",
                "display_name": "OGGB",
                "status": "generated",
                "rooms": 932,
                "floors": 12,
                "portals": 124,
                "warnings": [],
                "errors": [],
                "artifacts": {"manifest": "buildings/260-oggb/260-oggb_manifest.json"},
                "generation_hash": "abc",
            },
            {
                "slug": "423-conference-centre",
                "admin_id": "423",
                "display_name": "Conference Centre",
                "status": "failed",
                "rooms": 0,
                "floors": 0,
                "portals": 0,
                "warnings": [],
                "errors": ["No raw locations"],
                "artifacts": {},
                "generation_hash": "",
            },
        ],
    )

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["solution_id"] == "auckland"
    assert index["summary"]["generated"] == 1
    assert index["summary"]["failed"] == 1
    assert index["buildings"][1]["status"] == "failed"

from building3d.artifacts import artifact_names
from building3d.geometry import MeshData
from building3d.gltf import write_glb
from building3d.normalize import NormalizedDataset, RoomRecord
from building3d.validate import validate_dataset, validate_export_package


def test_validate_dataset_errors_when_room_anchor_missing():
    dataset = NormalizedDataset(
        building_id="oggb",
        building_admin_id="260",
        building_name="OGGB",
        rooms=[
            RoomRecord(
                source_id="room-115",
                external_id="260-115",
                display_name="Room",
                building_admin_id="260",
                floor_name="10",
                floor_index=1,
                category="other",
                aliases=["260-115"],
                anchor_lonlat=None,
                anchor_local=None,
                polygon_lonlat=[],
                polygon_local=[],
                source_properties={},
            )
        ],
    )

    result = validate_dataset(dataset)

    assert not result.ok
    assert "missing local anchor" in result.errors[0]


def test_validate_export_package_reports_missing_files(tmp_path):
    result = validate_export_package(tmp_path)

    assert not result.ok
    assert "oggb_visual.glb" in result.errors[0]


def test_validate_export_package_rejects_json_disguised_as_glb(tmp_path):
    names = artifact_names("oggb")
    write_glb([_triangle_mesh()], tmp_path / names.visual_glb)
    (tmp_path / names.nav_glb).write_text('{"issues":[{"severity":"error","message":"not glb"}]}', encoding="utf-8")
    (tmp_path / names.manifest).write_text("{}", encoding="utf-8")
    (tmp_path / names.readme).write_text("# Export\n", encoding="utf-8")

    result = validate_export_package(tmp_path, "oggb")

    assert not result.ok
    assert any("oggb_nav.glb is not a binary glTF file" in error for error in result.errors)


def _triangle_mesh() -> MeshData:
    return MeshData(
        name="triangle",
        vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        faces=[[0, 1, 2]],
    )

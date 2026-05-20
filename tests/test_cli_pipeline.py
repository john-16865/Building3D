import json
import subprocess
import sys

from building3d.cli import main


def test_process_command_writes_dataset_geometry_and_manifest(tmp_path):
    config_dir = tmp_path / "configs"
    raw_dir = tmp_path / "data" / "raw" / "oggb"
    config_dir.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    (config_dir / "oggb.yaml").write_text(
        """
building:
  id: oggb
  admin_id: "260"
  display_name: Sir Owen G Glenn Building OGGB
  origin: [174.771359951698, -36.8529245870962]
mapsindoors:
  building_details_url: ""
  locations_url: https://example.test/locations
  take: 1000
paths:
  raw_dir: data/raw/oggb
  processed_dir: data/processed/oggb
  export_dir: exports/oggb
floor_heights:
  "10": 4.2
""",
        encoding="utf-8",
    )
    (raw_dir / "locations_0000.json").write_text(
        json.dumps(
            [
                {
                    "id": "room-115",
                    "properties": {
                        "externalId": "260-115",
                        "name": "Fisher & Paykel Appliances Auditorium",
                        "building": "260",
                        "floorName": "10",
                        "type": "Lecture Theatre",
                        "anchor": {"coordinates": [174.7714, -36.8529]},
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[174.7714, -36.8529], [174.7715, -36.8529], [174.7714, -36.8528], [174.7714, -36.8529]]],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["process", "--config", str(config_dir / "oggb.yaml")])

    assert exit_code == 0
    assert (tmp_path / "data" / "processed" / "oggb" / "dataset.json").exists()
    assert (tmp_path / "data" / "processed" / "oggb" / "geometry.json").exists()
    manifest_path = tmp_path / "data" / "processed" / "oggb" / "oggb_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rooms"][0]["external_id"] == "260-115"
    assert manifest["rooms"][0]["anchor"] is not None


def test_python_module_entrypoint_displays_help():
    result = subprocess.run(
        [sys.executable, "-m", "building3d", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "fetch" in result.stdout

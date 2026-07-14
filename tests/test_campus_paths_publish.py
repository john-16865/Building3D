import json

from building3d.campus_paths import CampusPathsConfig
from building3d.unimate_publish import publish_campus_paths_to_unimate


def _seed_export(tmp_path):
    export_dir = tmp_path / "export" / "campus"
    export_dir.mkdir(parents=True)
    # Minimal but valid artifact set the publisher copies.
    (export_dir / "campus_roads.glb").write_bytes(b"glTF-stub")
    (export_dir / "campus_paths_graph.json").write_text(json.dumps({"nodes": {}, "edges": []}), encoding="utf-8")
    (export_dir / "campus_paths_stats.json").write_text(json.dumps({"edges": 0}), encoding="utf-8")
    (export_dir / "campus_building_routes.json").write_text(
        json.dumps({"engineering|science": [[0.0, 0.0], [1.0, 1.0]]}), encoding="utf-8"
    )
    return export_dir


def test_publish_copies_assets_and_writes_scripts(tmp_path):
    export_dir = _seed_export(tmp_path)
    godot_dir = tmp_path / "Godot"
    cfg = CampusPathsConfig()

    result = publish_campus_paths_to_unimate(export_dir, godot_dir, cfg, run_bake=False, apply_to_campus=False, run_probe=False)

    asset_dir = godot_dir / "Assets" / "Campus"
    assert (asset_dir / "campus_roads.glb").exists()
    assert (asset_dir / "campus_paths_graph.json").exists()
    # The optional per-pair optimal route polylines are copied when present.
    assert (asset_dir / "campus_building_routes.json").exists()
    assert (godot_dir / "Scene" / "campus_roads_source.tscn").exists()
    for name in (
        "generate_campus_roads_baked_scene.gd",
        "check_campus_roads.gd",
        "apply_campus_roads.gd",
        "probe_campus_roads_nav.gd",
        "probe_campus_optimal_line.gd",
    ):
        assert (godot_dir / "tools" / name).exists()
    # Source scene wires the GLB under a NavigationRegion3D of the roads node.
    source = (godot_dir / "Scene" / "campus_roads_source.tscn").read_text(encoding="utf-8")
    assert f'[node name="{cfg.roads_node_name}"' in source
    assert "NavigationRegion3D" in source
    assert "campus_roads.glb" in source
    assert result["baked_scene"].name == "campus_roads_baked.tscn"


def test_generated_scripts_contain_key_logic(tmp_path):
    export_dir = _seed_export(tmp_path)
    godot_dir = tmp_path / "Godot"
    cfg = CampusPathsConfig()

    publish_campus_paths_to_unimate(export_dir, godot_dir, cfg, run_bake=False, apply_to_campus=False, run_probe=False)
    tools = godot_dir / "tools"

    bake = (tools / "generate_campus_roads_baked_scene.gd").read_text(encoding="utf-8")
    assert "bake_navigation_mesh(false)" in bake
    assert "get_polygon_count()" in bake
    assert "ResourceSaver.save" in bake

    apply = (tools / "apply_campus_roads.gd").read_text(encoding="utf-8")
    # Idempotent upsert: removes any existing roads node, aligns to Buildings transform.
    assert "roads.transform = buildings.transform" in apply
    assert f'child.name == "{cfg.roads_node_name}"' in apply or "child.name == ROADS_NODE" in apply

    probe = (tools / "probe_campus_roads_nav.gd").read_text(encoding="utf-8")
    assert "map_get_path" in probe
    assert "CAMPUS ROADS NAV OK" in probe

    line_probe = (tools / "probe_campus_optimal_line.gd").read_text(encoding="utf-8")
    # Reads shipped pairs and asserts each drawn line follows the optimal route.
    assert "_precomputed_campus_waypoints" in line_probe
    assert "campus_building_routes.json" in line_probe
    assert "CAMPUS OPTIMAL LINE OK" in line_probe


def test_publish_is_deterministic(tmp_path):
    export_dir = _seed_export(tmp_path)
    godot_dir = tmp_path / "Godot"
    cfg = CampusPathsConfig()

    publish_campus_paths_to_unimate(export_dir, godot_dir, cfg, run_bake=False, apply_to_campus=False, run_probe=False)
    first = (godot_dir / "tools" / "apply_campus_roads.gd").read_text(encoding="utf-8")
    publish_campus_paths_to_unimate(export_dir, godot_dir, cfg, run_bake=False, apply_to_campus=False, run_probe=False)
    second = (godot_dir / "tools" / "apply_campus_roads.gd").read_text(encoding="utf-8")
    assert first == second

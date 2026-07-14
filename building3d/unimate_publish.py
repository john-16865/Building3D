from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from building3d.campus_placement import build_campus_placement, load_campus_placement_config


def publish_group_to_unimate(
    export_dir: str | Path,
    godot_dir: str | Path,
    *,
    run_bake: bool = False,
    apply_campus_placement: bool = False,
    run_campus_apply: bool = False,
    campus_placement_config: str | Path | None = None,
    godot_bin: str = "godot",
) -> dict[str, Any]:
    export_path = Path(export_dir)
    godot_path = Path(godot_dir)
    manifest_path = _single_existing(export_path, "*_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    group_id = str(manifest.get("building", {}).get("id") or manifest_path.name.removesuffix("_manifest.json"))
    asset_dir = godot_path / "Assets" / "Buildings" / asset_dir_name(group_id)
    scene_dir = godot_path / "Scene"
    tools_dir = godot_path / "tools"
    asset_dir.mkdir(parents=True, exist_ok=True)
    scene_dir.mkdir(parents=True, exist_ok=True)
    tools_dir.mkdir(parents=True, exist_ok=True)

    floor_labels = _floor_labels(manifest)
    copied_assets = _copy_floor_assets(export_path, asset_dir, group_id, manifest)
    copied_metadata = _copy_metadata(export_path, asset_dir, group_id)
    source_scene = _write_source_scene(export_path, scene_dir, asset_dir, group_id, floor_labels)
    bake_script = tools_dir / f"generate_{group_id}_baked_full_scene.gd"
    check_script = tools_dir / f"check_{group_id}_full_scene.gd"
    bake_script.write_text(_bake_script(group_id, floor_labels), encoding="utf-8", newline="\n")
    check_script.write_text(_check_script(group_id, manifest, floor_labels), encoding="utf-8", newline="\n")
    if run_bake:
        # Newly copied GLBs need Godot to generate their .import files before a
        # SceneTree script can load() them; a headless import pass does that.
        _run_godot_import(godot_path, godot_bin)
        _run_godot_script(godot_path, bake_script, godot_bin)
    result: dict[str, Any] = {
        "asset_dir": asset_dir,
        "source_scene": source_scene,
        "bake_script": bake_script,
        "check_script": check_script,
        "manifest": asset_dir / f"{group_id}_full_manifest.json",
        "copied_assets": copied_assets,
        "copied_metadata": copied_metadata,
    }
    if apply_campus_placement:
        baked_scene = scene_dir / f"{group_id}_baked_full.tscn"
        if not baked_scene.exists():
            raise FileNotFoundError(
                f"Campus placement requires baked scene {baked_scene}. "
                "Run the generated bake script first or pass run_bake=True."
            )
        config = load_campus_placement_config(campus_placement_config)
        placement = build_campus_placement(manifest, config)
        placement_path = asset_dir / f"{group_id}_campus_placement.json"
        placement_path.write_text(json.dumps(placement, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        apply_script = tools_dir / f"apply_{group_id}_campus_placement.gd"
        placement_res_path = f"res://Assets/Buildings/{asset_dir_name(group_id)}/{group_id}_campus_placement.json"
        apply_script.write_text(_campus_apply_script(placement_res_path), encoding="utf-8", newline="\n")
        result["campus_placement"] = placement_path
        result["campus_apply_script"] = apply_script
        if run_campus_apply:
            _run_godot_script(godot_path, apply_script, godot_bin)
    return result


def safe_floor_label(value: str) -> str:
    label = str(value).strip().lower()
    if re.fullmatch(r"b-\d+", label):
        label = label.replace("-", "")
    elif re.fullmatch(r"-\d+", label):
        label = f"b{label[1:]}"
    else:
        label = re.sub(r"[^a-z0-9]+", "_", label).strip("_")
    return label or "floor"


def asset_dir_name(group_id: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^a-zA-Z0-9]+", str(group_id)) if part)


def root_node_name(group_id: str) -> str:
    return f"{asset_dir_name(group_id)}BakedFull"


def wire_building_main(
    godot_dir: str | Path,
    group_id: str,
    *,
    scale: float = 2.85,
    building_main_rel: str = "Scene/BuildingMain.tscn",
) -> dict[str, Any]:
    """Add the group's baked scene as a child of BuildingMain's ``Buildings`` node.

    Idempotent: if BuildingMain already references ``{group_id}_baked_full.tscn``
    or already has a ``building_name = "{group_id}"`` node, nothing changes. The
    inserted node mirrors the existing baked buildings (Science/Business): a
    scaled instance under the same ``Buildings`` parent.
    """
    scene_path = Path(godot_dir) / building_main_rel
    if not scene_path.exists():
        raise FileNotFoundError(scene_path)
    text = scene_path.read_text(encoding="utf-8")

    node_name = asset_dir_name(group_id)
    building_name = str(group_id)
    baked_res = f"res://Scene/{group_id}_baked_full.tscn"
    if baked_res in text or f'building_name = "{building_name}"' in text:
        return {"building_main": str(scene_path), "changed": False, "node_name": node_name}

    parent_match = re.search(r'\[node name="Buildings"[^\]]*parent="([^"]+)"', text)
    if parent_match is None:
        raise ValueError(f"{scene_path} has no Buildings node to attach to")
    children_parent = f"{parent_match.group(1)}/Buildings"

    ext_id = _unique_ext_resource_id(text, f"{group_id}_building")
    ext_line = f'[ext_resource type="PackedScene" path="{baked_res}" id="{ext_id}"]'
    lines = text.split("\n")
    ext_indexes = [index for index, line in enumerate(lines) if line.startswith("[ext_resource")]
    if not ext_indexes:
        raise ValueError(f"{scene_path} has no ext_resource block")
    lines.insert(ext_indexes[-1] + 1, ext_line)
    text = "\n".join(lines)

    scale_text = "%g" % float(scale)
    node_block = (
        f'[node name="{node_name}" parent="{children_parent}" instance=ExtResource("{ext_id}")]\n'
        f"transform = Transform3D({scale_text}, 0, 0, 0, {scale_text}, 0, 0, 0, {scale_text}, -50, 0, 0)\n"
        f'building_name = "{building_name}"\n'
        "floor_separation_distance = 15.789474\n"
        "context_floor_separation = 52.63158\n"
        "lid_move_distance = 42.105263\n"
    )
    marker = f'parent="{children_parent}"'
    last_child = text.rfind(marker)
    if last_child == -1:
        raise ValueError(f"{scene_path} Buildings node has no existing child to anchor after")
    next_node = text.find("[node", last_child)
    if next_node == -1:
        text = text.rstrip("\n") + "\n\n" + node_block
    else:
        text = text[:next_node] + node_block + "\n" + text[next_node:]

    scene_path.write_text(text, encoding="utf-8", newline="\n")
    return {"building_main": str(scene_path), "changed": True, "node_name": node_name, "ext_id": ext_id}


def _unique_ext_resource_id(text: str, base: str) -> str:
    if f'id="{base}"' not in text:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}_{suffix}"
        if f'id="{candidate}"' not in text:
            return candidate
    raise ValueError(f"Could not allocate a unique ext_resource id for {base}")


def _copy_floor_assets(export_dir: Path, asset_dir: Path, group_id: str, manifest: dict[str, Any]) -> list[Path]:
    copied: list[Path] = []
    asset_sets = [
        ("floor_visual_glbs", "visual"),
        ("walkable_path_glbs", "walkable_paths"),
    ]
    for manifest_key, suffix in asset_sets:
        for record in manifest.get("assets", {}).get(manifest_key, []):
            floor_label = safe_floor_label(str(record.get("floor_name", record.get("floor_index", ""))))
            source = export_dir / str(record.get("filename", ""))
            target = asset_dir / f"{group_id}_full_floor_{floor_label}_{suffix}.glb"
            if not source.exists():
                raise FileNotFoundError(source)
            shutil.copy2(source, target)
            copied.append(target)
    return copied


def _copy_metadata(export_dir: Path, asset_dir: Path, group_id: str) -> list[Path]:
    mapping = [
        (f"{group_id}_manifest.json", f"{group_id}_full_manifest.json"),
        (f"{group_id}_room_door_points_route_derived.json", f"{group_id}_full_room_door_points_route_derived.json"),
        (f"{group_id}_external_entry_points_route_derived.json", f"{group_id}_full_external_entry_points_route_derived.json"),
        (f"{group_id}_vertical_links_route_derived.json", f"{group_id}_full_vertical_links_route_derived.json"),
        (f"{group_id}_portal_topology.json", f"{group_id}_full_portal_topology.json"),
        (f"{group_id}_portal_topology.json", f"{group_id}_portal_topology.json"),
        (f"{group_id}_door_research.md", f"{group_id}_full_door_research.md"),
        ("external_doors.json", f"{group_id}_full_external_doors.json"),
    ]
    copied: list[Path] = []
    for source_name, target_name in mapping:
        source = export_dir / source_name
        if not source.exists():
            continue
        target = asset_dir / target_name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def _write_source_scene(
    export_dir: Path,
    scene_dir: Path,
    asset_dir: Path,
    group_id: str,
    floor_labels: list[tuple[int, str]],
) -> Path:
    source = export_dir / f"{group_id}_unimate.tscn"
    text = source.read_text(encoding="utf-8")
    for floor_index, floor_name in floor_labels:
        safe_label = safe_floor_label(floor_name)
        text = text.replace(f"{group_id}_floor_{floor_index}_visual.glb", f"{group_id}_full_floor_{safe_label}_visual.glb")
        text = text.replace(
            f"{group_id}_floor_{floor_index}_walkable_paths.glb",
            f"{group_id}_full_floor_{safe_label}_walkable_paths.glb",
        )
    source_scene = scene_dir / f"{group_id}_full_source.tscn"
    source_scene.write_text(text, encoding="utf-8", newline="\n")
    (asset_dir / f"{group_id}_unimate.tscn").write_text(text, encoding="utf-8", newline="\n")
    return source_scene


def _floor_labels(manifest: dict[str, Any]) -> list[tuple[int, str]]:
    return [
        (int(floor.get("floor_index", 0)), str(floor.get("floor_name", "")))
        for floor in sorted(manifest.get("floors", []), key=lambda item: int(item.get("floor_index", 0)))
    ]


def _floor_numbers(floor_labels: list[tuple[int, str]]) -> list[int]:
    return [_floor_number(label) for _index, label in floor_labels]


def _floor_number(label: str) -> int:
    text = str(label).strip().upper()
    if text == "G":
        return 0
    if text.startswith("B-"):
        try:
            return -int(text[2:])
        except ValueError:
            return 0
    if text.startswith("M") and text[1:].isdigit():
        return int(text[1:])
    try:
        return int(float(text))
    except ValueError:
        return 0


def _bake_script(group_id: str, floor_labels: list[tuple[int, str]]) -> str:
    labels_literal = json.dumps([label for _index, label in floor_labels])
    return f"""@tool
extends SceneTree

const SOURCE_SCENE := "res://Scene/{group_id}_full_source.tscn"
const OUTPUT_SCENE := "res://Scene/{group_id}_baked_full.tscn"
const EXPECTED_FLOORS := {labels_literal}


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar source_scene := load(SOURCE_SCENE) as PackedScene
\tif source_scene == null:
\t\t_fail("Could not load source scene: %s" % SOURCE_SCENE, null)
\t\treturn

\tvar root := source_scene.instantiate() as Node3D
\tif root == null:
\t\t_fail("Could not instantiate source scene: %s" % SOURCE_SCENE, null)
\t\treturn

\troot.name = "{root_node_name(group_id)}"
\troot.set("building_name", "{group_id}")
\tget_root().add_child(root)

\tvar floors := root.get_node_or_null("Floors")
\tif floors == null:
\t\t_fail("Source scene is missing Floors", root)
\t\treturn
\tif floors.get_child_count() != EXPECTED_FLOORS.size():
\t\t_fail("Expected %d floors, got %d" % [EXPECTED_FLOORS.size(), floors.get_child_count()], root)
\t\treturn

\t_remove_nodes_named(root, "WalkablePathVisual")
\t_remove_nodes_named(root, "NavTarget")
\t_assign_scene_owner(root, root)

\tfor _i in range(4):
\t\tawait process_frame
\t\tawait physics_frame

\tvar baked_count := 0
\tfor floor_index in range(floors.get_child_count()):
\t\tvar floor := floors.get_child(floor_index) as Node3D
\t\tif floor == null:
\t\t\t_fail("Floor child %d is not a Node3D" % floor_index, root)
\t\t\treturn
\t\tvar expected_floor_name := str(EXPECTED_FLOORS[floor_index])
\t\tif str(floor.get("floor_name")) != expected_floor_name:
\t\t\t_fail("%s expected floor_name %s, got %s" % [floor.name, expected_floor_name, str(floor.get("floor_name"))], root)
\t\t\treturn

\t\tvar region := floor.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
\t\tif region == null:
\t\t\t_fail("%s is missing NavigationRegion3D" % floor.name, root)
\t\t\treturn
\t\tvar floor_mesh := region.get_node_or_null("FloorMesh")
\t\tif floor_mesh == null or floor_mesh.get_node_or_null("FloorVisual") == null:
\t\t\t_fail("%s is missing NavigationRegion3D/FloorMesh/FloorVisual" % floor.name, root)
\t\t\treturn

\t\t_configure_navigation_mesh(region)
\t\tregion.bake_navigation_mesh(false)
\t\tif region.navigation_mesh == null or region.navigation_mesh.get_polygon_count() <= 0:
\t\t\t_fail("Baked NavigationMesh is empty for %s" % floor.name, root)
\t\t\treturn
\t\tbaked_count += region.navigation_mesh.get_polygon_count()

\tvar packed := PackedScene.new()
\tvar pack_result := packed.pack(root)
\tif pack_result != OK:
\t\t_fail("Could not pack scene: %s" % error_string(pack_result), root)
\t\treturn

\tvar save_result := ResourceSaver.save(packed, OUTPUT_SCENE)
\tif save_result != OK:
\t\t_fail("Could not save scene %s: %s" % [OUTPUT_SCENE, error_string(save_result)], root)
\t\treturn

\tprint("Saved %s with %d baked nav polygons across %d floors" % [OUTPUT_SCENE, baked_count, floors.get_child_count()])
\troot.queue_free()
\tawait process_frame
\tquit(0)


func _configure_navigation_mesh(region: NavigationRegion3D) -> void:
\tregion.navigation_layers = 1
\tvar mesh := NavigationMesh.new()
\tmesh.agent_radius = 0.25
\tmesh.agent_height = 1.8
\tmesh.agent_max_climb = 0.4
\tmesh.agent_max_slope = 45.0
\tmesh.cell_size = 0.25
\tmesh.cell_height = 0.25
\tmesh.edge_max_error = 1.3
\tmesh.edge_max_length = 0.0
\tmesh.region_min_size = 2.0
\tmesh.region_merge_size = 20.0
\tmesh.vertices_per_polygon = 6.0
\tmesh.detail_sample_distance = 6.0
\tmesh.detail_sample_max_error = 1.0
\tmesh.filter_baking_aabb = AABB()
\tmesh.geometry_parsed_geometry_type = NavigationMesh.PARSED_GEOMETRY_MESH_INSTANCES
\tmesh.geometry_source_geometry_mode = NavigationMesh.SOURCE_GEOMETRY_ROOT_NODE_CHILDREN
\tregion.navigation_mesh = mesh


func _remove_nodes_named(root: Node, node_name: String) -> void:
\tfor child in root.get_children():
\t\t_remove_nodes_named(child, node_name)
\tif root.name == node_name and root.get_parent():
\t\troot.get_parent().remove_child(root)
\t\troot.queue_free()


func _assign_scene_owner(node: Node, owner_node: Node) -> void:
\tif node != owner_node:
\t\tnode.owner = owner_node
\tif node != owner_node and not node.scene_file_path.is_empty():
\t\treturn
\tfor child in node.get_children():
\t\t_assign_scene_owner(child, owner_node)


func _fail(message: String, root: Node) -> void:
\tpush_error(message)
\tif root != null:
\t\troot.queue_free()
\tquit(1)
"""


def _check_script(group_id: str, manifest: dict[str, Any], floor_labels: list[tuple[int, str]]) -> str:
    labels = [label for _index, label in floor_labels]
    expected_visuals = [f"{group_id}_full_floor_{safe_floor_label(label)}_visual.glb" for label in labels]
    floor_numbers = _floor_numbers(floor_labels)
    return f"""extends SceneTree

const BAKED_SCENE := "res://Scene/{group_id}_baked_full.tscn"
const EXPECTED_FLOORS := {json.dumps(labels)}
const EXPECTED_FLOOR_NUMBERS := {json.dumps(floor_numbers)}
const EXPECTED_VISUALS := {json.dumps(expected_visuals, indent=1)}


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar failures: Array[String] = []
\t_check_scene_text(failures)
\tvar baked_scene := load(BAKED_SCENE) as PackedScene
\tif baked_scene == null:
\t\tpush_error("Could not load baked scene: %s" % BAKED_SCENE)
\t\tquit(1)
\t\treturn
\tvar root := baked_scene.instantiate() as Node3D
\tif root == null:
\t\tpush_error("Could not instantiate baked scene: %s" % BAKED_SCENE)
\t\tquit(1)
\t\treturn
\tget_root().add_child(root)
\tfor _i in range(4):
\t\tawait process_frame
\t\tawait physics_frame
\t_check_scene_tree(root, failures)
\troot.queue_free()
\tawait process_frame
\tif failures.size() > 0:
\t\tfor failure in failures:
\t\t\tpush_error(failure)
\t\tquit(1)
\t\treturn
\tprint("{group_id}_baked_full structure OK")
\tquit(0)


func _check_scene_text(failures: Array[String]) -> void:
\tvar file := FileAccess.open(BAKED_SCENE, FileAccess.READ)
\tif file == null:
\t\tfailures.append("Could not read %s" % BAKED_SCENE)
\t\treturn
\tvar text := file.get_as_text()
\tfile.close()
\tfor visual in EXPECTED_VISUALS:
\t\tif not text.contains(visual):
\t\t\tfailures.append("Baked scene does not reference %s" % visual)
\tfor floor_index in range(EXPECTED_FLOORS.size()):
\t\tif text.contains("{group_id}_floor_%d_visual.glb" % floor_index):
\t\t\tfailures.append("Baked scene still references generic {group_id}_floor_%d_visual.glb" % floor_index)
\tif text.contains("walkable_paths.glb"):
\t\tfailures.append("Baked scene still references walkable path GLBs")
\tif text.contains("WalkablePathVisual"):
\t\tfailures.append("Baked scene still contains WalkablePathVisual")
\tif text.contains("NavTarget"):
\t\tfailures.append("Baked scene still contains NavTarget")


func _check_scene_tree(root: Node3D, failures: Array[String]) -> void:
\tif root.name != "{root_node_name(group_id)}":
\t\tfailures.append("Expected root node {root_node_name(group_id)}, got %s" % root.name)
\tif str(root.get("building_name")) != "{group_id}":
\t\tfailures.append("Expected building_name {group_id}, got %s" % str(root.get("building_name")))
\tvar floors := root.get_node_or_null("Floors")
\tif floors == null:
\t\tfailures.append("Missing Floors node")
\t\treturn
\tif floors.get_child_count() != EXPECTED_FLOORS.size():
\t\tfailures.append("Expected %d floors, got %d" % [EXPECTED_FLOORS.size(), floors.get_child_count()])
\tfor floor_index in range(min(floors.get_child_count(), EXPECTED_FLOORS.size())):
\t\tvar floor := floors.get_child(floor_index) as Node3D
\t\tif floor == null:
\t\t\tfailures.append("Floor child %d is not Node3D" % floor_index)
\t\t\tcontinue
\t\tvar expected_name := str(EXPECTED_FLOORS[floor_index])
\t\tif floor.name != "Floor%d" % floor_index:
\t\t\tfailures.append("Expected floor node Floor%d, got %s" % [floor_index, floor.name])
\t\tif str(floor.get("floor_name")) != expected_name:
\t\t\tfailures.append("%s floor_name should be %s, got %s" % [floor.name, expected_name, str(floor.get("floor_name"))])
\t\tif int(floor.get("floor_index")) != floor_index:
\t\t\tfailures.append("%s floor_index should be %d" % [floor.name, floor_index])
\t\tif int(floor.get("floor_number")) != int(EXPECTED_FLOOR_NUMBERS[floor_index]):
\t\t\tfailures.append("%s floor_number should be %d" % [floor.name, int(EXPECTED_FLOOR_NUMBERS[floor_index])])
\t\tvar region := floor.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
\t\tif region == null:
\t\t\tfailures.append("%s missing NavigationRegion3D" % floor.name)
\t\telse:
\t\t\tif region.navigation_mesh == null:
\t\t\t\tfailures.append("%s NavigationRegion3D has no NavigationMesh" % floor.name)
\t\t\telif region.navigation_mesh.get_polygon_count() <= 0:
\t\t\t\tfailures.append("%s NavigationMesh has no polygons" % floor.name)
\t\t\tvar floor_mesh := region.get_node_or_null("FloorMesh")
\t\t\tif floor_mesh == null:
\t\t\t\tfailures.append("%s NavigationRegion3D should contain FloorMesh" % floor.name)
\t\t\telif floor_mesh.get_node_or_null("FloorVisual") == null:
\t\t\t\tfailures.append("%s should keep FloorVisual under NavigationRegion3D/FloorMesh" % floor.name)
\t\tvar rooms := floor.get_node_or_null("Rooms")
\t\tif rooms == null:
\t\t\tfailures.append("%s missing Rooms node" % floor.name)
\t\telif rooms.get_child_count() <= 0:
\t\t\tfailures.append("%s should have room or portal nodes" % floor.name)
\tif _contains_node_named(root, "WalkablePathVisual"):
\t\tfailures.append("Baked scene tree still contains WalkablePathVisual")
\tif _contains_node_named(root, "NavTarget"):
\t\tfailures.append("Baked scene tree still contains NavTarget")


func _contains_node_named(root: Node, node_name: String) -> bool:
\tif root.name == node_name:
\t\treturn true
\tfor child in root.get_children():
\t\tif _contains_node_named(child, node_name):
\t\t\treturn true
\treturn false
"""


def _campus_apply_script(placement_res_path: str) -> str:
    return f"""@tool
extends SceneTree

const PLACEMENT_JSON := "{placement_res_path}"


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar placement := _load_json(PLACEMENT_JSON)
\tif placement.is_empty():
\t\tquit(1)
\t\treturn
\tvar campus_scene_path := str(placement.get("campus_scene", ""))
\tvar campus_scene := load(campus_scene_path) as PackedScene
\tif campus_scene == null:
\t\t_fail("Could not load campus scene: %s" % campus_scene_path, null)
\t\treturn
\tvar root := campus_scene.instantiate()
\tif root == null:
\t\t_fail("Could not instantiate campus scene: %s" % campus_scene_path, null)
\t\treturn
\tget_root().add_child(root)
\tvar buildings := root.get_node_or_null(NodePath(str(placement.get("buildings_parent", "")))) as Node3D
\tif buildings == null:
\t\t_fail("Could not find Buildings node: %s" % str(placement.get("buildings_parent", "")), root)
\t\treturn
\tif not _upsert_building(root, buildings, placement):
\t\t_fail("Could not upsert campus building %s" % str(placement.get("building_id", "")), root)
\t\treturn
\tvar packed := PackedScene.new()
\tvar pack_result := packed.pack(root)
\tif pack_result != OK:
\t\t_fail("Could not pack campus scene: %s" % error_string(pack_result), root)
\t\treturn
\tvar save_result := ResourceSaver.save(packed, campus_scene_path)
\tif save_result != OK:
\t\t_fail("Could not save campus scene %s: %s" % [campus_scene_path, error_string(save_result)], root)
\t\treturn
\tprint("Applied campus placement for %s" % str(placement.get("building_id", "")))
\troot.queue_free()
\tawait process_frame
\tquit(0)


func _load_json(path: String) -> Dictionary:
\tvar file := FileAccess.open(path, FileAccess.READ)
\tif file == null:
\t\tpush_error("Could not read placement JSON: %s" % path)
\t\treturn {{}}
\tvar text := file.get_as_text()
\tfile.close()
\tvar parsed = JSON.parse_string(text)
\tif typeof(parsed) != TYPE_DICTIONARY:
\t\tpush_error("Placement JSON must be a dictionary: %s" % path)
\t\treturn {{}}
\treturn parsed


func _upsert_building(scene_root: Node, buildings: Node3D, placement: Dictionary) -> bool:
\tvar building_id := str(placement.get("building_id", ""))
\tvar node_name := str(placement.get("node_name", building_id.capitalize()))
\tfor child in buildings.get_children():
\t\tif child.name == node_name or str(child.get("building_name")) == building_id:
\t\t\tbuildings.remove_child(child)
\t\t\tchild.queue_free()
\tvar building_scene := load(str(placement.get("scene_path", ""))) as PackedScene
\tif building_scene == null:
\t\tpush_error("Could not load building scene: %s" % str(placement.get("scene_path", "")))
\t\treturn false
\tvar building := building_scene.instantiate() as Node3D
\tif building == null:
\t\tpush_error("Could not instantiate building scene: %s" % str(placement.get("scene_path", "")))
\t\treturn false
\tbuilding.name = node_name
\tbuilding.set("building_name", building_id)
\tbuilding.transform = _transform_from_placement(placement)
\tbuildings.add_child(building)
\tbuilding.owner = scene_root
\t_upsert_entrance(scene_root, building, placement)
\treturn true


func _upsert_entrance(scene_root: Node, building: Node3D, placement: Dictionary) -> void:
\tvar entrance_data := placement.get("entrance", {{}}) as Dictionary
\tvar marker_name := str(entrance_data.get("marker_name", "Entrance"))
\tvar existing := building.get_node_or_null(NodePath(marker_name))
\tvar entrance := existing as CSGBox3D
\tif existing != null and entrance == null:
\t\tbuilding.remove_child(existing)
\t\texisting.queue_free()
\tif entrance == null:
\t\tentrance = CSGBox3D.new()
\t\tentrance.name = marker_name
\t\tbuilding.add_child(entrance)
\tentrance.owner = scene_root
\tvar anchor := entrance_data.get("anchor", [0.0, 0.0, 0.0]) as Array
\tentrance.transform = Transform3D(Basis(), Vector3(float(anchor[0]), float(anchor[1]), float(anchor[2])))
\tvar size := entrance_data.get("marker_size", [30.0, 30.0, 30.0]) as Array
\tentrance.size = Vector3(float(size[0]), float(size[1]), float(size[2]))
\tentrance.visible = false


func _transform_from_placement(placement: Dictionary) -> Transform3D:
\tvar transform_data := placement.get("transform", {{}}) as Dictionary
\tvar basis_data := transform_data.get("basis", []) as Array
\tvar origin_data := transform_data.get("origin", [0.0, 0.0, 0.0]) as Array
\tvar basis := Basis(
\t\tVector3(float(basis_data[0][0]), float(basis_data[0][1]), float(basis_data[0][2])),
\t\tVector3(float(basis_data[1][0]), float(basis_data[1][1]), float(basis_data[1][2])),
\t\tVector3(float(basis_data[2][0]), float(basis_data[2][1]), float(basis_data[2][2]))
\t)
\treturn Transform3D(basis, Vector3(float(origin_data[0]), float(origin_data[1]), float(origin_data[2])))


func _fail(message: String, root: Node) -> void:
\tpush_error(message)
\tif root != null:
\t\troot.queue_free()
\tquit(1)
"""


def _run_godot_script(godot_dir: Path, script_path: Path, godot_bin: str) -> None:
    subprocess.run(
        [
            godot_bin,
            "--headless",
            "--path",
            str(godot_dir),
            "--script",
            f"res://tools/{script_path.name}",
        ],
        check=True,
    )


def _run_godot_import(godot_dir: Path, godot_bin: str) -> None:
    """Import newly copied assets so scripts can load() them. Best-effort:
    Godot can exit non-zero on import warnings, and the bake fails loudly if a
    resource is genuinely missing, so a non-zero import here is not fatal."""
    subprocess.run(
        [godot_bin, "--headless", "--path", str(godot_dir), "--import"],
        check=False,
    )


def _single_existing(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No {pattern} in {directory}")
    if len(matches) > 1:
        raise ValueError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


# ===========================================================================
# Campus paths: publish the generated road mesh + nav into CampusMain.
# ===========================================================================

def publish_campus_paths_to_unimate(
    export_dir: str | Path,
    godot_dir: str | Path,
    cfg,
    *,
    run_bake: bool = False,
    apply_to_campus: bool = False,
    run_probe: bool = False,
    godot_bin: str = "godot",
) -> dict[str, Any]:
    """Copy the campus road artifacts into Godot and (optionally) bake + wire them.

    Machine-owned outputs (campus_roads_source/baked.tscn, the GLB, the graph
    JSON) are overwritten wholesale; CampusMain is edited only by the idempotent
    apply script, mirroring the building publish flow.
    """
    export_path = Path(export_dir)
    godot_path = Path(godot_dir)
    asset_dir = godot_path / "Assets" / "Campus"
    scene_dir = godot_path / "Scene"
    tools_dir = godot_path / "tools"
    for directory in (asset_dir, scene_dir, tools_dir):
        directory.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    required = ("campus_roads.glb", "campus_paths_graph.json", "campus_paths_stats.json")
    optional = ("campus_building_routes.json",)
    for name in required:
        source = export_path / name
        if not source.exists():
            raise FileNotFoundError(source)
        target = asset_dir / name
        shutil.copy2(source, target)
        copied.append(target)
    for name in optional:
        source = export_path / name
        if source.exists():
            target = asset_dir / name
            shutil.copy2(source, target)
            copied.append(target)

    roads_node = cfg.roads_node_name
    source_scene = scene_dir / "campus_roads_source.tscn"
    source_scene.write_text(_campus_roads_source_scene(roads_node), encoding="utf-8", newline="\n")

    bake_script = tools_dir / "generate_campus_roads_baked_scene.gd"
    check_script = tools_dir / "check_campus_roads.gd"
    apply_script = tools_dir / "apply_campus_roads.gd"
    probe_script = tools_dir / "probe_campus_roads_nav.gd"
    line_probe_script = tools_dir / "probe_campus_optimal_line.gd"
    bake_script.write_text(_campus_bake_script(roads_node, cfg.output_scene), encoding="utf-8", newline="\n")
    check_script.write_text(_campus_check_script(roads_node, cfg.output_scene), encoding="utf-8", newline="\n")
    apply_script.write_text(_campus_apply_roads_script(cfg), encoding="utf-8", newline="\n")
    probe_script.write_text(_campus_probe_script(cfg), encoding="utf-8", newline="\n")
    line_probe_script.write_text(_campus_optimal_line_probe_script(cfg), encoding="utf-8", newline="\n")

    result: dict[str, Any] = {
        "asset_dir": asset_dir,
        "source_scene": source_scene,
        "baked_scene": scene_dir / "campus_roads_baked.tscn",
        "bake_script": bake_script,
        "check_script": check_script,
        "apply_script": apply_script,
        "probe_script": probe_script,
        "line_probe_script": line_probe_script,
        "copied": copied,
    }
    if run_bake:
        _run_godot_import(godot_path, godot_bin)
        _run_godot_script(godot_path, bake_script, godot_bin)
        _run_godot_script(godot_path, check_script, godot_bin)
    if apply_to_campus:
        _run_godot_script(godot_path, apply_script, godot_bin)
    if run_probe:
        _run_godot_script(godot_path, probe_script, godot_bin)
        # Gate on the drawn campus line following the true MapsIndoors route.
        _run_godot_script(godot_path, line_probe_script, godot_bin)
    return result


def _campus_roads_source_scene(roads_node: str) -> str:
    return f"""[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://Assets/Campus/campus_roads.glb" id="1_roads_glb"]

[node name="{roads_node}" type="Node3D"]

[node name="NavigationRegion3D" type="NavigationRegion3D" parent="."]

[node name="RoadMesh" parent="NavigationRegion3D" instance=ExtResource("1_roads_glb")]
"""


def _campus_bake_script(roads_node: str, output_scene: str) -> str:
    return f"""@tool
extends SceneTree

const SOURCE_SCENE := "res://Scene/campus_roads_source.tscn"
const OUTPUT_SCENE := "{output_scene}"


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar source := load(SOURCE_SCENE) as PackedScene
\tif source == null:
\t\t_fail("Could not load %s" % SOURCE_SCENE, null)
\t\treturn
\tvar root := source.instantiate() as Node3D
\tif root == null:
\t\t_fail("Could not instantiate %s" % SOURCE_SCENE, null)
\t\treturn
\troot.name = "{roads_node}"
\tget_root().add_child(root)
\t_assign_owner(root, root)
\tfor _i in range(4):
\t\tawait process_frame
\t\tawait physics_frame

\tvar region := root.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
\tif region == null:
\t\t_fail("Source scene missing NavigationRegion3D", root)
\t\treturn
\t_configure_navigation_mesh(region)
\tregion.bake_navigation_mesh(false)
\tif region.navigation_mesh == null or region.navigation_mesh.get_polygon_count() <= 0:
\t\t_fail("Baked campus roads NavigationMesh is empty", root)
\t\treturn

\tvar packed := PackedScene.new()
\tvar pack_result := packed.pack(root)
\tif pack_result != OK:
\t\t_fail("Could not pack scene: %s" % error_string(pack_result), root)
\t\treturn
\tvar save_result := ResourceSaver.save(packed, OUTPUT_SCENE)
\tif save_result != OK:
\t\t_fail("Could not save %s: %s" % [OUTPUT_SCENE, error_string(save_result)], root)
\t\treturn
\tprint("Saved %s with %d campus road nav polygons" % [OUTPUT_SCENE, region.navigation_mesh.get_polygon_count()])
\troot.queue_free()
\tawait process_frame
\tquit(0)


func _configure_navigation_mesh(region: NavigationRegion3D) -> void:
\tregion.navigation_layers = 1
\tvar mesh := NavigationMesh.new()
\t# Narrow agent so thin footpaths/spurs are not eroded into disconnected islands.
\tmesh.agent_radius = 0.4
\tmesh.agent_height = 1.8
\tmesh.agent_max_climb = 0.5
\tmesh.agent_max_slope = 45.0
\t# Match the campus/world nav map cell size (0.25); a mismatched cell size makes
\t# NavigationServer3D silently reject this region from the shared map.
\tmesh.cell_size = 0.25
\tmesh.cell_height = 0.25
\tmesh.region_min_size = 2.0
\tmesh.region_merge_size = 20.0
\tmesh.edge_max_error = 1.3
\tmesh.vertices_per_polygon = 6.0
\tmesh.detail_sample_distance = 6.0
\tmesh.detail_sample_max_error = 1.0
\tmesh.geometry_parsed_geometry_type = NavigationMesh.PARSED_GEOMETRY_MESH_INSTANCES
\tmesh.geometry_source_geometry_mode = NavigationMesh.SOURCE_GEOMETRY_ROOT_NODE_CHILDREN
\tregion.navigation_mesh = mesh


func _assign_owner(node: Node, owner_node: Node) -> void:
\tif node != owner_node:
\t\tnode.owner = owner_node
\tif node != owner_node and not node.scene_file_path.is_empty():
\t\treturn
\tfor child in node.get_children():
\t\t_assign_owner(child, owner_node)


func _fail(message: String, root: Node) -> void:
\tpush_error(message)
\tif root != null:
\t\troot.queue_free()
\tquit(1)
"""


def _campus_check_script(roads_node: str, output_scene: str) -> str:
    return f"""extends SceneTree

const BAKED_SCENE := "{output_scene}"


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar failures: Array[String] = []
\tvar scene := load(BAKED_SCENE) as PackedScene
\tif scene == null:
\t\tpush_error("Could not load %s" % BAKED_SCENE)
\t\tquit(1)
\t\treturn
\tvar root := scene.instantiate() as Node3D
\tif root == null:
\t\tpush_error("Could not instantiate %s" % BAKED_SCENE)
\t\tquit(1)
\t\treturn
\tget_root().add_child(root)
\tfor _i in range(2):
\t\tawait process_frame
\tif root.name != "{roads_node}":
\t\tfailures.append("Expected root {roads_node}, got %s" % root.name)
\tvar region := root.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
\tif region == null:
\t\tfailures.append("Missing NavigationRegion3D")
\telif region.navigation_mesh == null or region.navigation_mesh.get_polygon_count() <= 0:
\t\tfailures.append("NavigationMesh has no polygons")
\tif root.get_node_or_null("NavigationRegion3D/RoadMesh") == null:
\t\tfailures.append("Missing NavigationRegion3D/RoadMesh")
\troot.queue_free()
\tawait process_frame
\tif not failures.is_empty():
\t\tfor failure in failures:
\t\t\tpush_error(failure)
\t\tquit(1)
\t\treturn
\tprint("campus_roads_baked structure OK")
\tquit(0)
"""


def _campus_apply_roads_script(cfg) -> str:
    return f"""@tool
extends SceneTree

const CAMPUS_SCENE := "{cfg.campus_scene}"
const ROADS_SCENE := "res://Scene/campus_roads_baked.tscn"
const BUILDINGS_PARENT := "{cfg.buildings_parent}"
const SUBVIEWPORT_PARENT := "{cfg.subviewport_parent}"
const ROADS_NODE := "{cfg.roads_node_name}"


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar campus := load(CAMPUS_SCENE) as PackedScene
\tif campus == null:
\t\t_fail("Could not load %s" % CAMPUS_SCENE, null)
\t\treturn
\tvar root := campus.instantiate()
\tif root == null:
\t\t_fail("Could not instantiate %s" % CAMPUS_SCENE, null)
\t\treturn
\tget_root().add_child(root)

\tvar subviewport := root.get_node_or_null(NodePath(SUBVIEWPORT_PARENT))
\tif subviewport == null:
\t\t_fail("Could not find SubViewport parent: %s" % SUBVIEWPORT_PARENT, root)
\t\treturn
\tvar buildings := root.get_node_or_null(NodePath(BUILDINGS_PARENT)) as Node3D
\tif buildings == null:
\t\t_fail("Could not find Buildings node: %s" % BUILDINGS_PARENT, root)
\t\treturn

\tfor child in subviewport.get_children():
\t\tif child.name == ROADS_NODE:
\t\t\tsubviewport.remove_child(child)
\t\t\tchild.queue_free()

\tvar roads_scene := load(ROADS_SCENE) as PackedScene
\tif roads_scene == null:
\t\t_fail("Could not load %s" % ROADS_SCENE, root)
\t\treturn
\tvar roads := roads_scene.instantiate() as Node3D
\tif roads == null:
\t\t_fail("Could not instantiate %s" % ROADS_SCENE, root)
\t\treturn
\troads.name = ROADS_NODE
\t# Road vertices are authored in Buildings-parent placement space, so the roads
\t# root carries the exact same transform as the Buildings node -> aligned.
\troads.transform = buildings.transform
\tsubviewport.add_child(roads)
\t_assign_owner(roads, root)

\tvar packed := PackedScene.new()
\tvar pack_result := packed.pack(root)
\tif pack_result != OK:
\t\t_fail("Could not pack campus scene: %s" % error_string(pack_result), root)
\t\treturn
\tvar save_result := ResourceSaver.save(packed, CAMPUS_SCENE)
\tif save_result != OK:
\t\t_fail("Could not save %s: %s" % [CAMPUS_SCENE, error_string(save_result)], root)
\t\treturn
\tprint("Applied campus roads (%s) to %s" % [ROADS_NODE, CAMPUS_SCENE])
\troot.queue_free()
\tawait process_frame
\tquit(0)


func _assign_owner(node: Node, owner_node: Node) -> void:
\tif node != owner_node:
\t\tnode.owner = owner_node
\tif node != owner_node and not node.scene_file_path.is_empty():
\t\treturn
\tfor child in node.get_children():
\t\t_assign_owner(child, owner_node)


func _fail(message: String, root: Node) -> void:
\tpush_error(message)
\tif root != null:
\t\troot.queue_free()
\tquit(1)
"""


def _campus_probe_script(cfg) -> str:
    return f"""extends SceneTree

const CAMPUS_SCENE := "{cfg.campus_scene}"
const BUILDINGS_PARENT := "{cfg.buildings_parent}"
const ROADS_PATH := "{cfg.subviewport_parent}/{cfg.roads_node_name}"
const MAX_SNAP := {cfg.entrance_spur_max_m + 20.0}


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar scene := load(CAMPUS_SCENE) as PackedScene
\tif scene == null:
\t\tpush_error("Could not load %s" % CAMPUS_SCENE)
\t\tquit(1)
\t\treturn
\tvar root := scene.instantiate()
\tif root == null:
\t\tpush_error("Could not instantiate %s" % CAMPUS_SCENE)
\t\tquit(1)
\t\treturn
\tget_root().add_child(root)
\tfor _i in range(6):
\t\tawait physics_frame

\tvar roads := root.get_node_or_null(NodePath(ROADS_PATH)) as Node3D
\tif roads == null:
\t\tpush_error("Campus roads node missing at %s" % ROADS_PATH)
\t\tquit(1)
\t\treturn
\tvar region := roads.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
\tif region == null or region.navigation_mesh == null or region.navigation_mesh.get_polygon_count() <= 0:
\t\tpush_error("Campus roads NavigationRegion3D not baked")
\t\tquit(1)
\t\treturn
\t# Build a dedicated single-region map from the roads mesh, exactly as the game's
\t# CampusMain._setup_campus_navigation_map does for the NPC, so this proves what
\t# the NPC will actually walk on (independent of shared-world sync timing).
\tvar map := NavigationServer3D.map_create()
\tNavigationServer3D.map_set_active(map, true)
\tNavigationServer3D.map_set_use_async_iterations(map, false)
\tNavigationServer3D.map_set_cell_size(map, region.navigation_mesh.cell_size)
\tNavigationServer3D.map_set_cell_height(map, region.navigation_mesh.cell_height)
\tvar nav_rid := NavigationServer3D.region_create()
\tNavigationServer3D.region_set_use_async_iterations(nav_rid, false)
\tNavigationServer3D.region_set_navigation_mesh(nav_rid, region.navigation_mesh)
\tNavigationServer3D.region_set_transform(nav_rid, region.global_transform)
\tNavigationServer3D.region_set_enabled(nav_rid, true)
\tNavigationServer3D.region_set_map(nav_rid, map)
\tfor _w in range(24):
\t\tNavigationServer3D.map_force_update(map)
\t\tawait physics_frame

\tvar buildings := root.get_node_or_null(NodePath(BUILDINGS_PARENT))
\tvar entrances := _entrances(buildings)
\tif entrances.size() < 2:
\t\tpush_error("Need >=2 building entrances to probe, found %d" % entrances.size())
\t\tquit(1)
\t\treturn

\tvar failures: Array[String] = []
\tvar names := entrances.keys()
\tfor i in range(names.size()):
\t\tfor j in range(i + 1, names.size()):
\t\t\tvar a: Vector3 = entrances[names[i]]
\t\t\tvar b: Vector3 = entrances[names[j]]
\t\t\tvar from := NavigationServer3D.map_get_closest_point(map, a)
\t\t\tvar to := NavigationServer3D.map_get_closest_point(map, b)
\t\t\tvar snap_a := from.distance_to(a)
\t\t\tvar snap_b := to.distance_to(b)
\t\t\tvar path := NavigationServer3D.map_get_path(map, from, to, true)
\t\t\tvar span := 0.0
\t\t\tif path.size() >= 2:
\t\t\t\tspan = path[0].distance_to(path[path.size() - 1])
\t\t\tif snap_a > MAX_SNAP or snap_b > MAX_SNAP:
\t\t\t\tfailures.append("%s<->%s snap too far (%.1f / %.1f)" % [names[i], names[j], snap_a, snap_b])
\t\t\telif path.size() < 2 or span < a.distance_to(b) * 0.4:
\t\t\t\tfailures.append("%s<->%s no road path (points=%d span=%.1f)" % [names[i], names[j], path.size(), span])
\t\t\telse:
\t\t\t\tprint("  %s -> %s : points=%d span=%.1f  OK" % [names[i], names[j], path.size(), span])

\troot.queue_free()
\tawait process_frame
\tif not failures.is_empty():
\t\tfor failure in failures:
\t\t\tpush_error(failure)
\t\tquit(1)
\t\treturn
\tprint("CAMPUS ROADS NAV OK")
\tquit(0)


func _entrances(buildings: Node) -> Dictionary:
\tvar result := {{}}
\tif buildings == null:
\t\treturn result
\tfor child in buildings.get_children():
\t\tvar entrance := child.get_node_or_null("Entrance") as Node3D
\t\tif entrance != null:
\t\t\tresult[str(child.name)] = entrance.global_position
\treturn result
"""


def _campus_optimal_line_probe_script(cfg) -> str:
    return f"""extends SceneTree

# Validates CampusMain._precomputed_campus_waypoints: for every shipped building
# pair the drawn campus line must follow the MapsIndoors-optimal polyline, not the
# merged-navmesh detour (which looped ~270u off-axis at ~3.9x the straight line).

const CAMPUS_SCENE := "{cfg.campus_scene}"
const ROUTES_JSON := "res://Assets/Campus/campus_building_routes.json"


func _init() -> void:
\tcall_deferred("_run")


func _run() -> void:
\tvar scene := load(CAMPUS_SCENE) as PackedScene
\tif scene == null:
\t\tpush_error("Could not load %s" % CAMPUS_SCENE)
\t\tquit(1)
\t\treturn
\tvar root := scene.instantiate()
\tget_root().add_child(root)
\tfor _i in range(30):
\t\tawait physics_frame

\tvar pairs := []
\tif FileAccess.file_exists(ROUTES_JSON):
\t\tvar file := FileAccess.open(ROUTES_JSON, FileAccess.READ)
\t\tvar parsed = JSON.parse_string(file.get_as_text())
\t\tfile.close()
\t\tif typeof(parsed) == TYPE_DICTIONARY:
\t\t\tfor key in parsed.keys():
\t\t\t\tvar ab: PackedStringArray = String(key).split("|")
\t\t\t\tif ab.size() == 2:
\t\t\t\t\tpairs.append([ab[0], ab[1]])
\tif pairs.is_empty():
\t\tpush_error("no precomputed building routes to probe (%s)" % ROUTES_JSON)
\t\tquit(1)
\t\treturn

\tvar dto := load("res://Scripts/shared/dto.gd")
\tvar failures := 0
\tvar worst_ratio := 0.0
\tvar worst_dev := 0.0
\tfor pair in pairs:
\t\tvar step = dto.MacroStep.new("campus_leg")
\t\tstep.from_building_id = pair[0]
\t\tstep.to_building_id = pair[1]
\t\tvar nav_start: Vector3 = root._get_building_entrance_position(pair[0])
\t\tvar nav_end: Vector3 = root._get_building_entrance_position(pair[1])
\t\tif nav_start.distance_to(nav_end) < 1.0:
\t\t\tcontinue
\t\tvar wp: PackedVector3Array = root._precomputed_campus_waypoints(step, nav_start, nav_end)
\t\tif wp.size() < 2:
\t\t\tpush_error("no precomputed waypoints for %s->%s" % [pair[0], pair[1]])
\t\t\tfailures += 1
\t\t\tcontinue
\t\tvar length := 0.0
\t\tvar max_z := -1.0e9
\t\tvar min_z := 1.0e9
\t\tvar max_x := -1.0e9
\t\tvar min_x := 1.0e9
\t\tfor i in range(wp.size()):
\t\t\tif i > 0:
\t\t\t\tlength += wp[i - 1].distance_to(wp[i])
\t\t\tmax_z = max(max_z, wp[i].z)
\t\t\tmin_z = min(min_z, wp[i].z)
\t\t\tmax_x = max(max_x, wp[i].x)
\t\t\tmin_x = min(min_x, wp[i].x)
\t\tvar straight: float = nav_start.distance_to(nav_end)
\t\tvar ratio: float = length / straight
\t\tvar dev_z: float = max(max_z - max(nav_start.z, nav_end.z), min(nav_start.z, nav_end.z) - min_z)
\t\tvar dev_x: float = max(max_x - max(nav_start.x, nav_end.x), min(nav_start.x, nav_end.x) - min_x)
\t\tvar dev: float = max(dev_z, dev_x)
\t\tworst_ratio = max(worst_ratio, ratio)
\t\tworst_dev = max(worst_dev, dev)
\t\tprint("%s->%s: pts=%d ratio=%.2fx off_corridor=%.0f" % [pair[0], pair[1], wp.size(), ratio, dev])
\t\tif ratio > 3.3 or dev > 160.0:
\t\t\tfailures += 1

\troot.queue_free()
\tawait process_frame
\tif failures == 0:
\t\tprint("CAMPUS OPTIMAL LINE OK (worst ratio=%.2fx off_corridor=%.0f)" % [worst_ratio, worst_dev])
\t\tquit(0)
\telse:
\t\tpush_error("campus optimal line FAILED for %d pair(s)" % failures)
\t\tquit(1)
"""

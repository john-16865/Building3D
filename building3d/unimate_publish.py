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


def _single_existing(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No {pattern} in {directory}")
    if len(matches) > 1:
        raise ValueError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]

extends SceneTree

const SCENE_PATH := "res://Scene/science.tscn"
const VISUAL_PATH := "res://Assets/Buildings/Science/science_visual.glb"
const NAV_PATH := "res://Assets/Buildings/Science/science_nav.glb"
const MANIFEST_PATH := "res://Assets/Buildings/Science/science_manifest.json"

var _stats := {}

func _init() -> void:
	_stats = {
		"scene_path": SCENE_PATH,
		"visual_path": VISUAL_PATH,
		"nav_path": NAV_PATH,
		"manifest_path": MANIFEST_PATH,
		"checks": {},
		"counts": {},
		"warnings": [],
		"errors": [],
	}
	_run()
	print(JSON.stringify(_stats, "\t"))
	quit(1 if _stats["errors"].size() > 0 else 0)


func _run() -> void:
	var manifest := _load_json(MANIFEST_PATH)
	if manifest.is_empty():
		_stats["errors"].append("Manifest did not load or was empty.")
	else:
		_stats["checks"]["manifest_loads"] = true
		_stats["counts"]["manifest_floors"] = _array_count(manifest.get("floors", []))
		_stats["counts"]["manifest_rooms"] = _array_count(manifest.get("rooms", []))
		_stats["counts"]["manifest_portals"] = _array_count(manifest.get("portals", []))
		_stats["counts"]["manifest_external_doors"] = _array_count(manifest.get("external_doors", []))

	var visual_resource := load(VISUAL_PATH)
	_stats["checks"]["visual_glb_loads_as_packed_scene"] = visual_resource is PackedScene
	if visual_resource is PackedScene:
		var visual_root: Node = visual_resource.instantiate()
		_stats["counts"]["visual_mesh_instances"] = _count_type(visual_root, "MeshInstance3D")
		_stats["counts"]["visual_navigation_regions"] = _count_type(visual_root, "NavigationRegion3D")
		_stats["counts"]["visual_navigation_links"] = _count_type(visual_root, "NavigationLink3D")
		_stats["counts"]["visual_nodes_total"] = _count_nodes(visual_root)
		_stats["checks"]["visual_has_meshes"] = _stats["counts"]["visual_mesh_instances"] > 0
		visual_root.free()
	else:
		_stats["errors"].append("Visual GLB failed to load as PackedScene.")

	var nav_resource := load(NAV_PATH)
	_stats["checks"]["nav_glb_loads_as_packed_scene"] = nav_resource is PackedScene
	if nav_resource is PackedScene:
		var nav_root: Node = nav_resource.instantiate()
		_stats["counts"]["nav_mesh_instances"] = _count_type(nav_root, "MeshInstance3D")
		_stats["counts"]["nav_navigation_regions"] = _count_type(nav_root, "NavigationRegion3D")
		_stats["counts"]["nav_navigation_links"] = _count_type(nav_root, "NavigationLink3D")
		nav_root.free()
	else:
		_stats["errors"].append("Navigation GLB failed to load as PackedScene.")

	var scene_resource := load(SCENE_PATH)
	_stats["checks"]["science_scene_loads_as_packed_scene"] = scene_resource is PackedScene
	if not scene_resource is PackedScene:
		_stats["errors"].append("Science scene failed to load as PackedScene.")
		return

	var root: Node = scene_resource.instantiate()
	_stats["checks"]["science_scene_instantiates"] = root != null
	if root == null:
		_stats["errors"].append("Science scene failed to instantiate.")
		return

	_stats["counts"]["scene_nodes_total"] = _count_nodes(root)
	_stats["counts"]["scene_mesh_instances"] = _count_type(root, "MeshInstance3D")
	_stats["counts"]["scene_navigation_regions"] = _count_type(root, "NavigationRegion3D")
	_stats["counts"]["scene_navigation_links"] = _count_type(root, "NavigationLink3D")

	var floors: Node = root.get_node_or_null("Floors")
	if floors == null:
		_stats["errors"].append("Science scene is missing Floors node.")
	else:
		_stats["counts"]["scene_floor_nodes"] = floors.get_child_count()
		var room_anchor_count := 0
		var empty_nav_regions := 0
		var empty_nav_meshes := 0
		var nav_mesh_vertices := 0
		var nav_mesh_polygons := 0
		var floor_names := []
		for floor in floors.get_children():
			if "floor_name" in floor:
				floor_names.append(floor.floor_name)
			var rooms: Node = floor.get_node_or_null("Rooms")
			if rooms != null:
				room_anchor_count += rooms.get_child_count()
			var nav_region: Node = floor.get_node_or_null("NavigationRegion3D")
			if nav_region is NavigationRegion3D:
				if nav_region.navigation_mesh == null:
					empty_nav_regions += 1
				else:
					var nav_mesh: NavigationMesh = nav_region.navigation_mesh
					var vertices := nav_mesh.get_vertices()
					var polygon_count := nav_mesh.get_polygon_count()
					nav_mesh_vertices += vertices.size()
					nav_mesh_polygons += polygon_count
					if vertices.is_empty() or polygon_count == 0:
						empty_nav_meshes += 1
		_stats["counts"]["scene_room_anchor_nodes"] = room_anchor_count
		_stats["counts"]["scene_empty_navigation_regions"] = empty_nav_regions
		_stats["counts"]["scene_empty_navigation_meshes"] = empty_nav_meshes
		_stats["counts"]["scene_navigation_mesh_vertices"] = nav_mesh_vertices
		_stats["counts"]["scene_navigation_mesh_polygons"] = nav_mesh_polygons
		_stats["floor_names"] = floor_names
		if manifest.has("floors") and floors.get_child_count() != _array_count(manifest.get("floors", [])):
			_stats["errors"].append("Scene floor count does not match manifest floor count.")
		var expected_anchors := _array_count(manifest.get("rooms", [])) + _array_count(manifest.get("portals", [])) + _array_count(manifest.get("external_doors", []))
		_stats["counts"]["manifest_room_portal_and_door_anchors"] = expected_anchors
		if expected_anchors > 0 and room_anchor_count != expected_anchors:
			_stats["warnings"].append("Scene anchor count does not match manifest rooms + portals.")
		if empty_nav_regions > 0:
			_stats["errors"].append("NavigationRegion3D nodes exist without assigned NavigationMesh resources.")
		if empty_nav_meshes > 0:
			_stats["errors"].append("NavigationRegion3D nodes have empty NavigationMesh resources.")

	var visual_node: Node = root.get_node_or_null("BuildingMesh/Visual")
	_stats["checks"]["scene_has_buildingmesh_visual_instance"] = visual_node != null
	if visual_node == null:
		_stats["errors"].append("Science scene is missing BuildingMesh/Visual.")
	else:
		_stats["counts"]["scene_visual_child_mesh_instances"] = _count_type(visual_node, "MeshInstance3D")
	root.free()


func _load_json(path: String) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return {}
	var parsed = JSON.parse_string(file.get_as_text())
	return parsed if parsed is Dictionary else {}


func _array_count(value) -> int:
	if value is Array:
		return value.size()
	if value is Dictionary:
		return value.size()
	return 0


func _count_nodes(node: Node) -> int:
	var total := 1
	for child in node.get_children():
		total += _count_nodes(child)
	return total


func _count_type(node: Node, class_name_value: String) -> int:
	var total := 1 if node.is_class(class_name_value) else 0
	for child in node.get_children():
		total += _count_type(child, class_name_value)
	return total

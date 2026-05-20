extends SceneTree


func _init() -> void:
	for method in ClassDB.class_get_method_list("NavigationServer3D"):
		var name := str(method.get("name", ""))
		if "closest" in name or "path" in name or "region_get" in name:
			print(JSON.stringify(method))
	quit()

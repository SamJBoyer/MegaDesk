root: {
	members: {},
	hierarchy: {
		layers: []
	}
}

# Fields

- members: map of canvas_id → MegaDesk member metadata (`type: "megadesk"`, `node_name`, position, scale, data, …)
- hierarchy.layers: ordered layers with id, name, visible, locked, and children (member canvas_ids on that layer)

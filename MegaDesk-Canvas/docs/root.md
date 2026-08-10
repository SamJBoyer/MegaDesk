root: {
	members: {}
}

# Fields

- members: map of canvas_id → MegaDesk member metadata (`type: "megadesk"`, `node_name`, position, scale, data, …). Field details: [`Docs/node_protocol.md`](../../Docs/node_protocol.md) (*Canvas member persistence*).
- FEs are hosted as native `dpg.node` items inside a `node_editor` (see *Hosted shell* in that doc).

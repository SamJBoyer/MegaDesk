root: {
	members: {}
}

# Fields

- members: map of `member_id` → MegaDesk member metadata (`type: "megadesk"`, `node_name`, `position`, `parameters`, `data`, …). Field details: [`Docs/node_protocol.md`](../../Docs/node_protocol.md) (*Graph member persistence*).
- Graphs are plain `.json` files (default `Graphs/default.json`). `canvas.json` is gone; legacy `scale` / `parents` / `children` are not stored.
- FEs are hosted as native `dpg.node` items inside a `node_editor` (see *Hosted shell* in that doc).

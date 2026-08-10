<Terms>

MegaDesk canvas / Canvas: Dear PyGui `node_editor` board that discovers Node FEs via `MegaDesk.nodes`, hosts them as native `dpg.node` items, and owns Supervisor (BE started on launch; collapsible operator panel as chrome — not a Catalog node).

Nodes: Programs in MegaDesk that perform some useful action or utility. Nodes can have a front-end (FE) that can be dragged and 
dropped on the Canvas and/or a backend (BE) that runs some async task. Supervisor is Canvas infrastructure, not a Node.

Sub-gui: a name for the front-end GUI part of a Node that is dropped on the Canvas. 

Node_protocol: the protocol by which the Canvas discovers, setups, and tears down new nodes. Canonical doc: [`Docs/node_protocol.md`](node_protocol.md).

Supervisor: Canvas-owned process lifecycle manager under `MegaDesk-Canvas/supervisor/`. BE: `python -m supervisor` (bootstrap via `ensure_supervisor_running()`). FE: collapsible panel. Redis: streams on DB 0; singleton / alive / RUNNINGNODES on DB 1.

</Terms>

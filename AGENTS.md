Read /Docs and Docs/glossary for information about specific terms. For how Nodes are discovered, hosted, and launched (FE/BE), read Docs/node_protocol.md — it is the single authority (MegaDesk-Canvas/docs/plugins.md and parent_gui_class.md redirect there).

This project follows a structure. MegaDesk is the name of the entire project and is the name of the root repo. MegaDesk-Canvas is the folder that contains the code for running the canvas that integrates the Nodes. 

MegaDesk-contracts contains the code for the contracts that allow Nodes to integrate with the canvas. MegaDesk-contracts is installable. Contracts should be written in there. If a contract is modified or added make sure it is reflected in MegaDesk-contracts

Nodes is the folder that contains the Nodes. Some nodes are both Front-end and back-end. Always make sure you are placing the content in the appropriate place. 

----

We use different REDIS database for different levels of persistence. DB 0 is termporary and DB 1 is persistent. 




**DB 0 (ephemeral)** — streams / Plant default traffic:
- **LAUNCHREQUEST** — consume `node_endpoint` (+ `parameters`, currently always `""`); discover BE via `MegaDesk.nodes` → `BeSpec`; `Popen` with `MEGADESK_*` env
- **KILLREQUEST** — match `node_endpoint` + `unique_id`, graceful→force shutdown, `DEL` the RUNNINGNODES hash
- **NODEEXIT** — published on natural exit (metadata only; no log bodies)
- Plant `WORKORDER` / `LIVEHARNESS` / `FINISHED` also live here (`redis://localhost:6379/0`)

**DB 1 (persistent):**
- **GBD:SUPERVISOR:SINGLETON** — one-BE lock
- **GBD:SUPERVISOR:ALIVE** — heartbeat (TTL ~5s)
- **RUNNINGNODES:<unique_id>** — hash registry (`status`, PID, `log_path`, …)

---

When designing the GUIs pay attention to these constraints:
	
This GUIS are going to combined on a master canvas which will have many other GUIS.
Because of this, you must be conservative with the amount of space you use. 

<DO-NOT>
- add verbose text to the GUIS that explain the functions, this is a waste of space. 
- double-add titles as this is a waste of space. 
- add any new features not explicitly requested
- preallocate more than 2 spaces in scrollboxes and lists that will populate with elements. 
</DO-NOT>

<DO>
- Prefer sleek and simplistic GUIS
- Prefer compact style with thin margins 
- Allocate space to fill scrollboxes and lists with items AS they populate
</DO>





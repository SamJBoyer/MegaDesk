
Context: I am extremily displeased with the way the canvas looks. Its junky and the subguis fit horribly on the canvas. The lines and grid are also mismatched with the canvas and the subguis have issues rendering. I'm concidering removing unneccessary features to get a better core experience. 


Command: Dispatch subagents to remove the zooming feature and the layers feature. Then, rework the integration of the subguis into the canvas to encourage tighter integration. Take inspration for the ___workspace___ to show the proper level of integration. 

Constraints: maintain the node_protocol method of discovering FE nodes. Keep the icons in the node cataloge. 




## Supervisor BE (today)

Windows-side process lifecycle manager for node backends. Canvas infrastructure under `MegaDesk-Canvas/supervisor/` — **not** a Catalog / `MegaDesk.nodes` entry. Orchestrates work that is not attributable to any individual node: Redis provision, launch/kill, process registry, logging, and exit reaping.

- **BE:** canvas startup calls `megadesk_contracts.ensure_supervisor_running()` → `python -m supervisor` (not via `LAUNCHREQUEST`). Singleton on Redis DB 1 so only one BE can run.
- **FE:** collapsible panel via `supervisor.panel.build_supervisor_panel` (chrome, not a droppable node).
- **Logs:** `MegaDesk-Canvas/logs/<endpoint>/<unique_id>.log`; self-log `MegaDesk-Canvas/logs/supervisor/supervisor.log`.

### Redis provision
- Prefer an existing Redis on `localhost:6379`
- Else start Docker `gbd-redis` (`redis:7`) and optional `gbd-redis-insight` on port `5540`


I'll look up what MEGADESK_* env vars the Supervisor injects on BE launch.

MEGADESK_* is shorthand for three env vars Supervisor injects into the BE subprocess on launch (on top of the normal inherited environment):

Variable	Value	Purpose
MEGADESK_UNIQUE_ID
UUID for this launch
Identifies this instance (matches RUNNINGNODES:<unique_id>)
MEGADESK_NODE
BeSpec.name
Node nickname (e.g. plant)
MEGADESK_LOG_PATH
Absolute path to MegaDesk-Canvas/logs/<endpoint>/<unique_id>.log
Where stdout/stderr are already redirected
They’re so the child process can know which instance it is and where its log file lives — useful for configure_node_logging() and any BE logic that needs instance identity. They are not Redis keys or a separate protocol; just process env.
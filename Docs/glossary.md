<Terms>

MegaDesk-Canvas (MDC/Canvas): This program is the central command center and workspace for a MegaDesk project. The MDC creates the canvas the displays the Node's front ends and allows you to create new graphs. The MDC is also responsible for controlling the supervisor via panel. 

Nodes: Programs in MegaDesk that perform some useful action or utility. Nodes can have a front-end (FE) that can be dragged and dropped on the Canvas and/or a backend (BE) that are managed by the Supervisor. 

Sub-GUI: a name for the front-end GUI part of a Node that is dropped on the Canvas. 

Supervisor: Life-cycle manager that launches/terminates nodes and creates pipes between nodes used for logging. 

Graph: a file that stores which nodes go onto the Canvas, their positions, and the parameters they will 
start with on boot. 

Logs: Worktree-root session transcripts owned by the Supervisor (`Logs/CURRENT` points at the live folder). One `{node}.md` per node per Supervisor generation, plus `agent-{guid}.md` per MachineFactory sandbox run. Canvas does not rotate these. 

Factory: a Node that deploys agents — it reads orders, builds somewhere for an agent to work, starts a harness that carries instructions in and results out, and follows the run. **MachineFactory** does that in Docker sandboxes on this machine; **CloudFactory** does it on Cursor-hosted VMs. Both implement the same `AgentFactory` surface (launch / poll / cancel) so a graph can place an agent either way. See `Nodes/Factory/README.md`. 

Floor: MachineFactory's local repo farm (`Nodes/Factory/MachineFactory/Floor/`) — one bare clone per repo plus the `dev`, `agents` and `tickets/*` worktrees agents actually work in. 

AgentHandler: the harness that runs inside a MachineFactory sandbox: it reads its own run hash, loads the order, runs the Cursor agent, and publishes the outcome. There is no cloud equivalent — the SDK is the harness there. 

Run: one agent doing one order. A Factory tracks it by a **run key** (a sandbox guid locally, a `bc-` agent id in the cloud) and reports one of `draft`, `queued`, `running`, `finished`, `error`, `cancelled`, `startup_error`. 

</Terms>

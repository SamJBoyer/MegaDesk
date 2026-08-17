MissionControl: FE + BE node under `Nodes/MissionControl/`. FE is the Floor monitor panel. BE is MissionControlManager (`python -m MissionControlManager`), which consumes Redis `WORKORDER` (consumer group `mission_control`) and launches AgentHandler Docker sandboxes. Discovery key / `FeSpec.name` / `BeSpec.name`: `mission_control`.

AgentHandler: One-shot sandbox worker (`python -m AgentHandler`) started by MissionControlManager. Reads Redis hash `AGENTHANDLER:<GUID>`, loads ticket details from `WORKORDER` via `ticket_id`, runs a Cursor agent on the mounted Floor worktree, then publishes `FINISHED:<REPO>` and deletes the hash.

Floor: Git worktree farm under MissionControl (`Floor/<repo>/.bare` plus `wt/dev`, `wt/agents`, `wt/tickets/<ticket>`). Ticket branches are created from `agents`.

CodeScope: FE + BE node under `Nodes/CodeScope/`. FE takes a repo URL and asks questions; BE is CodeScopeManager (`python -m CodeScopeManager`), which consumes `CODEQ:ASK` (group `code_scope`) and answers on `CODEQ:ANSWER`. Discovery key: `code_scope`.

Scope: CodeScope's clone directory (`Scope/<repo>`, overridable with `SCOPE_ROOT`). Deliberately not the Floor: plain clones, no branches, disposable, and never shared with a writing agent.

VoiceDeck: FE + BE node under `Nodes/VoiceDeck/`. BE is VoiceDeckManager (`python -m VoiceDeckManager`), which owns the microphone, the speaker, and the OpenAI Realtime socket, and routes the model's tool calls to CodeScope and CloudDispatcher. Audio never crosses Redis — only `VOICE:CONTROL` and `VOICE:EVENT` text. Discovery key: `voice_deck`.

CloudDispatcher: FE + BE node under `Nodes/CloudDispatcher/`. BE is CloudDispatcherManager (`python -m CloudDispatcherManager`), which consumes `CLOUDORDER`, launches Cursor **cloud** agents, tracks `CLOUDRUN:<agent_id>` on DB 1, and publishes `CLOUDFINISHED`. No worktree and no merge step, so MergeManager has no role. Discovery key: `cloud_dispatcher`.

Cloud agent: A Cursor agent that runs on Cursor's own VM, clones from GitHub itself, pushes a branch, and optionally opens a PR. Ids are prefixed `bc-`. It sees the pushed remote, never your uncommitted work.

Draft: A `CLOUDDRAFT:<order_id>` hash on DB 1 holding a `CLOUDORDER` nobody has agreed to yet. VoiceDeck writes one instead of publishing, so a misheard sentence cannot open a pull request; one click in the CloudDispatcher FE turns it into the stream entry unchanged.

Node_protocol: the protocol by which the Canvas discovers, setups, and tears down new nodes. Canonical doc: [`Docs/node_protocol.md`](node_protocol.md).

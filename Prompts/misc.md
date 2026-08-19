
MissionControl designs the way an agent recieves instructions, including easy standardized instructions like "you're an engineer, write a detailed
git commit, etc." 

- prompt control
- giving agents instructions
- presenting a common remerge interface 

This is essentially the same idea as skills, but since skills are repo/user scoped they won't make it into my sandbox 
or won't be centralized. TO fix this we can have skills stored in megadesk and then the text is extracted and placed approprietly 
according to the live harness




MDSuperHarness. 
- Track token usage 
- Detect infinite loops
- Maintain a log of the agents conversation and save that somewhere on disk in MegaDesk. 
- Pause/stop/resume agents according to a human or as part of a work flow. For example we might have an agent stop before writing a git commit,
have a reviewer AI node remote into the work tree and review the code, and then if approved the agent picks back up to write the git commit. 
    1. 
    2. Alert the human if the agent has stopped to ask it a question. Text the user's phone with whatsapp 

Alerting the user that work is done done is tasked to a MegaDesk node or something IDK? 


MDCloudManager: 
- manages agents in the cloud 

MDLocalManager
- manages agents on the machine or in sandboxes 

When to use no sandbox: 
- we have hardware level integration that can't be sandboxed
- real-time performance that would be too badly impacted by the virtualization layer when passed to a sandbox. 

When to use a sandbox: 
- the environment is hard to set up like Unity with Unity MCP 
- we need to use local devices
- we want something fast and cheap 

When to use a cloud agent: 
- if a sandbox is appropriate but we're out of capacity on the machine
- we want to run task but also use this computer for like idk gaming or something
- We're using multi-repo 
- we're doing a really big operation that might spin up so many agents our machine couldn't handle it 



It's gated by the gate machie or checkpoints 

Workflow: 

Human writes some prompt either in git issues or [an MD node that pushes to GitIssues (like how the itag system worked)]
TicketDispatcher polls the git remote for the target tag and loads the issues onto the panel. 
- the user selects the model (and does that stuff mission control does like showing the features) 
- the user sends the ticket which gets collected by Manager and executed asyncronously 
- the user waits for the ticket to be done or to get an alert 
- the finished ticket appears in the MergeManager and the user verifies the changes and then merges them a target branch 

Improvements: 
- reviewing nodes
- human alert nodes 

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

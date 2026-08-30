<human>
MegaDesk is my personal work orchestration platform. It built it as an infinitely customizable dashboard where I make custom GUIs (called nodes) to design a visual workflow for anything. 

MegaDesk is written in 3 parts: 
MegaDesk-Canvas is a python gui that contains the infinite canvas that nodes are placed onto. An instance of a canvas is called a “graph”. MegaDesk-Canvas is a unified front-end to orchestrate anything. 
MegaDesk-Supervisor is a python back-end manager that controls the lifecycles of each node’s backend. This allows node’s to have asynchronous backends that don’t lag the entire Canvas. It also stops you from losing all your work if you fat finger the close button or switch graphs. Also in-control of logs via pipes from node's procs. 
MegaDesk-VoiceDeck is a native voice-agent that should allow agentic/voice to control MegaDesk-Canvas entirely. 

Design principles: 

Expressiveness is the priority. The dream is to take any arduous process and turn it into a little digital factory board. This means nodes are designed to be fairly source agnostic. This makes it easier to hijack existing nodes to serve new purposes and serves the “re-useability” purpose of modularity. It is, however, kind of a security vulnerability and makes dependency control a little more difficult. 

What is a node: 


A node is basically just a script. Almost anything can be a node, as long as it has these characteristics: 
Have a pyproject wrapper that contains MEGADESK metadata. This is how the Canvas discovers available nodes.
Have get_fe_spec(), get_be_spec(), get_tool_spec(). Methods that are called by the Canvas, Supervisor, or VoiceDeck to get setup/teardown instructions. 


Implementation of what the methods due is left to node-based implementation. 

Nodes I use:
- WorkDispatcher: probes a git remote for a target label and turns issues into tickets. Pressing the tickets publishes them as work-orders for factories to build agentically
- MachineFactory: deploys a an agent in a sandbox to execute a LangGraph. 
- CloudFactory: deploys a cloud agent with Cursor. 
- GraphScope: tracks sandbox agent's langgraph execution state and visualizes for the user 
- PrManager: pull request manager to quickly validate agent's work and merge. 
- AutoIntegrate: looks for merge conflicts and allows you to dispatch an agent to merge them via the factory pathway 

Nodes I made but don't use: 
- VisionBoard: lucid-chart-like sticky note white board. Currently needs more attention for data management 
- PrompImprover: takes a prompt and improves it. Currently sucks isn't helpful. This could probably be a cloud-agent and not a node. 
- Notepad: hidious design and poor data management
- CodeScope: actually function with voice deck, but should be a cloud agent not a node.

Planned nodes: 
- Ralph node: takes a local repo and sends agents to work on it until all issues are resolved. Works serially and no sandbox. Used when i'm lazy and just want to bang something out on a clean computer
- Orchestrator: takes a huge design list and breaks them down into smaller pieces, creates issues, and dispatches agents. Natural balancer node. 

</human>


Individual modules:
- MegaDesk canvas (`MegaDesk-Canvas/`): endless Dear PyGui canvas. Discovers FE nodes from `MegaDesk.nodes` via `get_fe_spec()`. Owns Supervisor (BE started on launch via `ensure_supervisor_running()`; collapsible operator panel) and VoiceDeck (FE chrome panel that always boots; BE launched once via `ensure_voice_deck_running()`). Dropping a node that also has a BE `XADD`s `SUPERVISOR:LAUNCHREQUEST` over Redis. Install with `pip install -e MegaDesk-Canvas` (after `pip install -e MegaDesk-Contracts`).
  - Supervisor (`MegaDesk-Canvas/supervisor/`): Canvas infrastructure — process lifecycle manager (`SUPERVISOR:LAUNCHREQUEST` / `SUPERVISOR:KILLREQUEST` / `RUNNINGNODES`; Redis DB 0 streams, DB 1 persistent keys). Not a Catalog node.
  - VoiceDeck panel (`MegaDesk-Canvas/voice_deck/`): Canvas chrome — push-to-talk / transcript strip. Not a Catalog node. The BE is still the `voice_deck` node.

- Nodes (`Nodes/`): productivity nodes installed via `pip install -e Nodes/<name>` (or `.[canvas]` where noted).
  - Factories (`Nodes/Factory/`): the two nodes that deploy agents. Same three verbs, same status words, same shape — one runs them here, one runs them in the cloud, and a graph should be able to choose without the choice changing what an agent can do. See [`Nodes/Factory/README.md`](Nodes/Factory/README.md).
    - MachineFactory: FE + BE node — Redis WORKORDER poller that launches Docker agent sandboxes against git worktrees, plus a Floor monitor panel. Hands back a pull-request URL.
    - CloudFactory: FE + BE node — follows Cursor **cloud** agents to a PR. Orders come from WorkDispatcher or VoiceDeck; this panel does not take a GitHub URL or issue text.
  - PRManager: FE-only Dear PyGui tool that lists open PRs whose merge-check `mergeable` status succeeded, pulls the tracked PR into a gitignored `Scope/`, and opens it in the browser, VS Code, or Cursor.
  - Human gates (`Nodes/HumanGates/`): the nodes where a person approves a step. See [`Nodes/HumanGates/README.md`](Nodes/HumanGates/README.md).
    - WorkDispatcher: FE-only tool that lists `agent-ready` GitHub issues and publishes a WORKORDER or a CLOUDORDER.
    - AutoIntegrate: FE-only tool that lists PRs whose merge-check `mergeable` status failed and orders a factory to fix that pull request on the PR's own branch.
  - CodeScope: FE + BE node — clones a repo into `Scope/` and answers questions about it with a warm local Cursor agent. The agent's own search and file-read tools are the retrieval layer, so there is no index, no embeddings, and no RAG pipeline.
  - VoiceDeck: BE node + canvas chrome panel — a speech-to-speech loop (OpenAI Realtime) that calls into CodeScope and CloudFactory by tool call. The FE always boots with the canvas; the BE keeps the `voice_deck` identity and is launched once. Audio stays inside the BE; only transcripts and control messages cross Redis. Needs `[audio]` (PortAudio) and `OPENAI_API_KEY`.
  - GraphScope: FE-only node that draws a live AgentHandler work-graph run from `GRAPHRUN:<guid>` and `GRAPHEVENT`.
  - Notepad: FE-only tabbed notepad. Notes are `.txt` files saved inside the node. VoiceDeck can create, append, and switch documents over `NOTEPAD:CMD`.
  - Sargent: FE + BE node — a chat window that rewrites a rough prompt via one OpenAI Chat Completions call. Needs `OPENAI_API_KEY`.

Shared contract: installable `megadesk-contracts` package in `MegaDesk-Contracts/` (`FeSpec` / `BeSpec`, entry-point discovery, Supervisor client). Redis IPC docs live alongside it (DB 0 ephemeral / DB 1 Supervisor persistent).

## Tests

`pytest` from the repo root. The suite boots the real canvas off-screen, drops nodes from the Catalog, fires the widgets' real callbacks, and asserts the Redis payloads and git state that result — so bugs at module seams get caught mechanically. It needs a desktop session (Dear PyGui renders; it is not headless) and a local Redis, and uses DBs 14/15. See [`Docs/integration_testing.md`](Docs/integration_testing.md).

## Environment

Project env is the conda env `MEGADESK` (Python 3.13, at `anaconda3/envs/MEGADESK`) with all packages pip-installed `-e`. `.vscode/settings.json` auto-activates it in every Cursor-integrated terminal (PowerShell, Command Prompt, Git Bash) — new terminals start with `(MEGADESK)` already active. To activate it manually elsewhere: `conda activate MEGADESK`. To reinstall all Nodes from scratch, run `python scripts/refresh_nodes.py` from the MEGADESK env.

## Scripts (`scripts/`)

- `refresh_nodes.py` — uninstalls every node under `Nodes/` (at any depth) from `MEGADESK` and reinstalls it editable, then verifies entry-point discovery. `scripts/refresh_nodes` is a thin wrapper that execs the same file.
- `down_nodes.py` — stops live Supervisor and managed BEs so locks do not interfere with local work.
- `push_dev_local.sh` — pushes local `dev` to `origin/dev`, the branch factories start work from.

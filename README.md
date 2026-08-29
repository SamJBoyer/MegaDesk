MegaDesk is my personal custom idea-to-product software development platform. It contains a program
called MegaDesk-Canvas, which is a canvas-like command center where the user designs various custom nodes
to infinitely stream-line their process.

Features:
- custom GUIs: I want the board where I do ideation and the board where I operate my agent factory to be the same. The goal is easy and fluid integration from the idea to execution. The canvas hosts custom interactive GUIs that manage agents, track tokens, log open issues, and other factory tools.
- persistence: It would be very annoying if fat-fingering the X button on my canvas nukes the entire factory. Therefore, we will use a persistence scheme where tasks that need persistence are managed by a dedicated process lifecycle manager called the supervisor. Closing MegaDesk-Canvas won't close the back-end nodes on purpose
- REDIS IPC: Different processes will communicate with each other using REDIS.


The goal of Megadraft is actually to be fairly loose and encourage expressibility and customizability necessarily over tight optimization. It's meant to be like an expression piece. Nodes will often use protocols like Redis and PubSub to talk to each other, which are not safe in the sense that they don't really care what's publishing the signal and also not storable. This is on purpose. We want this to be an app that can kind of be opened and closed pretty efficiently whenever you want, and that means that there's a lot of staling issues. To fix a lot of these staling issues, we make it pretty fire and forget, which is open-ended design, not the most secure or auditable by point, but that it's supposed to allow more flexibility. If let's say a node doesn't care about where the sender actually is, we can kind of hijack the behavior of some of the other nodes to create more expressive kind of middle ground. So we're trying to keep the common pathways open. That's why we don't use something like let's say LaneGraph for this entire thing. LaneGraph is awesome and is a tool that we use inside of it, but it's not flexible. It's too well-regulated of a framework to allow the kind of thing that we want. We don't want to be closing and opening it over and over and over again.





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
  - Notepad: FE-only tabbed notepad. Notes are `.txt` files in a GitHub repo clone and can be `git add`ed. VoiceDeck can create, append, and switch documents over `NOTEPAD:CMD`.
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

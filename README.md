MegaDesk is my personal custom idea-to-product software development platform. I really like Lucidchart and naturally find myself using boards and sticky notes to develop my ideas.
As I continuously refined my thoughts, I hoped the digital whiteboards would transform from loose sprawls of ideas into valuable context, testable needs, and actionable items agents could use.
Sadly, Lucid's MCP tool is pretty bad which makes it so the visual language of the board gets really manangled when passing to a local machine. Very frustrating. Also, Lucid integrations are expensive and locked behind a paywall. After seeing this I was like, well, how hard could it be to make my own digital whiteboard, so my hope is to create a Lucidchart-like brain-board combined with some great back-end agent management to have an all-in-one idea-to-software development environment.

- metadata engineering: Good documentation should help you write good prompts. However, LLMs understand natural language and not a bunch of stickies on a whiteboard, even if those are structured in a way a human would find coherent. The board should have features that compress visual documentation into clean prompts with <xml> tags, consistent spelling and terminology, etc. The goal is to extract every ounce of value from the idea.
- endless canvas: I don't like to be cluttered, but I do want to have my entire project visible on 1 board. I think it looks pretty and also helps me reason. I want a Lucidchart-style endless canvas.
- custom GUIs: I want the board where I do ideation and the board where I operate my agent factory to be the same. The goal is easy and fluid integration from the idea to execution. The canvas hosts custom interactive GUIs that manage agents, track tokens, log open issues, and other factory tools.
- sandboxing: I know what I'm doing so I don't run agents on my local computer because you better believe they're running with admin. All agents MUST be run in the cloud or on a sandbox. Managing this will be a significant undertaking.
- persistence: It would be very annoying if fat-fingering the X button on my canvas nukes the entire factory. Therefore, we will use a persistence scheme where tasks that need persistence are managed by a dedicated process lifecycle manager.
- REDIS IPC: Different processes will communicate with each other using REDIS.


Individual modules:
- MegaDesk canvas (`MegaDesk-Canvas/`): endless Dear PyGui canvas. Discovers FE nodes from `MegaDesk.nodes` via `get_exec_spec("FE")`. Owns Supervisor (BE started on launch via `ensure_supervisor_running()`; collapsible operator panel). Dropping a node that also has a BE `XADD`s `LAUNCHREQUEST` over Redis. Install with `pip install -e MegaDesk-Canvas` (after `pip install -e MegaDesk-contracts`).
  - Supervisor (`MegaDesk-Canvas/supervisor/`): Canvas infrastructure — process lifecycle manager (`LAUNCHREQUEST` / `KILLREQUEST` / `RUNNINGNODES`; Redis DB 0 streams, DB 1 persistent keys). Not a Catalog node.

- Nodes (`Nodes/`): productivity nodes installed via `pip install -e Nodes/<name>` (or `.[canvas]` where noted).
  - MissionControl: FE + BE node — Redis WORKORDER poller that launches Docker agent sandboxes, plus Floor monitor panel.
  - MergeManager: FE-only Dear PyGui tool that merges finished worktrees into the agents branch.
  - TicketDispatcher: FE-only Dear PyGui tool that lists `agent-ready` GitHub issues and publishes WORKORDERs.
  - CodeScope: FE + BE node — clones a repo into `Scope/` and answers questions about it with a warm local Cursor agent. The agent's own search and file-read tools are the retrieval layer, so there is no index, no embeddings, and no RAG pipeline.
  - VoiceDeck: FE + BE node — a speech-to-speech loop (OpenAI Realtime) that calls into CodeScope and CloudDispatcher by tool call. Audio stays inside the BE; only transcripts and control messages cross Redis. Needs `[audio]` (PortAudio) and `OPENAI_API_KEY`.
  - CloudDispatcher: FE + BE node — sends Cursor **cloud** agents to open documentation PRs, and surfaces their runs and links. Voice can only create drafts here; opening a PR takes a click.

Shared contract: installable `megadesk-contracts` package in `MegaDesk-contracts/` (`FeSpec` / `BeSpec`, entry-point discovery, Supervisor client). Redis IPC docs live alongside it (DB 0 ephemeral / DB 1 Supervisor persistent).

## Tests

`pytest` from the repo root. The suite boots the real canvas off-screen, drops nodes from the Catalog, fires the widgets' real callbacks, and asserts the Redis payloads and git state that result — so bugs at module seams get caught mechanically. It needs a desktop session (Dear PyGui renders; it is not headless) and a local Redis, and uses DB 15. See [`Docs/integration_testing.md`](Docs/integration_testing.md).

## Environment

Project env is the conda env `MEGADESK` (Python 3.13, at `anaconda3/envs/MEGADESK`) with all packages pip-installed `-e`. `.vscode/settings.json` auto-activates it in every Cursor-integrated terminal (PowerShell, Command Prompt, Git Bash) — new terminals start with `(MEGADESK)` already active. To activate it manually elsewhere: `conda activate MEGADESK`. To reinstall all Nodes from scratch, run `scripts/refresh_nodes` (Git Bash).

## Scripts (`scripts/`, run in git bash)

- `refresh_nodes.sh` — uninstalls every node under `Nodes/` from `MEGADESK` and reinstalls it editable, then verifies entry-point discovery.
- `push_dev_local.sh` — pushes local `dev` to `origin/dev` and merges it into `origin/agents`, the branch agents work from.

MegaDesk is my personal custom idea-to-product software development platform. I really like Lucidchart and naturally find myself using boards and sticky notes to develop my ideas.
As I continuously refined my thoughts, I hoped the digital whiteboards would transform from loose sprawls of ideas into valuable context, testable needs, and actionable items agents could use.
Sadly, Lucid's MCP tool is pretty bad which makes it so the visual language of the board gets really manangled when passing to a local machine. Very frustrating. Also, Lucid integrations are expensive and locked behind a paywall. After seeing this I was like, well, how hard could it be to make my own digital whiteboard, so my hope is to create a Lucidchart-like brain-board combined with some great back-end agent management to have an all-in-one idea-to-software development environment.

- metadata engineering: Good documentation should help you write good prompts. However, LLMs understand natural language and not a bunch of stickies on a whiteboard, even if those are structured in a way a human would find coherent. The board should have features that compress visual documentation into clean prompts with <xml> tags, consistent spelling and terminology, etc. The goal is to extract every ounce of value from the idea.
- endless canvas: I don't like to be cluttered, but I do want to have my entire project visible on 1 board. I think it looks pretty and also helps me reason. I want a Lucidchart-style endless canvas.
- custom GUIs: I want the board where I do ideation and the board where I operate my agent factory to be the same. The goal is easy and fluid integration from the idea to execution. The canvas will have custom interactive GUIs that manage agents, track tokens, log open issues, etc., while also supporting sticky notes, drawn hierarchies, etc.
- sandboxing: I know what I'm doing so I don't run agents on my local computer because you better believe they're running with admin. All agents MUST be run in the cloud or on a sandbox. Managing this will be a significant undertaking.
- persistence: It would be very annoying if fat-fingering the X button on my canvas nukes the entire factory. Therefore, we will use a persistence scheme where tasks that need persistence are managed by a dedicated process lifecycle manager.
- REDIS IPC: Different processes will communicate with each other using REDIS.


Individual modules:
- Executive: endless Dear PyGui canvas. Discovers FE nodes from `MegaDesk.nodes` via `get_exec_spec("FE")`. Dropping a node that also has a BE pings Supervisor over Redis.
- Supervisor: process lifecycle manager. Discovers BE nodes via `get_exec_spec("BE")` and launches them as managed subprocesses (`launch_node` / `stop_node`).
- Plant: BE-only node — Redis WORKORDER poller that launches Docker agent sandboxes.
- MergeManager: FE-only Dear PyGui tool that merges finished worktrees into the agents branch.
- TicketDispatcher: FE-only Dear PyGui tool that lists `agent-ready` GitHub issues and publishes WORKORDERs.

Shared contract: installable `megadesk` package (`FeSpec` / `BeSpec`, entry-point discovery, Supervisor client). See `c.md`.

When to use a FE/BE split?

1. Stateful things should
2. Procs that launch/manage other procs should
3. Stateless procs shouldn't

# Product Requirements Document: GeniusBrainDisease (GBD)

**Source:** Lucid iPool · My Documents / CLAM / ClamShell / GeniusBrainDisease  
**Document:** https://lucid.app/lucidchart/e985bda3-1a44-4cd4-a89d-7e3c86574263/edit  
**Scope boundary:** Only requirements inside the **Scoped** container  
**Status:** Complete (MVP scoped + fixture smoke-tested)  
**Reference fixtures:** test manifest `ol.yaml` · test node `TrialRunnerOL`  
**Fixture smoke test:** 2026-07-24 — `TrialRunnerOL` launched from Redis params matching `ol.yaml` (see §11)

---

## 1. Product summary

Build a **Windows-side commander backend** that uses **local Redis** and **custom Pub/Sub protocols** so a frontend can **register manifests**, **execute them as managed processes**, and **control lifecycle** of those processes — without attributing that control logic to any individual node.

> iPool vision note (Scoped): *“We are building a backend for our service that is going to use Redis, and custom protocols to allow a frontend to create processes which are managed on the backend.”*

**MVP proof:** register and execute `ol.yaml`, which launches the `TrialRunnerOL` node (`trial_runner.py`) with Redis-backed parameters. If that path works end-to-end, the MVP is done.

---

## 2. Scope map (containers are the authority)

```
Scoped
├── Tool stack
├── Interface and Protocols
│   ├── Manifest / node YAML model
│   ├── Pub/Sub protocol for register
│   └── Pub/Sub protocol for execute  (container title duplicates “register”; content is execute)
├── Redis
├── Execution Engine
└── MVP frontend

Out of Scope  ← explicitly excluded from this PRD
├── Misc ideas
└── (installer, GUI engine, canvas, config CRUD, orphan kill, full node protocol, …)
```

| Container | Role in product |
|-----------|-----------------|
| **Tool stack** | Constrains implementation choices |
| **Interface and Protocols** | Defines contracts (manifests, Pub/Sub channels, caller identity) |
| **Redis** | Placement + persistent node parameter store |
| **Execution Engine** | Commander behavior: start, register, execute, kill, process tracking |
| **MVP frontend** | Minimal operator UI that drives register / validate / execute |

---

## 3. Goals & non-goals

### Goals (Scoped)

1. Easy start of the Windows commander backend.
2. Redis always on localhost; reuse existing Redis if present, otherwise Docker (Redis + Redis Insights).
3. Manifest register → GUID stash (session-only) → execute → launch node processes with Redis-backed params.
4. Identity-scoped Pub/Sub request/ack for register and execute.
5. Global `KILLALL` plus graceful-then-forceful node shutdown; keep process references for lifecycle.
6. MVP UI: path entry, send/register, validate (red/green), scroll of validated manifests, execute selection, Redis + backend status lights.
7. Prove the pipeline with **`ol.yaml` → `TrialRunnerOL`**.

### Non-goals (Out of Scope — do not build in this PRD)

- Installer
- Config file save/load/modify/delete for session startup
- GUI engine (Python/C++), PyQt6 as product surface, integrated node-GUI canvas
- Supervisor launch contract for GUIs
- Force-kill of orphaned nodes as a dedicated feature
- Full standardized node create/kill/IO protocol beyond what Execution Engine + Redis already imply
- Open questions left unresolved in-chart (“How do we start nodes?”, GUI fit confusion)

---

## 4. Personas & primary use case

**Operator** uses the MVP frontend to point at a manifest path, register/validate it via Redis Pub/Sub, then execute a validated/registered manifest so the commander launches and manages the declared nodes.

**External caller** (any Pub/Sub publisher with a `caller_identity`) can register or execute without going through the UI, using the same ack channels.

**Primary MVP use case:** Operator registers `ol.yaml`, receives a GUID, executes that GUID, and the commander launches `TrialRunnerOL` with the parameters declared in the manifest.

---

## 5. Requirements by container

### 5.1 Tool stack (constraints)

| ID | Requirement |
|----|-------------|
| TS-1 | Primary language: **Python** |
| TS-2 | Environment tooling: **Anaconda** (project standard; UV may exist on the machine but is not the MVP runtime) |
| TS-3 | **Redis** is a required dependency |
| TS-4 | **Docker** is used when Redis must be provisioned |

---

### 5.2 Interface and Protocols

#### Manifest model

Canonical format is defined by the reference manifest `ol.yaml`.

| ID | Requirement |
|----|-------------|
| IP-1 | A **manifest** is a YAML file with a top-level `nodes:` list of one or more node entries |
| IP-2 | Each node entry is keyed by **node nickname/ID** and contains: (a) `directory` — node working directory, (b) `target` — CLI-callable launch file/script, (c) `parameters` — parameter map uploaded to Redis on execute |
| IP-3 | `directory` may use the `~NODES/` prefix; the commander resolves this to the project’s nodes root (for MVP: repo-local node folders such as `TrialRunnerOL/`) |
| IP-4 | On launch, the commander runs `target` with cwd = resolved `directory` |
| IP-5 | Node nickname/ID (map key under `nodes:`) is the Redis identity for that node’s parameter hash. Commander writes Redis key **`PARAMETERS_<nickname>`** on **DB 1** (CLAM `PyNode` contract; see RD-2) |

**Reference manifest (`ol.yaml`):**

```yaml
nodes:
- TrialRunnerOL:
    directory: ~NODES/TrialRunnerOL
    target: trial_runner.py
    parameters:
      experiment_path: AS_OL.txt
      target_dir: assets/elbow/track
      start: assets/elbow/halfway.json
      threshold: 1
      speed: 5
      frame_rate: 60
```

| Field | Value in reference | Meaning |
|-------|--------------------|---------|
| Node ID | `TrialRunnerOL` | Nickname used for Redis param hash + process registry |
| `directory` | `~NODES/TrialRunnerOL` | Resolves to the `TrialRunnerOL` node folder |
| `target` | `trial_runner.py` | Process entrypoint launched from that directory |
| `parameters` | see above | Written to Redis before/at launch |

#### Pub/Sub — register

| ID | Requirement |
|----|-------------|
| IP-R1 | Backend **subscribes** to `register_manifest:*` |
| IP-R2 | Caller has `caller_identity` |
| IP-R3 | Caller **subscribes** to `acknowledgements:(caller_identity)` |
| IP-R4 | Caller **publishes** `register_manifest:(caller_identity)` with message body = **manifest path** (e.g. path to `ol.yaml`), then waits on `acknowledgements:(caller_identity)` |
| IP-R5 | Ack payload: `SUCCESS <GUID>` or `FAILED` |
| IP-R6 | Returned **GUID** identifies the registered manifest for later execute |

#### Pub/Sub — execute

*(Same parent container family; chart title on this box still says “register” but stickies are execute — treat as execute protocol.)*

| ID | Requirement |
|----|-------------|
| IP-E1 | Backend **subscribes** to `execute_manifest:*` |
| IP-E2 | Same caller-identity / ack subscription pattern as register |
| IP-E3 | Caller **publishes** `execute_manifest:(caller_identity)` with message body = **GUID** from register, then waits on `acknowledgements:(caller_identity)` |
| IP-E4 | Ack payload: `SUCCESS` or `FAILED` |

---

### 5.3 Redis

| ID | Requirement |
|----|-------------|
| RD-1 | **Redis is always on localhost** (default port **6379**) |
| RD-2 | Persistent store of **node-specific** info: Redis **hash** of parameters at key **`PARAMETERS_<nickname>`** on **database 1** (realtime traffic uses DB 0; commander parameter writes use DB 1) |
| RD-3 | For the reference node, after successful execute of `ol.yaml`, Redis DB 1 must contain hash **`PARAMETERS_TrialRunnerOL`** with at least: `experiment_path`, `target_dir`, `start`, `threshold`, `speed`, `frame_rate` matching the manifest values (string values OK) |

---

### 5.4 Execution Engine (commander)

Causal flow implied by iPool edges:

```
Commander
  ├─→ provision/attach Redis (+ Insights)
  └─→ Pub/Sub event surface
        ├─→ register manifest → (GUID stash)
        ├─→ execute manifest → launch each node YAML → store process refs
        └─→ KILLALL → graceful then force shutdown → update process store
```

| ID | Requirement |
|----|-------------|
| EE-1 | Provide an **easy way to start** the backend |
| EE-2 | Backend is a **Windows commander**: owns cross-node responsibilities not attributable to individual nodes |
| EE-3 | Support Pub/Sub from **external sources** that trigger backend events |
| EE-4 | Redis provision: prefer **attach to active localhost Redis**; else **spin up Docker** with **Redis server + Redis Insights** |
| EE-5 | **register manifest**: validate → stash under new GUID → ack `SUCCESS <GUID>` or publish/return **FAILURE**; **do not persist manifests across server reset** (anti-stale) |
| EE-6 | **execute manifest**: validate incoming GUID → execute that registered manifest |
| EE-7 | On execute: for each node entry, **upload parameters to Redis** (`PARAMETERS_<nickname>` on DB 1), **launch target as a process**, **retain process reference** for lifecycle. Upload before launch so the node can read params at init |
| EE-8 | Maintain **storage for launched nodes** (process lifecycle registry) |
| EE-9 | On **`KILLALL`** published by **anything**, kill all managed nodes |
| EE-10 | Shutdown path: **graceful, then forceful** |
| EE-11 | Validation of a manifest includes: parseable YAML, non-empty `nodes:`, each entry has resolvable `directory`, existing `target`, and a `parameters` map |
| EE-12 | Launch contract (CLAM `PyNode`): `python <target> -n <nickname> -i localhost -p 6379` with cwd = resolved `directory`. Nickname **must** equal the manifest node ID so `PARAMETERS_<nickname>` resolves |
| EE-13 | Executing the reference GUID for `ol.yaml` must launch `python trial_runner.py -n TrialRunnerOL -i localhost -p 6379` with cwd at the resolved `TrialRunnerOL` directory, after writing `PARAMETERS_TrialRunnerOL` |

---

### 5.5 MVP frontend

Wireframe elements in-chart: path field, **send**, **validate**, **execute**, scroll list of manifests; plus status lights.

| ID | Requirement |
|----|-------------|
| FE-1 | Text bar for **manifest path** |
| FE-2 | **Send** publishes manifest path via Pub/Sub for **registration** |
| FE-3 | **Validate** sends manifests via Pub/Sub for **dry-run validation only** (same rules as EE-11; **no GUID stash**) → UI **red** if invalid, **green** if valid. Distinct from Send/register |
| FE-4 | **Scroll view** of manifests that have been validated |
| FE-5 | **Text-file database** of manifests that **populates** the scroll panel (seed: `data/manifests.txt` includes `ol.yaml`) |
| FE-6 | Select a manifest in the scroll list + **Execute** → send execute to backend |
| FE-7 | Status light: **local Redis connection confirmed** |
| FE-8 | Status light: **backend running confirmed** |
| FE-9 | Operator can enter the path to `ol.yaml`, validate it green, register it, and execute it from the scroll list |

---

## 6. Reference fixtures (test node + test manifest)

These fixtures are part of the PRD. Implementation and acceptance are measured against them.

### 6.1 Test node: `TrialRunnerOL`

| Property | Value |
|----------|-------|
| Nickname / ID | `TrialRunnerOL` |
| Location | `TrialRunnerOL/` (repo-local; `~NODES/TrialRunnerOL`) |
| Entrypoint | `trial_runner.py` |
| Launch CLI | `python trial_runner.py -n TrialRunnerOL -i localhost -p 6379` |
| Redis params | Hash `PARAMETERS_TrialRunnerOL` on DB 1 (written by commander before launch) |
| Role | Open-loop CLAM trial controller used as the **only required node** for MVP verification |
| Node-local config | `clamnode.yaml` (CLAM profile; commander does not need to parse it for MVP launch) |

MVP does **not** require TrialRunnerOL’s experiment to complete successfully as a science run. It requires the commander to **register**, **launch**, **parameterize via Redis**, **track**, and **kill** the process.

### 6.2 Test manifest: `ol.yaml`

| Property | Value |
|----------|-------|
| Path | repo-root `ol.yaml` |
| Declared nodes | exactly one: `TrialRunnerOL` |
| Launch | `directory` + `target` as above |
| Parameters | `experiment_path`, `target_dir`, `start`, `threshold`, `speed`, `frame_rate` |

### 6.3 Expected Redis parameter payload (after execute)

Key: **`PARAMETERS_TrialRunnerOL`** · Database: **1**

| Field | Expected |
|-------|----------|
| Redis key | `PARAMETERS_TrialRunnerOL` |
| DB | `1` |
| Type | Hash |

| Hash field | Value |
|------------|-------|
| `experiment_path` | `AS_OL.txt` |
| `target_dir` | `assets/elbow/track` |
| `start` | `assets/elbow/halfway.json` |
| `threshold` | `1` |
| `speed` | `5` |
| `frame_rate` | `60` |

*(Value types are Redis hash strings; semantic equality to the table is required.)*

### 6.4 Expected process registry entry (after execute)

| Field | Expected |
|-------|----------|
| Node ID | `TrialRunnerOL` |
| Command | `python trial_runner.py -n TrialRunnerOL -i localhost -p 6379` |
| cwd | resolved `TrialRunnerOL/` directory |
| Process | alive handle for the launched process |
| Cleared by | `KILLALL` (graceful → force) |

---

## 7. End-to-end flows (Scoped)

### 7.1 Register

1. Operator enters path to `ol.yaml` (FE-1) → Send (FE-2) **or** external caller uses IP-R*.
2. Backend receives `register_manifest:*` (IP-R1) with manifest path body (IP-R4).
3. Validate against IP-1–5 / EE-11 + GUID stash (EE-5); ack `SUCCESS <GUID>` / `FAILED` (IP-R5).
4. Manifests are **session-scoped** only (EE-5).

### 7.2 Validate (UI)

1. Validate action (FE-3) over Pub/Sub.
2. Scroll list updates from validated set / text DB (FE-4, FE-5).
3. Per-item red/green feedback (FE-3); `ol.yaml` must validate **green** (FE-9).

### 7.3 Execute (golden path)

1. Operator selects validated `ol.yaml` entry + Execute (FE-6) **or** caller uses IP-E* with GUID from register.
2. Backend validates GUID and runs manifest (EE-6).
3. For `TrialRunnerOL`: resolve `~NODES/TrialRunnerOL`, write `PARAMETERS_TrialRunnerOL` on DB 1, launch `python trial_runner.py -n TrialRunnerOL -i localhost -p 6379`, track handle (EE-7, EE-8, EE-12, EE-13, RD-2, RD-3).
4. Ack SUCCESS/FAILED (IP-E4).

### 7.4 Kill

1. Any publisher emits `KILLALL` (EE-9).
2. Commander shuts `TrialRunnerOL` (and any other managed nodes) graceful → force (EE-10); updates launched-node store (EE-8).

### 7.5 Redis readiness

1. On commander start: detect localhost Redis; else Docker Redis + Insights (EE-4, RD-1, TS-4).
2. Frontend Redis light reflects confirmed local connection (FE-7).

---

## 8. Acceptance criteria (MVP)

Golden path uses **`ol.yaml`** + **`TrialRunnerOL`**.

- [ ] Backend starts with a simple operator path (EE-1).
- [ ] Redis on localhost: attach existing or Docker Redis+Insights (EE-4, RD-1).
- [ ] Frontend Redis light and backend light reflect real connectivity/liveness (FE-7, FE-8).
- [ ] Register path to `ol.yaml` via Pub/Sub returns `SUCCESS <GUID>`; server reset clears that GUID (IP-R*, EE-5).
- [ ] `ol.yaml` validates green in the UI; invalid manifests validate red (FE-3, FE-9).
- [ ] Execute GUID for `ol.yaml` writes `PARAMETERS_TrialRunnerOL` on DB 1 then launches `python trial_runner.py -n TrialRunnerOL -i localhost -p 6379` from the resolved directory (EE-6–7, EE-12–13, IP-1–5).
- [ ] Redis hash `PARAMETERS_TrialRunnerOL` (DB 1) matches §6.3 parameter table (RD-2, RD-3).
- [ ] Process registry retains a live reference to the launched node (EE-8, §6.4).
- [ ] Validate colors and scroll + text-file list work as specified (FE-3–5); validate does not stash a GUID.
- [ ] Select + Execute from the scroll list drives backend execute for `ol.yaml` (FE-6, FE-9).
- [ ] `KILLALL` stops `TrialRunnerOL` with graceful-then-force and clears/updates the registry (EE-9–10).
- [ ] Nothing from **Out of Scope** / **Misc ideas** is required to call MVP done.
- [x] **Fixture readiness:** `ol.yaml` paths + Redis param contract + TrialRunnerOL launch CLI smoke-tested (§11).

---

## 9. Explicit exclusions (Out of Scope container)

Documented so they are not pulled in as implied work:

| Theme | Ideas parked out of scope |
|-------|---------------------------|
| Packaging | Installer |
| Session config | Config file for nodes+params; save/load/modify/delete configs |
| GUI platform | Native Python/C++ GUI engine, PyQt6, integrated canvas for node GUIs, GUI→supervisor launch hints |
| Ops extras | Dedicated orphan force-kill; full standardized node create/kill/Redis-IO protocol; “backend operational” check as a separate product beyond FE lights |
| Open questions | GUI placement uncertainty; “How do we start nodes?” — answered by EE-12/EE-13 (`python <target> -n <nickname> -i localhost -p 6379` after Redis param upload) |

---

## 10. Decisions

| Decision | Resolution |
|----------|------------|
| Manifest shape | `ol.yaml` schema (`nodes:` list of nickname → `directory` / `target` / `parameters`) |
| Register message body | Absolute or repo-relative **path** to the manifest file |
| Execute message body | **GUID** returned from register |
| MVP test node | `TrialRunnerOL` |
| MVP test manifest | `ol.yaml` |
| How nodes start (MVP) | Commander writes Redis params, then launches `python <target> -n <nickname> -i localhost -p 6379` in resolved `directory` |
| Env tooling | **Anaconda** (TS-2) |
| Validate vs register | **Distinct:** validate = dry-run of EE-11 rules, **no GUID stash**; Send/register stashes GUID (FE-3 vs FE-2 / EE-5) |
| Redis parameter key | **`PARAMETERS_<nickname>` on DB 1** (confirmed via CLAM `PyNode.get_parameters` + fixture smoke test) |
| Chart labeling | Execute Pub/Sub container in iPool still titled “register” — rename when convenient (non-blocking) |

---

## 11. Fixture smoke test (TrialRunnerOL)

Validates that the **reference node + Redis param contract** work on this machine before commander implementation. Not a substitute for §8 product acceptance.

### Procedure

1. Ensure localhost Redis on port **6379** (attach existing or Docker Redis; Insights optional).
2. Write hash `PARAMETERS_TrialRunnerOL` on **DB 1** with §6.3 fields from `ol.yaml`.
3. From `TrialRunnerOL/`, run:
   ```
   python -u trial_runner.py -n TrialRunnerOL -i localhost -p 6379
   ```
4. Confirm process starts, loads params (no “Parameters not found”), parses `AS_OL.txt`, and enters the experiment loop.
5. Terminate the process (simulates commander kill for fixture check only).

### Result (2026-07-24)

| Check | Result |
|-------|--------|
| Redis localhost:6379 | Pass (`redis-master` Docker container) |
| Write/read `PARAMETERS_TrialRunnerOL` DB 1 | Pass — fields match §6.3 |
| Asset paths in `ol.yaml` exist under `TrialRunnerOL/` | Pass (`AS_OL.txt`, `assets/elbow/track`, `assets/elbow/halfway.json`) |
| Launch CLI starts node | Pass — printed `started`, loaded blocks, `initializing`, `looping 0` |
| Science-run correctness | **Not required** for MVP (node logged `invalid args must be even` on duration kwargs; out of commander scope) |

**Implication for implementation:** commander execute path must use the §6.3 Redis key/DB and EE-12 CLI; bare nickname keys or launch without `-n/-i/-p` will fail against TrialRunnerOL / PyNode.

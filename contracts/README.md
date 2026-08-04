# Contracts

Central registry of **expected Redis package layouts** and IPC conventions used across MegaDesk modules.

These docs are the cross-module source of truth for key names, stream/hash field shapes, consumer groups, and Pub/Sub channel patterns. Implementation helpers live in module-local `redis_packets.py` files; when those diverge, treat this folder as the intended contract and reconcile the code.

## Layout

| Path | Contents |
|------|----------|
| [`redis/README.md`](redis/README.md) | Connection defaults, DB split, encoding rules, package index |
| [`redis/plant-pipeline.md`](redis/plant-pipeline.md) | `WORKORDER` → `LIVEHARNESS:<GUID>` → `FINISHED:<REPO>` |
| [`redis/supervisor.md`](redis/supervisor.md) | Supervisor / GBD Pub/Sub, `PARAMETERS_*`, commander alive key |

## Modules that speak Redis

| Module | Role |
|--------|------|
| **TicketDispatcher** | Publishes `WORKORDER` (`new_wt=true`) |
| **Plant / PlantManager** | Consumes `WORKORDER`; writes `LIVEHARNESS:<GUID>` |
| **Plant / LiveHarness** | Reads harness hash + `WORKORDER`; publishes `FINISHED:<REPO>` |
| **MergeManager** | Consumes `FINISHED:<REPO>`; may republish conflict `WORKORDER`s (`new_wt=false`) |
| **Supervisor** | Pub/Sub manifest control + node parameter hashes (separate DB) |

**Executive** does not use Redis today.

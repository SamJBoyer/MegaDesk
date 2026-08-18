# GraphScope

Watch a live AgentHandler run as a graph. GraphScope is FE-only: it scans
`GRAPHRUN:<guid>` hashes and reads `GRAPHEVENT`, then draws the nodes and edges
the run actually published.

It never consumes a stream. A missing hash still means "no run".

## Halves

| Half | What it does |
|------|--------------|
| FE (`graph_scope_frontend/app.py`) | Run list, live node status, recent events |
| BE | none |

## Wire

Defined once in `megadesk_contracts.wire.graph`. See
[`MegaDesk-contracts/redis/work-graph.md`](../../MegaDesk-contracts/redis/work-graph.md).

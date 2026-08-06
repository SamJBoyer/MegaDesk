We are using Dear PyGui to create a gui to visualize agent-ready tickets on a git remote and then allow the user to dispatch them to the next stage of our pipeline.

Dispatch uses Redis stream `WORKREQUEST` via `XADD` with fields:
- REPO
- URL
- ticket
- instructions
- model

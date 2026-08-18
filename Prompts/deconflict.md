<context>

We're using MegaDesk to build MegaDesk 

MegaDesk uses a Redis server as an ipc. When using sandboxed agents to work on MegaDesk, the changes agent's make to the redis database effect
the work of all other agents, not just the one in the sandbox. Agents are consistently messing with the work of other agents via Redis.

We need to deconflict

We can start giving agents seperate databases in redis for them to work within/test their changes.

We use 2 dbs inside of Redis. The 0th is ephermeral message bus and the 1st stores persistent data. We can make a policy that the
0 and 1 db are the live dbs, and every other db from 2+ can be used as a temporary db for agents testing MegaDesk. For example, db 2 and db 3 belong to Agent 1 where db 2 is ephermeral and db 3 is persistent... db 4 and db 5 belong to Agent 2.... etc.

Db 1 should keep a master log of which dbs are currently in use by agents, and agents should be responsible for marking when their runs are done and the db is free.
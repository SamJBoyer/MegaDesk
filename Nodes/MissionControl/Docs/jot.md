

MissionControl Manager connects to REDIS and subs to the REPOS channel. When a alert comes through with a git URL, mission_control manager checks for valid main, dev, and agent branches. If true, it creates the worktree format in Floor/. There is never a need to place these in anywhere other than Floor and should be no option to do so. 

MissionControl Manager subscribes to TICKETS, which will contain a name of a new feature, a git url, and set of instructions. When reciving a new ticket, MissionControl Manager will check the repo has a worktree in Floor, then will create a new worktree in Floor/REPO/wt/Ticekts/ticketname 

MissionControl Manager will spin up a container with AgentHandler with the new worktree path mounted. A GUID will be passed to live harness. MissionControlManager will make a hash with AgentHandler:GUID. AgentHandler will look for AgentHandler:GUID hash for its instructions. This has will contain the instructions. The AgentHandler will ONLY create an agent and work on the passed worktree. There is NO option for using a different work tree, etc. 
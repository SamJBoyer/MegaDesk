

Plant Manager connects to REDIS and subs to the REPOS channel. When a alert comes through with a git URL, plant manager checks for valid main, dev, and agent branches. If true, it creates the worktree format in Floor/. There is never a need to place these in anywhere other than Floor and should be no option to do so. 

Plant Manager subscribes to TICKETS, which will contain a name of a new feature, a git url, and set of instructions. When reciving a new ticket, Plant Manager will check the repo has a worktree in Floor, then will create a new worktree in Floor/REPO/wt/Ticekts/ticketname 

Plant Manager will spin up a container with LiveHarness with the new worktree path mounted. A GUID will be passed to live harness. PlantManager will make a hash with LiveHarness:GUID. LiveHarness will look for LiveHarness:GUID hash for its instructions. This has will contain the instructions. The LiveHarness will ONLY create an agent and work on the passed worktree. There is NO option for using a different work tree, etc. 
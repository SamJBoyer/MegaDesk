We are creating a centralized agent pool that creates git repos, makes work trees, creates docker sandboxes for agents to work

<top-level-flow>
1. MissionControlManager reads Redis stream WORKREQUEST and lists MERGEREQUEST:*
2. create bare + work trees under Floor/ when needed (clone from URL)
3. spin up a docker container and mount the work tree
4. run AgentHandler inside the container with a GUID; it reads AGENTHANDLER:GUID
5. AgentHandler pushes FINISHED:REPO, deletes the hash, and exits the container
</top-level-flow>


# 1:

1. MissionControlManager reads entries from the WORKREQUEST stream (consumer group `mission_control`).
   Each entry has fields REPO, URL, ticket, instructions, and model. If Floor does not
   have REPO, validate URL branches main/dev/agents and create Floor/<REPO>/.bare plus
   wt/dev, wt/agents, wt/tickets/. Reject invalid remotes.
2. For each WORKREQUEST entry, create a ticket worktree under wt/tickets/<ticket> as branch
   ticket/<ticket> from agents, write AGENTHANDLER:<GUID>, and start an AgentHandler container.
3. MissionControlManager also scans MERGEREQUEST:* lists. Each item has absolute path `wt`. Mount that
   existing worktree (do not create a new one) and instruct the agent to merge into agents.

Example repo: TESTER
Floor/
    TESTER/
        .bare/
        wt/
            dev/
                *TESTER working directory from dev branch
            agents/
                *TESTER working directory from agents branch
            tickets/
                1/
                2/
                3/

# 2

Create a docker file that has a pre-baked python dev environment and cursor cli. It should automatically run AgentHandler (see #3). It should
be able to mount a worktree path.

# 3

AgentHandler

AgentHandler is an agent manager. It receives GUID via env, loads Redis hash AGENTHANDLER:GUID for instructions,
uses Cursor SDK to create an agent bound to the mounted worktree, waits for completion, pushes FINISHED:REPO,
deletes the hash, then exits. There is no option to choose a different worktree.

AGENTHANDLER:GUID fields: ticket, instructions, model, status, error, finished_at, agent_id, run_id, repo, workpath, agent_dir

FINISHED:REPO fields: ticket, repo, instructions, status, error, finished_at, agent_id, run_id, workpath, agent_dir

# 4

AgentHandler reports when the task is done on FINISHED:REPO (and deletes AGENTHANDLER:GUID), then the container exits.

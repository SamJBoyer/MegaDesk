<context>

In our current implementation of AgentHandler we have a linear LangGraph. We want to add a node called wt rectifier. Basically, when the wt is mounted in the sandbox we also have to mount the bare. Without this sandboxes won't work when using git. But now the git wt path is relative, so we need to switch the git worktree to be correct as we enter the container and then again while we exit.

I think the agents do this, but it needs to be done deterministically. Sometimes this last step isn't done and the worktree is unmergable. 

</context>
<command>

Check if my assessment of the git worktree scenerio is accurate.

If so,

Implement this in node and make it a standard part of default work graph in LangGraph 

</command>
<context>

We're upgrading our AgentHandler to run graphs. Lets do this with LangGraph. We can start with our 
default work graph 

The Work Graph: 

Nodes:
- startup_node: script that connects to redis and gets instructions using the LiveAgent redis contract. 
- pathfinder_node: agentic node that looks at the worktree, looks for requirements, and clears the way to make sure the environment is setup to fufill the work that needs to be done. this agent can be dumb. 
- workhorse: the deploys an agent to work on the ticket. 
- git node: agentic node that looks at the git diff and writes a great commit message
- teardown_node: script that tears down the container 

Shape: this is a straight line. It goes startup->pathfinder->workhorse->git->teardown. 

We also want a node that visualizes the graph, including the LangGraph nodes and their shape. 

</context> 
<command>

Implement this as a massive upgrade over AgentHandler. The floor is yours to implement this in whatever way you like

<constraints>

Don't go spiraling into fixing unrelated parts of the code base. Just focus on upgrading AgentHandler to use LangGraph. 

</command>


MissionControl designs the way an agent recieves instructions, including easy standardized instructions like "you're an engineer, write a detailed
git commit, etc." 

- prompt control
- giving agents instructions
- presenting a common remerge interface 

This is essentially the same idea as skills, but since skills are repo/user scoped they won't make it into my sandbox 
or won't be centralized. TO fix this we can have skills stored in megadesk and then the text is extracted and placed approprietly 
according to the live harness




MDSuperHarness. 
- Track token usage 
- Detect infinite loops
- Maintain a log of the agents conversation and save that somewhere on disk in MegaDesk. 
- Pause/stop/resume agents according to a human or as part of a work flow. For example we might have an agent stop before writing a git commit,
have a reviewer AI node remote into the work tree and review the code, and then if approved the agent picks back up to write the git commit. 
    1. 
    2. Alert the human if the agent has stopped to ask it a question. Text the user's phone with whatsapp 

Alerting the user that work is done done is tasked to a MegaDesk node or something IDK? 


MDCloudManager: 
- manages agents in the cloud 

MDLocalManager
- manages agents on the machine or in sandboxes 

When to use no sandbox: 
- we have hardware level integration that can't be sandboxed
- real-time performance that would be too badly impacted by the virtualization layer when passed to a sandbox. 

When to use a sandbox: 
- the environment is hard to set up like Unity with Unity MCP 
- we need to use local devices
- we want something fast and cheap 

When to use a cloud agent: 
- if a sandbox is appropriate but we're out of capacity on the machine
- we want to run task but also use this computer for like idk gaming or something
- We're using multi-repo 
- we're doing a really big operation that might spin up so many agents our machine couldn't handle it 



It's gated by the gate machie or checkpoints 

Workflow: 

Human writes some prompt either in git issues or [an MD node that pushes to GitIssues (like how the itag system worked)]
TicketDispatcher polls the git remote for the target tag and loads the issues onto the panel. 
- the user selects the model (and does that stuff mission control does like showing the features) 
- the user sends the ticket which gets collected by Manager and executed asyncronously 
- the user waits for the ticket to be done or to get an alert 
- the finished ticket appears in the MergeManager and the user verifies the changes and then merges them a target branch 

Improvements: 
- reviewing nodes
- human alert nodes 


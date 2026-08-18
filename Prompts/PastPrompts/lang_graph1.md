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


<context>

We're transforming 

The supervisor panel in MegaDesk should be fixed to the right hand side in the same manner the catalog panel is fixed. Both the supervisor panel and catalog panel should be collapseable. 

The supervisor panel should now have 2 tabs: nopes and logs. The Nodes tab should have all of the current features, but the logs should be moved seperatly to a Logs tab so I can see in a much larger box. 

Check the wiring to ensure that clicking on a node in the canvas displays its logs in the supervisor panel

</context>
<command>

Implement the features discribed in context. 

</command>






</context>


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


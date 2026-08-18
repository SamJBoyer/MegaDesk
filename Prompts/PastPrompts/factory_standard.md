
<context>

We have a mission controller and a cloud_dispatcher nodes. These have a similar idea with one running 
agents in sandboxes and the other running agents in the cloud. We want to make a more cohesive design 
setup. 

Our ultimate goal is to have agents running in a graph to autonomously tackle difficult problems. In this graph, nodes will represent an agent running on local machines or the cloud. It's almost certain that we will want our future graph controller to distribute work across machines/cloud without having to worry about certain capabilities being artifically locked based on machine/cloud dynamics. Therefore, 
we should invest in a polymorphic local/cloud interface whenever possible so functions can be distributed
accross the graph regardless of if the agents live locally or on the cloud. 

Unless there is an explicit technical reason, a cloud node and machine node should be as  interchangeable as possible. 

Our current implementation of cloud_dispatcher versus MissionController does not reflect this cohesion. 
First, lets introduce the concept of a Factory. Factories are programs we use to control the deployement of agents. We can rename MissionController to MachineFactory (to reflect the idea it runs local agents) and cloud_dispatch to CloudFactory (to reflect the idea it runs cloud agents). Both factories have a similar set of goals. Both factories are responsible for reading work orders, creating a cloud/sandbox environment where the agent will work, then creating harness that will allow the LLM to recieve instructions and every other command. 

A critical difference is that the MachineFactory deploys agents to a local sandbox using a AgentHandler which is fully customizeable. A CloudFactory will have to use the cursor cloud agent SDK and has less control due to no fully customizeable harness. It will also probably take longer to use a cloud-agent in
a graph because we don't have REDIS's superfast speed for IPC. Regardless, the features should be shared
as much as possible, while still respecting the inmuteable differences of edge/cloud infrastructure. 

</context>
<command>

Standardize and rename cloud_dispatcher and MissionControl. You have free reign over how to re-organize 
the project to achieve this, including combining both as sub-folders under a single Factory node. The only constraint is that CloudFactory and MachineFactory should still be 2 sperate nodes with unique identities. 

Do not force both nodes to be something that doesn't make sense. Pay respect to the inherent differences
between edge/cloud infrastructure 

</command>
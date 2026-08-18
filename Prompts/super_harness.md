<context>

We run sandboxed agents from MachineFactory in side a docker sandbox. AgentHandler manages the agent's lifecycle once its in its own VM. I have no clue what the agent is doing and no ability to evaluate if its stuck. That lack of transparency makes it impossible to trust agents and debug them if they go wrong (which they are)

</context>
<command>

Implement an audit log for agents so they log their progress. For now this can go in the same area that logs already go into. 

</command>






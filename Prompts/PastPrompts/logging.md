


<logging>

we need to reorganize our logging system so its easier for humans and agents to diagnose problems. 

We need to move the Log directory to an easier to reach spot because:
1. Humans are having a hard time finding it
2. Agents in sandboxes have no way to acccess it

Its very hard to track the records across time: 

Logs unrelated to MegaDesk's current sesesion should go into an Archive folder. In this folder there should be more folders with a timestamp of when MegaDesk launched. 

Instead of making having seperate folders for each node, we will have a single folder for each session and the node source name in the log file. There should only be 1 log file per node. Here is a markup of how the file structure should look: 

<example>
Logs
- Active
    - ... {source_node}{timestamp}.md 
- Archive 
    - {launch_timestamp}
        - ... {source_node}{timestamp}.md 
</example>

How to trigger? Because files can't be moved if the backend is still connected. 


</logging>





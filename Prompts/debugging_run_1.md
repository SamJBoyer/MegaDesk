<context>

If you look at the diff, you can see a huge number of changes were just added as an cloud and voice integration. A powerful model worked on this for 2 hours. 
When it landed in my branch it was broken. We need to tighten the testing infrastructure so future agents don't waste my time shipping dysfunctional code. Here are some
problems related to the current bugs along with bugs I've noticed from previous sessions: 

<n=1, supervisor-issue>

The supervisor has a UI that says "alive or excited procs" which is very unhelpful. It should only say "alive procs" because I don't care about which procs have died.
There is no point of tracking dead procs in the supervisor. 

The supervisor consistently shows alive nodes that are actually dead. You can verify this by looking for python processes in the task manager and comparing that to the 
number of nodes reported running by the supervisor. I belive this is either because: 
- the supervisor doesn't actually poll the reportedly running nodes to see if the procs are actually live
- the supervisor is getting confused between the PID of the pythonshell and the underlying PID of the program

I belive we can fix this issue by
- forcing nodes to send a heartbeat package with their PID and status every 5 seconds via REDIS. 
- forcing the nodes to poll the redis database for a shutdown value which will shutdown the node if 1. This will also terminate the node if it can't reach the 
redis server via exception catching, giving us a second informal way to kill all nodes
- allowing the supervisor to poll the alive procs PID to see if they're actually alive and matching the reported alive procs with the heartbeat packages. We can also do 
quick investigation if the shell proc PID vs node proc PID is a problem. 

These fixes should be centralized so we can quickly apply them to each python node and update the policies and procedures if needed. Perhaps this can be a node python class, but you can choose the most elegant solution. 

We also want to be able to use the shutdown value / redis kill switch to shut down each node as part of tests. Whenever we make a modification to the supervisor or any nodes containing BEs, we should make sure to down the supervisor and all nodes. This stops a staling bug where the a node will be changed but won't be launched by MEGADESK
because there is a running node with the same name. 

</n=1, supervisor-issue>
<n=2, logs-issue>

The supervisor redirects logs to a address that seems determined by the contracts installable. This has the undesired effect of routing logs to other worktrees. This needs to be fixed so the logs always route relative to the running supervisor. 

</n=2, logs-issue>
<n=3, stale-nodes>

Whenever nodes changed be sure to run a script that uninstalls all nodes from the MEGADESK conda env, and then reinstalls all of them. Then, modify AGENTS.md to reflect this convention. The purpose of this is to stop me from getting errors due to stale nodes installed to my environment despite the availablity of newer code. This should 
be a script that can be reused. AGENTS.md should also tell agents to be sure to use the MEGADESK python environment.  

</n=3, stale-nodes>
<n=4, verticle-slices>

We need verticle slices that test the entire workflow. Here is a test for TicketDispatcher, Plant, and MergeManager: 

Verticle Slice: 
    1. Use the ticket dispatcher on https://github.com/SamJBoyer/SMOKETESTREPO.git. This is a test repo with a single issue with an agent-ready issue. 
    2. Operate the TicketDispatcher to dispatch the single issue to Plant
    3. Verify Plant has correctly started the sandbox and LiveAgent is working properly 
    4. Once the agent is finished, merge the modification using MergeManager. 

</n=4, verticle-slices>
<n=5, missing-bes>

We should split the get_exec_specs("mode") into get_fe_spec() and get_be_spec. I think that's cleaner, but you can override me. 

When a canvas is opened both the FEs and BEs can start. We can achieve this by having the fe_spec() method return a set of commands that megadesk will place 
into redis. these should be the arguments that trigger the backend to start in supervisor. 

</n=5, missing-bes>
<n=6, inconsistent-gitignore>

There seems to be an inconsistent gitignore where some pycached things are kept. Make sure this doesn't happen and remove any cached ones in the github. 
In code scope the scope should be ignored so it doesn't get included in git. 

</n=6, inconsistent-gitignore>

</context>
<command>



</command>


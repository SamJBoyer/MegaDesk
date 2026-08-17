

<context>

We're refining the canvas.json to be sleeker and re-usable. We are first going to replace the convention of calling the file that stores the information consumed by MegaDesk-Canvas from "canvas" to "graph". graph.json is an upgrade to canvas.json, and canvas.json is no longer used. The term "canvas" to refer to the ongoings in MegaDesk-Canvas is also replaced by the term "graph" 

MDC will create a new bar on the top that allows the user to select a graph.json to load. This bar 
will allow the user to edit, save, and delete graphs. Graphs don't have to be named graph.json and any json file could be a graph so we need error catching in case the user attempts to user a random json file as a graph. 

A graph fufills the same purpose as the deprecated canvas, but it doesn't have the scale, parent, children as these features are artifacts. Each member of a graph has a new property called "parameters". 
Parameters store abritrary kvps that are consumed by the nodes when they get specs. When a graph loads 
and instantiates each node, the Canvas should pass the saved parameters from the graph to the get exec function. How each node will consume the parameters is implemented at the node-level. 

If a nodes has an FE and a BE, when the FE is discovered it will return the build instructions for the FE, and it will return a packet of values that should be dropped into REDIS. This will include the EndPoint for the BE (like what we currently have) and the appropriate parameters. 

The appropriate parameters could be a sub-set or new of the parameters the FE uses. 

Example: 
- TicketDispatcher should handle take parameters "GIT_URL" which will allow the user to save the target
in the graph so they don't have to enter it everytime. 

How will the FE's know the difference between saveable parameters and standard values? Each node that uses parameters will have a parameters.yaml file with a list of recognized parameter names.

Example:

- Param1
- Param2
- Param3

It is up to the internal node's implementation to use these incoming parameters. 



</context>
<command>

Implement the graph bar in MDC. There should also be a button that pressed the current values in the 
subguis into the graph as parameters. 

Implement the upgrade from canvas to graphs. 

Implement the TicketDispatcher example to make use of the new graph. The parameter file as already been set up 

</command>

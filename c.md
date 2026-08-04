A node is a modular tool inside MegaDesk. Nodes can have a front-end (FE),
back-end (BE), or both. The FE is ALWAYS a Dear PyGUI and can be a subgui
inside the Executive canvas. 

Nodes use pyproject for discovery. 
Nodes MUST have a pyproject with the entry points
MegaDesk.nodes. Nodes are always installed to the MegaDesk conda env.

Nodes are discovered on startup by Executive from the entry points and 
get_exec_spec(mode) is called. All get_exec_specs should make sure the node can launch
(however this is done individually). Mode can either be FE or BE, indiciting if the 
module should return instructions for how to launch the FE or BE parts. 

If mode is FE, get_exec_specs returns a description of the node, an icon, a 
gui executable instruction telling the Executive how to launch the Dear PyGui as a subnode 
inside the canvas. The executive then pings the supervisor with the node name to indicate
it should launch the backend. The Supervisor discover's the node in the same way, but uses
the BE argument, which will return a executeable instruction which is executed as a subprocessess
and is managed by the supervisor. 



 
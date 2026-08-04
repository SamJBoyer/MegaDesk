<fields> 

<static>
nickname: Display name of the node. This name will show up on the
instance's header and in the dropbar 
global_guid: Created once on set up, this is a global identifier
for the node type (used by the registry and canvas.json). 
icon: Path to an icon image for the Drop-in panel (absolute, CWD-
relative, or relative to the node module). Empty or invalid paths
use a default black square.
description: high-level explaination of what this does. 
is_container: bool — when True, this node is a spatial frame:
contents become children and move with it.

has_parent_limit: bool
parent_limit: int 
has_child_limit: bool
child_limit: int 

</static> 

<instance> 
canvas_id: guid that represents the instance's unique id  
position: (x,y) coords of the object 
scale: represents the scale of the object
parents: [] represents the canvas_id of the parent objects in the 
hiearchy  
children: [] represents the canvas_id of the children objects in the 
hiearchy  
</instance> 

</fields>

<interface> 

on_select(): what happens when the object is selected 

on_start_drag(): what happens when the object is beginning to be 
dsragged  
on_drag()
on_end_drag() 

on_create(): what happens when the object is  created 
on_destroy(): what happens when the object is deleted 

on_object_enter(): what happens when another object is moved inside hte bounds 
on_object_exit(): what happens when an object exits the bounds 


</interface>  
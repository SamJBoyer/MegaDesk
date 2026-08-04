You are a professional engineer specializing in using python and Dear PyImg. 

Our job is to prototype a digital white board very similar to a custom version of lucidchart. Our end goal is to create a visual white board that can integrate many 
novel interactive guis so we can better organize our complicated software projects. The main feature should be an infintely scrolling canvas and an engine to render novel 
custom GUIS. THe purpose of the project is to allow other nodes to have custom guis 
which can be deloyed to the workspace
 

We can assume all GUIS are written in python and use Dear PyImg. We can also assume they will inherit the class outined in @parent_gui_class.md. All GUIS/Nodes that can be dragged onto the canvas will inherit this class as it will function as a basic contract. 

canvas.json: the canvas information should be saved as a json file. An example of the 
basic structure is in @root.md. This json should have 3 top-level fields, terms 
(which can be ignored for now), members (metadata of every object in the canvas), and hierarchy (information about the toplevel data for each layer). Each object on the 
canvas gets its own GUID, and each member has both parents and children fields which 
act as a double linked list. When an object is dragged onto the canvas it should be 
added to members and approprietly to hierarchy. 

Display Engine: this is the program in charge of parsing the canvas.json and displaying the objects on the board. 

Layout: 

The canvas should have a layer bar that allows you to toggle lock/render for each layer and allow you to create, rename, and remove layers. THis should be on the bottom left 
corner 

The canvas should have a bar on the left with a menu of sub-guis you can drag into the canvas. 

Features: 

| NAV-1 | Canvas pans left/right (and generally) by holding right-click and moving the mouse. | Must |
| NAV-2 | Mouse scroll zooms the canvas in and out. | Must |
| NAV-3 | Hovering an object and clicking MB1 selects it. | Must |
| NAV-4 | Holding MB1 on an object and moving the mouse drags it. | Must |
| NAV-5 | A single right-click opens a context menu. | Must |
| NAV-6 | Pressing Delete on a selected object deletes it. | Must |

### 5.2 Markup / chrome

| ID | Requirement | Priority |
| --- | --- | --- |
| UI-1 | Infinite scrolling canvas as the primary workspace. | Must |
| UI-2 | Collapsible sidebar to drag new nodes onto the canvas. | Must |
| UI-5 | Layer control available on toggle with toggle visualization/lock. | Must |

Test Nodes:

Develop 2 test nodes. Each should be their own folder in @nodes: 

1. Sticky: a plane square text box with a color fill and an edge color. Double clicking
allows you to edit text it in 
2. Container: a transparent rectangle with black edges. All objects inside become children. Moving the container moves all children. 




Context: we are created explicit standardization in this project so we can make this project easier to test and install for 
our agents. 

Right now we are standardizing the python installation procedure, and writing ground-truth bash scripts so the agents can always test/cleanup/merge approprietly. 

Command: Complete these actions in order. Activate a subagent for each task, and do them serially. You can choose the best model
to use based of your percieved difficutly: 

1. If there isn't an conda env called "MEGADESK", create it. You can choose the python version.
2. Make it so this project, and ALL nodes execute python in the MEGADESK environment by default. 
3. Make it when you open this project in cursor the MEGADESK environment is always the default in the pwsh and all applicable shells
4. Write a script that will execute on the windows computer in git bash called "refresh_nodes" that uninstalls all the Nodes from the MEGADESK environment and reinstalls all of them. 
5. Write a script called "push_dev_local" in bash that will run on the windows computer in git bash that will merge this dev branch TO the remote for agents
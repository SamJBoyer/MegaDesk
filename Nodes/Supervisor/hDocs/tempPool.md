<wants>

1. An easy way of starting the backend
2. Storage for launched nodes
3. Protocol that allows pub/sub from external sources to trigger an event on the backend
4. For each node YAML in the manifest, launch the target as a process and upload the parameters to redis. Then maintain a reference to this process for life cycle management.
5. A way of spinning up a Docker container with Redis server and REDIS insights. Will first look for an active one on Local Host and attatch to that, if it exists.
6. The backend that runs on the Windows side that Is responsible  That can't be attributable to individual nodes. This is like a commander program.
7. register manifest: Triggers the register manifest method, which will check that the manifest is valid and then stash it for later if so under a GUID. Manifests are not saved on the backend after the server resets to prevent staling. If successful will return SUCCESS <GUID> with the auto-generated GUID for that manifest.  If failed will publish FAILURE
8. A graceful and then forceful way to shut down nodes
9. Kill all nodes if event on KILLALL published by anything.
10. Execute manifest: Triggers the execute manifest method, which checks that the incoming GUID is valid and then executes that manifest.
11. A config file that saves nodes and parameters together to make session startup faster.
12. Easy way of saving, loading, modifying, and deleting config files
13. An installer.
14. A GUI engine that can support both Python and C++ on Windows natively
15. Guis must have a way of telling the supervisor how to launch them
16. An integrated canvas that can host the node GUIs
17. Checks that the backend is operational
18. A way of force-killing orphaned nodes
19. A node protocol that creates a standardized method for creating nodes, killing nodes, and a standard redis-based IO
20. A standard method on start for accessing the parameters and loading them
21. Send button, which will drop the manifest path using Pub/Sub for registration
22. Pressing the validate key will send all the manifests to Redis using Pub/Sub For validation and then will be red if not valid and green if valid
23. Two lights: 
One that indicates that Redis connection to a local server has been confirmed.
And one light that confirms that the backend is running
24. Text bar to enter the path of the manifest.
25. A scroll view of the manifests that have been validated
26. Text file database of all the manifests that will populate the scroll panel
27. Pressing one of the manifests in the scroll bar and then pressing execute will send it to the backend using the execute.
28. Backend subscribes to execute_manifest:*
29. caller has identity caller_identity. It subscribes to acknowledgements:(caller_identity). Caller publishes execute_manifest:(caller_identity) and then waits for acknowledgement:(caller_identity) from backend which can return SUCCESS or FAILED.
30. caller has identity caller_identity. It subscribes to acknowledgements:(caller_identity). Caller publishes register_manifest:(caller_identity) and then waits for acknowledgement:(caller_identity) from backend which can return SUCCESS <GUID.> or FAILED. This GUID is used to identify the registered manifest
31. Backend subscribes to register_manifest:*
32. Persistent database of node-specific information. Stores a hash of the parameters that nodes use Accessed using the Node nickname/ID.

</wants>
<questions>

Initialized as empty.

</questions>
<wonders>

Initialized as empty.

</wonders>

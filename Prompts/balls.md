<context> 

The supervisor's job is to manage the life-cycle of the back-end nodes. This is necessary because duplicate back-end nodes can cause strange behaviors and node's can silently die and need rebooting. The supervisor is consistently wrong about the number of alive procs running. 

I know this because I can see multiple background python procs running AND I can see their heartbeat packets being published, yet the supervisor still reports no alive procs. 

Also, whenever there are 2 duplicate nodes we get this error in MachineFactory:

2026-08-18 19:51:21,560 [ERROR] manager: Unhandled error in poll loop
Traceback (most recent call last):
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 488, in run_forever
    self.poll_once()
    ~~~~~~~~~~~~~~^^
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 467, in poll_once
    return self.poll_orders() + self.poll_runs()
           ~~~~~~~~~~~~~~~~^^
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 360, in poll_orders
    for message_id, fields in self._read_workorders(redis, pending=pending):
                              ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 320, in _read_workorders
    results = redis.xreadgroup(
        groupname=self.group,
    ...<2 lines>...
        count=WORKORDER_BATCH,
    )
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\commands\core.py", line 8067, in xreadgroup
    response = self.execute_command("XREADGROUP", *pieces, **options)
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 867, in execute_command
    return self._execute_command(*args, **options)
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 889, in _execute_command
    result = conn.retry.call_with_retry(
        lambda: self._send_command_parse_response(
    ...<3 lines>...
        with_failure_count=True,
    )
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\retry.py", line 120, in call_with_retry
    return do()
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 890, in <lambda>
    lambda: self._send_command_parse_response(
            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        conn, command_name, *args, **options
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ),
    ^
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 819, in _send_command_parse_response
    return self.parse_response(conn, command_name, **options)
           ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 935, in parse_response
    response = connection.read_response()
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\connection.py", line 1437, in read_response
    raise response
redis.exceptions.ResponseError: NOGROUP No such key 'WORKORDER' or consumer group 'machine_factory' in XREADGROUP with GROUP option
2026-08-18 19:51:22,562 [ERROR] manager: Unhandled error in poll loop
Traceback (most recent call last):
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 488, in run_forever
    self.poll_once()
    ~~~~~~~~~~~~~~^^
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 467, in poll_once
    return self.poll_orders() + self.poll_runs()
           ~~~~~~~~~~~~~~~~^^
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 360, in poll_orders
    for message_id, fields in self._read_workorders(redis, pending=pending):
                              ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\GoodSirington\Desktop\MegaDesk-WS\wt\dev\Nodes\Factory\MachineFactory\MachineFactoryManager\manager.py", line 320, in _read_workorders
    results = redis.xreadgroup(
        groupname=self.group,
    ...<2 lines>...
        count=WORKORDER_BATCH,
    )
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\commands\core.py", line 8067, in xreadgroup
    response = self.execute_command("XREADGROUP", *pieces, **options)
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 867, in execute_command
    return self._execute_command(*args, **options)
           ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 889, in _execute_command
    result = conn.retry.call_with_retry(
        lambda: self._send_command_parse_response(
    ...<3 lines>...
        with_failure_count=True,
    )
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\retry.py", line 120, in call_with_retry
    return do()
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 890, in <lambda>
    lambda: self._send_command_parse_response(
            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        conn, command_name, *args, **options
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ),
    ^
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 819, in _send_command_parse_response
    return self.parse_response(conn, command_name, **options)
           ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\client.py", line 935, in parse_response
    response = connection.read_response()
  File "C:\Users\GoodSirington\anaconda3\envs\MEGADESK\Lib\site-packages\redis\connection.py", line 1437, in read_response
    raise response
redis.exceptions.ResponseError: NOGROUP No such key 'WORKORDER' or consumer group 'machine_factory' in XREADGROUP with GROUP option

I think its because 2 MachineFactories are fighting over the same workorder and triggering a race condition that spawns this error. 

We have a general staling problem in Redis and there isn't any clear logic when to flush the database. In standard operation this might be deseriable, but when doing debugging its burdensome. 

</context>
<command>

Break this task up into multiple subagents that execute serially to tackle each task. 

In redis, the supervisor is inside a structure called GDB. This is a legacy name and needs to be removed. The KILLREQUEST and LAUNCHREQUEST can also be folded into the supervisor namespace (SUPERVISOR:{var})

Change the name "alive procs" to "running nodes" in the supervisor panel. Ensure the supervisor BE uses the heartbeat packets to check for running nodes. Scan for bugs that might be responsible for this discrepency. 

Add a flag called DEV_FLUSH_MODE which makes it so booting MegaDesk-Canvas always flushes DB0 and DB1 of REDIS so we're always opening with a fresh slate. 

</command> 
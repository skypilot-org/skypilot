# Params
Change these params in `./test.yaml`:
```yaml
...
  instance_type: p3.2xlarge
  zone: us-east-1a

...

_sky_spot:
  gap_seconds: 30
  strategy: strawman
  strategy_args:
    deadline_hours: 52
    task_duration_hours: 48
    restart_overhead_hours: 0
```

# Local mode
For quick testing.  Construct a SpotController locally and run things in a Python shell:
```python
from sky.spot import *
c = SpotController(1, './test.yaml', False)
c.run()
```
Example output:
```
I 04-27 21:55:55 controller.py:72] sky_spot config: {'strategy': 'strawman', 'strategy_args': {'deadline_hours': 52, 'task_duration_hours': 48, 'restart_overhead_hours': 0}}
I 04-27 21:55:55 controller.py:76] SkySpot detected, force setting retry_until_up to False
I 04-27 21:55:55 controller.py:92] SkyPilotControllerEnv: sky-26d8-zongheng-1, zone us-east-1a, gap_seconds 30
I 04-27 21:55:55 controller.py:188] Constructed strategy: strawman({"name": "strawman", "deadline": 187200, "task_duration": 172800, "restart_overhead": 0})
>>> c.run()
I 04-27 21:55:57 controller.py:131] ************** Spot available **************
I 04-27 21:55:57 controller.py:160] p3.2xlarge:1 Created reservation cr-03a738fd7016777ec in us-east-1a
I 04-27 21:55:58 controller.py:166] Cancelled reservation cr-03a738fd7016777ec in us-east-1a
I 04-27 21:55:58 controller.py:226] Strategy decision: next_cluster_type ClusterType.SPOT
I 04-27 21:55:58 controller.py:230] Current cluster_type ClusterType.NONE
I 04-27 21:56:34 controller.py:240] ******** making changes ********
I 04-27 21:56:34 controller.py:210] Launching use_spot=True
...
I 04-27 22:00:29 recovery_strategy.py:254] Spot cluster launched.
I 04-27 22:00:36 spot_utils.py:70] === Checking the job status... ===
I 04-27 22:00:38 spot_utils.py:76] Job status: JobStatus.SUCCEEDED
I 04-27 22:00:38 spot_utils.py:79] ==================================
I 04-27 22:00:41 controller.py:131] ************** Spot available **************
I 04-27 22:00:41 controller.py:160] p3.2xlarge:1 Created reservation cr-07442c89eca18f77d in us-east-1a
I 04-27 22:00:41 controller.py:166] Cancelled reservation cr-07442c89eca18f77d in us-east-1a
I 04-27 22:00:41 controller.py:259] Sleeping for 30 seconds
I 04-27 22:01:17 controller.py:226] Strategy decision: next_cluster_type ClusterType.SPOT
I 04-27 22:01:17 controller.py:230] Current cluster_type ClusterType.SPOT
I 04-27 22:01:23 controller.py:259] Sleeping for 30 seconds
I 04-27 22:01:59 controller.py:226] Strategy decision: next_cluster_type ClusterType.SPOT
I 04-27 22:01:59 controller.py:230] Current cluster_type ClusterType.SPOT
I 04-27 22:02:05 controller.py:259] Sleeping for 30 seconds
...
```

- [ ] FIXME: running 2 things at once throws:
```
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/Users/zongheng/Dropbox/workspace/riselab/sky-computing/sky/spot/controller.py", line 68, in __init__
    self._sky_spot_init()
  File "/Users/zongheng/Dropbox/workspace/riselab/sky-computing/sky/spot/controller.py", line 83, in _sky_spot_init
    class SkyPilotControllerEnv(sky_spot_env.Env):
  File "/Users/zongheng/anaconda/envs/py37/lib/python3.7/site-packages/sky_spot/env.py", line 33, in __init_subclass__
    assert cls.NAME not in cls.SUBCLASSES and cls.NAME != 'abstract', f'Name {cls.NAME} already exists'
AssertionError: Name skypilot-controller already exists
```

# Actual `spot launch`
This actually uses the spot controller on the cloud to run the Strategy/Env logic:
```bash
sky spot launch -y test.yaml
```

- [ ] FIXME: this seems to get stuck in SUBMITTED
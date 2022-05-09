import getpass
import uuid

import sky
from sky.backends import backend_utils

# (username, mac addr last 4 chars): for uniquefying users on shared-account
# cloud providers.
_user_and_mac = f'{getpass.getuser()}-{hex(uuid.getnode())[-4:]}'
cluster = f'test-multi-echo-{_user_and_mac}'

# Create the cluster.
with sky.Dag() as dag:
    cluster_resources = sky.Resources(sky.AWS(), accelerators={'K80': 1})
    task = sky.Task(num_nodes=2).set_resources(cluster_resources)
# `detach_run` will only detach the `run` command. The provision and `setup` are
# still blocking.
sky.launch(dag, cluster_name=cluster, detach_run=True)


# Submit multiple tasks in parallel to trigger queueing behaviors.
def _exec(i):
    with sky.Dag() as dag:
        task = sky.Task(run=f'echo {i}; sleep 5')
        resources = sky.Resources(accelerators={'K80': 0.5})
        task.set_resources(resources)
    sky.exec(dag, cluster_name=cluster, detach_run=True)


backend_utils.run_in_parallel(_exec, range(32))

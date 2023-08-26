import getpass
import hashlib
from multiprocessing import pool
import socket
import sys
from typing import Optional

import sky


def run(cluster: Optional[str] = None, cloud: Optional[str] = None):
    if cluster is None:
        # (username, last 4 chars of hash of hostname): for uniquefying users on
        # shared-account cloud providers.
        hostname_hash = hashlib.md5(
            socket.gethostname().encode()).hexdigest()[-4:]
        _user_and_host = f'{getpass.getuser()}-{hostname_hash}'
        cluster = f'test-multi-echo-{_user_and_host}'

    if cloud is None:
        cloud = 'gcp'
    cloud = sky.clouds.CLOUD_REGISTRY.from_str(cloud)

    # Create the cluster.
    with sky.Dag() as dag:
        cluster_resources = sky.Resources(cloud, accelerators={'T4': 1})
        task = sky.Task(num_nodes=2).set_resources(cluster_resources)
    # `detach_run` will only detach the `run` command. The provision and
    # `setup` are still blocking.
    sky.launch(dag, cluster_name=cluster, detach_run=True)

    # Submit multiple tasks in parallel to trigger queueing behaviors.
    def _exec(i):
        task = sky.Task(run=f'echo {i}; sleep 5')
        resources = sky.Resources(accelerators={'T4': 0.5})
        task.set_resources(resources)
        sky.exec(task, cluster_name=cluster, detach_run=True)

    with pool.ThreadPool(8) as p:
        list(p.imap(_exec, range(32)))


if __name__ == '__main__':
    cluster = None
    cloud = None
    if len(sys.argv) > 1:
        # For smoke test passing in a cluster name.
        cluster = sys.argv[1]
    if len(sys.argv) > 2:
        cloud = sys.argv[2]
    run(cluster, cloud)

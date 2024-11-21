import getpass
import hashlib
from multiprocessing import pool
import socket
import sys
import time
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
    cloud = sky.utils.registry.CLOUD_REGISTRY.from_str(cloud)

    # Create the cluster.
    with sky.Dag() as dag:
        cluster_resources = sky.Resources(cloud,
                                          accelerators={'T4': 1},
                                          use_spot=True)
        task = sky.Task(num_nodes=2).set_resources(cluster_resources)
    # `detach_run` will only detach the `run` command. The provision and
    # `setup` are still blocking.
    request_id = sky.launch(dag, cluster_name=cluster)
    sky.stream_and_get(request_id)
    # TODO(SKY-981): figure out why cluster is not ready immediately here.
    # Then remove sleep.
    time.sleep(5)

    # Submit multiple tasks in parallel to trigger queueing behaviors.
    def _exec(i):
        task = sky.Task(run=f'echo {i}; sleep 60')
        resources = sky.Resources(accelerators={'T4': 0.05})
        task.set_resources(resources)
        request_id = sky.exec(task, cluster_name=cluster)
        print(f'Submitting task {i}...request_id={request_id}')

    print('Submitting tasks...')
    with pool.ThreadPool(8) as p:
        list(p.imap(_exec, range(150)))


if __name__ == '__main__':
    cluster = None
    cloud = None
    if len(sys.argv) > 1:
        # For smoke test passing in a cluster name.
        cluster = sys.argv[1]
    if len(sys.argv) > 2:
        cloud = sys.argv[2]
    run(cluster, cloud)

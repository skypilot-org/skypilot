import getpass
import hashlib
from multiprocessing import pool
import socket
import sys
from typing import Optional

import sky


def run(cluster: Optional[str] = None,
        infra: Optional[str] = None,
        use_spot: bool = True):
    if cluster is None:
        # (username, last 4 chars of hash of hostname): for uniquefying users on
        # shared-account cloud providers.
        hostname_hash = hashlib.md5(
            socket.gethostname().encode()).hexdigest()[-4:]
        _user_and_host = f'{getpass.getuser()}-{hostname_hash}'
        cluster = f'test-multi-echo-{_user_and_host}'

    if infra is None:
        infra = 'gcp'

    # Create the cluster.
    with sky.Dag() as dag:
        cluster_resources = sky.Resources(
            infra=infra,
            # We need to set CPUs to 5+ so that the total number of RUNNING jobs
            # is not limited by the number of CPU cores (5 x 2 x 2 = 20).
            cpus='5+',
            accelerators={'T4': 1},
            use_spot=use_spot)
        task = sky.Task(num_nodes=2).set_resources(cluster_resources)
    request_id = sky.launch(dag, cluster_name=cluster)
    sky.stream_and_get(request_id)

    # Submit multiple tasks in parallel to trigger queueing behaviors.
    def _exec(i):
        # Each job takes 2-3 seconds to schedule, so we set the sleep time to
        # 70 seconds, to test if the job scheduler schedules job fast enough as
        # expected.
        task = sky.Task(run=f'echo {i}; sleep 70')
        # Set to 0.1 so that there can be 20 RUNNING jobs in parallel.
        resources = sky.Resources(accelerators={'T4': 0.1})
        task.set_resources(resources)
        request_id = sky.exec(task, cluster_name=cluster)
        print(f'Submitting task {i}...request_id={request_id}')
        return request_id

    print('Submitting tasks...')
    with pool.ThreadPool(8) as p:
        list(p.imap(_exec, range(150)))


if __name__ == '__main__':
    cluster = None
    infra = None
    use_spot = True
    if len(sys.argv) > 1:
        # For smoke test passing in a cluster name.
        cluster = sys.argv[1]
    if len(sys.argv) > 2:
        infra = sys.argv[2]
    if len(sys.argv) > 3:
        use_spot = sys.argv[3] == '1'
    run(cluster, infra, use_spot)

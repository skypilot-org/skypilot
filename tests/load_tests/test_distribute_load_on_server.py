"""
A wrapper of test_load_on_server.py to test the load handling capabilities of
the API server from multiple clients.

example usage:

python tests/load_tests/test_distribute_load_on_server.py -t 10 --cpus 4+ --url http://${HOST}:46580 --n 100 -r launch
"""

import argparse
import os

import sky
from sky import jobs as managed_jobs
from sky.utils import subprocess_utils

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-t',
                        type=int,
                        default=1,
                        help='Number of distributed clients to run')
    parser.add_argument('--cpus',
                        type=str,
                        default='4+',
                        help='Number of CPU cores to use for each client')
    parser.add_argument('--url',
                        type=str,
                        default='',
                        help='The URL of the API server to benchmark')
    parser.add_argument('--workdir',
                        type=str,
                        default='',
                        help='The path to local skypilot repo')
    args, remaining = parser.parse_known_args()

    if not args.url:
        raise ValueError('The URL of the API server to benchmark is required')
    workdir = args.workdir
    if not workdir:
        workdir = os.getcwd()

    resps = []
    for i in range(args.t):
        setup = 'cd /benchmark-workdir && pip install -e ".[kubernetes,aws,gcp]"'
        run = f'cd /benchmark-workdir && \
            sky api login -e {args.url} && \
            python tests/load_tests/test_load_on_server.py {remaining}'
        task = sky.Task(setup=setup,
                        run=run)
        task.set_resources(sky.Resources(cpus=args.cpus))
        file_mounts = {
            workdir: '/benchmark-workdir'
        }
        task.set_file_mounts(file_mounts)
        resps.append(managed_jobs.launch(task, f'benchmark-{i}'))
    subprocess_utils.run_in_parallel(sky.stream_and_get,
                                     resps)
"""Utils for launching baseline load balancers."""

import argparse
import multiprocessing
import shlex
import subprocess
from typing import Any, Dict, List, Optional

from sky.lbbench import gen_cmd
from sky.lbbench import utils

enabled_systems = [
    i for i in gen_cmd.enabled_systems if i < len(utils.single_lb_clusters)
]
describes = [gen_cmd.raw_describes[i] for i in enabled_systems]


def _prepare_sky_global_lb(st: Dict[str, Any],
                           ct: List[Dict[str, Any]],
                           cluster_name: str,
                           policy: str,
                           extra_envs: Optional[Dict[str, str]] = None) -> str:
    ip = None
    for c in ct:
        if c['name'].startswith('sky-serve-controller'):
            ip = c['handle'].head_ip
            break
    if ip is None:
        raise ValueError('SkyServe controller not found')
    controller_port = st['controller_port']
    envs = {
        'IP': ip,
        'PORT': controller_port,
        'POLICY': policy,
    }
    if extra_envs is not None:
        envs.update(extra_envs)
    envs_str = ' '.join([f'--env {k}={v}' for k, v in envs.items()])
    return (f'sky launch -c {cluster_name} -d --fast '
            '-y examples/serve/external-lb/global-sky-lb.yaml '
            f'{envs_str}')


def _prepare_sgl_cmd(st: Dict[str, Any], cluster_name: str) -> str:
    worker_urls = []
    for r in st['replica_info']:
        worker_urls.append(r['endpoint'])
    worker_urls_str = shlex.quote(' '.join(worker_urls))
    return (f'sky launch -c {cluster_name} -d --fast '
            '-y examples/serve/external-lb/router.yaml '
            f'--env WORKER_URLS={worker_urls_str}')


def _run_cmd(cmd: str):
    print(f'Running command: {cmd}')
    subprocess.run(cmd, shell=True, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-names', type=str, nargs='+', required=True)
    parser.add_argument('--yes', '-y', action='store_true', default=False)
    args = parser.parse_args()
    sns = args.service_names
    if len(sns) != len(enabled_systems):
        raise ValueError(f'Expected {len(enabled_systems)} service names for '
                         f'{", ".join(describes)}')
    print(sns)
    all_st = utils.sky_serve_status()
    ct = utils.sky_status()
    sn2st = {s['name']: s for s in all_st}

    def _get_single_cmd(idx: int) -> str:
        idx_in_sns = enabled_systems.index(idx)
        cluster_name = utils.single_lb_clusters[idx]
        if idx == 0:
            return _prepare_sgl_cmd(sn2st[sns[idx_in_sns]], cluster_name)
        if idx < len(utils.single_lb_clusters):
            policy, extra_envs = utils.single_lb_policy_and_extra_args[idx]
            assert policy is not None
            return _prepare_sky_global_lb(sn2st[sns[idx_in_sns]], ct,
                                          cluster_name, policy, extra_envs)
        raise ValueError(f'Invalid index: {idx}')

    commands = [_get_single_cmd(i) for i in enabled_systems]
    for cmd in commands:
        print(cmd)
    if not args.yes:
        input('Press Enter to launch LBs...')
    print('\nLaunching load balancers...\n')

    processes: List[multiprocessing.Process] = []
    for cmd in commands:
        process = multiprocessing.Process(target=_run_cmd, args=(cmd,))
        processes.append(process)
        process.start()
    for process in processes:
        process.join()

    logs = [
        f'sky logs {cluster_name}\n'
        for i, cluster_name in enumerate(utils.single_lb_clusters)
        if i in enabled_systems
    ]

    print('Both load balancers have been launched successfully. '
          'Check status with: \n'
          f'{"=" * 70}\n'
          f'{"".join(logs)}')


if __name__ == '__main__':
    main()

"""Utils for launching baseline load balancers."""

import argparse
import multiprocessing
import shlex
import subprocess
from typing import Any, Dict, List

from sky.lbbench import utils


def _prepare_sky_sgl_enhanced_cmd(st: Dict[str, Any],
                                  ct: List[Dict[str, Any]]) -> str:
    ip = None
    for c in ct:
        if c['name'].startswith('sky-serve-controller'):
            ip = c['handle'].head_ip
            break
    if ip is None:
        raise ValueError('SkyServe controller not found')
    controller_port = st['controller_port']
    return (f'sky launch -c {utils.sky_sgl_enhanced_cluster} -d --fast '
            '-y examples/serve/external-lb/global-sky-lb.yaml '
            f'--env IP={ip} --env PORT={controller_port}')


def _prepare_sgl_cmd(st: Dict[str, Any]) -> str:
    worker_urls = []
    for r in st['replica_info']:
        worker_urls.append(r['endpoint'])
    worker_urls_str = shlex.quote(' '.join(worker_urls))
    return (f'sky launch -c {utils.sgl_cluster} -d --fast '
            '-y examples/serve/external-lb/router.yaml '
            f'--env WORKER_URLS={worker_urls_str}')


def _run_cmd(cmd: str):
    print(f'Running command: {cmd}')
    subprocess.run(cmd, shell=True, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-names', type=str, nargs='+', required=True)
    args = parser.parse_args()
    sns = args.service_names
    if len(sns) != 2:
        raise ValueError('Expected 2 service names for '
                         'sgl, sky-sgl-enhanced')
    print(sns)
    all_st = utils.sky_serve_status()
    ct = utils.sky_status()
    sn2st = {s['name']: s for s in all_st}
    sgl_cmd = _prepare_sgl_cmd(sn2st[sns[0]])
    sky_sgl_enhanced_cmd = _prepare_sky_sgl_enhanced_cmd(sn2st[sns[1]], ct)
    print(sgl_cmd)
    print(sky_sgl_enhanced_cmd)
    input('Press Enter to launch LBs...')
    commands = [sky_sgl_enhanced_cmd, sgl_cmd]
    processes = []
    for cmd in commands:
        process = multiprocessing.Process(target=_run_cmd, args=(cmd,))
        processes.append(process)
        process.start()
    for process in processes:
        process.join()
    print('Both load balancers have been launched successfully. '
          'Check status with: \n'
          f'sky logs {utils.sgl_cluster}\n'
          f'sky logs {utils.sky_sgl_enhanced_cluster}\n')


if __name__ == '__main__':
    # py -m sky.lbbench.launch_lb --service-names c11 c12 c13
    main()

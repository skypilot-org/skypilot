"""Utils for generating benchmark commands."""
import argparse
import multiprocessing
import shlex
import subprocess
from typing import Any, Dict, List

import sky

sky_sgl_enhanced_cluster = 'sky-global'
sgl_cluster = 'router'
describes = ['sgl', 'sky_sgl_enhanced', 'sky']
presents = ['Baseline', 'Baseline\\n[Enhanced]', 'Ours']


def _sky_serve_status() -> List[Dict[str, Any]]:
    req = sky.serve.status(None)
    return sky.client.sdk.get(req)


def _sky_status() -> List[Dict[str, Any]]:
    req = sky.status()
    return sky.client.sdk.get(req)


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
    return (f'sky launch -c {sky_sgl_enhanced_cluster} -d '
            '-y examples/serve/external-lb/global-sky-lb.yaml '
            f'--env IP={ip} --env PORT={controller_port}')


def _prepare_sgl_cmd(st: Dict[str, Any]) -> str:
    worker_urls = []
    for r in st['replica_info']:
        worker_urls.append(r['endpoint'])
    worker_urls_str = shlex.quote(' '.join(worker_urls))
    return (f'sky launch -c {sgl_cluster} -d '
            '-y examples/serve/external-lb/router.yaml '
            f'--env WORKER_URLS={worker_urls_str}')


def _run_cmd(cmd: str):
    print(f'Running command: {cmd}')
    subprocess.run(cmd, shell=True, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--service-names', type=str, nargs='+', required=True)
    parser.add_argument('--skip-lb-launch', action='store_true')
    parser.add_argument('--exp-name', type=str, required=True)
    parser.add_argument('--extra-args', type=str, default='')
    args = parser.parse_args()
    sns = args.service_names
    if len(sns) != 3:
        raise ValueError('Expected 3 service names for '
                         'sky, sky-sgl-enhanced, sgl')
    print(sns)
    all_st = _sky_serve_status()
    ct = _sky_status()
    sn2st = {s['name']: s for s in all_st}
    for sn in sns:
        if sn not in sn2st:
            raise ValueError(f'Service {sn} not found')
    sky_sgl_enhanced_cmd = _prepare_sky_sgl_enhanced_cmd(sn2st[sns[1]], ct)
    sgl_cmd = _prepare_sgl_cmd(sn2st[sns[2]])
    print(sky_sgl_enhanced_cmd)
    print(sgl_cmd)
    if not args.skip_lb_launch:
        input('Press Enter to launch LBs...')
        commands = [sky_sgl_enhanced_cmd, sgl_cmd]
        processes = []
        for cmd in commands:
            process = multiprocessing.Process(target=_run_cmd, args=(cmd,))
            processes.append(process)
            process.start()
        for process in processes:
            process.join()
        print('Both load balancers have been launched successfully.')
    else:
        print('Skipping load balancer launch.')
    sky_sgl_enhanced_ip, sgl_ip = None, None
    for c in ct:
        if c['name'] == sky_sgl_enhanced_cluster:
            sky_sgl_enhanced_ip = c['handle'].head_ip
        elif c['name'] == sgl_cluster:
            sgl_ip = c['handle'].head_ip

    endpoints = [
        f'{sgl_ip}:9001', f'{sky_sgl_enhanced_ip}:9002',
        sn2st[sns[0]]['endpoint']
    ]
    print(endpoints)
    name_mapping = []
    ens = []
    for e, d, p in zip(endpoints, describes, presents):
        en = f'{args.exp_name}_{d}'
        ens.append(en)
        name_mapping.append(f'    \'{en}\': \'{p}\',')
        print(f'py -m sky.lbbench.bench --exp-name {en} --backend-url {e} '
              f'{args.extra_args}')
    print('=' * 30)
    for en in ens:
        print(f'    \'{en}\',')
    print('=' * 30)
    for nm in name_mapping:
        print(nm)


if __name__ == '__main__':
    # py -m sky.lbbench.gen_cmd --service-names b1 b2 b3  --skip-lb-launch --exp-name arena_syn_r3_c2000_u250_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 250' # pylint: disable=line-too-long
    main()

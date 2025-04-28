"""Utils for generating benchmark commands."""
import argparse
import collections
import json
import os
from pathlib import Path
import shlex
import tempfile
from typing import List, Optional

from sky.lbbench import utils

describes = [
    'sgl',
    'sky_sgl_enhanced',
    'sky_pull_pull',
    'sky_push_pull',
    'sky_push_push',
    'sky_pull_pull_rate_limit',
    'gke_gateway',
]
presents = [
    'Baseline',
    'Baseline\\n[Pull]',
    'Ours\\n[Pull/Stealing+Pull]',
    'Ours\\n[Push+Pull]',
    'Ours\\n[Push+Push]',
    'Ours\\n[Pull/RateLimit+Pull]',
    'GKE Gateway',
]

# Full list of systems indices - will be filtered by --run-systems
all_systems = [
    0,  # sgl router
    1,  # sgl router enhanced
    2,  # sky pulling in lb, pulling in replica, but workload stealing
    3,  # sky pushing in lb, pulling in replica
    4,  # sky pushing in lb, pushing in replica
    5,  # sky pulling in lb, pulling in replica, but rate limit
    6,  # gke
]

# Default to just running GKE
enabled_systems = [6]  # gke

ct = None
sn2st = None


def _get_head_ip_for_cluster(cluster: str) -> str:
    global ct
    if ct is None:
        ct = utils.sky_status()
    for c in ct:
        if c['name'] == cluster:
            return c['handle'].head_ip
    raise ValueError(f'Cluster {cluster} not found')


def _get_endpoint_for_traffic(index: int,
                              sns: List[str],
                              gke_endpoint: Optional[str] = None) -> str:
    if index == 6:  # GKE Gateway
        if gke_endpoint:
            if not gke_endpoint.startswith(('http://', 'https://')):
                return f'http://{gke_endpoint}'
            return gke_endpoint
        return 'http://34.117.239.237:80'  # Default GKE endpoint
    if index == 0:
        sgl_ip = _get_head_ip_for_cluster(utils.sgl_cluster)
        return f'{sgl_ip}:9001'
    if index == 1:
        sky_sgl_enhanced_ip = _get_head_ip_for_cluster(
            utils.sky_sgl_enhanced_cluster)
        return f'{sky_sgl_enhanced_ip}:9002'
    global sn2st
    if sn2st is None:
        sn2st = {s['name']: s for s in utils.sky_serve_status()}
    idx_in_sns = enabled_systems.index(index)
    if sns[idx_in_sns] not in sn2st:
        raise ValueError(f'Service {sns[idx_in_sns]} not found')
    return sn2st[sns[idx_in_sns]]['endpoint']


def _region_cluster_name(r: str) -> str:
    return f'llmc-{r}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--service-names',
        type=str,
        nargs='*',
        default=[],
        help='Service names for SkyPilot services (indices 2-5)')
    parser.add_argument('--exp-name', type=str, required=True)
    parser.add_argument('--extra-args', type=str, default='')
    parser.add_argument('--output-dir', type=str, default='@temp')
    parser.add_argument('--regions', type=str, default=None, nargs='+')
    parser.add_argument('--region-to-args', type=str, default=None)
    parser.add_argument('--gke-endpoint',
                        type=str,
                        default='34.117.239.237:80',
                        help='GKE Gateway endpoint (IP:port)')
    parser.add_argument(
        '--run-systems',
        type=int,
        nargs='+',
        default=all_systems,
        help='Indices of systems to run (default: all)')
    args = parser.parse_args()

    # Update enabled_systems based on --run-systems
    global enabled_systems, describes, presents
    enabled_systems = args.run_systems

    # Filter describes and presents based on enabled_systems
    describes = [describes[i] for i in enabled_systems]
    presents = [presents[i] for i in enabled_systems]

    # Only require service-names for SkyPilot systems (2-5)
    sky_systems_count = sum(1 for s in enabled_systems if 0 <= s <= 5)
    sns = args.service_names
    if len(sns) != sky_systems_count:
        if sky_systems_count > 0:
            raise ValueError(
                f'Expected {sky_systems_count} service names for SkyPilot')

    # If no SkyPilot services needed, use empty list for non-sky systems
    if sky_systems_count == 0:
        sns = [''] * len(enabled_systems)

    endpoints = [
        _get_endpoint_for_traffic(i, sns, args.gke_endpoint)
        for i in enabled_systems
    ]
    print(endpoints)
    if any('None' in e for e in endpoints):
        raise ValueError('Some endpoints are not found')

    name_mapping = []
    ens = []
    scps = []
    exp2backend = {}
    cn2cmds = collections.defaultdict(list)

    regions = None
    if args.regions is not None and args.region_to_args is not None:
        raise ValueError('--regions and --region-to-args cannot both be set')
    if args.regions is not None:
        regions = args.regions
    elif args.region_to_args is not None:
        regions = json.loads(args.region_to_args)

    if regions is None:
        raise ValueError('Regions must be set by --regions or --region-to-args')

    for r in regions:
        cluster = _region_cluster_name(r)
        cn2cmds[cluster].append(f'sky launch --region {r} -c {cluster} -y '
                                f'--fast --env CMD=whoami --env HF_TOKEN '
                                'examples/serve/external-lb/client.yaml')

    output = '~/bench_result'
    output_local = f'{args.output_dir}/result'
    signal_file = tempfile.NamedTemporaryFile(delete=False).name
    queue_status_file = tempfile.NamedTemporaryFile(delete=False).name

    for e, d, p in zip(endpoints, describes, presents):
        en = f'{args.exp_name}_{d}'
        ens.append(en)
        name_mapping.append(f'    \'{en}\': \'{p}\',')
        cmd = (f'python3 -m sky.lbbench.bench --exp-name {en} --backend-url '
               f'{e} {args.extra_args} --output-dir {output} -y')
        exp2backend[en] = e
        scps.append(f'mkdir -p {output_local}/metric/{en}')
        for r in regions:
            cluster = _region_cluster_name(r)
            region_cmd = f'{cmd} --seed {r}'
            if isinstance(regions, dict):
                region_cmd += f' {regions[r]}'
            # TODO(tian): Instead of --fast, how about sky launch once
            # and then sky exec?
            cn2cmds[cluster].append(
                f'sky exec {cluster} --detach-run '
                f'--env CMD={shlex.quote(region_cmd)} '
                '--env HF_TOKEN examples/serve/external-lb/client.yaml')
            output_remote = f'{cluster}:{output}'
            met = f'{output_remote}/metric/{en}.json'
            scps.append(f'scp {met} {output_local}'
                        f'/metric/{en}/{cluster}.json')

    status_puller_cmd = (
        f'python3 -m sky.lbbench.queue_fetcher -y '
        f'--exp2backend {shlex.quote(json.dumps(exp2backend))} '
        f'--output-dir {output_local} --signal-file {signal_file}')

    script_path = Path(output_local) / 'scripts' / f'{args.exp_name}.bash'
    script_path.parent.mkdir(parents=True, exist_ok=True)
    # Generate bash script for parallel execution
    cnt = 1
    # Sometimes we want to add another system but still under the same exp name.
    # In this case, we keep the old script.
    while script_path.exists():
        script_path = (Path(output_local) / 'scripts' /
                       f'{args.exp_name}_v{cnt}.bash')
        cnt += 1
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write('#!/usr/bin/env bash\n')
        f.write('set -euo pipefail\n\n')

        # Write region-specific launch functions
        f.write('# ---------- region-specific launch functions\n')
        for i, r in enumerate(regions):
            cluster = _region_cluster_name(r)
            region_safe = r.replace('-', '_')
            f.write(f'launch_{region_safe}() {{\n')
            f.write(f'  if sky status {cluster} | grep -q "UP"; then\n')
            f.write(f'    echo "Cluster {cluster} is already '
                    'UP, skipping launch"\n')
            f.write('  else\n')
            f.write(f'    {cn2cmds[cluster][0]}\n')
            f.write('  fi\n')
            f.write('}\n\n')

        # Write region-specific exec functions for each experiment
        f.write('# ---------- region-specific exec functions\n')
        for i, d in enumerate(describes):
            for r in regions:
                cluster = _region_cluster_name(r)
                region_safe = r.replace('-', '_')
                f.write(f'exec_{region_safe}_{d}() {{\n')
                # Each cluster has launch command at index 0,
                # then exec commands for each experiment
                cmd_idx = 1 + i  # Skip the launch command
                assert cmd_idx < len(cn2cmds[cluster])
                f.write(f'  {cn2cmds[cluster][cmd_idx]}\n')
                f.write('}\n\n')

        # Phase 0: Start the status puller in the background
        f.write('# ---------- phase 0: start status puller in background\n')
        f.write(f'{status_puller_cmd} > {queue_status_file} 2>&1 &\n')
        f.write('status_puller_pid=$!\n\n')

        # Phase 1: Launch clusters in parallel
        f.write(
            '# ---------- phase 1: launches in parallel, barrier via wait\n')
        for r in regions:
            region_safe = r.replace('-', '_')
            f.write(f'launch_{region_safe} &  pid_launch_{region_safe}=$!\n')

        f.write('\n# Wait for all launches to complete\n')
        for r in regions:
            region_safe = r.replace('-', '_')
            f.write(f'wait $pid_launch_{region_safe}\n')

        f.write('# Wait for queue status puller to initialize\n')
        f.write('echo "Waiting for queue status puller to initialize..."\n')
        f.write(f'echo "Check log file: tail -f {queue_status_file}"\n')
        f.write(
            'while ! grep -q "Pulling queue status\\|Skipping queue polling" '
            f'{queue_status_file}; do\n')
        f.write('  sleep 1\n')
        f.write('  echo -n "."\n')
        f.write('done\n')
        f.write('echo "Queue status puller is ready!"\n\n')

        # Phase 2: Execute benchmarks in parallel for each experiment
        for i, d in enumerate(describes):
            f.write(f'\n# ---------- phase 2.{i+1}: exec {d} in parallel\n')
            for r in regions:
                region_safe = r.replace('-', '_')
                f.write(f'exec_{region_safe}_{d} &  '
                        f'pid_exec_{region_safe}_{d}=$!\n')

            f.write('\n# Wait for all execs to complete\n')
            for r in regions:
                region_safe = r.replace('-', '_')
                f.write(f'wait $pid_exec_{region_safe}_{d}\n')

        # Phase 3: Wait for all queues to be empty
        f.write('\n# ---------- phase 3: wait for all queues to be empty\n')
        f.write('wait_for_empty_queues() {\n')
        f.write('  start_time=$(date +%s)\n')
        f.write('  wait_count=0\n')
        f.write('  while true; do\n')
        f.write('    current_time=$(date +%s)\n')
        f.write('    elapsed=$((current_time - start_time))\n')
        f.write('    wait_count=$((wait_count + 1))\n')
        f.write('    queue_output=$(sky queue')
        for r in regions:
            cluster = _region_cluster_name(r)
            f.write(f' {cluster}')
        f.write(')\n')
        f.write('    if ! echo "$queue_output" | grep -q "RUNNING"; then\n')
        f.write('      echo "All queues are empty after '
                '$elapsed seconds ($wait_count checks)."\n')
        f.write('      break\n')
        f.write('    fi\n')
        f.write('    echo "Waiting for queues to be empty... Elapsed time: '
                '$elapsed seconds, Check #$wait_count"\n')
        # f.write('    echo "$queue_output"\n')
        f.write('    sleep 10\n')
        f.write('  done\n')
        f.write('}\n\n')
        f.write('wait_for_empty_queues\n')

        # Phase 4: Sync results
        f.write('\n# ---------- phase 4: sync results\n')
        f.write('echo "Starting to sync results at $(date)"\n')
        f.write('# Run scp commands in parallel\n')
        for i, s in enumerate(scps):
            f.write(f'{s} & pid_scp_{i}=$!\n')
        f.write('\n# Wait for all scp commands to complete\n')
        for i in range(len(scps)):
            f.write(f'wait $pid_scp_{i}\n')
        f.write('echo "Finished syncing results at $(date)"\n')

        # Phase 4: Stop the status puller
        f.write('\n# ---------- phase 5: stop status puller\n')
        f.write(f'echo stop > {signal_file}\n')
        f.write('echo "Status puller stopped at $(date)"\n')

        # Make the script executable
        os.system(f'chmod +x {script_path}')

    print(f'{"Parallel execution script":=^70}')
    print(f'Generated parallel execution script at {script_path}')
    print(f'Run with: bash {script_path}')

    # print(f'{"Queue status puller (Running locally)":=^70}')
    # print(status_puller_cmd)
    # print(f'{"Launch Clients":=^70}')
    # # Use this order so that the overhead to launch the cluster is aligned.
    # for _, cmds in cn2cmds.items():
    #     for c in cmds:
    #         print(c)
    #     print('*' * 30)
    # print(f'{"Sync down results":=^70}')
    # for s in scps:
    #     print(s)

    print(f'{"Generate result table":=^70}')
    for nm in name_mapping:
        print(nm)


if __name__ == '__main__':
    # py -m sky.lbbench.gen_cmd --service-names b1 b2 b3 --exp-name arena_syn_r3_c2000_u250_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 250' # pylint: disable=line-too-long
    # py -m sky.lbbench.gen_cmd --service-names d1 d2 d3 --exp-name arena_syn_mrc_tail_c2000_u300_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000 --num-users 150' --region-to-users '{"us-east-2":200,"ap-northeast-1"}'
    main()

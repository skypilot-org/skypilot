"""Utils for generating benchmark commands."""
import argparse
import collections
import json
import os
from pathlib import Path
import shlex
import tempfile
from typing import List

from sky.lbbench import utils

raw_describes = [
    'sgl',
    'sky_least_load',
    'sky_consistent_hashing',
    'sky_round_robin',
    'sky_walker_prefix',
    'sky_walker_ch',
]
raw_presents = [
    'SGL',
    'LL',
    'CH',
    'RR',
    'SkyWalker/Prefix',
    'SkyWalker/CH',
]

enabled_systems = [
    0,  # sgl router
    1,  # vanilla least load
    2,  # consistent hashing
    3,  # round robin
    4,  # skywalker with prefix tree
    5,  # skywalker with consistent hashing
]

describes = [raw_describes[i] for i in enabled_systems]
presents = [raw_presents[i] for i in enabled_systems]

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


def _get_endpoint_for_traffic(index: int, sns: List[str]) -> str:
    if index < len(utils.single_lb_clusters):
        cluster = utils.single_lb_clusters[index]
        cluster_ip = _get_head_ip_for_cluster(cluster)
        port = 9001 if index == 0 else 9002
        return f'{cluster_ip}:{port}'
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
    parser.add_argument('--service-names', type=str, nargs='+', required=True)
    parser.add_argument('--exp-name', type=str, required=True)
    parser.add_argument('--extra-args', type=str, default='')
    parser.add_argument('--output-dir', type=str, default='exp-result')
    parser.add_argument('--regions', type=str, default=None, nargs='+')
    parser.add_argument('--region-to-args', type=str, default=None)
    parser.add_argument('--reload-client', action='store_true')
    args = parser.parse_args()
    sns = args.service_names
    if len(sns) != len(describes):
        raise ValueError(f'Expected {len(describes)} service names for '
                         f'{", ".join(describes)}')

    endpoints = [_get_endpoint_for_traffic(i, sns) for i in enabled_systems]
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
        fast = '--fast' if not args.reload_client else ''
        cn2cmds[cluster].append(f'sky launch --region {r} -c {cluster} -y '
                                f'{fast} --env CMD=whoami --env HF_TOKEN '
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
            region_cmd = f'{cmd} --region {r} --seed {r} '
            if isinstance(regions, dict):
                region_cmd += f' {regions[r]}'
            # TODO(tian): Instead of --fast, how about sky launch once
            # and then sky exec?
            cn2cmds[cluster].append(
                f'sky exec {cluster} --detach-run '
                f'--env CMD={shlex.quote(region_cmd)} '
                '--env HF_TOKEN examples/serve/external-lb/client.yaml '
                f'--name {en}')
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
            if args.reload_client:
                f.write(f'launch_{region_safe}() {{\n')
                f.write(f'  {cn2cmds[cluster][0]}\n')
                f.write('}\n\n')
            else:
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
        f.write('while ! grep -q "Pulling queue status" '
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
        f.write(f'  expected_jobs={len(regions) * len(describes)}\n')
        f.write(f'  exp_name="{args.exp_name}"\n')
        f.write('  while true; do\n')
        f.write('    current_time=$(date +%s)\n')
        f.write('    elapsed=$((current_time - start_time))\n')
        f.write('    wait_count=$((wait_count + 1))\n')
        f.write('    queue_output=$(sky queue')
        for r in regions:
            cluster = _region_cluster_name(r)
            f.write(f' {cluster}')
        f.write(')\n')
        f.write(
            '    filtered_output=$(echo "$queue_output" | grep "$exp_name" || true)\n'
        )
        f.write('    if echo "$filtered_output" | grep -q "FAILED"; then\n')
        f.write(
            '      echo "ERROR: Found FAILED jobs for experiment $exp_name!"\n')
        f.write('      echo "$filtered_output" | grep "FAILED"\n')
        f.write('      exit 1\n')
        f.write('    fi\n')
        f.write(
            '    num_succeeded=$(echo "$filtered_output" | grep -c "SUCCEEDED" || true)\n'
        )
        f.write(
            '    num_running=$(echo "$filtered_output" | grep -c "RUNNING" || true)\n'
        )
        f.write('    total_jobs=$((num_succeeded + num_running))\n')
        f.write('    if [ "$total_jobs" -ne "$expected_jobs" ]; then\n')
        f.write(
            '      echo "WARNING: Expected $expected_jobs jobs but found $total_jobs (Finished: $num_succeeded, Running: $num_running)"\n'
        )
        f.write('      echo "Retrying queue status fetch..."\n')
        f.write('      sleep 5\n')
        f.write('      continue\n')
        f.write('    fi\n')
        f.write(
            '    if echo "$filtered_output" | grep -q "SUCCEEDED" && ! echo "$filtered_output" | grep -q "RUNNING"; then\n'
        )
        f.write(
            '      echo "All queues are empty after '
            '$elapsed seconds ($wait_count checks). Finished: $num_succeeded, Running: $num_running"\n'
        )
        f.write('      break\n')
        f.write('    fi\n')
        f.write(
            '    echo "Waiting for queues to be empty... Elapsed time: '
            '$elapsed seconds, Check #$wait_count, Finished: $num_succeeded, Running: $num_running"\n'
        )
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
    run_log = 'exp-result/run.log'
    print(f'Run with: bash {script_path} > {run_log} 2>&1')
    print(f'Tail the log file: tail -f {run_log}')


if __name__ == '__main__':
    main()

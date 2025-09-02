"""Load balancer benchmark script."""

import argparse
import asyncio
import dataclasses
import importlib
import json
import os
from pathlib import Path
import time

import numpy as np
from rich import print as rp

from sky.lbbench import oai


async def launch_task(args: argparse.Namespace, workload_module) -> None:
    if args.backend_url is None:
        raise ValueError('backend_url is required')
    url = args.backend_url
    if not url.startswith('http://'):
        url = 'http://' + url
    await oai.init_oai(url)

    num_users = args.num_users
    tic = time.time()
    tasks = workload_module.launch_user_tasks(args, num_users)
    total_tasks = len(tasks)
    finished = 0
    for completed_task in asyncio.as_completed(tasks):
        await completed_task
        finished += 1
        rp(f'> Progress: {finished}/{total_tasks} users finished')
    latency = time.time() - tic
    rp(f'All E2E Latency: {latency:.3f}')

    total_tpt_tokens = 0
    total_times = 0.
    ttfts = []
    e2e_latencies = []
    total_input_tokens = 0
    total_cached_tokens = 0
    rp(f'num metrics: {len(oai.global_metrics)=}')
    for m in oai.global_metrics:
        if (m.ttft is None or m.e2e_latency is None or m.input_tokens is None or
                m.output_tokens is None or m.cached_tokens is None):
            continue
        total_tpt_tokens += m.input_tokens + 2 * m.output_tokens
        total_times += m.e2e_latency
        ttfts.append(m.ttft)
        e2e_latencies.append(m.e2e_latency)
        total_input_tokens += m.input_tokens
        total_cached_tokens += m.cached_tokens
    rp(f'{"TPT":=^50}')
    rp(f'Per request: {total_tpt_tokens / total_times:.3f}')
    rp(f'Per second: {total_tpt_tokens / latency:.3f}')
    rp(f'{"TTFT":=^50}')
    rp(f'Mean: {np.mean(ttfts):.3f}')
    rp(f'P50: {np.percentile(ttfts, 50):.3f}')
    rp(f'P90: {np.percentile(ttfts, 90):.3f}')
    rp(f'P99: {np.percentile(ttfts, 99):.3f}')
    rp(f'{"E2E Latency":=^50}')
    rp(f'Mean: {np.mean(e2e_latencies):.3f}')
    rp(f'P50: {np.percentile(e2e_latencies, 50):.3f}')
    rp(f'P90: {np.percentile(e2e_latencies, 90):.3f}')
    rp(f'P99: {np.percentile(e2e_latencies, 99):.3f}')
    rp(f'{"KV Cache Hit Rate":=^50}')
    rp(f'Mean: {total_cached_tokens / total_input_tokens:.3f}')

    if not args.yes:
        input('Press Enter to save results...')

    result_file = (Path(args.output_dir).expanduser() / 'metric' /
                   f'{args.exp_name}.json')
    result_file.parent.mkdir(parents=True, exist_ok=True)

    with open(result_file, 'w', encoding='utf-8') as fout:
        value = {
            'workload': args.workload,
            'latency': round(latency, 3),
            'metrics': [dataclasses.asdict(m) for m in oai.global_metrics],
            'backend_url': args.backend_url,
            'workload_args': workload_module.args_to_dict(args),
        }
        fout.write(json.dumps(value) + '\n')


def main():
    all_workloads_file = os.listdir(
        os.path.join(os.path.dirname(__file__), 'workloads'))
    all_workloads = [f.split('.')[0] for f in all_workloads_file]
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp-name', type=str, default='sky-exp')
    parser.add_argument('--num-users', type=int, default=1)
    parser.add_argument('--backend-url', type=str, default=None)
    parser.add_argument('-y', '--yes', action='store_true')
    parser.add_argument('--output-dir', type=str, default='exp-result')
    parser.add_argument('--workload',
                        type=str,
                        required=True,
                        choices=all_workloads)
    parser.add_argument('--region', type=str, default='none')

    # First parse just the workload argument to import the right module
    temp_args, _ = parser.parse_known_args()

    # Import the workload module and add its arguments
    workload_module = importlib.import_module(
        f'.workloads.{temp_args.workload}', package=__package__)
    workload_module.add_args(parser)

    # Parse all arguments after adding workload-specific ones
    args = parser.parse_args()

    asyncio.run(launch_task(args, workload_module))


if __name__ == '__main__':
    main()

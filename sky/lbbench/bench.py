"""Load balancer benchmark script."""

import argparse
import asyncio
import dataclasses
import importlib
import json
import os
import time
from typing import List

import aiohttp
import colorama
import numpy as np
from rich import print as rp

import sky
from sky.lbbench import oai
from sky.utils import rich_utils
from sky.utils import ux_utils

SGL_ROUTER_IDENTIFIER = 'sglang-router'


async def launch_task(args: argparse.Namespace, workload_module) -> None:
    if args.backend_url is None:
        raise ValueError('backend_url is required')
    url = args.backend_url
    if not url.startswith('http://'):
        url = 'http://' + url
    await oai.init_oai(url)

    num_users = args.num_users
    tic = time.time()
    await asyncio.gather(*workload_module.launch_user_tasks(args, num_users))
    latency = time.time() - tic
    rp(f'All E2E Latency: {latency:.3f}')

    total_tpt_tokens = 0
    total_times = 0.
    ttfts = []
    e2e_latencies = []
    total_input_tokens = 0
    total_cached_tokens = 0
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

    input('Press Enter to save results...')

    result_file = f'@temp/result/metric/{args.exp_name}.json'

    with open(result_file, 'w', encoding='utf-8') as fout:
        value = {
            'workload': args.workload,
            'latency': round(latency, 3),
            'metrics': [dataclasses.asdict(m) for m in oai.global_metrics],
            'backend_url': args.backend_url,
            'workload_args': workload_module.args_to_dict(args),
        }
        fout.write(json.dumps(value) + '\n')


def prepare_lb_endpoints_and_confirm(backend_url: str) -> List[str]:
    with rich_utils.client_status(
            ux_utils.spinner_message(
                '[bold cyan]Checking External LB Endpoints[/]')) as spinner:
        if 'aws.cblmemo.net' in backend_url:
            service_name = backend_url.split('.')[0]
            req = sky.serve.status(service_name)
            st = sky.client.sdk.get(req)
            # rp('Service status:')
            print(sky.serve.format_service_table(st, show_all=False))
            if len(st) != 1:
                raise ValueError('More than one service found. '
                                 'Please specify the service name.')
            endpoints = [r['endpoint'] for r in st[0]['external_lb_info']]
        elif '9002' in backend_url:  # Single Global Sky LB
            url = backend_url
            if not url.startswith('http://'):
                url = 'http://' + url
            endpoints = [url]
        elif '9001' in backend_url:  # SGLang Router
            endpoints = [SGL_ROUTER_IDENTIFIER]
        else:
            return []
            # raise ValueError(f'Unknown backend URL: {backend_url}')
        print(f'External Load Balancer Endpoints: {colorama.Fore.GREEN}'
              f'{endpoints}{colorama.Style.RESET_ALL}')
        spinner.update('Press Enter to confirm the endpoints are correct...')
        input()
        return endpoints


async def pull_queue_status(exp_name: str, endpoints: List[str],
                            event: asyncio.Event) -> None:
    tmp_name = f'@temp/trash/result_queue_size_{exp_name}.txt'
    dest_name = f'@temp/result/queue_size/{exp_name}.txt'
    print(f'Pulling queue status:      tail -f {tmp_name} | jq')
    if SGL_ROUTER_IDENTIFIER in endpoints:
        while not event.is_set():
            await asyncio.sleep(1)
        os.system(f'sky logs router --no-follow > {dest_name} 2>&1')
    else:
        async with aiohttp.ClientSession() as session:
            with open(tmp_name, 'w', encoding='utf-8') as f:
                while not event.is_set():
                    lb2confs = {'time': time.time()}
                    for endpoint in endpoints:
                        async with session.get(endpoint + '/conf') as resp:
                            conf = await resp.json()
                        async with session.get(endpoint +
                                               '/raw-queue-size') as resp:
                            raw_queue_size = (await resp.json())['queue_size']
                        conf['raw_queue_size'] = raw_queue_size
                        lb2confs[endpoint] = conf
                    print(json.dumps(lb2confs), file=f, flush=True)
                    await asyncio.sleep(1)
        os.rename(tmp_name, dest_name)


def main():
    # py examples/serve/external-lb/bench.py --data-path @temp/test.jsonl
    # --exp-name sky-exp --num-branches 2 --num-users 5 --num-questions 1
    # --backend-url vllmtest.aws.cblmemo.net:8000
    all_workloads_file = os.listdir(
        os.path.join(os.path.dirname(__file__), 'workloads'))
    all_workloads = [f.split('.')[0] for f in all_workloads_file]
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp-name', type=str, default='sky-exp')
    parser.add_argument('--num-users', type=int, default=1)
    parser.add_argument('--backend-url', type=str, default=None)
    parser.add_argument('--workload',
                        type=str,
                        required=True,
                        choices=all_workloads)

    # First parse just the workload argument to import the right module
    temp_args, _ = parser.parse_known_args()

    # Import the workload module and add its arguments
    workload_module = importlib.import_module(
        f'.workloads.{temp_args.workload}', package=__package__)
    workload_module.add_args(parser)

    # Parse all arguments after adding workload-specific ones
    args = parser.parse_args()

    endpoints = prepare_lb_endpoints_and_confirm(args.backend_url)

    async def run_all():
        event = asyncio.Event()
        queue_status_task = asyncio.create_task(
            pull_queue_status(args.exp_name, endpoints, event))
        await launch_task(args, workload_module)
        event.set()
        await queue_status_task

    asyncio.run(run_all())


if __name__ == '__main__':
    main()

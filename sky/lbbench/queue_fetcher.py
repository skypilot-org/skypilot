"""Queue status fetcher."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Dict, List

import aiohttp
import colorama

import sky
from sky.lbbench import utils


def prepare_lb_endpoints_and_confirm(exp2backend: Dict[str, str],
                                     yes: bool) -> Dict[str, List[str]]:
    # Only fetch Serve status if there are SkyPilot Serve endpoints
    st = []
    needs_serve_status = any(
        'aws.cblmemo.net' in url for url in exp2backend.values())

    if needs_serve_status:
        try:
            req = sky.serve.status(None)
            st = sky.client.sdk.get(req)
            print(sky.serve.format_service_table(st, show_all=False))
        except Exception as e:  # pylint: disable=broad-except
            print(f'Warning: Could not fetch SkyPilot Serve status: {e}')
            st = []
    else:
        print('No SkyPilot Serve endpoints found; skipping `sky serve status` '
              'call.')

    def _get_one_endpoints(backend_url: str) -> List[str]:
        if 'aws.cblmemo.net' in backend_url:
            if not st:
                print(f'Warning: No SkyPilot Serve status available for '
                      f'{backend_url}')
                return []

            service_name = backend_url.split('.')[0]
            st4svc = None
            for svc in st:
                if svc['name'] == service_name:
                    st4svc = svc
                    break
            if st4svc is None:
                raise ValueError(f'Service {service_name} not found')
            endpoints = [r['endpoint'] for r in st4svc['external_lb_info']]
        elif '9002' in backend_url:  # Single Global Sky LB
            url = backend_url
            if not url.startswith('http://'):
                url = 'http://' + url
            endpoints = [url]
        elif '9001' in backend_url:  # SGLang Router
            endpoints = [utils.sgl_cluster]
        elif '34.117.239.237' in backend_url:  # GKE endpoint
            url = backend_url
            if not url.startswith('http://'):
                url = 'http://' + url
            endpoints = [url]
        else:
            return []
            # raise ValueError(f'Unknown backend URL: {backend_url}')
        print(f'External Load Balancer Endpoints for {backend_url}: '
              f'{colorama.Fore.GREEN}{endpoints}{colorama.Style.RESET_ALL}')
        return endpoints

    exp2endpoints = {
        exp: _get_one_endpoints(backend)
        for exp, backend in exp2backend.items()
    }
    if not yes:
        input('Press Enter to confirm the endpoints are correct...')
    return exp2endpoints


async def pull_queue_status(exp_name: str, endpoints: List[str],
                            event: asyncio.Event, output_dir: str) -> None:
    # Check if this is a GKE endpoint (34.117.239.237)
    is_gke_baseline = False
    if endpoints and any(
            '34.117.239.237' in endpoint for endpoint in endpoints):
        is_gke_baseline = True

    tmp_name = os.path.join(tempfile.gettempdir(),
                            f'result_queue_size_{exp_name}.txt')
    dest_name = (Path(output_dir).expanduser() / 'queue_size' /
                 f'{exp_name}.txt')
    dest_name.parent.mkdir(parents=True, exist_ok=True)

    # If this is GKE, skip queue polling
    if is_gke_baseline:
        print(f'Skipping queue polling for GKE baseline: {exp_name}',
              flush=True)
        with open(dest_name, 'w', encoding='utf-8') as f:
            f.write(
                json.dumps({
                    'time': time.time(),
                    'status': 'skipped'
                }) + '\n')
        await event.wait()  # Wait for stop signal
        print(f'Queue fetcher finished (skipped) for GKE: {exp_name}')
        return

    # Force flush it to make the tee works
    print(f'Pulling queue status:      tail -f {tmp_name} | jq', flush=True)
    if utils.sgl_cluster in endpoints:
        while not event.is_set():
            await asyncio.sleep(1)
        await asyncio.to_thread(  # type: ignore[attr-defined]
            lambda: os.system(
                f'sky logs {utils.sgl_cluster} --no-follow > {dest_name} 2>&1'))
    else:
        async with aiohttp.ClientSession() as session:
            with open(tmp_name, 'w', encoding='utf-8') as f:
                while not event.is_set():
                    lb2confs = {'time': time.time()}
                    for endpoint in endpoints:
                        async with session.get(endpoint + '/conf') as resp:
                            conf = await resp.json()
                        # async with session.get(endpoint +
                        #                        '/raw-queue-size') as resp:
                        #     raw_queue_size = (await resp.json())['queue_size']
                        conf['raw_queue_size'] = conf['queue_size']
                        lb2confs[endpoint] = conf
                    print(json.dumps(lb2confs), file=f, flush=True)
                    await asyncio.sleep(1)
        shutil.move(tmp_name, dest_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp2backend', type=str, required=True)
    parser.add_argument('-y', '--yes', action='store_true')
    parser.add_argument('--output-dir', type=str, default='@temp')
    parser.add_argument('--signal-file', type=str, required=True)
    args = parser.parse_args()
    exp2backend = json.loads(args.exp2backend)
    exp2endpoints = prepare_lb_endpoints_and_confirm(exp2backend, args.yes)

    async def run_all() -> None:
        tasks: List[asyncio.Task] = []
        events: List[asyncio.Event] = []
        for exp_name, endpoints in exp2endpoints.items():
            event = asyncio.Event()
            events.append(event)
            tasks.append(
                asyncio.create_task(
                    pull_queue_status(exp_name, endpoints, event,
                                      args.output_dir)))
        while True:
            if os.path.exists(args.signal_file):
                with open(args.signal_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                if 'stop' in content:
                    break
            await asyncio.sleep(1)
        for event in events:
            event.set()
        await asyncio.gather(*tasks)
        print('Queue status puller finished.')

    asyncio.run(run_all())


if __name__ == '__main__':
    main()

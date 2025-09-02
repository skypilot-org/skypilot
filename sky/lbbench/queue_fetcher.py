"""Queue status fetcher."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Dict, List

import aiohttp
import colorama

import sky
from sky.lbbench import utils


def prepare_lb_endpoints_and_confirm(exp2backend: Dict[str, str],
                                     yes: bool) -> Dict[str, List[str]]:
    req = sky.serve.status(None)
    st = sky.client.sdk.get(req)
    print(sky.serve.format_service_table(st, show_all=False))

    def _get_one_endpoints(backend_url: str) -> List[str]:
        if 'aws.cblmemo.net' in backend_url:
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
            endpoints = [utils.single_lb_clusters[0]]
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
    tmp_name = os.path.join(tempfile.gettempdir(),
                            f'result_queue_size_{exp_name}.txt')
    dest_name = (Path(output_dir).expanduser() / 'queue_size' /
                 f'{exp_name}.txt')
    dest_name.parent.mkdir(parents=True, exist_ok=True)
    # Force flush it to make the tee works
    print(f'Pulling queue status:      tail -f {tmp_name} | jq', flush=True)
    if utils.single_lb_clusters[0] in endpoints:
        while not event.is_set():
            await asyncio.sleep(1)
        await asyncio.to_thread(  # type: ignore[attr-defined]
            lambda: os.system(f'sky logs {utils.single_lb_clusters[0]} '
                              f'--no-follow > {dest_name} 2>&1'))
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
        os.rename(tmp_name, dest_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exp2backend', type=str, required=True)
    parser.add_argument('-y', '--yes', action='store_true')
    parser.add_argument('--output-dir', type=str, default='exp-result')
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

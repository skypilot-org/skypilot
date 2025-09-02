"""Chatbot Arena 1m multi-turn conversation workload."""

import argparse
import asyncio
import collections
import time
import traceback
from typing import Any, Awaitable, Dict, List

import datasets
from rich import print as rp

from sky.lbbench import oai
from sky.lbbench import utils
from sky.utils import rich_utils
from sky.utils import ux_utils

DATASET_NAME = 'lmsys/chatbot_arena_conversations'
CONV_SELECTOR = 'conversation_a'


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--num-conv', type=int, default=1000)
    # parser.add_argument('--scale-factor', type=int, default=300)
    parser.add_argument('--run-seconds', type=int, default=120)
    parser.add_argument('--uid', type=str, default='default')


def args_to_dict(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        'num-conv': args.num_conv,
        # 'scale-factor': args.scale_factor,
        'run-seconds': args.run_seconds,
        'uid': args.uid,
    }


def _load_dataset(num_conv: int) -> List[Dict[str, Any]]:
    tic = time.time()
    multi_turn_data = []
    chunk_data = datasets.load_dataset(DATASET_NAME, split='train')
    for d in chunk_data:
        if d['turn'] > 1:
            multi_turn_data.append({
                'turn': d['turn'],
                'tstamp': d['tstamp'],
                'user': d['judge'],
                'conv': d[CONV_SELECTOR],
            })
        if len(multi_turn_data) >= num_conv:
            break
    print(f'Got {len(multi_turn_data)} multi-turn conversations '
          f'(took {time.time() - tic:.2f}s)')
    return multi_turn_data


def _plot_request_rate(groups: Dict[int, List[Dict[str, Any]]]) -> None:
    # pylint: disable=import-outside-toplevel
    import matplotlib.pyplot as plt
    import numpy as np
    plt.figure(figsize=(10, 6))
    agg_window_size = 100000
    for i, group in enumerate(groups.values()):
        timestamps = [conv['tstamp'] for conv in group]
        timestamps.sort()

        if timestamps:
            start_time = timestamps[0]
            relative_times = [(ts - start_time) for ts in timestamps]
            max_time = relative_times[-1] if relative_times else 0
            bins = np.arange(0, max_time + agg_window_size, agg_window_size)
            counts, _ = np.histogram(relative_times, bins=bins)
            counts_per_minute = counts / (agg_window_size / 60)
            plt.plot(bins[:-1] / 60,
                     counts_per_minute,
                     label=f'User Group {i+1}')
    plt.xlabel('Time')
    plt.ylabel(f'Requests per minute (window size: {agg_window_size}s)')
    plt.title('Aggregated Request Rate Over Time for Each User Group')
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    fn = 'exp-result/user_request_rates.pdf'
    plt.savefig(fn)
    plt.close()
    rp(f'Aggregated request rate plot saved to {fn}')


async def _multi_turn_conv(uid: str,
                           conv: Dict[str, Any]) -> List[utils.OAIChatHistory]:
    history = []
    for i, msg in enumerate(conv['conv']):
        if i % 2 == 0:
            assert msg['role'] == 'user'
            history.append(msg)
        else:
            assert msg['role'] == 'assistant'
            result = await oai.call_chat_completion_async(
                history,
                temperature=0.0,
                max_tokens=256,
                uid=uid,
                stop=None,
                only_return_new_round=True)
            if isinstance(result, Exception):
                return history
            assert len(result) == 1
            history.extend(result)
    return history


async def _user_task(uid: int, convs: List[Dict[str,
                                                Any]], intervals: List[int],
                     real_uid: str) -> List[utils.OAIChatHistory]:
    rp(f'User {uid}: {len(convs)} conversations in total. '
       f'Duration: {sum(intervals)} seconds')
    time_to_sleep = uid * 10
    rp(f'User {uid}: sleep for {time_to_sleep} seconds to start')
    await asyncio.sleep(time_to_sleep)
    rp(f'User {uid}: start sending requests')
    tic = time.time()
    tasks = []
    for interval, conv in zip(intervals, convs):
        await asyncio.sleep(interval)
        tasks.append(asyncio.create_task(_multi_turn_conv(real_uid, conv)))
    rp(f'User {uid}: All requests ({len(tasks)}) scheduled')
    results = []
    for i, task in enumerate(asyncio.as_completed(tasks)):
        result = await task
        if isinstance(result, Exception):
            rp(f'User {uid} FAILED: {result}.'
               f'  Traceback: {traceback.format_exc()}')
            continue
        results.extend(result)
        # Only print progress 10 times
        if len(convs) <= 10 or (i + 1) % (len(convs) //
                                          10) == 0 or i + 1 == len(convs):
            progress = f'({i+1}/{len(convs)})'
            rp(f'User {uid}: {progress:^10} conversations completed. '
               f'Latency: {time.time() - tic:.3f}')
    return results


def launch_user_tasks(
        args: argparse.Namespace,
        num_users: int) -> List[Awaitable[List[utils.OAIChatHistory]]]:
    with rich_utils.client_status(
            ux_utils.spinner_message(
                f'[bold cyan]Loading dataset {DATASET_NAME}[/]')) as spinner:
        convs = _load_dataset(args.num_conv)
        # assert num_users == 1, "Multiple users are not supported yet"
        # groups = {0: convs[:args.num_conv]}
        spinner.update('[bold cyan]Grouping conversations[/]')
        user_to_convs: Dict[str,
                            List[Dict[str,
                                      Any]]] = collections.defaultdict(list)
        for conv in convs:
            user_to_convs[conv['user']].append(conv)
        sorted_users = sorted(user_to_convs.keys(),
                              key=lambda u: len(user_to_convs[u]),
                              reverse=True)
        groups: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
        # Assign each user's conversations to the group with the fewest entries
        for user in sorted_users:
            user_convs = user_to_convs[user]
            min_group_idx = min(range(num_users),
                                key=lambda idx: len(groups[idx]))
            groups[min_group_idx].extend(user_convs)
        print(f'Grouped conversations into {num_users} groups '
              f'with sizes: {sorted([len(g) for g in groups.values()])}')

        # _plot_request_rate(groups)

    max_duration = 0
    for uid, user_convs in groups.items():
        if not user_convs:
            continue
        user_convs.sort(key=lambda conv: conv['tstamp'])
        if len(user_convs) > 1:
            duration = user_convs[-1]['tstamp'] - user_convs[0]['tstamp']
            max_duration = max(max_duration, duration)

    scale_factor = (max_duration / args.run_seconds
                    if max_duration > args.run_seconds else 1.0)
    print(f'Scale factor: {scale_factor}')

    tasks: List[Awaitable[List[utils.OAIChatHistory]]] = []

    for uid, user_convs in groups.items():
        if not user_convs:
            continue

        intervals = [0]
        for i in range(1, len(user_convs)):
            interval = user_convs[i]['tstamp'] - user_convs[i - 1]['tstamp']
            assert interval > 0, interval
            interval /= scale_factor
            intervals.append(interval)
        tasks.append(_user_task(uid, user_convs, intervals, str(args.uid)))
    return tasks

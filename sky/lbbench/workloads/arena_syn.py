"""Chatbot Arena multi-turn conversation workload with Synthetic timestamps."""

import argparse
import asyncio
import collections
import time
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
    parser.add_argument('--duration', type=int, default=120)


def args_to_dict(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        'num-conv': args.num_conv,
        'duration': args.duration,
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


async def _multi_turn_conv(duration: int, tic: float, uid: int,
                           conv: Dict[str, Any]) -> List[utils.OAIChatHistory]:
    history = []
    for i, msg in enumerate(conv['conv']):
        elapsed = time.time() - tic
        remaining = duration - elapsed
        if remaining <= 0:
            break
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


async def _user_task(duration: int, tic: float, uid: int, max_uid: int,
                     convs: List[Dict[str, Any]]) -> List[utils.OAIChatHistory]:
    uid_repr = f'{uid:<{len(str(max_uid))}}'
    rp(f'User {uid_repr}: {len(convs)} conversations in total.')
    results = []

    def _log(idx):
        # Only print progress 10 times
        if len(convs) <= 10 or (idx + 1) % (len(convs) //
                                            10) == 0 or idx + 1 == len(convs):
            progress = f'({idx+1}/{len(convs)})'
            latency = time.time() - tic
            rp(f'User {uid_repr}: {progress:^10} conversations completed. '
               f'Latency: {latency:.3f}')

    for i, conv in enumerate(convs):
        # Make sure sleep doesn't exceed duration
        elapsed = time.time() - tic
        remaining = duration - elapsed
        if remaining <= 0:
            break
        coro = _multi_turn_conv(duration, tic, uid, conv)
        try:
            # result = await asyncio.wait_for(coro, timeout=remaining)
            result = await coro
            results.extend(result)
            _log(i)
        except asyncio.TimeoutError:
            rp(f'User {uid_repr}: Conversation {i+1} timed '
               f'out after {remaining:.2f}s')
            break
    return results


async def _user_task_loop(
        duration: int, uid: int, max_uid: int,
        convs: List[Dict[str, Any]]) -> List[utils.OAIChatHistory]:
    tic = time.time()
    results = []
    while True:
        results_one_round = await _user_task(duration, tic, uid, max_uid, convs)
        results.extend(results_one_round)
        if time.time() - tic > duration:
            break
    return results


def launch_user_tasks(
        args: argparse.Namespace,
        num_users: int) -> List[Awaitable[List[utils.OAIChatHistory]]]:
    with rich_utils.client_status(
            ux_utils.spinner_message(
                f'[bold cyan]Loading dataset {DATASET_NAME}[/]')) as spinner:
        min_tstamp = float('inf')
        max_tstamp = 0
        convs = _load_dataset(args.num_conv)
        spinner.update('[bold cyan]Grouping conversations[/]')
        user_to_convs: Dict[str,
                            List[Dict[str,
                                      Any]]] = collections.defaultdict(list)
        for conv in convs:
            user_to_convs[conv['user']].append(conv)
            min_tstamp = min(min_tstamp, conv['tstamp'])
            max_tstamp = max(max_tstamp, conv['tstamp'])
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

    tasks: List[Awaitable[List[utils.OAIChatHistory]]] = []

    for uid, user_convs in groups.items():
        if not user_convs:
            continue
        user_convs.sort(key=lambda conv: conv['tstamp'])
        tasks.append(
            _user_task_loop(args.duration, uid, len(groups), user_convs))
    return tasks

"""WildChat-1M multi-turn conversation workload with synthetic timestamps."""

import argparse
import asyncio
import collections
import random
import time
from typing import Any, Awaitable, Dict, List

import datasets
from rich import print as rp

from sky.lbbench import oai
from sky.lbbench import utils
from sky.utils import rich_utils
from sky.utils import ux_utils

DATASET_NAME = 'allenai/WildChat-1M'


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--num-conv', type=int, default=1000)
    parser.add_argument('--duration', type=int, default=120)
    parser.add_argument('--seed', type=str, default='default')


def args_to_dict(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        'num-conv': args.num_conv,
        'duration': args.duration,
        'seed': args.seed,
    }


def _load_dataset(num_conv: int) -> List[Dict[str, Any]]:
    tic = time.time()
    chunk_data = datasets.load_dataset(DATASET_NAME, split='train[:100000]')
    multi_turn_data = []
    for d in chunk_data:
        # At least 2 full turns: user + assistant + user + assistant (len >= 4)
        if d['turn'] >= 2 and isinstance(d['conversation'], list) and len(d['conversation']) >= 4:
            multi_turn_data.append({
                'turn': d['turn'],
                'timestamp': d['timestamp'],
                'conv': d['conversation'],
                'user': d.get('hashed_ip', 'unknown')
            })
    print(f'Got {len(multi_turn_data)} multi-turn conversations (took {time.time() - tic:.2f}s)')
    random.shuffle(multi_turn_data)
    return multi_turn_data[:num_conv]


async def _multi_turn_conv(duration: int, tic: float, uid: str,
                           conv: Dict[str, Any]) -> List[utils.OAIChatHistory]:
    history = []
    conv_msgs = conv['conv']

    for i in range(0, len(conv_msgs), 2):
        elapsed = time.time() - tic
        if elapsed >= duration:
            break

        if i >= len(conv_msgs) or conv_msgs[i]['role'] != 'user':
            continue
        history.append({
            'role': 'user',
            'content': conv_msgs[i]['content']
        })

        if i + 1 >= len(conv_msgs) or conv_msgs[i + 1]['role'] != 'assistant':
            continue
        result = await oai.call_chat_completion_async(
            history,
            temperature=0.0,
            max_tokens=256,
            uid=uid,
            stop=None,
            only_return_new_round=True
        )
        if isinstance(result, Exception):
            return history
        history.extend(result)
    return history


async def _user_task(duration: int, tic: float, uid: int, max_uid: int,
                     convs: List[Dict[str, Any]],
                     real_uid: str) -> List[utils.OAIChatHistory]:
    uid_repr = f'{uid:<{len(str(max_uid))}}'
    rp(f'User {uid_repr}: {len(convs)} conversations in total.')
    results = []

    for i, conv in enumerate(convs):
        elapsed = time.time() - tic
        if elapsed >= duration:
            break
        coro = _multi_turn_conv(duration, tic, real_uid, conv)
        try:
            result = await coro
            results.extend(result)
            if len(convs) <= 10 or (i + 1) % (len(convs) // 10) == 0 or i + 1 == len(convs):
                rp(f'User {uid_repr}: ({i+1}/{len(convs)}) conversations completed. '
                   f'Elapsed: {time.time() - tic:.2f}s')
        except asyncio.TimeoutError:
            rp(f'User {uid_repr}: Conversation {i+1} timed out after {duration}s')
            break
    return results


async def _user_task_loop(duration: int, uid: int, max_uid: int,
                          convs: List[Dict[str, Any]],
                          real_uid: str) -> List[utils.OAIChatHistory]:
    tic = time.time()
    results = []
    while True:
        results_one_round = await _user_task(duration, tic, uid, max_uid, convs, real_uid)
        results.extend(results_one_round)
        if time.time() - tic > duration:
            break
    return results


def launch_user_tasks(
        args: argparse.Namespace,
        num_users: int) -> List[Awaitable[List[utils.OAIChatHistory]]]:
    with rich_utils.client_status(
            ux_utils.spinner_message(f'[bold cyan]Loading dataset {DATASET_NAME}[/]')) as spinner:
        random.seed(args.seed)
        convs = _load_dataset(args.num_conv)
        spinner.update('[bold cyan]Grouping conversations[/]')
        user_to_convs: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        for conv in convs:
            user_to_convs[conv['user']].append(conv)
        sorted_users = sorted(user_to_convs.keys(), key=lambda u: len(user_to_convs[u]), reverse=True)
        groups: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
        for user in sorted_users:
            min_group_idx = min(range(num_users), key=lambda idx: len(groups[idx]))
            groups[min_group_idx].extend(user_to_convs[user])
        print(f'Grouped conversations into {num_users} groups with sizes: '
              f'{sorted([len(g) for g in groups.values()])}')

    tasks: List[Awaitable[List[utils.OAIChatHistory]]] = []
    for uid, user_convs in groups.items():
        if not user_convs:
            continue
        user_convs.sort(key=lambda conv: conv['timestamp'])
        tasks.append(
            _user_task_loop(args.duration, uid, len(groups), user_convs, str(args.seed))
        )
    return tasks
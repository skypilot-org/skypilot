"""Chatbot Arena multi-turn conversation workload with Synthetic timestamps."""

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

DATASET_NAME = 'lmsys/chatbot_arena_conversations'
DATASET_NAME_1M = 'lmsys/lmsys-chat-1m'
CONV_SELECTOR = 'conversation_a'


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


seed2idx = {
    'us-east-2': 0,
    'ap-northeast-1': 1,
    'eu-central-1': 2,
}


def _extract(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'turn': d['turn'],
        'tstamp': d['tstamp'],
        'user': d['judge'],
        'conv': d[CONV_SELECTOR],
    }


def _load_dataset(seed: str) -> List[Dict[str, Any]]:
    if seed not in seed2idx:
        raise ValueError(f'Invalid seed: {seed}')
    desired_idx = seed2idx[seed]
    tic = time.time()
    multi_turn_data = []
    chunk_data = datasets.load_dataset(DATASET_NAME, split='train')
    i = 0
    for d in chunk_data:
        if d['turn'] > 1:
            i += 1
            if i % len(seed2idx) != desired_idx:
                continue
            multi_turn_data.append(_extract(d))
    print(f'Got {len(multi_turn_data)} multi-turn conversations '
          f'(took {time.time() - tic:.2f}s)')
    return multi_turn_data


def _load_dataset_1m(seed: str) -> List[Dict[str, Any]]:
    tic = time.time()
    step = 100000
    slice_idx = seed2idx[seed]
    slice_str = f'{slice_idx*step}:{(slice_idx+1)*step}'
    chunk_data = datasets.load_dataset(DATASET_NAME_1M,
                                       split=f'train[{slice_str}]')
    multi_turn_data = []
    for d in chunk_data:
        if d['turn'] > 1:
            multi_turn_data.append(_extract(d))
    print(f'Got {len(multi_turn_data)} multi-turn conversations '
          f'(took {time.time() - tic:.2f}s)')
    return multi_turn_data


# _load_dataset_1m('us-east-2')


async def _multi_turn_conv(uid: int, duration: int, tic: float, real_uid: str,
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
            task = asyncio.create_task(
                oai.call_chat_completion_async(
                    history,
                    temperature=0.0,
                    max_tokens=512,
                    uid=real_uid,
                    stop=None,
                    only_return_new_round=True,
                    tic=tic,
                    duration=duration,
                    hash_key=conv['user'],
                ))
            while not task.done():
                if time.time() - tic > duration:
                    task.cancel()
                    return history
                await asyncio.sleep(0.1)
            result = await task
            if isinstance(result, Exception):
                return history
            print(f'[{tic:.1f}][{time.time() - tic:.3f}] User {uid} '
                  f'done one task, {len(result)=}')
            assert len(result) == 1
            history.extend(result)
    return history


async def _user_task(duration: int, tic: float, uid: int, max_uid: int,
                     convs: List[Dict[str, Any]],
                     real_uid: str) -> List[utils.OAIChatHistory]:
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
        coro = _multi_turn_conv(uid, duration, tic, real_uid, conv)
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


async def _user_task_loop(duration: int, uid: int, max_uid: int,
                          convs: List[Dict[str, Any]],
                          real_uid: str) -> List[utils.OAIChatHistory]:
    tic = time.time()
    results = []
    while True:
        results_one_round = await _user_task(duration, tic, uid, max_uid, convs,
                                             real_uid)
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
        random.seed(args.seed)
        convs = _load_dataset(args.seed)
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

    tasks: List[Awaitable[List[utils.OAIChatHistory]]] = []

    for uid, user_convs in groups.items():
        if not user_convs:
            continue
        user_convs.sort(key=lambda conv: conv['tstamp'])
        tasks.append(
            _user_task_loop(args.duration, uid, len(groups), user_convs,
                            str(args.seed)))
    return tasks

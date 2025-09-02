"""WildChat-1M multi-turn conversation workload with synthetic timestamps."""

import argparse
import asyncio
import collections
import time
from typing import Any, Awaitable, Dict, List, Optional

import datasets

from sky.lbbench import oai
from sky.lbbench import utils
from sky.utils import rich_utils
from sky.utils import ux_utils

DATASET_NAME = 'allenai/WildChat-1M'


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--duration', type=int, default=120)
    parser.add_argument('--seed', type=str, default='default')
    parser.add_argument('--start-index', type=int, default=0)
    parser.add_argument('--open-loop-threshold', type=int, default=None)


def args_to_dict(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        'duration': args.duration,
        'seed': args.seed,
        'start_index': args.start_index,
        'open_loop_threshold': args.open_loop_threshold,
    }


def _filter_conv_by_region(conv: Dict[str, Any], region: str) -> bool:
    state = conv['state']
    country = conv['country']

    if region not in ['us-east-2', 'ap-northeast-1', 'eu-central-1']:
        return True

    if region == 'us-east-2':
        # List 20 US East states
        if state in [
                'Virginia', 'North Carolina', 'South Carolina', 'Georgia',
                'Florida', 'Tennessee', 'Kentucky', 'West Virginia', 'Maryland',
                'Delaware', 'Pennsylvania', 'Ohio', 'Indiana', 'Illinois',
                'Michigan', 'Wisconsin', 'Missouri', 'Iowa', 'Minnesota',
                'Arkansas'
        ]:
            return True
        # Actually just route neighboring countries to US East
        if country in ['Canada', 'United States']:
            return True

    elif region == 'ap-northeast-1':
        # List 20 AP countries:
        if country in [
                'Japan', 'South Korea', 'China', 'Pakistan'
                'Singapore', 'Thailand', 'Malaysia', 'Philippines', 'Vietnam',
                'Indonesia', 'India', 'Bangladesh', 'Sri Lanka', 'Nepal',
                'Pakistan', 'Mongolia', 'Brunei', 'Cambodia', 'Laos', 'Myanmar',
                'Russia'
        ]:
            return True
    elif region == 'eu-central-1':
        # List 20 Europe countries
        if country in [
                'United Kingdom', 'Ireland', 'Spain', 'Italy', 'Portugal',
                'Greece', 'Sweden', 'Norway', 'Finland', 'Denmark',
                'Czech Republic', 'Poland', 'Hungary', 'Slovakia', 'Slovenia',
                'Croatia', 'Romania', 'Bulgaria', 'Estonia', 'Latvia',
                'New Zealand', 'France', 'Romania', 'Norway'
        ]:
            return True

    # print(f"Conversation with state {state} and "
    #       f"country {country} does not match region {region}.")
    return False


def _load_dataset(region: str, start_index: int) -> List[Dict[str, Any]]:
    tic = time.time()
    split_slice = f'{start_index*100000}:{(start_index+1)*100000}'
    chunk_data = datasets.load_dataset(DATASET_NAME,
                                       split=f'train[{split_slice}]')
    multi_turn_data = []
    for d in chunk_data:
        # At least 2 full turns: user + assistant + user + assistant (len >= 4)
        if d['turn'] >= 2 and isinstance(d['conversation'],
                                         list) and len(d['conversation']) >= 4:
            if d.get('hashed_ip', 'unknown') == 'unknown':
                continue
            if not _filter_conv_by_region(d, region):
                continue
            conv = {
                'turn': d['turn'],
                'timestamp': d['timestamp'].timestamp(),
                'conv': d['conversation'],
                'user': d['hashed_ip'],
                'state': d['state'],
                'country': d['country'],
            }
            multi_turn_data.append(conv)
    print(f'Got {len(multi_turn_data)} multi-turn '
          f'conversations (took {time.time() - tic:.2f}s)')
    # random.shuffle(multi_turn_data)
    return multi_turn_data


# _load_dataset('us-east-2')
# _load_dataset('eu-central-1')
# _load_dataset('ap-northeast-1')


async def _multi_turn_conv(uid: int, idx: int, duration: int, tic: float,
                           real_uid: str, conv: Dict[str, Any],
                           open_loop_threshold: Optional[int]) -> None:
    history = []
    for i, msg in enumerate(conv['conv']):
        elapsed = time.time() - tic
        remaining = duration - elapsed
        if remaining <= 0:
            break
        if i % 2 == 0:
            assert msg['role'] == 'user'
            history.append(utils.get_one_round('user', msg['content']))
        else:
            st_this_round = time.time()
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
                    program_id=f'{real_uid}-{uid}-{idx}',
                ))
            while not task.done():
                if time.time() - tic > duration:
                    task.cancel()
                    return
                await asyncio.sleep(0.1)
            result = await task
            if isinstance(result, Exception):
                return
            assert len(result) == 1
            history.extend(result)
            print(f'[{tic:.1f}][{time.time() - tic:.3f}] User {uid} '
                  f'done one task, {len(history)=}')
            if open_loop_threshold is not None:
                remaining_to_wait = max(
                    0, open_loop_threshold - (time.time() - st_this_round))
                remaining_to_wait = min(remaining_to_wait,
                                        duration - (time.time() - tic))
                if remaining_to_wait > 0:
                    await asyncio.sleep(remaining_to_wait)


async def _user_task(duration: int, tic: float, uid: int, max_uid: int,
                     convs: List[Dict[str, Any]], real_uid: str, region: str,
                     open_loop_threshold: Optional[int]) -> None:
    uid_repr = f'{uid:<{len(str(max_uid))}}'
    print(f'User {uid_repr}: {len(convs)} conversations in total.')

    for i, conv in enumerate(convs):
        elapsed = time.time() - tic
        if elapsed >= duration:
            break
        # We already filtered the conversations by region in _load_dataset
        assert _filter_conv_by_region(conv, region)
        coro = _multi_turn_conv(uid, i, duration, tic, real_uid, conv,
                                open_loop_threshold)
        try:
            await coro
            print(f'User {uid_repr}: ({i+1}/{len(convs)}) conversations '
                  f'completed. Elapsed: {time.time() - tic:.2f}s')
        except asyncio.TimeoutError:
            print(f'User {uid_repr}: Conversation {i+1} '
                  f'timed out after {duration}s')
            break


async def _user_task_loop(duration: int, uid: int, max_uid: int,
                          convs: List[Dict[str,
                                           Any]], real_uid: str, region: str,
                          open_loop_threshold: Optional[int]) -> None:
    tic = time.time()
    while True:
        await _user_task(duration, tic, uid, max_uid, convs, real_uid, region,
                         open_loop_threshold)
        if time.time() - tic > duration:
            break


def launch_user_tasks(args: argparse.Namespace,
                      num_users: int) -> List[Awaitable[None]]:
    with rich_utils.client_status(
            ux_utils.spinner_message(
                f'[bold cyan]Loading dataset {DATASET_NAME}[/]')) as spinner:
        # random.seed(args.seed, args.region)
        convs = _load_dataset(args.region, args.start_index)
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
        for user in sorted_users:
            min_group_idx = min(range(num_users),
                                key=lambda idx: len(groups[idx]))
            groups[min_group_idx].extend(user_to_convs[user])
        print(f'Grouped conversations into {num_users} groups with sizes: '
              f'{sorted([len(g) for g in groups.values()])}')

    tasks: List[Awaitable[None]] = []
    for uid, user_convs in groups.items():
        if not user_convs:
            continue
        user_convs.sort(key=lambda conv: conv['timestamp'])
        tasks.append(
            _user_task_loop(args.duration, uid, len(groups), user_convs,
                            str(args.seed), args.region,
                            args.open_loop_threshold))
    return tasks

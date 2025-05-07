"""Tree of Thoughts workload."""

import argparse
import asyncio
import collections
import copy
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Awaitable, Dict, List, Union

from rich import print as rp

from sky.lbbench import oai
from sky.lbbench import utils

dataset_link = 'https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/train.jsonl'  # pylint: disable=line-too-long


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--data-path', type=str, default='@temp/test.jsonl')
    parser.add_argument('--num-branches', type=int, default=2)
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--seed', type=str, default='default')


def args_to_dict(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        'data-path': args.data_path,
        'num-branches': args.num_branches,
        'duration': args.duration,
        'seed': args.seed,
    }


# pylint: disable=line-too-long
PROPOSE_PLAN_PROMPT = """Please generate a high-level plan for solving the following question. As the first step, just say what method and idea you will use to solve the question. You can reorganize the information in the question. Do not do the actual calculation. Keep your response concise and within 80 words. Question: {question}"""
EXECUTE_PLAN_PROMPT = """The plan looks good! Now, use real numbers and do the calculation. Please solve the question step-by-step according to the high-level plan. Give me the final answer. Make your response short."""
REFLECT_PLAN_PROMPT = """Okay. Now, evaluate your own solution and give it a score on a scale of 1 to 5. Please do rigorous check of the correctness."""
FINAL_ANSWER_PROMPT = """Based on your reflection, do you change your mind? Now, give me the final answer after careful consideration."""
# pylint: enable=line-too-long

# Use a low temperature to make the results more deterministic
# and the comparison more fair.
temp = 0.001


async def _dummy_start() -> utils.OAIChatHistory:
    return []


async def _tree_search(uid: int, question: str, num_branches: int, tic: float,
                       duration: float,
                       real_user: str) -> List[utils.OAIChatHistory]:
    del uid
    prompts = [
        PROPOSE_PLAN_PROMPT.format(question=question), EXECUTE_PLAN_PROMPT,
        REFLECT_PLAN_PROMPT, FINAL_ANSWER_PROMPT
    ]
    tasks: List[asyncio.Task[Union[utils.OAIChatHistory, Exception]]] = [
        asyncio.create_task(_dummy_start())
    ]
    results: List[utils.OAIChatHistory] = []
    while tasks:
        if time.time() - tic > duration:
            # print(f'[{tic:.1f}][{time.time() - tic:.3f}]'
            #       f'[START CANCEL] User {uid} '
            #       f'cancelling {len(tasks)} tasks')
            for task in tasks:
                task.cancel()
            # print(f'[{tic:.1f}][{time.time() - tic:.3f}]'
            #       f'[END CANCEL] User {uid} '
            #       f'cancelled {len(tasks)} tasks')
            return results
        st_wait = time.time()
        # print(f'[{tic:.1f}][{st_wait - tic:.3f}][START WAIT] User {uid} '
        #       f'len tasks: {len(tasks)}')
        # Check every 10 seconds.
        timeout = min(10, duration - (time.time() - tic))
        try:
            done, pending = await asyncio.wait_for(asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED, timeout=timeout),
                                                   timeout=timeout)
        except asyncio.TimeoutError:
            continue
            # done, pending = [], tasks
        ed_wait = time.time()
        # print(f'[{tic:.1f}][{ed_wait - tic:.3f}]'
        #       f'[END WAIT] User {uid} elapsed: '
        #       f'{ed_wait - st_wait:.3f}, timeout: {timeout:.3f}, '
        #       f'len done: {len(done)}, len pending: {len(pending)}')
        if ed_wait - st_wait > timeout + 2:
            print('>>> event-loop blocked for', ed_wait - st_wait, 's',
                  len(done), len(pending), '        ', len(asyncio.all_tasks()))
            # print(">>> loop time:", asyncio.get_running_loop().time(),
            #     " wall:", time.perf_counter())
            # import traceback
            # for t in asyncio.all_tasks():
            #     print('='*100, t, t.get_coro())
            #     if not t.done():
            #         traceback.print_stack(t.get_stack()[-1])
        tasks = list(pending)
        for task in done:
            s = task.result()
            if isinstance(s, Exception):
                continue
            # st_copy = time.time()
            s = copy.deepcopy(s)
            # ed_copy = time.time()
            # print(f'[{tic:.1f}][{ed_copy - tic:.3f}]'
            #       f'[COPY] User {uid} elapsed: '
            #       f'{ed_copy - st_copy:.3f}')
            if len(s) == len(prompts) * 2 or time.time() - tic > duration:
                results.append(s)
                continue
            prompt = prompts[len(s) // 2]
            s.append(utils.get_one_round('user', prompt))
            for _ in range(num_branches):
                call_llm_coro = oai.call_chat_completion_async(
                    s,
                    temperature=temp,
                    max_tokens=256,
                    uid=real_user,
                    stop=None,
                    tic=tic,
                    duration=duration)
                tasks.append(asyncio.create_task(call_llm_coro))
    return results


async def _user_task(tic: float, uid: int, questions: List[str],
                     num_branches: int, duration: float,
                     real_user: str) -> List[utils.OAIChatHistory]:
    # time_to_sleep = uid * 10
    # rp(f'User {uid}: sleep for {time_to_sleep} seconds to start')
    # rp(f'User {uid}: {len(questions)} questions in total')
    # await asyncio.sleep(time_to_sleep)
    # rp(f'User {uid}: start sending requests')
    # tic = time.time()
    # tasks = []
    results = []
    cnt = 0
    iteration_cnt = 0
    while True:
        iteration_cnt += 1
        for question in questions:
            cnt += 1
            # print(f'[{tic:.1f}][{time.time() - tic:.3f}]'
            #       f'[START] User {uid} question {cnt} '
            #       f'iteration {iteration_cnt}', flush=True)
            result = await _tree_search(uid, question, num_branches, tic,
                                        duration, real_user)
            # print(f'[{tic:.1f}][{time.time() - tic:.3f}]'
            #       f'[END] User {uid} question {cnt} '
            #       f'iteration {iteration_cnt}', flush=True)
            results.extend(result)
            if time.time() - tic > duration:
                return results
    # for i, task in enumerate(asyncio.as_completed(tasks)):
    #     result = await task
    #     if isinstance(result, Exception):
    #         rp(f'User {uid} FAILED: {result}.'
    #            f'  Traceback: {traceback.format_exc()}')
    #         continue
    #     results.extend(result)
    #     progress = f'({i+1}/{len(questions)})'
    #     rp(f'User {uid}: {progress:^8} questions completed. '
    #        f'Latency: {time.time() - tic:.3f}')
    # return results


def download_dataset(dp: str) -> None:
    if not os.path.exists(dp):
        Path(dp).parent.mkdir(parents=True, exist_ok=True)
        rp(f'Data path {dp} does not exist. Downloading...')
        subprocess.run(['wget', f'{dataset_link}', '-O', dp], check=True)


seed2idx = {
    'us-east-2': 0,
    'ap-northeast-1': 1,
    'eu-central-1': 2,
}


def launch_user_tasks(
        args: argparse.Namespace,
        num_users: int) -> List[Awaitable[List[utils.OAIChatHistory]]]:
    uid2questions = collections.defaultdict(list)
    idx = 0
    dp = args.data_path
    download_dataset(dp)
    seed = args.seed
    if seed not in seed2idx:
        raise ValueError(f'Invalid seed: {seed}')
    desired_idx = seed2idx[seed]
    with open(dp, encoding='utf-8') as fin:
        for i, line in enumerate(fin):
            if line.startswith('#'):
                continue
            # Use different sets of questions for different regions.
            if i % len(seed2idx) != desired_idx:
                continue
            uid2questions[idx % num_users].append(json.loads(line)['question'])
            idx += 1
            if idx >= 100 * num_users:
                break
    tic = time.time()
    return [
        asyncio.create_task(
            _user_task(tic, uid, questions, args.num_branches, args.duration,
                       seed)) for uid, questions in uid2questions.items()
    ]

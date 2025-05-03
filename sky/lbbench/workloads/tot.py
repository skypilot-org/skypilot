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


async def _tree_search(question: str, num_branches: int, tic: float,
                       duration: float,
                       real_user: str) -> List[utils.OAIChatHistory]:
    prompts = [
        PROPOSE_PLAN_PROMPT.format(question=question), EXECUTE_PLAN_PROMPT,
        REFLECT_PLAN_PROMPT, FINAL_ANSWER_PROMPT
    ]
    tasks: List[asyncio.Task[Union[utils.OAIChatHistory, Exception]]] = [
        asyncio.create_task(_dummy_start())
    ]
    results: List[utils.OAIChatHistory] = []
    while tasks:
        done, pending = await asyncio.wait(tasks,
                                           return_when=asyncio.FIRST_COMPLETED)
        tasks = list(pending)
        for task in done:
            s = task.result()
            if isinstance(s, Exception):
                continue
            s = copy.deepcopy(s)
            if len(s) == len(prompts) * 2:
                results.append(s)
                continue
            if time.time() - tic > duration:
                continue
            prompt = prompts[len(s) // 2]
            s.append(utils.get_one_round('user', prompt))
            for _ in range(num_branches):
                call_llm_coro = oai.call_chat_completion_async(s,
                                                               temperature=temp,
                                                               max_tokens=256,
                                                               uid=real_user,
                                                               stop=None)
                tasks.append(asyncio.create_task(call_llm_coro))
    return results


async def _user_task(uid: int, questions: List[str], num_branches: int,
                     duration: float,
                     real_user: str) -> List[utils.OAIChatHistory]:
    # time_to_sleep = uid * 10
    # rp(f'User {uid}: sleep for {time_to_sleep} seconds to start')
    rp(f'User {uid}: {len(questions)} questions in total')
    # await asyncio.sleep(time_to_sleep)
    rp(f'User {uid}: start sending requests')
    tic = time.time()
    # tasks = []
    results = []
    while True:
        for question in questions:
            result = await _tree_search(question, num_branches, tic, duration,
                                        real_user)
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
            if i % num_users != desired_idx:
                continue
            uid2questions[idx % num_users].append(json.loads(line)['question'])
            idx += 1
            if idx >= 100 * num_users:
                break
    return [
        _user_task(uid, questions, args.num_branches, args.duration, seed)
        for uid, questions in uid2questions.items()
    ]

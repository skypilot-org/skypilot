"""Tree of Thoughts workload."""

import argparse
import asyncio
import collections
import copy
import json
import os
import subprocess
import time
import traceback
from typing import Any, Awaitable, Dict, List, Union

from rich import print as rp

from sky.lbbench import oai
from sky.lbbench import utils

dataset_link = 'https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl'  # pylint: disable=line-too-long


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--data-path', type=str, default='@temp/test.jsonl')
    parser.add_argument('--num-questions', type=int, default=200)
    parser.add_argument('--num-branches', type=int, default=2)


def args_to_dict(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        'data-path': args.data_path,
        'num-questions': args.num_questions,
        'num-branches': args.num_branches,
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


async def _tree_search(uid: int, question: str,
                       num_branches: int) -> List[utils.OAIChatHistory]:
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
            prompt = prompts[len(s) // 2]
            s.append(utils.get_one_round('user', prompt))
            for _ in range(num_branches):
                call_llm_coro = oai.call_chat_completion_async(s,
                                                               temperature=temp,
                                                               max_tokens=256,
                                                               uid=uid,
                                                               stop=None)
                tasks.append(asyncio.create_task(call_llm_coro))
    return results


async def _user_task(uid: int, questions: List[str],
                     num_branches: int) -> List[utils.OAIChatHistory]:
    time_to_sleep = uid * 10
    rp(f'User {uid}: sleep for {time_to_sleep} seconds to start')
    rp(f'User {uid}: {len(questions)} questions in total')
    await asyncio.sleep(time_to_sleep)
    rp(f'User {uid}: start sending requests')
    tic = time.time()
    tasks = []
    for question in questions:
        tasks.append(
            asyncio.create_task(_tree_search(uid, question, num_branches)))
    results = []
    for i, task in enumerate(asyncio.as_completed(tasks)):
        result = await task
        if isinstance(result, Exception):
            rp(f'User {uid} FAILED: {result}.'
               f'  Traceback: {traceback.format_exc()}')
            continue
        results.extend(result)
        progress = f'({i+1}/{len(questions)})'
        rp(f'User {uid}: {progress:^8} questions completed. '
           f'Latency: {time.time() - tic:.3f}')
    return results


def launch_user_tasks(
        args: argparse.Namespace,
        num_users: int) -> List[Awaitable[List[utils.OAIChatHistory]]]:
    uid2questions = collections.defaultdict(list)
    idx = 0
    dp = args.data_path
    if not os.path.exists(dp):
        rp(f'Data path {dp} does not exist. Downloading...')
        subprocess.run(['wget', f'wget {dataset_link}', '-O', dp], check=True)
    with open(dp, encoding='utf-8') as fin:
        for line in fin:
            if line.startswith('#'):
                continue
            uid2questions[idx % num_users].append(json.loads(line)['question'])
            idx += 1
            if idx >= args.num_questions:
                break
    return [
        _user_task(uid, questions, args.num_branches)
        for uid, questions in uid2questions.items()
    ]

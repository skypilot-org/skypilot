import argparse
import asyncio
import collections
import copy
import dataclasses
import json
import threading
import time
import traceback
from typing import Optional

import numpy as np
import openai  # 1.68.0
from openai.types.chat import ChatCompletionStreamOptionsParam
import requests
from rich import print


@dataclasses.dataclass
class Metric:
    uid: int
    start: Optional[float] = None
    end: Optional[float] = None
    ttft: Optional[float] = None
    e2e_latency: Optional[float] = None
    failed: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: int = 0


def _get_one_round(role, content):
    return {"role": role, "content": content}


global_metrics: list[Metric] = []
lock = threading.Lock()

async_client: Optional[openai.AsyncOpenAI] = None
model: Optional[str] = None


async def oai_call_chat_completion_async(messages,
                                         temperature,
                                         max_tokens,
                                         uid,
                                         stop=None):
    output = ""
    metric = Metric(uid=uid)
    assert async_client is not None and model is not None
    try:
        res = await async_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            stream=True,
            stream_options=ChatCompletionStreamOptionsParam(include_usage=True),
            extra_headers={"x-hash-key": str(uid)},
        )
        st = time.perf_counter()
        metric.start = st
        fetch_metric_at_end = False
        async for chunk in res:
            t_this_round = time.perf_counter()
            choice = chunk.choices[0]
            if metric.ttft is None:
                metric.ttft = t_this_round - st
            if chunk.usage is None:
                fetch_metric_at_end = True
            if not fetch_metric_at_end:
                if metric.input_tokens is None:
                    metric.input_tokens = chunk.usage.prompt_tokens
                else:
                    assert metric.input_tokens == chunk.usage.prompt_tokens
                metric.output_tokens += chunk.usage.completion_tokens
            # For vLLM, finish_reason is None; for SGLang, finish_reason is empty string ''.
            if choice.finish_reason:
                metric.e2e_latency = t_this_round - st
                metric.end = t_this_round
                if fetch_metric_at_end:
                    # For SGLang, the usage is only included in the last chunk.
                    async for chunk in res:
                        metric.input_tokens = chunk.usage.prompt_tokens
                        metric.output_tokens += chunk.usage.completion_tokens
                        break
                break
            delta = choice.delta.content
            output += delta
    except Exception as e:
        exception = e
        print(f"Error: {e}\n"
              f"  Traceback: {traceback.format_exc()}")
        metric.failed = str(e)
    with lock:
        global_metrics.append(metric)
    if metric.failed is not None:
        return exception
    return messages + [_get_one_round("assistant", output)]


PROPOSE_PLAN_PROMPT = """Please generate a high-level plan for solving the following question. As the first step, just say what method and idea you will use to solve the question. You can reorganize the information in the question. Do not do the actual calculation. Keep your response concise and within 80 words. Question: {question}"""
EXECUTE_PLAN_PROMPT = """The plan looks good! Now, use real numbers and do the calculation. Please solve the question step-by-step according to the high-level plan. Give me the final answer. Make your response short."""
REFLECT_PLAN_PROMPT = """Okay. Now, evaluate your own solution and give it a score on a scale of 1 to 5. Please do rigorous check of the correctness."""
FINAL_ANSWER_PROMPT = """Based on your reflection, do you change your mind? Now, give me the final answer after careful consideration."""

# Use a low temp to make the results more deterministic and the comparison more fair.
temp = 0.001


async def _dummy_start():
    return []


async def tree_search(uid, question, num_branches):
    prompts = [
        PROPOSE_PLAN_PROMPT.format(question=question), EXECUTE_PLAN_PROMPT,
        REFLECT_PLAN_PROMPT, FINAL_ANSWER_PROMPT
    ]
    tasks = [asyncio.create_task(_dummy_start())]
    results = []
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
            s.append(_get_one_round("user", prompt))
            for _ in range(num_branches):
                call_llm_coro = oai_call_chat_completion_async(s,
                                                               temperature=temp,
                                                               max_tokens=256,
                                                               uid=uid,
                                                               stop=None)
                tasks.append(asyncio.create_task(call_llm_coro))
    return results


async def user_task(uid, questions, num_branches):
    time_to_sleep = uid * 10
    print(f"User {uid}: sleep for {time_to_sleep} seconds to start")
    print(f"User {uid}: {len(questions)} questions in total")
    await asyncio.sleep(time_to_sleep)
    tic = time.time()
    tasks = []
    for question in questions:
        tasks.append(
            asyncio.create_task(tree_search(uid, question, num_branches)))
    results = []
    for i, task in enumerate(asyncio.as_completed(tasks)):
        result = await task
        results.append(result)
        progress = f"({i+1}/{len(questions)})"
        print(f"User {uid}: {progress:^8} questions completed. "
              f"Latency: {time.time() - tic:.3f}")
    return results


async def main(args):
    num_users = args.num_users
    uid2questions = collections.defaultdict(list)
    idx = 0
    with open(args.data_path) as fin:
        for line in fin:
            if line.startswith("#"):
                continue
            uid2questions[idx % num_users].append(json.loads(line)["question"])
            idx += 1
            if idx >= args.num_questions:
                break

    if args.backend_url is None:
        raise ValueError("backend_url is required")
    url = args.backend_url
    if not url.startswith("http://"):
        url = "http://" + url
    resp = requests.get(f"{url}/v1/models")
    global model, async_client
    model = resp.json()['data'][0]['id']
    async_client = openai.AsyncOpenAI(base_url=f"{url}/v1",
                                      api_key="placeholder")

    tic = time.time()
    results = await asyncio.gather(*[
        user_task(uid, questions, args.num_branches)
        for uid, questions in uid2questions.items()
    ])
    latency = time.time() - tic
    print(f"All E2E Latency: {latency:.3f}")

    with open(args.result_file, "w") as fout:
        value = {
            "task": "tree_of_thought_gsm8k",
            "latency": round(latency, 3),
            "metrics": [dataclasses.asdict(m) for m in global_metrics],
            "num_requests": args.num_questions,
            "other": {
                "num_questions": args.num_questions,
                "num_branches": args.num_branches,
                # "parallel": args.parallel,
                "backend_url": args.backend_url,
            },
        }
        fout.write(json.dumps(value) + "\n")

    total_tpt_tokens = 0
    total_times = 0
    ttfts = []
    e2e_latencies = []
    for m in global_metrics:
        total_tpt_tokens += m.input_tokens + 2 * m.output_tokens
        total_times += m.e2e_latency
        ttfts.append(m.ttft)
        e2e_latencies.append(m.e2e_latency)
    print(f"{'TPT':=^50}")
    print(f"Per request: {total_tpt_tokens / total_times:.3f}")
    print(f"Per second: {total_tpt_tokens / latency:.3f}")
    print(f"{'TTFT':=^50}")
    print(f"Mean: {np.mean(ttfts):.3f}")
    print(f"P50: {np.percentile(ttfts, 50):.3f}")
    print(f"P90: {np.percentile(ttfts, 90):.3f}")
    print(f"P99: {np.percentile(ttfts, 99):.3f}")
    print(f"{'E2E Latency':=^50}")
    print(f"Mean: {np.mean(e2e_latencies):.3f}")
    print(f"P50: {np.percentile(e2e_latencies, 50):.3f}")
    print(f"P90: {np.percentile(e2e_latencies, 90):.3f}")
    print(f"P99: {np.percentile(e2e_latencies, 99):.3f}")


if __name__ == "__main__":
    # wget https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl
    # py examples/serve/external-lb/bench.py --data-path @temp/test.jsonl --result-file @temp/result.jsonl --num-branches 2 --num-users 5 --num-questions 1 --backend-url vllmtest.aws.cblmemo.net:8000
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-path", type=str, default="test.jsonl")
    parser.add_argument("--result-file", type=str, default="result.json")
    parser.add_argument("--num-questions", type=int, default=200)
    parser.add_argument("--num-branches", type=int, default=2)
    # TODO(tian): Implement different users.
    parser.add_argument("--num-users", type=int, default=5)
    parser.add_argument("--backend-url", type=str, default=None)
    # parser.add_argument("--parallel", type=int, default=64)
    parser.add_argument("--n-ctx", type=int, default=4096)
    args = parser.parse_args()
    asyncio.run(main(args))

"""OpenAI client."""

import asyncio
import json
import time
import typing
from typing import List, Optional, Union

import aiohttp
import openai  # 1.68.0
from openai.types.chat import ChatCompletionStreamOptionsParam
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_message_param import (
    ChatCompletionMessageParam)

from sky.lbbench import utils

global_metrics: List[utils.Metric] = []
lock = None

async_client: Optional[openai.AsyncOpenAI] = None
model: Optional[str] = None


async def init_oai(url: str) -> None:
    global model, async_client, lock
    async with aiohttp.ClientSession() as session:
        async with session.get(f'{url}/v1/models') as resp:
            try:
                data = await resp.json()
            except Exception:  # pylint: disable=broad-except
                # SGLang Router does not has the Content-Type header.
                # Here we manually parse the response as JSON.
                data = json.loads(await resp.text())
            model = data['data'][0]['id']
    async_client = openai.AsyncOpenAI(base_url=f'{url}/v1',
                                      api_key='placeholder',
                                      max_retries=0)
    lock = asyncio.Lock()


async def call_chat_completion_async(
    messages: utils.OAIChatHistory,
    temperature: float,
    max_tokens: int,
    uid: str,
    stop: Optional[List[str]] = None,
    only_return_new_round: bool = False,
    tic: Optional[float] = None,
    duration: Optional[float] = None,
    hash_key: Optional[str] = None,
    program_id: Optional[str] = None,
) -> Union[utils.OAIChatHistory, Exception]:
    assert lock is not None
    typed_messages = typing.cast(List[ChatCompletionMessageParam], messages)
    output = ''
    metric = utils.Metric(uid=uid)
    metric.hash_key = hash_key
    metric.program_id = program_id
    exception: Optional[Exception] = None
    assert async_client is not None and model is not None
    try:
        default_timeout = 100.
        if tic is not None and duration is not None:
            if time.time() - tic > duration:
                return []
            default_timeout = min(default_timeout,
                                  duration - (time.time() - tic))
        st = time.time()
        metric.start = st
        res = await asyncio.wait_for(async_client.chat.completions.create(
            model=model,
            messages=typed_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            stream=True,
            stream_options=ChatCompletionStreamOptionsParam(include_usage=True),
            extra_headers={
                'x-hash-key': str(uid) if hash_key is None else hash_key
            },
            timeout=default_timeout,
        ),
                                     timeout=default_timeout)
        metric.headers = dict(res.response.headers)
        metric.response_start = time.time()
        if metric.response_start - st > default_timeout:
            print('/' * 30, metric.response_start - st, default_timeout)
        fetch_metric_at_end = False
        async for chunk in res:
            if tic is not None and duration is not None and time.time(
            ) - tic > duration:
                metric.failed = 'timeout'
                exception = RuntimeError('timeout')
                break
            t_this_round = time.time()
            choice = chunk.choices[0]
            if metric.streaming_start is None:
                metric.streaming_start = t_this_round
            if metric.ttft is None:
                metric.ttft = t_this_round - st
            if chunk.usage is None:
                fetch_metric_at_end = True
            if not fetch_metric_at_end:
                assert chunk.usage is not None
                if metric.input_tokens is None:
                    metric.input_tokens = chunk.usage.prompt_tokens
                else:
                    assert metric.input_tokens == chunk.usage.prompt_tokens
                metric.output_tokens += chunk.usage.completion_tokens
            # For vLLM, finish_reason is None;
            # for SGLang, finish_reason is empty string ''.
            if choice.finish_reason:
                metric.e2e_latency = t_this_round - st
                metric.end = t_this_round
                if fetch_metric_at_end:

                    def _record_metric(chunk: ChatCompletionChunk) -> None:
                        metric.input_tokens = chunk.usage.prompt_tokens
                        metric.output_tokens += chunk.usage.completion_tokens
                        metric.cached_tokens = (
                            chunk.usage.prompt_tokens_details.cached_tokens)

                    # For SGLang, the usage is only included in the last chunk.
                    # v0.4.3.post2, usage in next chunk
                    # async for chunk in res:
                    #     _record_metric(chunk)
                    #     break
                    # v0.4.5, usage in current chunk
                    _record_metric(chunk)
                break
            delta = choice.delta.content
            if delta is not None:
                output += delta
    except Exception as e:  # pylint: disable=broad-except
        exception = e
        # rp(f'Error: {e}\n'
        #    f'  Traceback: {traceback.format_exc()}')
        metric.failed = str(e)
    async with lock:
        global_metrics.append(metric)
    if metric.failed is not None:
        assert exception is not None
        return exception
    new_round = [utils.get_one_round('assistant', output)]
    if only_return_new_round:
        return new_round
    return messages + new_round

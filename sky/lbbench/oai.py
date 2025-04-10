"""OpenAI client."""

import threading
import time
import traceback
import typing
from typing import List, Optional, Union

import aiohttp
import openai  # 1.68.0
from openai.types.chat import ChatCompletionStreamOptionsParam
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_message_param import (
    ChatCompletionMessageParam)
from rich import print as rp

from sky.lbbench import utils

global_metrics: List[utils.Metric] = []
lock = threading.Lock()

async_client: Optional[openai.AsyncOpenAI] = None
model: Optional[str] = None


async def init_oai(url: str) -> None:
    global model, async_client
    async with aiohttp.ClientSession() as session:
        async with session.get(f'{url}/v1/models') as resp:
            data = await resp.json()
            model = data['data'][0]['id']
    async_client = openai.AsyncOpenAI(base_url=f'{url}/v1',
                                      api_key='placeholder')


async def call_chat_completion_async(
        messages: utils.OAIChatHistory,
        temperature: float,
        max_tokens: int,
        uid: int,
        stop: Optional[List[str]] = None
) -> Union[utils.OAIChatHistory, Exception]:
    typed_messages = typing.cast(List[ChatCompletionMessageParam], messages)
    output = ''
    metric = utils.Metric(uid=uid)
    exception: Optional[Exception] = None
    assert async_client is not None and model is not None
    try:
        res = await async_client.chat.completions.create(
            model=model,
            messages=typed_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            stream=True,
            stream_options=ChatCompletionStreamOptionsParam(include_usage=True),
            extra_headers={'x-hash-key': str(uid)},
            timeout=100,
        )
        metric.headers = dict(res.response.headers)
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
        rp(f'Error: {e}\n'
           f'  Traceback: {traceback.format_exc()}')
        metric.failed = str(e)
    with lock:
        global_metrics.append(metric)
    if metric.failed is not None:
        assert exception is not None
        return exception
    return messages + [utils.get_one_round('assistant', output)]

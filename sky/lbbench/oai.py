"""OpenAI client."""

import json
import logging
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

logger = logging.getLogger(__name__)

async def init_oai(url: str) -> None:
    global model, async_client
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
                                      api_key='placeholder')


async def call_chat_completion_async(
    messages: utils.OAIChatHistory,
    temperature: float,
    max_tokens: int,
    uid: str,
    stop: Optional[List[str]] = None,
    only_return_new_round: bool = False
) -> Union[utils.OAIChatHistory, Exception]:
    typed_messages = typing.cast(List[ChatCompletionMessageParam], messages)
    output = ''
    metric = utils.Metric(uid=uid)
    exception: Optional[Exception] = None
    assert async_client is not None and model is not None
    try:
        st = time.time()
        metric.start = st
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
        metric.response_start = time.time()
        fetch_metric_at_end = False

        iterator = res.__aiter__()
        while True:
            try:
                chunk = await iterator.__anext__()

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

                logger.info(f"completion_tokens: {chunk.usage.completion_tokens}")
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

            except StopAsyncIteration:
                # Stream finished normally, but maybe without a finish_reason chunk?
                logger.info("Stream finished via StopAsyncIteration.")
                if metric.end is None:  # Record end time if not already set
                    metric.end = time.time()
                if metric.e2e_latency is None and metric.start is not None:
                    metric.e2e_latency = metric.end - metric.start
                break  # Exit while loop

            except json.JSONDecodeError as json_err:
                # Catch the specific JSON error likely caused by heartbeats
                logger.warning("json value: %s", chunk)
                if "Expecting value: line 1 column 1 (char 0)" in str(json_err):
                    logger.warning(f"Ignoring JSONDecodeError likely caused by heartbeat/empty event: {json_err}")
                    # Simply continue to the next iteration to wait for valid data
                    continue
                else:
                    # Unexpected JSON error
                    logger.error(f"Unexpected JSONDecodeError while iterating stream: {json_err}")
                    exception = json_err
                    metric.failed = str(json_err)
                    if metric.end is None:
                        metric.end = time.time()
                    break  # Exit while loop
            except Exception as e:
                # Catch other errors during iteration
                logger.exception(f"Error during stream iteration: {e}")
                exception = e
                metric.failed = str(e)
                if metric.end is None:
                    metric.end = time.time()
                break  # Exit while loop
        # --- END Manual Iteration ---

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
    new_round = [utils.get_one_round('assistant', output)]
    if only_return_new_round:
        return new_round
    return messages + new_round

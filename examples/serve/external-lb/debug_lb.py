import asyncio
import json
import logging
import multiprocessing
import random
import threading
import time

import fastapi
import uvicorn

from sky.serve import load_balancer

REPLICA_URLS = {}
PROCESSES = []
CONTROLLER_PORT = 20018
MAX_CONCURRENCY = 5
LB_PORTS = [6001 + i for i in range(3)]
WORD_TO_STREAM = 'Hello world! Nice to meet you!'
TIME_TO_SLEEP = 1.2
REPLICA_KEY = 'self'


class MetricsFilter(logging.Filter):

    def filter(self, record):
        return '/metrics' not in record.getMessage()


def _start_streaming_replica(port):
    app = fastapi.FastAPI()

    @app.get('/')
    async def stream():

        async def generate_words():
            for word in WORD_TO_STREAM.split():
                yield word + "\n"
                # print('===========WORKER', word)
                await asyncio.sleep(TIME_TO_SLEEP)
            yield f'{port}\n'

        return fastapi.responses.StreamingResponse(generate_words(),
                                                   media_type="text/plain")

    @app.post('/sleep')
    async def sleep(request: fastapi.Request):
        time_to_sleep = (await request.json())['time_to_sleep']
        await asyncio.sleep(time_to_sleep)
        return {'message': 'Sleeping done'}

    @app.post('/v1/chat/completions')
    async def chat_completions(request: fastapi.Request):
        body = await request.json()
        messages = body.get('messages', [])
        temperature = body.get('temperature', 0.7)
        max_tokens = body.get('max_tokens', 100)
        stream = body.get('stream', False)
        uid = random.randint(1, 10000)

        if stream:

            async def generate_stream():
                # Simulate streaming response
                for i in range(5):
                    await asyncio.sleep(0.2)
                    chunk = {
                        "choices": [{
                            "delta": {
                                "content": f"chunk {i} "
                            },
                            "finish_reason": None if i < 4 else "stop"
                        }]
                    }
                    if i == 4:
                        chunk["choices"][0]["finish_reason"] = "stop"
                        chunk["usage"] = {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "prompt_tokens_details": {
                                "cached_tokens": 5
                            }
                        }

                    yield f"data: {json.dumps(chunk)}\n\n"

                final_chunk = {}

                yield f"data: {json.dumps(final_chunk)}\n\n"

                yield "data: [DONE]\n\n"

            return fastapi.responses.StreamingResponse(
                generate_stream(), media_type="text/event-stream")
        else:
            await asyncio.sleep(1)
            return {
                "choices": [{
                    "message": {
                        "content": "This is a test response"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15
                }
            }

    @app.get('/v1/models')
    async def models():
        return {
            'data': [{
                'id': 'meta-llama/Meta-Llama-3-8B-Instruct',
                'object': 'model',
                'owned_by': 'meta'
            }]
        }

    @app.get('/metrics')
    async def metrics():
        return fastapi.responses.PlainTextResponse(
            "# HELP vllm:num_requests_waiting Number of requests waiting in queue\n"
            "# TYPE vllm:num_requests_waiting gauge\n"
            "vllm:num_requests_waiting 0.0\n")

    @app.get('/non-stream')
    async def non_stream():
        return {'message': WORD_TO_STREAM}

    @app.get('/error')
    async def error():
        raise fastapi.HTTPException(status_code=500,
                                    detail='Internal Server Error')

    @app.on_event('startup')
    def on_startup():
        uvicorn_access_logger = logging.getLogger('uvicorn.access')
        for handler in uvicorn_access_logger.handlers:
            # handler.setFormatter(sky_logging.FORMATTER)
            handler.addFilter(MetricsFilter())

    uvicorn.run(app, host='0.0.0.0', port=port)


def _start_streaming_replica_in_process(port, lb_port):
    global PROCESSES, REPLICA_URLS
    STREAMING_REPLICA_PROCESS = multiprocessing.Process(
        target=_start_streaming_replica, args=(port,))
    STREAMING_REPLICA_PROCESS.start()
    PROCESSES.append(STREAMING_REPLICA_PROCESS)
    rk = f'{REPLICA_KEY}-{lb_port}'
    if rk not in REPLICA_URLS:
        REPLICA_URLS[rk] = []
    REPLICA_URLS[rk].append(f'http://0.0.0.0:{port}')


def _start_controller(rurls):
    app = fastapi.FastAPI()
    flip_flop = False

    @app.post('/controller/load_balancer_sync')
    async def lb_sync(request: fastapi.Request):
        print('===========LB SYNC')
        print(rurls)
        return {
            'ready_replica_urls': rurls,
            'ready_lb_urls': {
                REPLICA_KEY: [f'http://0.0.0.0:{lp}' for lp in LB_PORTS]
            }
        }

        # with open('@temp/ready_replica_urls.txt', 'r') as f:
        #     replica_urls = f.read().splitlines()
        # return {'ready_replica_urls': replica_urls}

        # nonlocal flip_flop
        # if flip_flop:
        #     ready_replica_urls = []
        # else:
        #     ready_replica_urls = [f'http://0.0.0.0:{i}' for i in range(7001, 7101)]
        # flip_flop = not flip_flop
        # return {'ready_replica_urls': ready_replica_urls}

    uvicorn.run(app, host='0.0.0.0', port=CONTROLLER_PORT)


def _start_controller_in_process(rurls):
    global PROCESSES
    CONTROLLER_PROCESS = multiprocessing.Process(target=_start_controller,
                                                 args=(rurls,))
    CONTROLLER_PROCESS.start()
    PROCESSES.append(CONTROLLER_PROCESS)


def _start_lb_in_process(lb_port):
    global PROCESSES
    lb = load_balancer.SkyServeLoadBalancer(
        controller_url=f'http://0.0.0.0:{CONTROLLER_PORT}',
        load_balancer_port=lb_port,
        meta_load_balancing_policy_name='prefix_tree',
        load_balancing_policy_name='prefix_tree',
        # load_balancing_policy_name='least_load',
        region=f'{REPLICA_KEY}-{lb_port}',
        max_concurrent_requests=MAX_CONCURRENCY,
        is_local_debug_mode=True,
        use_ie_queue_indicator=False)
    lb_proc = threading.Thread(target=lb.run, daemon=True)
    lb_proc.start()
    # PROCESSES.append(lb_proc)


if __name__ == '__main__':
    try:
        replica_port_start = 7001
        for lb_port in LB_PORTS:
            num_replica = random.randint(1, 3)
            for _ in range(num_replica):
                _start_streaming_replica_in_process(replica_port_start, lb_port)
                replica_port_start += 1
        print('===========REPLICA URLS')
        print(REPLICA_URLS)
        _start_controller_in_process(REPLICA_URLS)
        for lb_port in LB_PORTS:
            _start_lb_in_process(lb_port)
        for p in PROCESSES:
            p.join()
    finally:
        for p in PROCESSES:
            if isinstance(p, multiprocessing.Process):
                p.terminate()

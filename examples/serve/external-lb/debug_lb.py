import asyncio
import multiprocessing
import random
import threading

import fastapi
import uvicorn

from sky.serve import load_balancer

REPLICA_URLS = {}
PROCESSES = []
CONTROLLER_PORT = 20018
LB_PORTS = [6001, 6002]
WORD_TO_STREAM = 'Hello world! Nice to meet you!'
TIME_TO_SLEEP = 0.2
REPLICA_KEY = 'self'


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

    @app.get('/metrics')
    async def metrics():
        return {'gauge_gpu_cache_usage': random.choice([0.2, 0.3, 0.5])}

    @app.get('/non-stream')
    async def non_stream():
        return {'message': WORD_TO_STREAM}

    @app.get('/error')
    async def error():
        raise fastapi.HTTPException(status_code=500,
                                    detail='Internal Server Error')

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
        # meta_load_balancing_policy_name='proximate_first',
        load_balancing_policy_name='consistent_hashing',
        # load_balancing_policy_name='least_load',
        region=f'{REPLICA_KEY}-{lb_port}',
        max_concurrent_requests=2,
        max_queue_size=10000,
        is_local_debug_mode=True)
    lb_proc = threading.Thread(target=lb.run, daemon=True)
    lb_proc.start()
    # PROCESSES.append(lb_proc)


if __name__ == '__main__':
    try:
        _start_streaming_replica_in_process(7001, LB_PORTS[0])
        _start_streaming_replica_in_process(7002, LB_PORTS[0])
        _start_streaming_replica_in_process(7003, LB_PORTS[1])
        _start_streaming_replica_in_process(7004, LB_PORTS[1])
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

"""Main application for Sky Map.
"""
import argparse
import asyncio
import atexit
import json

from backing_store import BackingStore
import fastapi
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response
import uvicorn

from sky import sky_logging
from sky.map import map_utils
from sky.map.active_probe import ActiveProbe
from sky.map.zone_monitor import ZoneMonitor

logger = sky_logging.init_logger('sky.map.main')

app = fastapi.FastAPI()
backing_store = BackingStore('')
zone_monitor = ZoneMonitor()
active_explorer = ActiveProbe()
templates = Jinja2Templates(directory='sky/map/templates')
counter = 0


def exit_handler():
    print('Flushing data to disk...')
    backing_store.store(zone_monitor)


@app.get('/')
def home():
    return 'Sky Map'


@app.get('/health')
def healthy():
    return 'Healthy'


@app.post('/add-preempt')
def add_preempt(request: fastapi.Request):
    data = asyncio.run(request.json())
    zone = data['zone']
    time = data['time']
    resource = data['resource']
    zone_monitor.add_zone_preempt_data(zone, float(time), resource)
    backing_store.store(zone_monitor)
    user_data = {
        'zone': zone,
        'time': time,
        'resources': resource,
        'status': 'success'
    }
    return fastapi.responses.JSONResponse(content=user_data, status_code=201)


@app.post('/add-wait')
def add_wait(request: fastapi.Request):
    data = asyncio.run(request.json())
    zone = data['zone']
    time = data['time']
    resource = data['resource']
    zone_monitor.add_zone_wait_data(zone, float(time), resource)
    backing_store.store(zone_monitor)
    user_data = {
        'zone': zone,
        'time': time,
        'resources': resource,
        'status': 'success'
    }
    return fastapi.responses.JSONResponse(content=user_data, status_code=201)


@app.post('/flush-to-disk')
def flush_to_disk():
    backing_store.store(zone_monitor)
    user_data = {'status': 'success'}
    return fastapi.responses.JSONResponse(content=user_data, status_code=201)


@app.post('/active-explore/{cloud}/{region}/{accelerator}/{password}')
def active_explore(cloud: str, region: str, accelerator: str, password: str):
    active_explorer.active_probe(cloud, region, accelerator, password)
    user_data = {'status': 'success'}
    return fastapi.responses.JSONResponse(content=user_data, status_code=201)


@app.get('/get-average-wait-time/{zone}/{time}/{resource}')
def get_average_wait_time(zone: str, time: str, resource: str):
    wait_time = zone_monitor.get_zone_average_wait_time(zone, float(time),
                                                        resource)
    data = {'wait_time': wait_time}
    return fastapi.responses.JSONResponse(content=data, status_code=200)


@app.get('/get-average-preempt-time/{zone}/{time}/{resource}')
def get_average_preempt_time(zone: str, time: str, resource: str):
    print(zone, time, resource)
    preempt_time = zone_monitor.get_zone_average_preempt_time(
        zone, float(time), resource)
    data = {'preempt_time': preempt_time}
    return fastapi.responses.JSONResponse(content=data, status_code=200)


@app.get('/get-average-wait-time/{zone}/{resource}')
def get_average_wait_time_all_time(zone: str, resource: str):
    wait_time = zone_monitor.get_zone_average_wait_time(zone, -1, resource)
    data = {'wait_time': wait_time}
    return fastapi.responses.JSONResponse(content=data, status_code=200)


@app.get('/get-average-preempt-time/{zone}/{resource}')
def get_average_preempt_time_all_time(zone: str, resource: str):
    preempt_time = zone_monitor.get_zone_average_preempt_time(
        zone, -1, resource)
    data = {'preempt_time': preempt_time}
    return fastapi.responses.JSONResponse(content=data, status_code=200)


@app.get('/get-num-preempt/{zone}/{time}/{resource}')
def get_num_preempt(zone: str, time: str, resource: str):
    num_preempt = zone_monitor.get_num_preempt(zone, float(time), resource)
    data = {'num_preempt': num_preempt}
    return fastapi.responses.JSONResponse(content=data, status_code=200)


@app.get('/get-num-wait/{zone}/{time}/{resource}')
def get_num_wait(zone: str, time: str, resource: str):
    num_wait = zone_monitor.get_num_wait(zone, float(time), resource)
    data = {'num_wait': num_wait}
    return fastapi.responses.JSONResponse(content=data, status_code=200)


@app.get('/get-all-zone-info')
def get_all_zone_info():
    zone_info = zone_monitor.get_zone_info()
    data = {
        'zone_info': zone_info,
    }
    return map_utils.PrettyJSONResponse(content=data, status_code=200)


async def retrieve_zone_preempt_data(zone: str, resource: str):

    idx = 0
    while True:
        duration, timestamp = zone_monitor.get_preempt_data_with_idx(
            zone, idx, resource)
        if timestamp is None:
            await asyncio.sleep(1)
            continue

        json_data = json.dumps({
            'time': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'duration': duration,
        })
        yield f'data:{json_data}\n\n'
        idx += 1


async def retrieve_zone_wait_data(zone: str, resource: str):

    idx = 0
    while True:
        duration, timestamp = zone_monitor.get_wait_data_with_idx(
            zone, idx, resource)
        if timestamp is None:
            await asyncio.sleep(1)
            continue

        json_data = json.dumps({
            'time': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'duration': duration,
        })
        yield f'data:{json_data}\n\n'
        idx += 1


# https://github.com/roniemartinez/real-time-charts-with-fastapi/tree/master
@app.get('/chart-preempt-data/{zone}/{resource}')
async def chart_preempt_data(zone: str, resource: str) -> StreamingResponse:
    response = StreamingResponse(retrieve_zone_preempt_data(zone, resource),
                                 media_type='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@app.get('/chart-wait-data/{zone}/{resource}')
async def chart_wait_data(zone: str, resource: str) -> StreamingResponse:
    response = StreamingResponse(retrieve_zone_wait_data(zone, resource),
                                 media_type='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@app.get('/visualize-preempt/{zone}/{resource}', response_class=HTMLResponse)
async def preempt_index(request: Request, zone: str, resource: str) -> Response:
    return templates.TemplateResponse('visualize-preempt.html', {
        'request': request,
        'zone': zone,
        'resource': resource
    })


@app.get('/visualize-wait/{zone}/{resource}', response_class=HTMLResponse)
async def wait_index(request: Request, zone: str, resource: str) -> Response:
    return templates.TemplateResponse('visualize-wait.html', {
        'request': request,
        'zone': zone,
        'resource': resource
    })


atexit.register(exit_handler)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyMap Server')
    parser.add_argument('--port', type=int, required=False, default=8081)
    parser.add_argument('--backing-store',
                        type=str,
                        required=False,
                        default='~/store.db')
    args = parser.parse_args()
    backing_store = BackingStore(args.backing_store)
    zone_monitor = backing_store.load()
    print('serving at port', args.port)
    uvicorn.run(app, host='0.0.0.0', port=args.port)

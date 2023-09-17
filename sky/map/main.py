"""Main application for Sky Map.
"""
import argparse
import asyncio
import fastapi
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

import uvicorn
import json
import random
from zone_monitor import ZoneMonitor
from typing import Iterator
from sky import sky_logging

logger = sky_logging.init_logger('sky.serve.zone_monitor')

app = fastapi.FastAPI()
zone_monitor = ZoneMonitor()
templates = Jinja2Templates(directory="templates")

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
    zone_monitor.add_zone_preempt_data(zone, float(time))
    user_data = {
        'zone': zone,
        'time': time,
        'status': 'success'
    }
    return fastapi.responses.JSONResponse(content=user_data,
                                          status_code=201)

@app.post('/add-wait')
def add_wait(request: fastapi.Request):
    data = asyncio.run(request.json())
    zone = data['zone']
    time = data['time']
    zone_monitor.add_zone_wait_data(zone, float(time))
    user_data = {
        'zone': zone,
        'time': time,
        'status': 'success'
    }
    return fastapi.responses.JSONResponse(content=user_data,
                                          status_code=201)

@app.get('/get-average-wait-time/{zone}/{time}')
def get_average_wait_time(zone: str, time: str):
    wait_time = zone_monitor.get_zone_average_wait_time(zone, float(time))
    data = {
        'wait_time': wait_time
    }
    return fastapi.responses.JSONResponse(content=data,
                                          status_code=200)

@app.get('/get-average-preempt-time/{zone}/{time}')
def get_average_preempt_time(zone: str, time: str):
    preempt_time = zone_monitor.get_zone_average_preempt_time(zone, float(time))
    data = {
        'preempt_time': preempt_time
    }
    return fastapi.responses.JSONResponse(content=data,
                                          status_code=200)

@app.get('/get-zone-info')
def get_zone_info():
    zone_info = zone_monitor.get_zone_info()
    data = {
        'zone_info': zone_info,
    }
    return fastapi.responses.JSONResponse(content=data,
                                          status_code=200)

async def retrieve_zone_preempt_data(zone: str) -> Iterator[str]:
    
    idx = 0
    while True:
        timestamp, duration = zone_monitor.get_preempt_data_with_idx(zone, idx)
        if timestamp is None:
            await asyncio.sleep(1)
            continue
            
        json_data = json.dumps(
            {
                "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": duration,
            }
        )
        yield f"data:{json_data}\n\n"
        idx += 1
        await asyncio.sleep(1)

# https://github.com/roniemartinez/real-time-charts-with-fastapi/tree/master
@app.get("/chart-data/{zone}")
async def chart_data(zone: str) -> StreamingResponse:
    response = StreamingResponse(retrieve_zone_preempt_data(zone), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

@app.get("/visualize/{zone}", response_class=HTMLResponse)
async def index(request: Request, zone:str) -> Response:
    return templates.TemplateResponse("index.html", {"request": request, "zone": zone})
    
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyMap Server')
    parser.add_argument('--port', type=int, required=False, default=8081)
    args = parser.parse_args()
    print('serving at port', args.port)
    uvicorn.run(app, host='0.0.0.0', port=args.port)

from typing import Union, Optional

import asyncio
import datetime
import os
import time
from concurrent import futures

import fastapi
from fastapi import responses
from fastapi import staticfiles
from fastapi import templating
import uvicorn

from sky import provision
from sky.provision import common
from sky.controller import config as ssl_config
from sky.controller import dynamodb
from sky.controller import operation_logs
from sky.controller import chat_completion

app = fastapi.FastAPI()
LOCAL_DIR = directory = os.path.dirname(__file__)
templates = templating.Jinja2Templates(directory=LOCAL_DIR + "/templates")
app.mount("/node_modules",
          staticfiles.StaticFiles(directory=LOCAL_DIR + "/node_modules"),
          name="node_modules")
app.mount("/static",
          staticfiles.StaticFiles(directory=LOCAL_DIR + "/static"),
          name="static")

pool = futures.ThreadPoolExecutor(max_workers=32)


def _validate_user(user_id: Optional[str]) -> bool:
    # TODO(suquark): use a database
    return user_id in {'Alice', 'Bob'}


def _encapsulate_cluster_name(user_id: str, cluster_name: str) -> str:
    # add a prefix for cluster name to isolate users
    return user_id + '-' + cluster_name


@app.post('/api/bootstrap/{provider_name}/{region}/{cluster_name}')
async def bootstrap(
    provider_name: str,
    region: str,
    cluster_name: str,
    config: common.InstanceConfig,
    user_id: Union[str, None] = fastapi.Header(default=None)
) -> common.InstanceConfig:
    if not _validate_user(user_id):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized user")
    cluster_name = _encapsulate_cluster_name(user_id, cluster_name)

    return await asyncio.wrap_future(
        pool.submit(provision.bootstrap, provider_name, region, cluster_name,
                    config))


@app.post('/api/start_instances/{provider_name}/{region}/{cluster_name}')
async def start_instances(
    provider_name: str,
    region: str,
    cluster_name: str,
    config: common.InstanceConfig,
    user_id: Union[str, None] = fastapi.Header(default=None)
) -> common.ProvisionMetadata:
    if not _validate_user(user_id):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized user")
    cluster_name = _encapsulate_cluster_name(user_id, cluster_name)

    clusters = await dynamodb.load_user_clusters(user_id)
    add_cluster = True
    for c in clusters:
        if cluster_name == c['cluster_name']:
            add_cluster = False
    if add_cluster:
        clusters.append({
            'cluster_name': cluster_name,
            'provider_name': provider_name,
            'region': region,
            'creation_time': time.time(),
        })
    await dynamodb.save_user_clusters(user_id, clusters)

    return await asyncio.wrap_future(
        pool.submit(provision.start_instances, provider_name, region,
                    cluster_name, config))


@app.post('/api/stop_instances/{provider_name}/{region}/{cluster_name}')
async def stop_instances(
    provider_name: str,
    region: str,
    cluster_name: str,
    user_id: Union[str, None] = fastapi.Header(default=None)
) -> None:
    if not _validate_user(user_id):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized user")
    cluster_name = _encapsulate_cluster_name(user_id, cluster_name)

    await asyncio.wrap_future(
        pool.submit(provision.stop_instances, provider_name, region,
                    cluster_name))


@app.post('/api/terminate_instances/{provider_name}/{region}/{cluster_name}')
async def terminate_instances(
    provider_name: str,
    region: str,
    cluster_name: str,
    user_id: Union[str, None] = fastapi.Header(default=None)
) -> None:
    if not _validate_user(user_id):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized user")
    cluster_name = _encapsulate_cluster_name(user_id, cluster_name)

    await asyncio.wrap_future(
        pool.submit(provision.terminate_instances, provider_name, region,
                    cluster_name))

    clusters = await dynamodb.load_user_clusters(user_id)
    remaining_clusters = []
    for c in clusters:
        if c['cluster_name'] != cluster_name:
            remaining_clusters.append(c)
    await dynamodb.save_user_clusters(user_id, remaining_clusters)


@app.get('/api/wait_instances/{provider_name}/{region}/{cluster_name}')
async def wait_instances(
    provider_name: str,
    region: str,
    cluster_name: str,
    state: str,
    user_id: Union[str, None] = fastapi.Header(default=None)) -> None:
    if not _validate_user(user_id):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized user")
    cluster_name = _encapsulate_cluster_name(user_id, cluster_name)

    await asyncio.wrap_future(
        pool.submit(provision.wait_instances, provider_name, region,
                    cluster_name, state))


@app.get('/api/get_cluster_metadata/{provider_name}/{region}/{cluster_name}')
async def get_cluster_metadata(
    provider_name: str,
    region: str,
    cluster_name: str,
    user_id: Union[str, None] = fastapi.Header(default=None)
) -> common.ClusterMetadata:
    if not _validate_user(user_id):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized user")
    cluster_name = _encapsulate_cluster_name(user_id, cluster_name)

    return await asyncio.wrap_future(
        pool.submit(provision.get_cluster_metadata, provider_name, region,
                    cluster_name))


@app.get('/console/list_clusters', response_class=responses.HTMLResponse)
async def list_clusters(request: fastapi.Request):
    results = await dynamodb.scan_clusters()
    expand_results = []
    for r in results:
        for c in r['clusters']:
            expand_results.append({
                'user_id': r['user_id'],
                'cluster_name': c['cluster_name'],
                'provider_name': c['provider_name'],
                'creation_time': datetime.datetime.fromtimestamp(
                    c['creation_time']).isoformat(),
            })
    return templates.TemplateResponse("list_clusters.html", {
        "request": request,
        "results": expand_results
    })


@app.get('/console/list_operations', response_class=responses.HTMLResponse)
async def list_operations(request: fastapi.Request):
    results = await operation_logs.scan_operation_logs()
    expand_results = []
    for opr in results:
        expand_results.append({
            'operation_id': opr['operation_id'],
            'user_id': opr['metadata']['user_id'],
            'operation': opr['metadata']['operation'],
            'timestamp': datetime.datetime.fromtimestamp(
                opr['metadata']['timestamp']).isoformat(),
        })
    return templates.TemplateResponse("list_operations.html", {
        "request": request,
        "results": expand_results
    })


@app.get('/', response_class=responses.HTMLResponse)
async def homepage():
    with open(f'{LOCAL_DIR}/templates/homepage.html') as f:
        return responses.HTMLResponse(content=f.read(), status_code=200)


@app.get('/console/chat', response_class=responses.HTMLResponse)
async def chat():
    with open(f'{LOCAL_DIR}/templates/chat.html') as f:
        return responses.HTMLResponse(content=f.read(), status_code=200)


@app.post('/api/chat')
async def chat_api(req: dict):
    reply = chat_completion.chat(req['message'])
    return {'reply': reply}


@app.get('/console/terminal', response_class=responses.HTMLResponse)
async def terminal():
    with open(f'{LOCAL_DIR}/templates/terminal.html') as f:
        return responses.HTMLResponse(content=f.read(), status_code=200)


if __name__ == '__main__':
    # NOTE: We also have a bundle file: 'certificates/server.bundle.crt'
    # CA bundle is a file that contains root and intermediate certificates.
    # The end-entity certificate along with a CA bundle constitutes the
    # certificate chain. The chain is required to improve compatibility
    # of the certificates with web browsers and other kind of clients so
    # that browsers recognize your certificate and no security warnings
    # appear.
    # Here we only use 'localhost.crt' for direct API calls.
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config['formatters']['access'][
        'fmt'] = '%(asctime)s - %(levelname)s - %(message)s'
    uvicorn.run('sky.controller.server:app',
                reload=True,
                port=8080,
                debug=True,
                log_config=log_config,
                log_level='debug',
                **ssl_config.SERVER_SSL_CONFIG)

"""Rest APIs for SkyServe."""

import asyncio
import pathlib
from typing import Any, Dict, List, Optional

import fastapi
import httpx

from sky import sky_logging
from sky.adaptors import kubernetes as k8s_adaptor
from sky.serve.server import core
from sky.server import common as server_common
from sky.server import stream_utils
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as api_requests
from sky.skylet import constants
from sky.utils import common

logger = sky_logging.init_logger(__name__)
router = fastapi.APIRouter()

# Port-forward manager for cross-cluster service access
_PORT_FORWARD_MAP: Dict[str, Dict[str, Any]] = {}
_NEXT_LOCAL_PORT = 18100


def _ensure_port_forward(
    context: str,
    namespace: str,
    k8s_svc_name: str,
    target_port: int,
) -> Optional[str]:
    """Ensure a port-forward exists for a remote K8s service.

    Returns the localhost URL or None if port-forward can't be established.
    """
    global _NEXT_LOCAL_PORT
    import subprocess  # pylint: disable=import-outside-toplevel

    key = f'{context}/{namespace}/{k8s_svc_name}'
    if key in _PORT_FORWARD_MAP:
        info = _PORT_FORWARD_MAP[key]
        # Check if the process is still alive
        if info['proc'].poll() is None:
            return f'http://localhost:{info["local_port"]}'
        # Process died, clean up and re-create
        del _PORT_FORWARD_MAP[key]

    local_port = _NEXT_LOCAL_PORT
    _NEXT_LOCAL_PORT += 1

    try:
        cmd = [
            'kubectl', '--context', context, '-n', namespace,
            'port-forward', f'svc/{k8s_svc_name}',
            f'{local_port}:{target_port}', '--address=0.0.0.0'
        ]
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time  # pylint: disable=import-outside-toplevel
        time.sleep(2)  # Brief wait for port-forward to establish
        if proc.poll() is not None:
            logger.debug(f'Port-forward failed for {key}')
            return None
        _PORT_FORWARD_MAP[key] = {
            'proc': proc,
            'local_port': local_port,
        }
        logger.info(f'Port-forward established: localhost:{local_port} -> '
                     f'{k8s_svc_name}:{target_port} ({context}/{namespace})')
        return f'http://localhost:{local_port}'
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error creating port-forward for {key}: {e}')
        return None


@router.post('/up')
async def up(
    request: fastapi.Request,
    up_body: payloads.ServeUpBody,
) -> None:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_UP,
        request_body=up_body,
        func=core.up,
        schedule_type=api_requests.ScheduleType.LONG,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )


@router.post('/update')
async def update(
    request: fastapi.Request,
    update_body: payloads.ServeUpdateBody,
) -> None:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_UPDATE,
        request_body=update_body,
        func=core.update,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )


@router.post('/down')
async def down(
    request: fastapi.Request,
    down_body: payloads.ServeDownBody,
) -> None:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_DOWN,
        request_body=down_body,
        func=core.down,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )


@router.post('/terminate-replica')
async def terminate_replica(
    request: fastapi.Request,
    terminate_replica_body: payloads.ServeTerminateReplicaBody,
) -> None:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_TERMINATE_REPLICA,
        request_body=terminate_replica_body,
        func=core.terminate_replica,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )


@router.post('/status')
async def status(
    request: fastapi.Request,
    status_body: payloads.ServeStatusBody,
) -> None:
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_STATUS,
        request_body=status_body,
        func=core.status,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )


@router.post('/logs')
async def tail_logs(
    request: fastapi.Request, log_body: payloads.ServeLogsBody,
    background_tasks: fastapi.BackgroundTasks
) -> fastapi.responses.StreamingResponse:
    executor.check_request_thread_executor_available()
    request_task = await executor.prepare_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_LOGS,
        request_body=log_body,
        func=core.tail_logs,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )
    task = executor.execute_request_in_coroutine(request_task)
    # Cancel the coroutine after the request is done or client disconnects
    background_tasks.add_task(task.cancel)
    return stream_utils.stream_response_for_long_request(
        request_id=request_task.request_id,
        logs_path=request_task.log_path,
        background_tasks=background_tasks,
        kill_request_on_disconnect=False,
    )


@router.post('/sync-down-logs')
async def download_logs(
    request: fastapi.Request,
    download_logs_body: payloads.ServeDownloadLogsBody,
) -> None:
    user_hash = download_logs_body.env_vars[constants.USER_ID_ENV_VAR]
    timestamp = sky_logging.get_run_timestamp()
    logs_dir_on_api_server = (
        pathlib.Path(server_common.api_server_user_logs_dir_prefix(user_hash)) /
        'service' / f'{download_logs_body.service_name}_{timestamp}')
    logs_dir_on_api_server.mkdir(parents=True, exist_ok=True)
    # We should reuse the original request body, so that the env vars, such as
    # user hash, are kept the same.
    download_logs_body.local_dir = str(logs_dir_on_api_server)
    await executor.schedule_request_async(
        request_id=request.state.request_id,
        request_name=request_names.RequestName.SERVE_SYNC_DOWN_LOGS,
        request_body=download_logs_body,
        func=core.sync_down_logs,
        schedule_type=api_requests.ScheduleType.SHORT,
        request_cluster_name=common.SKY_SERVE_CONTROLLER_NAME,
        auth_user=request.state.auth_user,
    )


def _get_v2_services_for_context(
    context: Optional[str] = None,
    namespace: str = 'skyserve-v2',
) -> List[Dict[str, Any]]:
    """Get SkyServe v2 services from a K8s context using Python client.

    Discovers services via LLMInferenceService CRDs (KServe mode) and
    Deployments with the skypilot-serve or llminferenceservice labels.
    """
    services = {}  # keyed by service name to deduplicate

    # Strategy 1: Query LLMInferenceService CRDs
    try:
        crd_api = k8s_adaptor.custom_objects_api(context)
        llm_svcs = crd_api.list_namespaced_custom_object(
            group='serving.kserve.io',
            version='v1alpha1',
            namespace=namespace,
            plural='llminferenceservices',
        )
        for item in llm_svcs.get('items', []):
            name = item['metadata']['name']
            svc_status = item.get('status', {})
            conditions = svc_status.get('conditions', [])
            ready = any(
                c.get('type') == 'Ready' and c.get('status') == 'True'
                for c in conditions)

            # Get model from spec
            model = ''
            model_spec = item.get('spec', {}).get('modelSpec', {})
            if model_spec:
                for arg in model_spec.get('args', []):
                    if arg.startswith('--model='):
                        model = arg.split('=', 1)[1]
                args_list = model_spec.get('args', [])
                for i, arg in enumerate(args_list):
                    if arg == '--model' and i + 1 < len(args_list):
                        model = args_list[i + 1]
                        break

            # Get GPU info
            gpus = 0
            gpu_type = ''
            resources = model_spec.get('resources', {})
            limits = resources.get('limits', {})
            if 'nvidia.com/gpu' in limits:
                gpus = int(limits['nvidia.com/gpu'])
            node_sel = model_spec.get('nodeSelector', {})
            gpu_type = node_sel.get('gpu.nvidia.com/class', '')

            services[name] = {
                'name': name,
                'status': 'READY' if ready else 'PROVISIONING',
                'endpoint': '',
                'uptime': 0,
                'replica_info': [],
                'requested_resources_str': (
                    f'{gpu_type}:{gpus}' if gpus > 0 else '-'),
                'model': model,
                'context': context or 'in-cluster',
                'namespace': namespace,
                'is_v2': True,
            }
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error listing LLMInferenceService for context '
                     f'{context}: {e}')

    # Strategy 2: Get Deployments with KServe labels for replica info
    kserve_label = 'app.kubernetes.io/part-of=llminferenceservice'
    try:
        apps = k8s_adaptor.apps_api(context)
        deploys = apps.list_namespaced_deployment(
            namespace=namespace, label_selector=kserve_label)
        for d in deploys.items:
            labels = d.metadata.labels or {}
            svc_name = labels.get('app.kubernetes.io/name',
                                  d.metadata.name)
            role = labels.get('llm-d.ai/role', 'both')
            spec_replicas = d.spec.replicas or 0
            ready = (d.status.ready_replicas or 0) if d.status else 0

            # Get GPU info from pod spec
            gpus = 0
            for c in d.spec.template.spec.containers:
                if c.resources and c.resources.limits:
                    gpu_count = c.resources.limits.get(
                        'nvidia.com/gpu', 0)
                    gpus += int(gpu_count)

            # Get model from container args/env
            model = ''
            for c in d.spec.template.spec.containers:
                if c.args:
                    for i, arg in enumerate(c.args):
                        if (arg in ('--model', '--served-model-name')
                                and i + 1 < len(c.args)):
                            model = c.args[i + 1]
                            break
                        if arg.startswith('--model='):
                            model = arg.split('=', 1)[1]
                            break
                        if arg.startswith('--served-model-name='):
                            model = arg.split('=', 1)[1]
                            break
                if c.env:
                    for env_var in c.env:
                        if env_var.name == 'MODEL_ID' and env_var.value:
                            model = env_var.value

            # Create or update service entry
            if svc_name not in services:
                svc_status = 'READY' if ready == spec_replicas else (
                    'PROVISIONING' if ready > 0 else 'PENDING')
                node_sel = d.spec.template.spec.node_selector or {}
                gpu_type = node_sel.get('gpu.nvidia.com/class', '')
                if gpus > 0:
                    res_str = (f'{gpu_type}:{gpus}'
                               if gpu_type else f'GPU:{gpus}')
                else:
                    res_str = '-'
                services[svc_name] = {
                    'name': svc_name,
                    'status': svc_status,
                    'endpoint': '',
                    'uptime': 0,
                    'replica_info': [],
                    'requested_resources_str': res_str,
                    'model': model,
                    'context': context or 'in-cluster',
                    'namespace': namespace,
                    'is_v2': True,
                }

            # Add replica info
            svc_entry = services[svc_name]
            for i in range(spec_replicas):
                svc_entry['replica_info'].append({
                    'replica_id': len(svc_entry['replica_info']),
                    'status': 'READY' if i < ready else 'PROVISIONING',
                    'role': role,
                })
            if model and not svc_entry.get('model'):
                svc_entry['model'] = model
            if gpus > 0 and svc_entry.get(
                    'requested_resources_str', '-') == '-':
                node_sel = d.spec.template.spec.node_selector or {}
                gpu_type = node_sel.get('gpu.nvidia.com/class', '')
                svc_entry['requested_resources_str'] = (
                    f'{gpu_type}:{gpus}' if gpu_type else f'GPU:{gpus}')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error listing v2 deployments for context '
                     f'{context}: {e}')

    # Get endpoints for workload services (ClusterIP, LoadBalancer, NodePort)
    workload_label = ('app.kubernetes.io/component='
                      'llminferenceservice-workload')
    try:
        core_api = k8s_adaptor.core_api(context)
        svc_list = core_api.list_namespaced_service(
            namespace=namespace,
            label_selector=workload_label)
        for svc in svc_list.items:
            svc_labels = svc.metadata.labels or {}
            svc_name = svc_labels.get('app.kubernetes.io/name',
                                      svc.metadata.name)
            # For endpoint services named <name>-endpoint, extract base name
            raw_name = svc.metadata.name
            if raw_name.endswith('-endpoint'):
                base_name = raw_name[:-len('-endpoint')]
                if base_name in services:
                    svc_name = base_name
            port = 8000
            if svc.spec.ports:
                port = svc.spec.ports[0].port
            if svc_name not in services:
                continue

            svc_type = svc.spec.type or 'ClusterIP'

            # Don't let ClusterIP overwrite a previously resolved
            # LoadBalancer or NodePort endpoint for the same service.
            existing_type = services[svc_name].get('service_type')
            if (svc_type == 'ClusterIP' and
                    existing_type in ('LoadBalancer', 'NodePort')):
                continue
            services[svc_name]['service_type'] = svc_type

            # Always set up port-forward for cross-cluster proxy access
            if context is not None:
                local_url = _ensure_port_forward(
                    context, namespace, svc.metadata.name, port)
                if local_url:
                    services[svc_name]['proxy_endpoint'] = local_url

            if svc_type == 'LoadBalancer':
                ingress = (svc.status.load_balancer.ingress
                           if svc.status and svc.status.load_balancer
                           else None)
                if ingress:
                    host = ingress[0].ip or ingress[0].hostname
                    services[svc_name][
                        'endpoint'] = f'http://{host}:{port}'
                else:
                    services[svc_name]['endpoint'] = ''
            elif svc_type == 'NodePort':
                node_port = (svc.spec.ports[0].node_port
                             if svc.spec.ports else None)
                if node_port:
                    services[svc_name][
                        'endpoint'] = f'<node-ip>:{node_port}'
                else:
                    services[svc_name]['endpoint'] = ''
            else:
                # ClusterIP - use port-forward as the endpoint
                proxy = services[svc_name].get('proxy_endpoint')
                if proxy:
                    services[svc_name]['endpoint'] = proxy
                elif context is None:
                    cluster_ip = svc.spec.cluster_ip
                    services[svc_name][
                        'endpoint'] = f'http://{cluster_ip}:{port}'
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error listing v2 service endpoints for context '
                     f'{context}: {e}')

    # Also check skypilot-serve managed services (direct mode)
    skypilot_label = 'app.kubernetes.io/managed-by=skypilot-serve'
    try:
        core_api = k8s_adaptor.core_api(context)
        svc_list = core_api.list_namespaced_service(
            namespace=namespace,
            label_selector=skypilot_label)
        for svc in svc_list.items:
            svc_labels = svc.metadata.labels or {}
            svc_name = svc_labels.get('skypilot.co/service',
                                      svc.metadata.name)
            if svc_name not in services:
                continue
            port = 8000
            if svc.spec.ports:
                port = svc.spec.ports[0].port
            svc_type = svc.spec.type or 'ClusterIP'

            # Don't let ClusterIP overwrite a previously resolved
            # LoadBalancer or NodePort.
            existing_type = services[svc_name].get('service_type')
            if (svc_type == 'ClusterIP' and
                    existing_type in ('LoadBalancer', 'NodePort')):
                continue
            # Skip if endpoint already resolved with same or better type
            if (services[svc_name].get('endpoint') and
                    svc_type == 'ClusterIP'):
                continue
            services[svc_name]['service_type'] = svc_type

            # Always set up port-forward for cross-cluster proxy access
            if context is not None:
                local_url = _ensure_port_forward(
                    context, namespace, svc.metadata.name, port)
                if local_url:
                    services[svc_name]['proxy_endpoint'] = local_url

            if svc_type == 'LoadBalancer':
                ingress = (svc.status.load_balancer.ingress
                           if svc.status and svc.status.load_balancer
                           else None)
                if ingress:
                    host = ingress[0].ip or ingress[0].hostname
                    services[svc_name][
                        'endpoint'] = f'http://{host}:{port}'
                else:
                    services[svc_name]['endpoint'] = ''
            elif svc_type == 'NodePort':
                node_port = (svc.spec.ports[0].node_port
                             if svc.spec.ports else None)
                if node_port:
                    services[svc_name][
                        'endpoint'] = f'<node-ip>:{node_port}'
                else:
                    services[svc_name]['endpoint'] = ''
            else:
                # ClusterIP - use port-forward as the endpoint
                proxy = services[svc_name].get('proxy_endpoint')
                if proxy:
                    services[svc_name]['endpoint'] = proxy
                elif context is None:
                    cluster_ip = svc.spec.cluster_ip
                    services[svc_name][
                        'endpoint'] = f'http://{cluster_ip}:{port}'
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Error listing skypilot-serve services for context '
                     f'{context}: {e}')

    return list(services.values())


@router.get('/v2_status')
async def v2_status(
    context: Optional[str] = None,
) -> fastapi.responses.JSONResponse:
    """Get SkyServe v2 services across all registered K8s contexts."""
    from sky import core as sky_core  # pylint: disable=import-outside-toplevel

    all_services = []

    if context:
        contexts = [context]
    else:
        try:
            contexts = sky_core.get_all_contexts()
        except Exception:  # pylint: disable=broad-except
            contexts = []
        # Also try in-cluster
        contexts = [None] + contexts

    loop = asyncio.get_event_loop()
    tasks = []
    for ctx in contexts:
        tasks.append(
            loop.run_in_executor(
                None, _get_v2_services_for_context, ctx, 'skyserve-v2'))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            all_services.extend(result)

    # Deduplicate by name (same service found via different paths)
    seen = set()
    unique = []
    for svc in all_services:
        key = (svc['name'], svc.get('context', ''))
        if key not in seen:
            seen.add(key)
            unique.append(svc)

    return fastapi.responses.JSONResponse(content=unique)


@router.post('/chat_proxy')
async def chat_proxy(request: fastapi.Request) -> fastapi.responses.StreamingResponse:
    """Proxy chat completion requests to a service endpoint.

    The dashboard cannot call service endpoints directly due to CORS.
    This endpoint forwards the request server-side and streams the
    response back to the browser.

    Request body must include:
        endpoint: str - The service endpoint URL
        ...rest: dict - The OpenAI-compatible chat completion payload
    """
    body = await request.json()
    # Prefer proxy_endpoint (port-forward) for server-side routing;
    # fall back to endpoint (which may be an external LB IP).
    endpoint = (body.pop('proxy_endpoint', None) or
                body.pop('endpoint', None))
    if not endpoint:
        return fastapi.responses.JSONResponse(
            status_code=400,
            content={'error': 'endpoint is required'})

    url = f'{endpoint.rstrip("/")}/v1/chat/completions'
    is_stream = body.get('stream', False)

    try:
        if is_stream:

            async def _proxy_stream():
                async with httpx.AsyncClient(timeout=httpx.Timeout(
                        connect=10, read=300, write=10,
                        pool=10)) as client:
                    async with client.stream(
                            'POST',
                            url,
                            json=body,
                            headers={
                                'Content-Type': 'application/json'
                            }) as resp:
                        async for chunk in resp.aiter_bytes():
                            yield chunk

            return fastapi.responses.StreamingResponse(
                _proxy_stream(),
                media_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no',
                })

        # Non-streaming: forward and return JSON
        async with httpx.AsyncClient(timeout=httpx.Timeout(
                connect=10, read=300, write=10, pool=10)) as client:
            resp = await client.post(url,
                                     json=body,
                                     headers={
                                         'Content-Type': 'application/json'
                                     })
            return fastapi.responses.Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type='application/json')
    except httpx.ConnectError:
        return fastapi.responses.JSONResponse(
            status_code=502,
            content={
                'error': f'Cannot connect to service endpoint: {endpoint}'
            })
    except httpx.TimeoutException:
        return fastapi.responses.JSONResponse(
            status_code=504,
            content={'error': 'Service endpoint timed out'})

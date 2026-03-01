"""SkyServe v2: Main orchestration logic.

Provides the high-level operations:
  - up: Deploy a model service
  - status: Check service status
  - down: Tear down a service
  - logs: View service logs
  - endpoint: Get service endpoint URL
"""
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    from sky.serve import kserve_generator
    from sky.serve import kserve_prereqs
    from sky.serve import model_registry
    from sky.serve import serve_spec_v2
except (ImportError, ModuleNotFoundError):
    import sys
    kserve_generator = sys.modules.get('sky.serve.kserve_generator')
    kserve_prereqs = sys.modules.get('sky.serve.kserve_prereqs')
    model_registry = sys.modules.get('sky.serve.model_registry')
    serve_spec_v2 = sys.modules.get('sky.serve.serve_spec_v2')

logger = logging.getLogger(__name__)

# Label selector for SkyServe-managed resources
SKYPILOT_LABEL = 'app.kubernetes.io/managed-by=skypilot-serve'


def _run_kubectl(args: List[str], timeout: int = 60,
                 input_data: Optional[str] = None,
                 context: Optional[str] = None) -> Tuple[int, str, str]:
    """Run kubectl and return (returncode, stdout, stderr)."""
    cmd = ['kubectl']
    if context:
        cmd.extend(['--context', context])
    cmd.extend(args)
    logger.debug('Running: %s', ' '.join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, input=input_data)
    if result.returncode != 0:
        logger.debug('kubectl error: %s', result.stderr)
    return result.returncode, result.stdout, result.stderr


def _wait_for_loadbalancer_ip(
    svc_name: str,
    namespace: str,
    context: Optional[str] = None,
    timeout: int = 120,
) -> Optional[str]:
    """Poll until a LoadBalancer Service gets an external IP/hostname."""
    for _ in range(timeout // 5):
        # Try IP first
        rc, out, _ = _run_kubectl([
            'get', 'svc', svc_name, '-n', namespace,
            '-o', 'jsonpath={.status.loadBalancer.ingress[0].ip}',
        ], context=context)
        if rc == 0 and out.strip():
            return out.strip()
        # Try hostname (AWS ELBs use hostname instead of IP)
        rc, out, _ = _run_kubectl([
            'get', 'svc', svc_name, '-n', namespace,
            '-o', 'jsonpath={.status.loadBalancer.ingress[0].hostname}',
        ], context=context)
        if rc == 0 and out.strip():
            return out.strip()
        time.sleep(5)
    return None


def up(yaml_path: str,
       namespace: str = 'skyserve-v2',
       hf_token: Optional[str] = None,
       force_direct: bool = False,
       context: Optional[str] = None,
       auto_install: bool = True,
       service_type: Optional[str] = None) -> Dict[str, Any]:
    """Deploy a model service.

    Args:
        yaml_path: Path to SkyServe v2 YAML spec.
        namespace: K8s namespace to deploy into.
        hf_token: HuggingFace token for gated models.
        force_direct: Force direct Deployment mode (skip KServe).
        context: Target Kubernetes context. None for default.
        auto_install: Auto-install missing prerequisites.
        service_type: Override K8s Service type (ClusterIP/LoadBalancer/NodePort).

    Returns:
        Dict with deployment info (service_name, namespace, mode, etc.)
    """
    # Parse spec
    spec = serve_spec_v2.parse_spec(yaml_path)

    # CLI flag overrides YAML spec
    if service_type is not None:
        spec.service.service_type = service_type

    errors = serve_spec_v2.validate_spec(spec)
    if errors:
        raise ValueError(f'Invalid spec: {"; ".join(errors)}')

    # Auto-detect HF token from environment
    if hf_token is None:
        hf_token = os.environ.get('HF_TOKEN') or os.environ.get(
            'HUGGING_FACE_HUB_TOKEN')

    # Auto-install prerequisites if needed
    if auto_install and not force_direct:
        try:
            from sky.serve import kserve_installer
            print('\nChecking and installing prerequisites...')
            install_results = kserve_installer.ensure_all_prerequisites(
                context=context)
            print()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Auto-install failed: %s. Continuing...', e)

    # Create namespace if needed
    rc, out, _ = _run_kubectl([
        'create', 'namespace', namespace,
        '--dry-run=client', '-o', 'yaml',
    ], context=context)
    if rc == 0:
        _run_kubectl(['apply', '-f', '-'], input_data=out,
                     context=context)

    # Check prerequisites and choose deployment mode
    use_kserve = (not force_direct and
                  kserve_prereqs.can_use_kserve(context))

    if use_kserve:
        mode = 'kserve'
        resources, model_config = kserve_generator.generate_resources(
            spec, namespace=namespace, hf_token=hf_token)
    else:
        mode = 'direct'
        logger.info('KServe not available, using direct Deployment mode')
        resources, model_config = kserve_generator.generate_direct_deployment(
            spec, namespace=namespace, hf_token=hf_token)

    service_name = kserve_generator.generate_service_name(spec)

    # Apply resources
    yaml_str = kserve_generator.resources_to_yaml(resources)
    print(f'\nDeploying {model_config.model_id}...')
    print(f'  Mode: {mode}')
    print(f'  Namespace: {namespace}')
    print(f'  Service: {service_name}')
    print(f'  GPU: {model_config.num_gpus}x {model_config.gpu_type}')
    print(f'  Tensor Parallelism: {model_config.tensor_parallel}')
    print(f'  Replicas: {spec.service.replicas}')
    if spec.service.service_type != 'ClusterIP':
        print(f'  Service Type: {spec.service.service_type}')

    # Write to temp file and apply
    with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_str)
        temp_path = f.name

    try:
        rc, out, err = _run_kubectl(['apply', '-f', temp_path],
                                    context=context)
        if rc != 0:
            raise RuntimeError(f'Failed to apply resources: {err}')
        print(f'\nResources applied successfully.')
        for line in out.strip().split('\n'):
            print(f'  {line}')
    finally:
        os.unlink(temp_path)

    # For KServe mode, add Prometheus scrape annotations to the Deployment
    # (LLMInferenceService template is PodSpec, doesn't support pod annotations)
    if mode == 'kserve':
        deploy_name = f'{service_name}-kserve'
        _run_kubectl([
            'patch', 'deployment', deploy_name,
            '-n', namespace,
            '--type=merge',
            '-p', json.dumps({
                'spec': {
                    'template': {
                        'metadata': {
                            'annotations': {
                                'prometheus.io/scrape': 'true',
                                'prometheus.io/port': '8000',
                                'prometheus.io/path': '/metrics',
                            }
                        }
                    }
                }
            }),
        ], context=context)

    # Wait for LoadBalancer external IP if applicable
    endpoint = None
    if spec.service.service_type == 'LoadBalancer':
        # Determine the Service name to watch
        if mode == 'kserve':
            lb_svc_name = f'{service_name}-endpoint'
        else:
            lb_svc_name = service_name
        print(f'\nWaiting for LoadBalancer external IP...')
        external_host = _wait_for_loadbalancer_ip(
            lb_svc_name, namespace, context=context)
        if external_host:
            endpoint = f'http://{external_host}:8000'
            print(f'  Endpoint: {endpoint}')
        else:
            print(f'  External IP not yet assigned. Check with: '
                  f'kubectl get svc {lb_svc_name} -n {namespace}')

    return {
        'service_name': service_name,
        'namespace': namespace,
        'mode': mode,
        'model': model_config.model_id,
        'gpu_type': model_config.gpu_type,
        'num_gpus': model_config.num_gpus,
        'replicas': spec.service.replicas,
        'service_type': spec.service.service_type,
        'endpoint': endpoint,
    }


def status(service_name: Optional[str] = None,
           namespace: str = 'skyserve-v2',
           context: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get status of deployed services.

    Args:
        service_name: Optional specific service name. If None, list all.
        namespace: K8s namespace.

    Returns:
        List of service status dicts.
    """
    results = []

    # Find all SkyServe-managed deployments
    label_selector = SKYPILOT_LABEL
    if service_name:
        label_selector += f',skypilot.co/service={service_name}'

    # Check deployments
    rc, out, _ = _run_kubectl([
        'get', 'deployments', '-n', namespace,
        '-l', label_selector, '-o', 'json',
    ], context=context)
    if rc == 0:
        try:
            data = json.loads(out)
            for deploy in data.get('items', []):
                name = deploy['metadata']['name']
                spec_replicas = deploy['spec'].get('replicas', 0)
                status_data = deploy.get('status', {})
                ready = status_data.get('readyReplicas', 0)
                available = status_data.get('availableReplicas', 0)

                results.append({
                    'name': name,
                    'namespace': namespace,
                    'type': 'Deployment',
                    'replicas': f'{ready}/{spec_replicas}',
                    'ready': ready == spec_replicas,
                    'status': 'Ready' if ready == spec_replicas else (
                        'Starting' if ready > 0 else 'Pending'),
                })
        except json.JSONDecodeError:
            pass

    # Check LLMInferenceService (if KServe available)
    rc, out, _ = _run_kubectl([
        'get', 'llminferenceservice', '-n', namespace,
        '-l', label_selector, '-o', 'json',
    ], context=context)
    if rc == 0:
        try:
            data = json.loads(out)
            for svc in data.get('items', []):
                name = svc['metadata']['name']
                svc_status = svc.get('status', {})
                url = svc_status.get('url', '')
                conditions = svc_status.get('conditions', [])
                ready = any(
                    c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in conditions)

                results.append({
                    'name': name,
                    'namespace': namespace,
                    'type': 'LLMInferenceService',
                    'url': url,
                    'ready': ready,
                    'status': 'Ready' if ready else 'Pending',
                    'conditions': conditions,
                })
        except json.JSONDecodeError:
            pass

    # Add pod details
    rc, out, _ = _run_kubectl([
        'get', 'pods', '-n', namespace,
        '-l', label_selector, '-o', 'json',
    ], context=context)
    if rc == 0:
        try:
            data = json.loads(out)
            pods = []
            for pod in data.get('items', []):
                pod_name = pod['metadata']['name']
                phase = pod['status'].get('phase', 'Unknown')
                node = pod['spec'].get('nodeName', '')
                containers = pod['status'].get('containerStatuses', [])
                ready_containers = sum(
                    1 for c in containers if c.get('ready', False))
                total_containers = len(containers)

                # Get GPU allocation
                gpus = 0
                for c in pod['spec'].get('containers', []):
                    gpus += int(c.get('resources', {}).get(
                        'limits', {}).get('nvidia.com/gpu', 0))

                pods.append({
                    'name': pod_name,
                    'phase': phase,
                    'node': node,
                    'ready': f'{ready_containers}/{total_containers}',
                    'gpus': gpus,
                })
            if pods:
                for r in results:
                    r['pods'] = pods
        except json.JSONDecodeError:
            pass

    return results


def down(service_name: str, namespace: str = 'skyserve-v2',
         context: Optional[str] = None) -> bool:
    """Tear down a service.

    Deletes all resources with the skypilot.co/service label.
    """
    label_selector = (f'{SKYPILOT_LABEL},'
                      f'skypilot.co/service={service_name}')

    print(f'Tearing down service {service_name} in {namespace}...')

    # Delete all labeled resources
    resource_types = [
        'deployment', 'service', 'secret', 'configmap',
        'scaledobject',  # KEDA
    ]

    # Try LLMInferenceService separately (may not exist as CRD)
    _run_kubectl([
        'delete', 'llminferenceservice', '-n', namespace,
        '-l', label_selector, '--ignore-not-found',
    ], context=context)

    for rt in resource_types:
        rc, out, _ = _run_kubectl([
            'delete', rt, '-n', namespace,
            '-l', label_selector, '--ignore-not-found',
        ], context=context)
        if rc == 0 and out.strip():
            for line in out.strip().split('\n'):
                print(f'  {line}')

    print('Done.')
    return True


def logs(service_name: str, namespace: str = 'skyserve-v2',
         follow: bool = False, tail: int = 100,
         replica: Optional[int] = None,
         context: Optional[str] = None) -> str:
    """Get logs from a service.

    Args:
        service_name: Service name.
        namespace: K8s namespace.
        follow: Follow log output.
        tail: Number of lines to show.
        replica: Specific replica index (0-based). None for all.
        context: Target Kubernetes context. None for default.

    Returns:
        Log output string.
    """
    label_selector = (f'{SKYPILOT_LABEL},'
                      f'skypilot.co/service={service_name}')

    # Get pods
    rc, out, _ = _run_kubectl([
        'get', 'pods', '-n', namespace,
        '-l', label_selector, '-o', 'json',
    ], context=context)
    if rc != 0:
        return f'Failed to get pods for service {service_name}'

    try:
        data = json.loads(out)
        pods = data.get('items', [])
    except json.JSONDecodeError:
        return 'Failed to parse pod list'

    if not pods:
        return f'No pods found for service {service_name}'

    if replica is not None:
        if replica >= len(pods):
            return (f'Replica {replica} not found. '
                    f'Available: 0-{len(pods)-1}')
        pods = [pods[replica]]

    all_logs = []
    for pod in pods:
        pod_name = pod['metadata']['name']
        args = ['logs', pod_name, '-n', namespace,
                '-c', 'vllm', f'--tail={tail}']
        if follow:
            args.append('-f')

        rc, out, err = _run_kubectl(args, timeout=30, context=context)
        if rc == 0:
            all_logs.append(f'=== {pod_name} ===\n{out}')
        else:
            all_logs.append(f'=== {pod_name} ===\n(no logs: {err.strip()})')

    return '\n'.join(all_logs)


def endpoint(service_name: str,
             namespace: str = 'skyserve-v2',
             context: Optional[str] = None) -> Optional[str]:
    """Get the service endpoint URL.

    Checks for LoadBalancer/NodePort endpoints first, then falls back
    to ClusterIP service URL for port-forwarding.
    """
    # Check for endpoint Service (created for non-ClusterIP types)
    # Also check KServe workload service naming convention
    for svc_name in [f'{service_name}-endpoint', service_name,
                     f'{service_name}-kserve-workload-svc']:
        rc, out, _ = _run_kubectl([
            'get', 'svc', svc_name, '-n', namespace,
            '-o', 'jsonpath={.spec.type}',
        ], context=context)
        if rc != 0:
            continue
        svc_type = out.strip()

        if svc_type == 'LoadBalancer':
            # Try external IP
            rc, out, _ = _run_kubectl([
                'get', 'svc', svc_name, '-n', namespace,
                '-o', ('jsonpath={.status.loadBalancer'
                       '.ingress[0].ip}'),
            ], context=context)
            if rc == 0 and out.strip():
                return f'http://{out.strip()}:8000'
            # Try hostname (AWS ELBs)
            rc, out, _ = _run_kubectl([
                'get', 'svc', svc_name, '-n', namespace,
                '-o', ('jsonpath={.status.loadBalancer'
                       '.ingress[0].hostname}'),
            ], context=context)
            if rc == 0 and out.strip():
                return f'http://{out.strip()}:8000'

        elif svc_type == 'NodePort':
            rc, out, _ = _run_kubectl([
                'get', 'svc', svc_name, '-n', namespace,
                '-o', 'jsonpath={.spec.ports[0].nodePort}',
            ], context=context)
            if rc == 0 and out.strip():
                return f'<node-ip>:{out.strip()}'

    # Check for LLMInferenceService URL
    rc, out, _ = _run_kubectl([
        'get', 'llminferenceservice', service_name, '-n', namespace,
        '-o', 'jsonpath={.status.url}',
    ], context=context)
    if rc == 0 and out.strip():
        return out.strip()

    # Fall back to ClusterIP service (try direct name and KServe workload name)
    for svc_name in [service_name, f'{service_name}-kserve-workload-svc']:
        rc, out, _ = _run_kubectl([
            'get', 'svc', svc_name, '-n', namespace,
            '-o', 'jsonpath={.spec.clusterIP}',
        ], context=context)
        if rc == 0 and out.strip():
            return f'http://{out.strip()}:8000'

    return None


def port_forward(service_name: str, namespace: str = 'skyserve-v2',
                 local_port: int = 8000,
                 context: Optional[str] = None) -> subprocess.Popen:
    """Start port forwarding to a service.

    Returns the subprocess.Popen object.
    """
    cmd = ['kubectl']
    if context:
        cmd.extend(['--context', context])
    cmd.extend([
        'port-forward',
        f'svc/{service_name}', f'{local_port}:8000',
        '-n', namespace,
    ])
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Give it a moment to start
    time.sleep(2)
    if proc.poll() is not None:
        _, err = proc.communicate()
        raise RuntimeError(f'Port forward failed: {err.decode()}')
    return proc


def wait_for_ready(service_name: str, namespace: str = 'skyserve-v2',
                   timeout: int = 600, poll_interval: int = 10,
                   context: Optional[str] = None) -> bool:
    """Wait for a service to become ready.

    Args:
        service_name: Service name.
        namespace: K8s namespace.
        timeout: Max time to wait in seconds.
        poll_interval: Time between status checks.
        context: Target Kubernetes context. None for default.

    Returns:
        True if service became ready, False if timed out.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        statuses = status(service_name, namespace, context=context)
        if statuses:
            for s in statuses:
                if s.get('ready'):
                    return True
                # Print progress
                pods = s.get('pods', [])
                for pod in pods:
                    elapsed = int(time.time() - start_time)
                    print(f'  [{elapsed}s] Pod {pod["name"]}: '
                          f'{pod["phase"]} (ready: {pod["ready"]}, '
                          f'GPUs: {pod["gpus"]})')

        time.sleep(poll_interval)

    return False


def _query_prometheus(query: str,
                      prometheus_url: Optional[str] = None,
                      context: Optional[str] = None) -> Optional[Any]:
    """Query Prometheus for a metric value.

    Auto-discovers Prometheus URL from well-known service names.
    """
    if prometheus_url is None:
        # Try well-known Prometheus URLs
        candidates = [
            'http://skypilot-prometheus-server.skypilot.svc.cluster.local:80',
            'http://prometheus.monitoring.svc.cluster.local:9090',
            'http://prometheus-server.monitoring.svc.cluster.local:80',
        ]
        for url in candidates:
            rc, out, _ = _run_kubectl([
                'run', '--rm', '-i', '--restart=Never',
                '--image=curlimages/curl',
                f'prom-probe-{hash(url) % 10000}',
                '-n', 'skypilot',
                '--', 'curl', '-s', '--connect-timeout', '2',
                f'{url}/api/v1/query?query=up',
            ], timeout=15, context=context)
            if rc == 0 and '"success"' in out:
                prometheus_url = url
                break
        if prometheus_url is None:
            return None

    rc, out, _ = _run_kubectl([
        'exec', '-n', 'skypilot', 'prom-debug',
        '--', 'curl', '-s', '-G',
        f'{prometheus_url}/api/v1/query',
        '--data-urlencode', f'query={query}',
    ], timeout=15, context=context)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        return data.get('data', {}).get('result', [])
    except json.JSONDecodeError:
        return None


def _get_service_metrics(
    service_name: str,
    prometheus_url: str = ('http://skypilot-prometheus-server'
                           '.skypilot.svc.cluster.local:80'),
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """Get observability metrics for a service from Prometheus."""
    metrics = {}

    queries = {
        'kv_cache_usage':
            f'vllm:kv_cache_usage_perc{{skypilot_co_service="{service_name}"}}',
        'requests_running':
            f'vllm:num_requests_running{{skypilot_co_service="{service_name}"}}',
        'requests_waiting':
            f'vllm:num_requests_waiting{{skypilot_co_service="{service_name}"}}',
        'total_requests':
            (f'sum(vllm:request_success_total'
             f'{{skypilot_co_service="{service_name}"}})'),
        'prompt_tokens':
            f'vllm:prompt_tokens_total{{skypilot_co_service="{service_name}"}}',
        'generation_tokens':
            (f'vllm:generation_tokens_total'
             f'{{skypilot_co_service="{service_name}"}}'),
    }

    for name, query in queries.items():
        result = _query_prometheus(query, prometheus_url, context=context)
        if result:
            # Sum values across replicas
            total = sum(float(r['value'][1]) for r in result)
            metrics[name] = total

    return metrics


def print_status(service_name: Optional[str] = None,
                 namespace: str = 'skyserve-v2',
                 show_metrics: bool = True,
                 context: Optional[str] = None):
    """Print formatted service status."""
    statuses = status(service_name, namespace, context=context)

    if not statuses:
        print('No SkyServe v2 services found.')
        return

    print(f'\nSkyServe v2 Services ({namespace})')
    print('=' * 70)

    for svc in statuses:
        svc_status = 'READY' if svc.get('ready') else 'PENDING'
        print(f'\n  Service:  {svc["name"]}')
        print(f'  Type:     {svc.get("type", "unknown")}')
        print(f'  Status:   {svc_status}')
        if svc.get('replicas'):
            print(f'  Replicas: {svc["replicas"]}')
        if svc.get('url'):
            print(f'  URL:      {svc["url"]}')

        pods = svc.get('pods', [])
        if pods:
            print(f'  Pods:')
            for pod in pods:
                print(f'    - {pod["name"]}: {pod["phase"]} '
                      f'(ready: {pod["ready"]}, '
                      f'node: {pod.get("node", "?")}, '
                      f'GPUs: {pod["gpus"]})')

        # Observability metrics from Prometheus
        if show_metrics and svc.get('ready'):
            metrics = _get_service_metrics(svc['name'], context=context)
            if metrics:
                print(f'  Metrics:')
                if 'kv_cache_usage' in metrics:
                    print(f'    KV Cache Usage:     '
                          f'{metrics["kv_cache_usage"]:.1%}')
                if 'requests_running' in metrics:
                    print(f'    Requests Running:   '
                          f'{int(metrics["requests_running"])}')
                if 'requests_waiting' in metrics:
                    print(f'    Requests Waiting:   '
                          f'{int(metrics["requests_waiting"])}')
                if 'total_requests' in metrics:
                    print(f'    Total Requests:     '
                          f'{int(metrics["total_requests"])}')
                if 'prompt_tokens' in metrics:
                    print(f'    Prompt Tokens:      '
                          f'{int(metrics["prompt_tokens"])}')
                if 'generation_tokens' in metrics:
                    print(f'    Generation Tokens:  '
                          f'{int(metrics["generation_tokens"])}')

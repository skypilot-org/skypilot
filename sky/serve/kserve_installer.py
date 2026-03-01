"""Auto-installer for SkyServe v2 prerequisites on Kubernetes clusters.

Installs missing components in dependency order:
  Phase 1 (parallel): cert-manager, KEDA, Prometheus
  Phase 2: KServe (needs cert-manager)
  Phase 3: Gateway API CRDs

llm-d is deployed automatically by KServe when LLMInferenceService
specifies spec.router.scheduler, so no separate installation is needed.
"""
import concurrent.futures
import logging
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from sky.serve.kserve_prereqs import PrereqStatus

logger = logging.getLogger(__name__)

# Component versions
CERT_MANAGER_VERSION = 'v1.16.3'
KSERVE_VERSION = 'v0.15.1'
KSERVE_LLMISVC_VERSION = 'v0.16.0'
GATEWAY_API_VERSION = 'v1.2.0'
GATEWAY_INFERENCE_EXT_VERSION = 'v1.3.0'
LWS_VERSION = 'v0.7.0'
ENVOY_GATEWAY_VERSION = 'v1.6.3'

# Install URLs
CERT_MANAGER_URL = (
    f'https://github.com/cert-manager/cert-manager/releases/download/'
    f'{CERT_MANAGER_VERSION}/cert-manager.yaml')
KSERVE_URL = (
    f'https://github.com/kserve/kserve/releases/download/'
    f'{KSERVE_VERSION}/kserve.yaml')
GATEWAY_API_URL = (
    f'https://github.com/kubernetes-sigs/gateway-api/releases/download/'
    f'{GATEWAY_API_VERSION}/standard-install.yaml')
GATEWAY_INFERENCE_EXT_URL = (
    f'https://github.com/kubernetes-sigs/'
    f'gateway-api-inference-extension/releases/download/'
    f'{GATEWAY_INFERENCE_EXT_VERSION}/manifests.yaml')
LWS_URL = (
    f'https://github.com/kubernetes-sigs/lws/releases/download/'
    f'{LWS_VERSION}/manifests.yaml')
LLMISVC_CRD_CHART_URL = (
    f'https://github.com/kserve/kserve/releases/download/'
    f'{KSERVE_LLMISVC_VERSION}/'
    f'helm-chart-llmisvc-crd-{KSERVE_LLMISVC_VERSION}.tgz')
LLMISVC_RESOURCES_CHART_URL = (
    f'https://github.com/kserve/kserve/releases/download/'
    f'{KSERVE_LLMISVC_VERSION}/'
    f'helm-chart-llmisvc-resources-{KSERVE_LLMISVC_VERSION}.tgz')

# KEDA
KEDA_VERSION = 'v2.16.1'
KEDA_MANIFEST_URL = (
    f'https://github.com/kedacore/keda/releases/download/'
    f'{KEDA_VERSION}/keda-{KEDA_VERSION[1:]}.yaml')
KEDA_CHART = 'kedacore/keda'
KEDA_REPO_URL = 'https://kedacore.github.io/charts'
KEDA_NAMESPACE = 'keda-system'

PROMETHEUS_CHART = 'prometheus-community/prometheus'
PROMETHEUS_REPO_URL = 'https://prometheus-community.github.io/helm-charts'
PROMETHEUS_NAMESPACE = 'skypilot'
PROMETHEUS_RELEASE = 'skypilot-prometheus'

# Prometheus values for remote clusters (matches examples/metrics/
# prometheus-values.yaml). Annotation-based pod scraping is enabled by
# default in the prometheus-community chart.
PROMETHEUS_VALUES = {
    'server': {
        'persistentVolume': {
            'enabled': False,
        },
        'retention': '7d',
    },
    'kube-state-metrics': {
        'enabled': True,
        'metricLabelsAllowlist': [
            'pods=[skypilot-cluster,skypilot-cluster-name,'
            'skypilot.co/service]',
        ],
    },
    'prometheus-node-exporter': {
        'enabled': False,
    },
    'prometheus-pushgateway': {
        'enabled': False,
    },
    'alertmanager': {
        'enabled': False,
    },
}


def _run_kubectl_ctx(args: List[str],
                     context: Optional[str] = None,
                     timeout: int = 60,
                     input_data: Optional[str] = None) -> Tuple[int, str, str]:
    """Run kubectl with optional --context flag."""
    cmd = ['kubectl']
    if context:
        cmd.extend(['--context', context])
    cmd.extend(args)
    logger.debug('Running: %s', ' '.join(cmd))
    try:
        result = subprocess.run(cmd,
                                capture_output=True,
                                text=True,
                                timeout=timeout,
                                input=input_data)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, '', f'Command timed out after {timeout}s'
    except FileNotFoundError:
        return 1, '', 'kubectl not found'


def _run_helm_ctx(args: List[str],
                  context: Optional[str] = None,
                  timeout: int = 180) -> Tuple[int, str, str]:
    """Run helm with optional --kube-context flag."""
    cmd = ['helm']
    if context:
        cmd.extend(['--kube-context', context])
    cmd.extend(args)
    logger.debug('Running: %s', ' '.join(cmd))
    try:
        result = subprocess.run(cmd,
                                capture_output=True,
                                text=True,
                                timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, '', f'Command timed out after {timeout}s'
    except FileNotFoundError:
        return 1, '', 'helm not found'


def _wait_for_crd(context: Optional[str],
                  crd_name: str,
                  timeout: int = 120) -> bool:
    """Wait for a CRD to become available."""
    start = time.time()
    while time.time() - start < timeout:
        rc, _, _ = _run_kubectl_ctx(
            ['get', 'crd', crd_name, '--no-headers'],
            context=context,
            timeout=10)
        if rc == 0:
            return True
        time.sleep(5)
    return False


def _wait_for_pods_ready(context: Optional[str],
                         namespace: str,
                         label_selector: str,
                         timeout: int = 300) -> bool:
    """Wait for pods matching a label selector to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        rc, out, _ = _run_kubectl_ctx(
            ['get', 'pods', '-n', namespace, '-l', label_selector,
             '-o', 'jsonpath={range .items[*]}{.status.phase}{" "}{end}'],
            context=context,
            timeout=10)
        if rc == 0 and out.strip():
            phases = out.strip().split()
            if phases and all(p == 'Running' for p in phases):
                return True
        elapsed = int(time.time() - start)
        logger.info('  [%ds] Waiting for pods (%s) in %s...', elapsed,
                     label_selector, namespace)
        time.sleep(10)
    return False


def _check_crd_exists(context: Optional[str], crd_name: str) -> bool:
    """Quick check if a CRD exists."""
    rc, _, _ = _run_kubectl_ctx(
        ['get', 'crd', crd_name, '--no-headers'],
        context=context,
        timeout=10)
    return rc == 0


def _check_namespace_exists(context: Optional[str], namespace: str) -> bool:
    """Quick check if a namespace exists."""
    rc, _, _ = _run_kubectl_ctx(
        ['get', 'namespace', namespace, '--no-headers'],
        context=context,
        timeout=10)
    return rc == 0


def _check_helm_release(context: Optional[str], release: str,
                        namespace: str) -> bool:
    """Check if a Helm release exists."""
    rc, _, _ = _run_helm_ctx(
        ['status', release, '-n', namespace],
        context=context,
        timeout=15)
    return rc == 0


# ── Individual installers ──────────────────────────────────────────


def install_cert_manager(context: Optional[str] = None,
                         timeout: int = 180) -> PrereqStatus:
    """Install cert-manager if not present."""
    if _check_namespace_exists(context, 'cert-manager'):
        return PrereqStatus('cert-manager', True, message='Already installed')

    print('  Installing cert-manager...')
    rc, out, err = _run_kubectl_ctx(
        ['apply', '-f', CERT_MANAGER_URL],
        context=context,
        timeout=120)
    if rc != 0:
        logger.warning('cert-manager install failed: %s', err[:500])
        return PrereqStatus('cert-manager', False,
                            message=f'Install failed: {err[:200]}')

    # Wait for webhook pod to be ready (critical for KServe)
    print('  Waiting for cert-manager pods...')
    ready = _wait_for_pods_ready(
        context, 'cert-manager',
        'app.kubernetes.io/instance=cert-manager', timeout)
    if not ready:
        logger.warning('cert-manager pods not ready within %ds', timeout)
        return PrereqStatus('cert-manager', True,
                            message='Installed but pods not fully ready')

    # Extra wait for webhook to start serving
    time.sleep(10)
    return PrereqStatus('cert-manager', True,
                        version=CERT_MANAGER_VERSION,
                        message='Installed')


def install_kserve(context: Optional[str] = None,
                   timeout: int = 180) -> PrereqStatus:
    """Install KServe if not present."""
    if _check_crd_exists(context,
                         'llminferenceservices.serving.kserve.io'):
        return PrereqStatus('KServe', True,
                            message='Already installed (LLMInferenceService)')

    # cert-manager must be ready
    if not _check_namespace_exists(context, 'cert-manager'):
        return PrereqStatus('KServe', False,
                            message='Skipped: cert-manager not installed')

    print('  Installing KServe...')
    # Use --server-side to avoid annotation size limit with large CRDs
    rc, out, err = _run_kubectl_ctx(
        ['apply', '--server-side', '--force-conflicts', '-f', KSERVE_URL],
        context=context,
        timeout=120)
    if rc != 0:
        logger.warning('KServe install failed: %s', err[:500])
        return PrereqStatus('KServe', False,
                            message=f'Install failed: {err[:200]}')

    # Wait for the CRD to be registered
    print('  Waiting for KServe CRDs...')
    if _wait_for_crd(context, 'inferenceservices.serving.kserve.io',
                     timeout):
        return PrereqStatus('KServe', True,
                            version=KSERVE_VERSION,
                            message='Installed')

    return PrereqStatus('KServe', True,
                        message='Installed but CRDs not yet ready')


def install_gateway_api(context: Optional[str] = None,
                        timeout: int = 60) -> PrereqStatus:
    """Install Gateway API CRDs if not present."""
    if _check_crd_exists(context, 'gateways.gateway.networking.k8s.io'):
        return PrereqStatus('Gateway API', True,
                            message='Already installed')

    print('  Installing Gateway API CRDs...')
    rc, out, err = _run_kubectl_ctx(
        ['apply', '-f', GATEWAY_API_URL],
        context=context,
        timeout=60)
    if rc != 0:
        logger.warning('Gateway API install failed: %s', err[:500])
        return PrereqStatus('Gateway API', False,
                            message=f'Install failed: {err[:200]}')

    return PrereqStatus('Gateway API', True,
                        version=GATEWAY_API_VERSION,
                        message='Installed')


def install_gateway_inference_ext(context: Optional[str] = None,
                                   timeout: int = 60) -> PrereqStatus:
    """Install Gateway API Inference Extension CRDs."""
    if _check_crd_exists(context,
                         'inferencepools.inference.networking.x-k8s.io'):
        return PrereqStatus('Gateway Inference Extension', True,
                            message='Already installed')

    print('  Installing Gateway API Inference Extension...')
    rc, out, err = _run_kubectl_ctx(
        ['apply', '-f', GATEWAY_INFERENCE_EXT_URL],
        context=context,
        timeout=60)
    if rc != 0:
        logger.warning('Gateway Inference Ext install failed: %s', err[:500])
        return PrereqStatus('Gateway Inference Extension', False,
                            message=f'Install failed: {err[:200]}')

    return PrereqStatus('Gateway Inference Extension', True,
                        version=GATEWAY_INFERENCE_EXT_VERSION,
                        message='Installed')


def install_lws(context: Optional[str] = None,
                timeout: int = 60) -> PrereqStatus:
    """Install LeaderWorkerSet (LWS) for multi-GPU inference."""
    if _check_crd_exists(context, 'leaderworkersets.leaderworkerset.x-k8s.io'):
        return PrereqStatus('LeaderWorkerSet', True,
                            message='Already installed')

    print('  Installing LeaderWorkerSet...')
    rc, out, err = _run_kubectl_ctx(
        ['apply', '--server-side', '-f', LWS_URL],
        context=context,
        timeout=60)
    if rc != 0:
        logger.warning('LWS install failed: %s', err[:500])
        return PrereqStatus('LeaderWorkerSet', False,
                            message=f'Install failed: {err[:200]}')

    return PrereqStatus('LeaderWorkerSet', True,
                        version=LWS_VERSION,
                        message='Installed')


def install_envoy_gateway(context: Optional[str] = None,
                           timeout: int = 120) -> PrereqStatus:
    """Install Envoy Gateway via Helm."""
    if _check_namespace_exists(context, 'envoy-gateway-system'):
        return PrereqStatus('Envoy Gateway', True,
                            message='Already installed')

    print('  Installing Envoy Gateway...')
    rc, _, _ = _run_helm_ctx(['version'], timeout=5)
    if rc != 0:
        return PrereqStatus('Envoy Gateway', False,
                            message='Helm required but not found')

    rc, out, err = _run_helm_ctx(
        ['upgrade', '-i', 'eg',
         f'oci://docker.io/envoyproxy/gateway-helm',
         '--version', ENVOY_GATEWAY_VERSION,
         '-n', 'envoy-gateway-system', '--create-namespace',
         '--wait', '--timeout', f'{timeout}s'],
        context=context,
        timeout=timeout + 30)
    if rc != 0:
        logger.warning('Envoy Gateway install failed: %s', err[:500])
        return PrereqStatus('Envoy Gateway', False,
                            message=f'Install failed: {err[:200]}')

    return PrereqStatus('Envoy Gateway', True,
                        version=ENVOY_GATEWAY_VERSION,
                        message='Installed')


def install_llmisvc(context: Optional[str] = None,
                     timeout: int = 120) -> PrereqStatus:
    """Install KServe LLMInferenceService CRD and controller.

    Uses Helm if available, otherwise downloads chart and applies via kubectl.
    """
    if _check_crd_exists(context,
                         'llminferenceservices.serving.kserve.io'):
        return PrereqStatus('LLMInferenceService', True,
                            message='Already installed')

    # Check KServe is installed first
    if not _check_crd_exists(context,
                             'inferenceservices.serving.kserve.io'):
        return PrereqStatus('LLMInferenceService', False,
                            message='Skipped: KServe not installed')

    print('  Installing LLMInferenceService CRD + controller...')

    # Try Helm
    rc_helm, _, _ = _run_helm_ctx(['version'], timeout=5)
    if rc_helm == 0:
        # Install CRD chart
        rc, _, err = _run_helm_ctx(
            ['install', 'kserve-llmisvc-crd',
             LLMISVC_CRD_CHART_URL,
             '-n', 'kserve', '--create-namespace'],
            context=context,
            timeout=60)
        if rc != 0 and 'already exists' not in err:
            logger.warning('LLMInferenceService CRD chart failed: %s',
                           err[:300])

        # Install resources chart via template + kubectl
        # (avoids ConfigMap ownership conflicts with base KServe)
        rc, out, err = _run_helm_ctx(
            ['template', 'kserve-llmisvc',
             LLMISVC_RESOURCES_CHART_URL,
             '--namespace', 'kserve'],
            context=context,
            timeout=30)
        if rc == 0 and out.strip():
            rc2, _, err2 = _run_kubectl_ctx(
                ['apply', '--server-side', '--force-conflicts', '-f', '-'],
                context=context,
                input_data=out,
                timeout=60)
            if rc2 != 0:
                logger.warning('LLMInferenceService resources failed: %s',
                               err2[:300])
                return PrereqStatus(
                    'LLMInferenceService', False,
                    message=f'Resources apply failed: {err2[:200]}')
        else:
            return PrereqStatus(
                'LLMInferenceService', False,
                message=f'Chart template failed: {err[:200]}')
    else:
        # Without Helm, download and extract chart tarballs
        print('  Helm not available, downloading charts via curl...')
        # Use kubectl run to download and apply from inside the cluster
        install_script = f"""
set -e
cd /tmp
curl -sL {LLMISVC_CRD_CHART_URL} -o llmisvc-crd.tgz
tar xzf llmisvc-crd.tgz
for f in $(find . -name '*.yaml' -path '*/templates/*'); do
    kubectl apply --server-side --force-conflicts -f "$f" -n kserve 2>/dev/null || true
done
curl -sL {LLMISVC_RESOURCES_CHART_URL} -o llmisvc-res.tgz
tar xzf llmisvc-res.tgz
for f in $(find . -name '*.yaml' -path '*/templates/*'); do
    kubectl apply --server-side --force-conflicts -f "$f" -n kserve 2>/dev/null || true
done
echo "DONE"
"""
        rc, out, err = _run_kubectl_ctx(
            ['run', '--rm', '-i', '--restart=Never',
             '--image=bitnami/kubectl:latest',
             'llmisvc-installer', '-n', 'kserve',
             '--', 'bash', '-c', install_script],
            context=context,
            input_data=None,
            timeout=120)
        if rc != 0 or 'DONE' not in out:
            return PrereqStatus(
                'LLMInferenceService', False,
                message=f'Install failed: {err[:200]}')

    # Wait for CRD
    if _wait_for_crd(context, 'llminferenceservices.serving.kserve.io',
                     timeout):
        return PrereqStatus('LLMInferenceService', True,
                            version=KSERVE_LLMISVC_VERSION,
                            message='Installed')

    return PrereqStatus('LLMInferenceService', True,
                        message='Installed but CRD not yet ready')


def install_keda(context: Optional[str] = None,
                 timeout: int = 120) -> PrereqStatus:
    """Install KEDA if not present. Tries Helm first, falls back to kubectl."""
    if _check_crd_exists(context, 'scaledobjects.keda.sh'):
        return PrereqStatus('KEDA', True, message='Already installed')

    print('  Installing KEDA...')

    # Try Helm first
    rc, _, _ = _run_helm_ctx(['version'], timeout=5)
    if rc == 0:
        _run_helm_ctx(['repo', 'add', 'kedacore', KEDA_REPO_URL],
                      context=context, timeout=30)
        _run_helm_ctx(['repo', 'update'], context=context, timeout=30)
        rc, out, err = _run_helm_ctx(
            ['upgrade', '--install', 'keda', KEDA_CHART,
             '-n', KEDA_NAMESPACE, '--create-namespace', '--wait',
             '--timeout', f'{timeout}s'],
            context=context,
            timeout=timeout + 30)
        if rc == 0:
            return PrereqStatus('KEDA', True, message='Installed via Helm')

    # Fall back to kubectl apply with manifest
    print('  Helm not available, installing KEDA via kubectl...')
    rc, out, err = _run_kubectl_ctx(
        ['apply', '--server-side', '--force-conflicts',
         '-f', KEDA_MANIFEST_URL],
        context=context,
        timeout=120)
    if rc != 0:
        logger.warning('KEDA install failed: %s', err[:500])
        return PrereqStatus('KEDA', False,
                            message=f'Install failed: {err[:200]}')

    # Wait for the CRD to be registered
    print('  Waiting for KEDA CRDs...')
    if _wait_for_crd(context, 'scaledobjects.keda.sh', timeout):
        return PrereqStatus('KEDA', True,
                            version=KEDA_VERSION,
                            message='Installed via kubectl')

    return PrereqStatus('KEDA', True,
                        message='Installed but CRDs not yet ready')


def install_prometheus(context: Optional[str] = None,
                       timeout: int = 120) -> PrereqStatus:
    """Install Prometheus via Helm if not present in skypilot namespace."""
    # Check if Prometheus service already exists
    rc, out, _ = _run_kubectl_ctx(
        ['get', 'svc', '-n', PROMETHEUS_NAMESPACE,
         'skypilot-prometheus-server', '--no-headers'],
        context=context, timeout=10)
    if rc == 0 and out.strip():
        return PrereqStatus('Prometheus', True,
                            message='Already installed')

    # Check for any existing Prometheus (e.g., Victoria Metrics on CoreWeave)
    rc, out, _ = _run_kubectl_ctx(
        ['get', 'svc', '-A', '-l',
         'app.kubernetes.io/name=prometheus', '--no-headers'],
        context=context, timeout=10)
    if rc == 0 and out.strip():
        return PrereqStatus('Prometheus', True,
                            message='Existing Prometheus found')

    print('  Installing Prometheus...')
    # Add Helm repo
    _run_helm_ctx(
        ['repo', 'add', 'prometheus-community', PROMETHEUS_REPO_URL],
        context=context, timeout=30)
    _run_helm_ctx(['repo', 'update'], context=context, timeout=30)

    # Write values to temp file
    import yaml
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(PROMETHEUS_VALUES, f)
        values_path = f.name

    try:
        rc, out, err = _run_helm_ctx(
            ['upgrade', '--install', PROMETHEUS_RELEASE, PROMETHEUS_CHART,
             '-n', PROMETHEUS_NAMESPACE, '--create-namespace',
             '-f', values_path, '--wait',
             '--timeout', f'{timeout}s'],
            context=context,
            timeout=timeout + 30)
    finally:
        import os
        os.unlink(values_path)

    if rc != 0:
        logger.warning('Prometheus install failed: %s', err[:500])
        return PrereqStatus('Prometheus', False,
                            message=f'Install failed: {err[:200]}')

    return PrereqStatus('Prometheus', True, message='Installed')


# ── Orchestrator ───────────────────────────────────────────────────


def ensure_all_prerequisites(
    context: Optional[str] = None,
) -> Dict[str, PrereqStatus]:
    """Install all prerequisites in dependency order.

    Phase 1 (parallel): cert-manager, KEDA, Prometheus, LWS
    Phase 2: KServe base (depends on cert-manager)
    Phase 3 (parallel): Gateway API, Gateway Inference Extension,
                         Envoy Gateway
    Phase 4: LLMInferenceService (depends on KServe + Gateway API)

    Returns dict of component name -> PrereqStatus.
    """
    results: Dict[str, PrereqStatus] = {}

    # Phase 1: Independent components in parallel
    print('\nPhase 1: Installing independent components...')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        cert_future = pool.submit(install_cert_manager, context)
        keda_future = pool.submit(install_keda, context)
        prom_future = pool.submit(install_prometheus, context)
        lws_future = pool.submit(install_lws, context)

        try:
            results['cert_manager'] = cert_future.result(timeout=300)
        except Exception as e:  # pylint: disable=broad-except
            results['cert_manager'] = PrereqStatus(
                'cert-manager', False, message=str(e))

        try:
            results['keda'] = keda_future.result(timeout=240)
        except Exception as e:  # pylint: disable=broad-except
            results['keda'] = PrereqStatus('KEDA', False, message=str(e))

        try:
            results['prometheus'] = prom_future.result(timeout=240)
        except Exception as e:  # pylint: disable=broad-except
            results['prometheus'] = PrereqStatus(
                'Prometheus', False, message=str(e))

        try:
            results['lws'] = lws_future.result(timeout=120)
        except Exception as e:  # pylint: disable=broad-except
            results['lws'] = PrereqStatus(
                'LeaderWorkerSet', False, message=str(e))

    for name, st in results.items():
        print(f'  {st}')

    # Phase 2: KServe base (needs cert-manager)
    print('\nPhase 2: Installing KServe...')
    if results['cert_manager'].installed:
        results['kserve'] = install_kserve(context)
    else:
        results['kserve'] = PrereqStatus(
            'KServe', False,
            message='Skipped: cert-manager not available')
    print(f'  {results["kserve"]}')

    # Phase 3: Gateway components (parallel)
    print('\nPhase 3: Installing Gateway components...')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        gw_future = pool.submit(install_gateway_api, context)
        gw_inf_future = pool.submit(install_gateway_inference_ext, context)
        envoy_future = pool.submit(install_envoy_gateway, context)

        try:
            results['gateway_api'] = gw_future.result(timeout=120)
        except Exception as e:  # pylint: disable=broad-except
            results['gateway_api'] = PrereqStatus(
                'Gateway API', False, message=str(e))
        try:
            results['gateway_inference_ext'] = (
                gw_inf_future.result(timeout=120))
        except Exception as e:  # pylint: disable=broad-except
            results['gateway_inference_ext'] = PrereqStatus(
                'Gateway Inference Extension', False, message=str(e))
        try:
            results['envoy_gateway'] = envoy_future.result(timeout=180)
        except Exception as e:  # pylint: disable=broad-except
            results['envoy_gateway'] = PrereqStatus(
                'Envoy Gateway', False, message=str(e))

    for k in ['gateway_api', 'gateway_inference_ext', 'envoy_gateway']:
        print(f'  {results[k]}')

    # Phase 4: LLMInferenceService (needs KServe + Gateway API)
    print('\nPhase 4: Installing LLMInferenceService...')
    if results['kserve'].installed:
        results['llmisvc'] = install_llmisvc(context)
    else:
        results['llmisvc'] = PrereqStatus(
            'LLMInferenceService', False,
            message='Skipped: KServe not available')
    print(f'  {results["llmisvc"]}')

    return results

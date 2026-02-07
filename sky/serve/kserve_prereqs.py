"""Prerequisite checker for SkyServe v2 on Kubernetes.

Verifies that the target K8s cluster has the required components installed:
- KServe v0.15+
- Gateway API Inference Extension
- Envoy Gateway or Istio
- KEDA (optional, for autoscaling)
- GPU operator
- Prometheus (optional, for observability)
"""
import json
import logging
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PrereqStatus:
    """Status of a prerequisite check."""
    def __init__(self, name: str, installed: bool,
                 version: Optional[str] = None,
                 message: str = '',
                 install_hint: str = ''):
        self.name = name
        self.installed = installed
        self.version = version
        self.message = message
        self.install_hint = install_hint

    def __str__(self):
        status = 'OK' if self.installed else 'MISSING'
        ver = f' (v{self.version})' if self.version else ''
        hint = f'\n  Install: {self.install_hint}' if (
            not self.installed and self.install_hint) else ''
        msg = f' - {self.message}' if self.message else ''
        return f'  [{status}] {self.name}{ver}{msg}{hint}'


def _run_kubectl(args: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    """Run a kubectl command and return (returncode, stdout, stderr)."""
    cmd = ['kubectl'] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, '', 'Command timed out'
    except FileNotFoundError:
        return 1, '', 'kubectl not found'


def check_kubectl() -> PrereqStatus:
    """Check if kubectl is available and configured."""
    rc, out, err = _run_kubectl(['version', '--client', '-o', 'json'])
    if rc != 0:
        return PrereqStatus('kubectl', False,
                           message='kubectl not found or not configured',
                           install_hint='https://kubernetes.io/docs/tasks/'
                                       'tools/install-kubectl/')
    try:
        data = json.loads(out)
        version = data.get('clientVersion', {}).get('gitVersion', 'unknown')
    except json.JSONDecodeError:
        version = 'unknown'
    return PrereqStatus('kubectl', True, version=version)


def check_cluster_connection() -> PrereqStatus:
    """Check if we can connect to the K8s cluster."""
    rc, out, err = _run_kubectl(['cluster-info'])
    if rc != 0:
        return PrereqStatus('K8s cluster', False,
                           message=f'Cannot connect: {err.strip()[:200]}')
    return PrereqStatus('K8s cluster', True, message='Connected')


def check_gpu_nodes() -> PrereqStatus:
    """Check if the cluster has GPU nodes."""
    rc, out, err = _run_kubectl([
        'get', 'nodes', '-o', 'json',
    ])
    if rc != 0:
        return PrereqStatus('GPU nodes', False, message='Cannot list nodes')

    try:
        data = json.loads(out)
        gpu_nodes = 0
        total_gpus = 0
        for node in data.get('items', []):
            alloc = node.get('status', {}).get('allocatable', {})
            gpus = int(alloc.get('nvidia.com/gpu', '0'))
            if gpus > 0:
                gpu_nodes += 1
                total_gpus += gpus
    except (json.JSONDecodeError, ValueError):
        return PrereqStatus('GPU nodes', False,
                           message='Cannot parse node info')

    if gpu_nodes == 0:
        return PrereqStatus(
            'GPU nodes', False,
            message='No GPU nodes found',
            install_hint='Ensure NVIDIA GPU operator is installed and '
                        'GPU nodes are in the cluster')
    return PrereqStatus(
        'GPU nodes', True,
        message=f'{gpu_nodes} node(s) with {total_gpus} total GPUs')


def check_kserve() -> PrereqStatus:
    """Check if KServe is installed."""
    # Check for LLMInferenceService CRD
    rc, out, _ = _run_kubectl([
        'get', 'crd', 'llminferenceservices.serving.kserve.io',
        '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus(
            'KServe (LLMInferenceService)', True,
            message='LLMInferenceService CRD found')

    # Check for regular InferenceService CRD
    rc, out, _ = _run_kubectl([
        'get', 'crd', 'inferenceservices.serving.kserve.io',
        '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus(
            'KServe', True,
            message='InferenceService CRD found (but no LLMInferenceService)',
        )

    return PrereqStatus(
        'KServe', False,
        message='KServe CRDs not found',
        install_hint='kubectl apply -f '
                    'https://github.com/kserve/kserve/releases/download/'
                    'v0.15.1/kserve.yaml')


def check_gateway_api() -> PrereqStatus:
    """Check if Gateway API Inference Extension is installed."""
    rc, out, _ = _run_kubectl([
        'get', 'crd', 'inferencepools.inference.networking.x-k8s.io',
        '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus('Gateway API Inference Extension', True)

    # Check for basic Gateway API
    rc, out, _ = _run_kubectl([
        'get', 'crd', 'gateways.gateway.networking.k8s.io',
        '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus(
            'Gateway API', True,
            message='Gateway API found (but no Inference Extension)')

    return PrereqStatus(
        'Gateway API', False,
        message='Gateway API CRDs not found',
        install_hint='kubectl apply -f https://github.com/kubernetes-sigs/'
                    'gateway-api/releases/download/v1.2.0/'
                    'standard-install.yaml')


def check_keda() -> PrereqStatus:
    """Check if KEDA is installed."""
    rc, out, _ = _run_kubectl([
        'get', 'crd', 'scaledobjects.keda.sh', '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus('KEDA', True)

    return PrereqStatus(
        'KEDA', False,
        message='KEDA not found (optional, for autoscaling)',
        install_hint='helm install keda kedacore/keda -n keda-system '
                    '--create-namespace')


def check_prometheus() -> PrereqStatus:
    """Check if Prometheus is available."""
    rc, out, _ = _run_kubectl([
        'get', 'svc', '-A', '-l',
        'app.kubernetes.io/name=prometheus',
        '--no-headers',
    ])
    if rc == 0 and out.strip():
        return PrereqStatus('Prometheus', True, message='Found')

    # Also check victoria-metrics (common alternative)
    rc, out, _ = _run_kubectl([
        'get', 'namespace', 'cw-victoria-metrics', '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus('Metrics', True,
                           message='Victoria Metrics found')

    return PrereqStatus(
        'Prometheus', False,
        message='No Prometheus found (optional, for observability)',
        install_hint='helm install prometheus prometheus-community/'
                    'kube-prometheus-stack -n monitoring --create-namespace')


def check_cert_manager() -> PrereqStatus:
    """Check if cert-manager is installed."""
    rc, out, _ = _run_kubectl([
        'get', 'namespace', 'cert-manager', '--no-headers',
    ])
    if rc == 0:
        return PrereqStatus('cert-manager', True)
    return PrereqStatus(
        'cert-manager', False,
        message='Required by KServe',
        install_hint='kubectl apply -f https://github.com/cert-manager/'
                    'cert-manager/releases/download/v1.16.3/'
                    'cert-manager.yaml')


def check_all() -> Dict[str, PrereqStatus]:
    """Run all prerequisite checks.

    Returns dict of check_name -> PrereqStatus.
    """
    checks = {
        'kubectl': check_kubectl(),
        'cluster': check_cluster_connection(),
        'gpu_nodes': check_gpu_nodes(),
        'cert_manager': check_cert_manager(),
        'kserve': check_kserve(),
        'gateway_api': check_gateway_api(),
        'keda': check_keda(),
        'prometheus': check_prometheus(),
    }
    return checks


def print_prereq_report(checks: Optional[Dict[str, PrereqStatus]] = None):
    """Print a formatted prerequisites report."""
    if checks is None:
        checks = check_all()

    print('\nSkyServe v2 Prerequisites Check')
    print('=' * 50)

    required = ['kubectl', 'cluster', 'gpu_nodes']
    recommended = ['cert_manager', 'kserve', 'gateway_api']
    optional = ['keda', 'prometheus']

    print('\nRequired:')
    for name in required:
        if name in checks:
            print(checks[name])

    print('\nFor KServe backend:')
    for name in recommended:
        if name in checks:
            print(checks[name])

    print('\nOptional:')
    for name in optional:
        if name in checks:
            print(checks[name])

    # Summary
    all_required_ok = all(
        checks[n].installed for n in required if n in checks)
    kserve_ok = all(
        checks[n].installed for n in recommended if n in checks)

    print('\n' + '-' * 50)
    if all_required_ok and kserve_ok:
        print('All prerequisites met. Ready for KServe deployment.')
    elif all_required_ok:
        print('Basic prerequisites met. KServe not fully installed.')
        print('Will use direct Deployment mode (without KServe features).')
    else:
        print('MISSING required prerequisites. See install hints above.')

    return all_required_ok


def can_use_kserve() -> bool:
    """Quick check if KServe LLMInferenceService is available."""
    rc, _, _ = _run_kubectl([
        'get', 'crd', 'llminferenceservices.serving.kserve.io',
        '--no-headers',
    ])
    return rc == 0


def can_use_direct_deployment() -> bool:
    """Check if we can do a direct Deployment (without KServe)."""
    checks = {
        'kubectl': check_kubectl(),
        'cluster': check_cluster_connection(),
        'gpu_nodes': check_gpu_nodes(),
    }
    return all(c.installed for c in checks.values())

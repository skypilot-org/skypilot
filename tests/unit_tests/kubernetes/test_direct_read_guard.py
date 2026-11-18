"""Read-only Kubernetes reads go through sky.provision.kubernetes.reads.

A direct read is a call of a list_*, read_* or get_* method of a kubernetes
client API class, or a `kubectl get` command. Every direct read outside
reads.py must be listed in _ALLOWED with a reason; a migration to the read
layer deletes its line.
"""
# pylint: disable=redefined-outer-name
import ast
import inspect
import pathlib
import re
from typing import Dict, Iterator, Optional, Set, Tuple

import kubernetes.client
import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_EXEMPT = {
    'sky/provision/kubernetes/reads.py',
}

WRITE_PATH = 'write_path'  # provisioning, teardown, or reading back a write
PREFLIGHT = 'preflight'  # credential, permission and capability checks
DIAGNOSIS = 'diagnosis'  # failure diagnosis and debug dumps
NOT_CACHEABLE = 'not_cacheable'  # logs and secrets
UNMIGRATED = 'unmigrated'  # read-only; should read through the read layer

_ALLOWED: Dict[str, Dict[str, str]] = {
    'sky/provision/kubernetes/config.py': {
        '_configure_autoscaler_cluster_role': WRITE_PATH,
        '_configure_autoscaler_cluster_role_binding': WRITE_PATH,
        '_configure_autoscaler_role': WRITE_PATH,
        '_configure_autoscaler_role_binding': WRITE_PATH,
        '_configure_autoscaler_service_account': WRITE_PATH,
        '_configure_fuse_mounting': WRITE_PATH,
        '_configure_services': WRITE_PATH,
    },
    'sky/provision/kubernetes/debug.py': {
        '_dump_gpu_metrics_pods': DIAGNOSIS,
        '_dump_kueue_cluster_objects._probe': DIAGNOSIS,
        '_dump_labeled_resources': DIAGNOSIS,
        '_dump_pod_group': DIAGNOSIS,
        '_dump_pods': DIAGNOSIS,
        '_dump_workload': DIAGNOSIS,
        '_dump_workload._probe': DIAGNOSIS,
        'context_reachable': DIAGNOSIS,
    },
    'sky/provision/kubernetes/instance.py': {
        '_detect_cluster_event_reason_occurred': DIAGNOSIS,
        '_force_remove_terminating_pod': WRITE_PATH,
        '_get_pending_pvcs': DIAGNOSIS,
        '_get_pod_events': DIAGNOSIS,
        '_get_pod_failure_reason_from_status': DIAGNOSIS,
        '_head_block_from_configmap': WRITE_PATH,
        '_inspect_nodes._inspect_node': UNMIGRATED,
        '_raise_pod_scheduling_errors': DIAGNOSIS,
        '_read_host_network_sshd_ports': WRITE_PATH,
        '_record_pod_missing_events': DIAGNOSIS,
        '_tpu_slice_pods_by_node_pool': UNMIGRATED,
        '_wait_for_deployment_pod': WRITE_PATH,
        '_wait_for_pod_gone': WRITE_PATH,
        '_wait_for_pods_to_run': WRITE_PATH,
        '_wait_for_pods_to_schedule': WRITE_PATH,
        'get_cluster_autostop_event': UNMIGRATED,
        'is_high_availability_cluster_by_kubectl': UNMIGRATED,
        'list_namespaced_pod': UNMIGRATED,
    },
    'sky/provision/kubernetes/kai.py': {
        '_get_cluster_object': PREFLIGHT,
        '_get_pod_group': DIAGNOSIS,
        'eviction_reason': DIAGNOSIS,
    },
    'sky/provision/kubernetes/network_utils.py': {
        'create_or_replace_namespaced_ingress': WRITE_PATH,
        'create_or_replace_namespaced_service': WRITE_PATH,
        'get_ingress_external_ip_and_ports': UNMIGRATED,
        'get_loadbalancer_ip': UNMIGRATED,
        'get_pod_ip': UNMIGRATED,
        'ingress_controller_exists': PREFLIGHT,
    },
    'sky/provision/kubernetes/oci_nccl.py': {
        '_read_configmap': PREFLIGHT,
    },
    'sky/provision/kubernetes/utils.py': {
        '_diagnose_deleted_pod': DIAGNOSIS,
        '_get_unfiltered_kubernetes_nodes': UNMIGRATED,
        '_gpu_resource_key_helper': UNMIGRATED,
        '_self_exit_output': NOT_CACHEABLE,
        'check_credentials': PREFLIGHT,
        'check_nvidia_runtime_class': PREFLIGHT,
        'check_secret_exists': NOT_CACHEABLE,
        'create_namespace': WRITE_PATH,
        'diagnose_terminated_pod': DIAGNOSIS,
        'filter_pods': WRITE_PATH,
        'get_allocated_resources_by_node': UNMIGRATED,
        'get_port': UNMIGRATED,
        'get_pvc_events': DIAGNOSIS,
        'get_skypilot_pods': UNMIGRATED,
        'read_cluster_uid': PREFLIGHT,
    },
    'sky/provision/kubernetes/volume.py': {
        '_check_cluster_has_default_storage_class': PREFLIGHT,
        '_check_pvc_access_mode_error': DIAGNOSIS,
        '_check_storage_class_volume_binding_mode': PREFLIGHT,
        '_find_pvc_by_name_or_label': WRITE_PATH,
        '_get_volume_usedby': UNMIGRATED,
        '_validate_explicit_storage_class': PREFLIGHT,
        '_wait_for_daemonset_ready': WRITE_PATH,
        'check_pvc_usage_for_pod': WRITE_PATH,
        'create_persistent_volume_claim': WRITE_PATH,
        'get_all_volumes_state': UNMIGRATED,
        'get_all_volumes_usedby': UNMIGRATED,
    },
    'sky/utils/kubernetes/config_map_utils.py': {
        'initialize_configmap_sync_on_startup': WRITE_PATH,
    },
    'sky/utils/kubernetes/kubernetes_deploy_utils.py': {
        'deploy_local_cluster': WRITE_PATH,
    },
}

# `kubectl` at the start, then `get` as a word before any shell separator.
_KUBECTL_GET = re.compile(r'\s*kubectl\s[^;&|\n]*?\bget\b')


def _client_read_methods() -> Set[str]:
    names = set()
    for attr in dir(kubernetes.client):
        cls = getattr(kubernetes.client, attr)
        if inspect.isclass(cls) and attr.endswith(
                'Api') and attr != 'ApiClient':
            names.update(name for name in dir(cls)
                         if re.match(r'(list|read|get)_', name))
    return names


_READ_METHODS = _client_read_methods()


def _direct_reads(source: str) -> Iterator[Tuple[str, str]]:
    """Yields (function, read) for each direct read in a module's source.

    function is the dotted name of the enclosing functions and classes.
    """

    def read_in(node: ast.AST) -> Optional[str]:
        if (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and
                node.func.attr in _READ_METHODS):
            return node.func.attr
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and
                _KUBECTL_GET.match(node.value)):
            return 'kubectl get'
        if isinstance(node, (ast.List, ast.Tuple)):
            words = [getattr(e, 'value', None) for e in node.elts]
            if words[:1] == ['kubectl'] and 'get' in words:
                return 'kubectl get'
        return None

    def visit(node: ast.AST, scope: str) -> Iterator[Tuple[str, str]]:
        if isinstance(node,
                      (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f'{scope}.{node.name}' if scope else node.name
        read = read_in(node)
        if read is not None:
            yield scope or '<module>', read
        for child in ast.iter_child_nodes(node):
            yield from visit(child, scope)

    yield from visit(ast.parse(source), '')


def _scan() -> Dict[Tuple[str, str], Set[str]]:
    found: Dict[Tuple[str, str], Set[str]] = {}
    for path in sorted((_REPO_ROOT / 'sky').rglob('*.py')):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel in _EXEMPT or rel.startswith('sky/schemas/generated/'):
            continue
        for function, read in _direct_reads(path.read_text(encoding='utf-8')):
            found.setdefault((rel, function), set()).add(read)
    return found


@pytest.fixture(scope='module')
def found():
    return _scan()


def _allowed_keys() -> Set[Tuple[str, str]]:
    return {(path, function)
            for path, functions in _ALLOWED.items()
            for function in functions}


def test_no_new_direct_reads(found):
    allowed = _allowed_keys()
    new = {
        f'{path}:{function}': sorted(reads)
        for (path, function), reads in found.items()
        if (path, function) not in allowed
    }
    assert not new, (
        'Read Kubernetes objects through sky.provision.kubernetes.reads, or '
        f'add these functions to _ALLOWED with a reason: {new}')


def test_allowed_functions_still_read_directly(found):
    stale = sorted(f'{path}:{function}' for path, function in _allowed_keys()
                   if (path, function) not in found)
    assert not stale, f'Remove these entries from _ALLOWED: {stale}'


def test_allowed_reasons_are_known():
    reasons = {WRITE_PATH, PREFLIGHT, DIAGNOSIS, NOT_CACHEABLE, UNMIGRATED}
    assert {
        reason for functions in _ALLOWED.values()
        for reason in functions.values()
    } <= reasons


@pytest.mark.parametrize('source,expected', [
    ('def f(c):\n    kubernetes.core_api(c).list_node()\n', [('f', 'list_node')
                                                            ]),
    ('class A:\n    def f(self):\n        api.read_namespaced_pod("p", "n")\n',
     [('A.f', 'read_namespaced_pod')]),
    ('def f():\n    def g():\n        x.get_namespaced_custom_object()\n', [
        ('f.g', 'get_namespaced_custom_object')
    ]),
    ('def f():\n    subprocess.run(["kubectl", "get", "pods"])\n', [
        ('f', 'kubectl get')
    ]),
    ('def f():\n    subprocess.run("kubectl --context c get pods")\n', [
        ('f', 'kubectl get')
    ]),
    ('def f():\n    subprocess.run(["kubectl", "--context", "c", "get"])\n', [
        ('f', 'kubectl get')
    ]),
    ('def f():\n    subprocess.run("kubectl get nodes -o json", shell=True)\n',
     [('f', 'kubectl get')]),
    ('def f(c):\n    kubernetes.core_api(c).create_namespaced_pod()\n', []),
    ('def f():\n    print("To debug, run: kubectl get pods")\n', []),
    ('def f(d):\n    d.get("key")\n', []),
])
def test_scan_finds_direct_reads(source, expected):
    assert list(_direct_reads(source)) == expected

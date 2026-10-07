"""NCCL settings for OCI bare-metal GPU shapes on Kubernetes (OKE).

Oracle tunes NCCL per instance shape. Clusters built with Oracle's OKE HPC
stack carry the set in a per-shape ConfigMap; this module reads it, falls back
to a built-in copy of Oracle's table, and resolves the set for the nodes a pod
can land on.
"""
import re
from typing import Dict, List, Optional, Set, Tuple

from sky import sky_logging
from sky.adaptors import kubernetes
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

# Node label carrying the OCI shape, e.g. 'BM.GPU.B300.8'.
SHAPE_LABEL_KEY = 'node.kubernetes.io/instance-type'
# Stands in for an eligible node without that label: its NICs are unknown, so
# no shape's exact set may be claimed for the pod.
UNLABELED_SHAPE = '<no instance-type label>'

# Oracle's recommended NCCL parameters per OCI bare-metal NVIDIA GPU shape,
# verbatim from oracle-quickstart/oci-hpc-oke
# terraform/via-provider-nccl-rccl-configmap.tf (commit c48c2fbe). Oracle's
# stack writes the same values into a per-shape ConfigMap, which takes
# precedence; this table covers clusters built without it. A leading '=' in
# NCCL_IB_HCA is NCCL's exact-name-match prefix, not a typo.
_SHAPE_NCCL_PARAMS: Dict[str, Dict[str, str]] = {
    'BM.GPU4.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_QPS_PER_CONNECTION': '4',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_2,mlx5_6,mlx5_8,mlx5_10,mlx5_12,'
                        'mlx5_14,mlx5_16,mlx5_1,mlx5_3,mlx5_7,mlx5_9,mlx5_11,'
                        'mlx5_13,mlx5_15,mlx5_17'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
    },
    'BM.GPU.A100-v2.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_QPS_PER_CONNECTION': '4',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_1,mlx5_2,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_7,'
                        'mlx5_8,mlx5_14,mlx5_15,mlx5_16,mlx5_17,mlx5_9,'
                        'mlx5_10,mlx5_11,mlx5_12'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
    },
    'BM.GPU.H100.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_CUMEM_ENABLE': '0',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_1,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_7,'
                        'mlx5_8,mlx5_9,mlx5_10,mlx5_12,mlx5_13,mlx5_14,'
                        'mlx5_15,mlx5_16,mlx5_17'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_IGNORE_CPU_AFFINITY': '1',
    },
    'BM.GPU.H200.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_CUMEM_ENABLE': '0',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_9,mlx5_10,'
                        'mlx5_11'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_IGNORE_CPU_AFFINITY': '1',
    },
    'BM.GPU.RTXPRO.8': {
        'NCCL_MIN_NCHANNELS': '8',
        'NCCL_ALGO': 'Tree',
        'NCCL_DEBUG': 'WARN',
        'NCCL_CUMEM_ENABLE': '0',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_QPS_PER_CONNECTION': '1',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_6,mlx5_7,mlx5_8,'
                        'mlx5_9'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_NET_PLUGIN': 'none',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_IGNORE_CPU_AFFINITY': '1',
    },
    'BM.GPU.B4.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_QPS_PER_CONNECTION': '4',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_1,mlx5_2,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_7,'
                        'mlx5_8,mlx5_14,mlx5_15,mlx5_16,mlx5_17,mlx5_9,'
                        'mlx5_10,mlx5_11,mlx5_12'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
    },
    'BM.GPU.B200.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_CUMEM_ENABLE': '0',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_9,mlx5_10,'
                        'mlx5_11'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_IGNORE_CPU_AFFINITY': '1',
    },
    'BM.GPU.B300.8': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_CUMEM_ENABLE': '0',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_1,mlx5_7,mlx5_8,mlx5_9,mlx5_10,mlx5_11,'
                        'mlx5_12,mlx5_13,mlx5_14,mlx5_16,mlx5_17,mlx5_18,'
                        'mlx5_19,mlx5_20,mlx5_21'),
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_IGNORE_CPU_AFFINITY': '1',
    },
    'BM.GPU.GB200.4': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_MNNVL_ENABLE': '1',
        'NCCL_CUMEM_ENABLE': '1',
        'NCCL_NET_PLUGIN': 'sys',
        'NCCL_IB_HCA': 'mlx5_0,mlx5_1,mlx5_3,mlx5_4',
        'NCCL_NVLS_ENABLE': '1',
        'NCCL_SOCKET_IFNAME': 'eth0',
    },
    'BM.GPU.GB200-v2.4': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_MNNVL_ENABLE': '1',
        'NCCL_CUMEM_ENABLE': '1',
        'NCCL_NET_PLUGIN': 'sys',
        'NCCL_IB_HCA': 'mlx5_0,mlx5_1,mlx5_3,mlx5_4',
        'NCCL_NVLS_ENABLE': '1',
        'NCCL_SOCKET_IFNAME': 'eth0',
    },
    'BM.GPU.GB200-v3.4': {
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TC': '41',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_DEBUG': 'WARN',
        'NCCL_IB_QPS_PER_CONNECTION': '1',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_CUMEM_ENABLE': '1',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_5,mlx5_6,mlx5_7,'
                        'mlx5_8'),
        'NCCL_NET_GDR_C2C': '1',
        'NCCL_MNNVL_ENABLE': '1',
        'NCCL_NET_PLUGIN': 'none',
    },
    'BM.GPU.GB300.4': {
        'NCCL_DEBUG': 'WARN',
        'NCCL_MNNVL_ENABLE': '1',
        'NCCL_CUMEM_ENABLE': '1',
        'NCCL_NET_PLUGIN': 'none',
        'NCCL_IB_HCA': ('=mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_5,mlx5_6,mlx5_7,'
                        'mlx5_8'),
        'NCCL_NVLS_ENABLE': '1',
        'NCCL_SOCKET_IFNAME': 'eth0',
        'NCCL_NET_GDR_C2C': '1',
        'NCCL_IB_GID_INDEX': '3',
        'NCCL_IB_TC': '41',
        'NCCL_IB_SL': '0',
        'NCCL_IB_TIMEOUT': '22',
        'NCCL_BUFFSIZE': '16777216',
        'NCCL_IB_QPS_PER_CONNECTION': '4',
        'NCCL_IB_SPLIT_DATA_ON_QPS': '0',
        'NCCL_DMABUF_ENABLE': '1',
    },
}

# SkyPilot's tuning on top of Oracle's set. Applied only where the shape's set
# leaves the key unset, so an operator's explicit ConfigMap value wins.
_SHAPE_NCCL_OVERLAY: Dict[str, Dict[str, str]] = {
    # With NET_GDR_C2C on, NCCL's GDR cutoff is PATH_P2C, and a GPU whose NIC
    # sits one PCIe host bridge away falls outside it -- GDR silently off. PHB
    # widens the cutoff by exactly that one level; nothing else changes.
    'BM.GPU.GB300.4': {
        'NCCL_NET_GDR_LEVEL': 'PHB'
    },
}

# Grace Blackwell shapes (GB200/GB300) get no UCX settings, as before: UCX_TLS
# =tcp would push UCX users such as NIXL off RDMA and NVLink.
# Relies on OCI's shape naming; list the shapes if that ever breaks.
# TODO(hailong): no shape needs pod-wide UCX settings (Oracle passes them on
# its mpirun lines only); drop them everywhere once verified on hardware.
_GRACE_SHAPE_PREFIX = 'BM.GPU.GB'

_CONFIGMAP_KEY = 'nccl.conf'
# Only NCCL/RCCL settings are taken from the ConfigMap. This scopes it to NCCL
# tuning and is not a security boundary: NCCL uses the values as given, and
# e.g. NCCL_NET_PLUGIN names a library it loads. Whoever can write these
# ConfigMaps is trusted like a cluster admin.
_NCCL_CONF_KEY_PATTERN = re.compile(r'^[NR]CCL_[A-Z0-9_]+$')


def configmap_name(shape: str) -> str:
    """Name of Oracle's per-shape NCCL ConfigMap, e.g. for BM.GPU.H100.8:
    oci-nccl-parameters-bm-gpu-h100-8."""
    return 'oci-nccl-parameters-' + shape.lower().replace('.', '-')


def parse_nccl_conf(text: str) -> Dict[str, str]:
    """Parses `KEY=value` lines the way NCCL reads /etc/nccl.conf.

    Splits on the first '=' only, so `NCCL_IB_HCA==mlx5_0` keeps its
    exact-match prefix. Keys other than NCCL_*/RCCL_* are dropped.
    """
    params: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        key = key.strip()
        if not sep or not _NCCL_CONF_KEY_PATTERN.match(key):
            logger.warning(f'Ignoring non-NCCL line in OCI NCCL ConfigMap: '
                           f'{line!r}')
            continue
        params[key] = value.strip()
    return params


def _read_configmap(context: Optional[str], namespaces: List[str],
                    shape: str) -> Optional[Tuple[Dict[str, str], str]]:
    """Returns (params, source) from the shape's ConfigMap, or None.

    The first namespace holding a readable ConfigMap wins. A missing or
    forbidden ConfigMap moves on to the next namespace; any other failure
    gives up, since the caller has a fallback and a launch must not fail on
    a tuning hint.
    """
    name = configmap_name(shape)
    for namespace in namespaces:
        try:
            cm = kubernetes.core_api(context).read_namespaced_config_map(
                name=name,
                namespace=namespace,
                _request_timeout=kubernetes.API_TIMEOUT)
        except kubernetes.api_exception() as e:
            if e.status in (403, 404):
                continue
            logger.warning(f'Failed to read ConfigMap {namespace}/{name}: '
                           f'{e}')
            return None
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to read ConfigMap {namespace}/{name}: '
                           f'{common_utils.format_exception(e)}')
            return None
        params = parse_nccl_conf((cm.data or {}).get(_CONFIGMAP_KEY) or '')
        if not params:
            # An empty or placeholder ConfigMap must not wipe the tuning.
            logger.warning(f'ConfigMap {namespace}/{name} has no NCCL '
                           'settings; ignoring it.')
            continue
        return params, f'ConfigMap {namespace}/{name}'
    return None


def candidate_shapes(context: Optional[str], k8s_acc_label_key: Optional[str],
                     k8s_acc_label_values: Optional[List[str]],
                     node_selector: Optional[Dict[str, str]]) -> Set[str]:
    """OCI shapes of the GPU nodes the pod's affinity and nodeSelector
    allow. Empty for a CPU-only request or when no such node is up."""
    if not k8s_acc_label_key or not k8s_acc_label_values:
        return set()
    node_selector = node_selector or {}
    shapes = set()
    for node in kubernetes_utils.get_kubernetes_nodes(context=context):
        labels = node.metadata.labels or {}
        if labels.get(k8s_acc_label_key) not in k8s_acc_label_values:
            continue
        if any(labels.get(k) != v for k, v in node_selector.items()):
            continue
        shapes.add(labels.get(SHAPE_LABEL_KEY) or UNLABELED_SHAPE)
    return shapes


def get_env_vars(context: Optional[str], namespace: str, shapes: Set[str],
                 pod_local_rdma: bool) -> Dict[str, str]:
    """NCCL env vars for an OCI RoCE pod that can land on any of `shapes`.

    Per shape: Oracle's ConfigMap (the pod's namespace, then `default`), else
    the built-in copy of Oracle's table. That set replaces the generic RoCE
    profile's NCCL keys only when every shape resolves to the same one;
    otherwise the env cannot depend on which node the scheduler picks, and
    the generic profile is used.

    Args:
        pod_local_rdma: The pod receives its own RDMA devices (SR-IOV virtual
            functions), whose names differ from the host's; an exact
            NCCL_IB_HCA list would match none of them, and NCCL would fall
            back to TCP silently. Widening to the mlx5 prefix leaves NIC
            selection to the device plugin, which only hands out fabric VFs.
    """
    base = (kubernetes_utils.KubernetesHighPerformanceNetworkType.OCI_ROCE.
            get_network_env_vars())
    if shapes and all(s.startswith(_GRACE_SHAPE_PREFIX) for s in shapes):
        base = {k: v for k, v in base.items() if not k.startswith('UCX_')}
    namespaces = list(
        dict.fromkeys([namespace, kubernetes_utils.DEFAULT_NAMESPACE]))
    resolved: Dict[str, Tuple[Dict[str, str], str]] = {}
    missing = []
    for shape in sorted(shapes):
        if shape == UNLABELED_SHAPE:
            missing.append(shape)
            continue
        found = _read_configmap(context, namespaces, shape)
        if found is None and shape in _SHAPE_NCCL_PARAMS:
            found = (_SHAPE_NCCL_PARAMS[shape],
                     f'built-in profile for shape {shape}')
        if found is None:
            missing.append(shape)
        else:
            resolved[shape] = found

    param_sets = [params for params, _ in resolved.values()]
    if not shapes:
        reason = 'node shape unknown'
    elif missing:
        reason = f'no NCCL parameters for shape {", ".join(missing)}'
    elif any(params != param_sets[0] for params in param_sets):
        reason = f'NCCL parameters differ across shapes {", ".join(resolved)}'
    else:
        reason = None
    if reason is not None:
        logger.info('OCI network_tier=best: using generic RoCE NCCL profile '
                    f'({reason}).')
        return base

    # Oracle's set is NCCL only; the generic profile's UCX settings stay.
    env = {k: v for k, v in base.items() if k.startswith('UCX_')}
    env.update(param_sets[0])
    overlays = [_SHAPE_NCCL_OVERLAY.get(shape, {}) for shape in resolved]
    if all(overlay == overlays[0] for overlay in overlays):
        for key, value in overlays[0].items():
            env.setdefault(key, value)
    if pod_local_rdma:
        env['NCCL_IB_HCA'] = 'mlx5'
    sources = '; '.join(source for _, source in resolved.values())
    logger.info(f'OCI network_tier=best: using NCCL parameters from {sources}.')
    return env

"""Kubernetes DRA (Dynamic Resource Allocation) utilities.

Read-side support for clusters that advertise GPUs via DRA
(``resource.k8s.io``: DeviceClass / ResourceSlice / ResourceClaim) instead
of (or in addition to) the device plugin's extended resources
(e.g. ``nvidia.com/gpu``).

The write side is unchanged: SkyPilot pods keep requesting the extended
resource key and rely on KEP-5004 (DRA extended resource mapping, i.e.
``DeviceClass.spec.extendedResourceName``) for the kube-scheduler to
translate those requests into ResourceClaims. See ``DRA_SUPPORT_DESIGN.md``
at the repository root for the full design.

All functions in this module are best-effort and read-only: if the
``resource.k8s.io`` API group is unavailable (older clusters, missing RBAC),
they degrade to empty results rather than raising, so device-plugin-only
clusters are unaffected.
"""
import collections
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Only the GA API (Kubernetes >= 1.34) is supported.
_DRA_API_GROUP = 'resource.k8s.io'
_DRA_API_VERSION = 'v1'

# Known DRA drivers that advertise GPU devices, mapping the driver name to
# the device attribute holding the (human-readable) GPU product name.
# https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu
_GPU_DRIVER_PRODUCT_ATTRIBUTES: Dict[str, str] = {
    'gpu.nvidia.com': 'productName',
}


def _list_dra_objects(context: Optional[str], plural: str) -> Dict[str, Any]:
    """Lists cluster-scoped resource.k8s.io/v1 objects, returning a dict.

    Uses a raw REST call instead of the typed client so we do not depend on
    the installed ``kubernetes`` python client shipping the (still moving)
    ``resource.k8s.io`` models. Raises ApiException on HTTP errors.
    """
    api = kubernetes.api_client(context)
    return api.call_api(
        f'/apis/{_DRA_API_GROUP}/{_DRA_API_VERSION}/{plural}',
        'GET',
        auth_settings=['BearerToken'],
        response_type='object',
        _return_http_data_only=True,
        _request_timeout=kubernetes.API_TIMEOUT,
    )


@annotations.lru_cache(scope='request', maxsize=10)
def _dra_api_available(context: Optional[str] = None) -> bool:
    """Returns True if the resource.k8s.io/v1 API is readable.

    Returns False if the API group is unavailable (pre-1.34 or pre-GA
    cluster) or unreadable (missing RBAC) — callers then fall back to
    device-plugin-only behavior.
    """
    try:
        _list_dra_objects(context, 'deviceclasses')
        return True
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.debug(f'DRA API unreadable in context {context!r} '
                         f'(status {e.status}); DRA support disabled for '
                         'this context.')
        return False
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to probe DRA API in context {context!r}: {e}')
        return False


@annotations.lru_cache(scope='request', maxsize=10)
def get_extended_resource_mapping(
        context: Optional[str] = None) -> Dict[str, str]:
    """Returns {extendedResourceName: deviceClassName} from DeviceClasses.

    A non-empty mapping means KEP-5004 is configured on the cluster: pods may
    keep requesting the extended resource (e.g. ``nvidia.com/gpu``) and the
    scheduler will translate the request into a ResourceClaim.
    """
    if not _dra_api_available(context):
        return {}
    mapping: Dict[str, str] = {}
    try:
        device_classes = _list_dra_objects(context, 'deviceclasses')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to list DeviceClasses: {e}')
        return {}
    for item in device_classes.get('items', []) or []:
        extended_resource_name = item.get('spec',
                                          {}).get('extendedResourceName')
        if extended_resource_name:
            mapping[extended_resource_name] = item['metadata']['name']
    return mapping


def _get_device_attributes(device: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts the attributes dict from a ResourceSlice device."""
    return device.get('attributes') or {}


def _get_accelerator_from_device(driver: str,
                                 device: Dict[str, Any]) -> Optional[str]:
    """Maps a DRA device to a canonical SkyPilot accelerator name.

    Returns None if the driver is not a known GPU driver or the device does
    not carry a recognizable product name.
    """
    product_attribute = _GPU_DRIVER_PRODUCT_ATTRIBUTES.get(driver)
    if product_attribute is None:
        return None
    attributes = _get_device_attributes(device)
    attribute_value = attributes.get(product_attribute, {})
    if not isinstance(attribute_value, dict):
        return None
    product_name = attribute_value.get('string')
    if not product_name:
        return None
    # DRA product names are human readable (e.g. 'NVIDIA H100 80GB HBM3')
    # while GFD label values are dash-separated
    # (e.g. 'NVIDIA-H100-80GB-HBM3'). Normalize and reuse the GFD
    # canonicalization so both allocation modes yield identical accelerator
    # names.
    # pylint: disable-next=import-outside-toplevel
    from sky.provision.kubernetes import utils as kubernetes_utils
    return kubernetes_utils.GFDLabelFormatter.get_accelerator_from_label_value(
        product_name.replace(' ', '-'))


@annotations.lru_cache(scope='request', maxsize=10)
def _get_device_index(
    context: Optional[str] = None
) -> Dict[Tuple[str, str, str], Tuple[str, str]]:
    """Indexes DRA GPU devices from ResourceSlices.

    Returns {(driver, pool, device_name): (node_name, accelerator_name)}.
    Only devices from known GPU drivers that map to a node and a
    recognizable accelerator name are included.
    """
    if not _dra_api_available(context):
        return {}
    try:
        slices = _list_dra_objects(context, 'resourceslices')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to list ResourceSlices: {e}')
        return {}
    index: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    for item in slices.get('items', []) or []:
        spec = item.get('spec', {})
        driver = spec.get('driver', '')
        if driver not in _GPU_DRIVER_PRODUCT_ATTRIBUTES:
            continue
        node_name = spec.get('nodeName')
        if not node_name:
            # Network-attached or multi-node pools cannot be attributed to a
            # single node; skip (not expected for GPU drivers today).
            logger.debug(f'Skipping non-node-local ResourceSlice '
                         f'{item.get("metadata", {}).get("name")!r} from '
                         f'driver {driver!r}.')
            continue
        pool_name = spec.get('pool', {}).get('name', '')
        for device in spec.get('devices', []) or []:
            accelerator = _get_accelerator_from_device(driver, device)
            if accelerator is None:
                continue
            device_name = device.get('name', '')
            index[(driver, pool_name, device_name)] = (node_name, accelerator)
    return index


def detect_dra(context: Optional[str] = None) -> bool:
    """Returns True if the cluster advertises GPU devices via DRA."""
    return bool(_get_device_index(context))


def get_dra_node_capacity(
        context: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """Returns per-node DRA GPU capacity.

    Returns {node_name: {accelerator_name: count}}. Empty if the cluster has
    no (readable) DRA GPU devices.
    """
    capacity: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: collections.defaultdict(int))
    for (node_name, accelerator) in _get_device_index(context).values():
        capacity[node_name][accelerator] += 1
    return {node: dict(accs) for node, accs in capacity.items()}


def get_dra_node_count_for_acc(context: Optional[str], node_name: str,
                               acc_type: str) -> int:
    """Returns the DRA GPU count on a node matching the accelerator type."""
    node_capacity = get_dra_node_capacity(context).get(node_name, {})
    # pylint: disable-next=import-outside-toplevel
    from sky.provision.kubernetes import utils as kubernetes_utils
    count = 0
    for accelerator, acc_count in node_capacity.items():
        # pylint: disable-next=protected-access
        if kubernetes_utils._accelerator_name_matches(acc_type, [accelerator]):
            count += acc_count
    return count


def get_dra_gpu_node_names(context: Optional[str] = None) -> Set[str]:
    """Returns the names of nodes whose GPUs are managed via DRA.

    A node is a "DRA node" when it appears in a GPU driver's
    ResourceSlices. This is the single source of truth for choosing a
    node's accounting mechanism: DRA nodes are accounted from
    ResourceSlices/ResourceClaims, all other nodes from
    allocatable/pod requests. If a node (mis)advertises the same GPUs
    through both the device plugin and DRA, the DRA side wins.
    """
    return set(get_dra_node_capacity(context))


def is_dra_node(context: Optional[str], node_name: str) -> bool:
    """Returns True if the node's GPUs are managed via DRA.

    See ``get_dra_gpu_node_names`` for the definition.
    """
    return node_name in get_dra_gpu_node_names(context)


def get_dra_allocated_by_node(
        context: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """Returns per-node GPU devices currently allocated via ResourceClaims.

    Returns {node_name: {accelerator_name: count}}. Includes claims created
    by the scheduler for KEP-5004 extended-resource requests, so callers that
    also sum container requests must exclude those pods (see
    ``V1Pod.status.has_extended_resource_claims`` handling in
    ``get_allocated_resources_by_node``) to avoid double counting.
    """
    device_index = _get_device_index(context)
    if not device_index:
        return {}
    try:
        claims = _list_dra_objects(context, 'resourceclaims')
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to list ResourceClaims: {e}')
        return {}
    allocated: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: collections.defaultdict(int))
    for claim in claims.get('items', []) or []:
        allocation = claim.get('status', {}).get('allocation')
        if not allocation:
            continue
        results = allocation.get('devices', {}).get('results', []) or []
        for result in results:
            if result.get('adminAccess'):
                # Admin-access allocations (monitoring etc.) do not consume
                # the device.
                continue
            key = (result.get('driver',
                              ''), result.get('pool',
                                              ''), result.get('device', ''))
            device_info = device_index.get(key)
            if device_info is None:
                continue
            node_name, accelerator = device_info
            allocated[node_name][accelerator] += 1
    return {node: dict(accs) for node, accs in allocated.items()}


def has_dra_accelerator(context: Optional[str], acc_type: str,
                        acc_count: int) -> bool:
    """Returns True if some node advertises >= acc_count matching DRA GPUs."""
    for node_name in get_dra_node_capacity(context):
        if get_dra_node_count_for_acc(context, node_name,
                                      acc_type) >= acc_count:
            return True
    return False


def list_dra_accelerators(context: Optional[str] = None) -> List[str]:
    """Returns the distinct DRA-advertised accelerator names."""
    accelerators: Set[str] = set()
    for accs in get_dra_node_capacity(context).values():
        accelerators.update(accs.keys())
    return sorted(accelerators)


def maybe_remap_resource_key_for_dra(context: Optional[str],
                                     resource_key: str) -> str:
    """Validates/remaps the pod resource key for DRA-only clusters.

    SkyPilot's write side always requests an extended resource key (e.g.
    ``nvidia.com/gpu``). On a cluster where GPUs are advertised only via DRA
    (no device plugin), such a request is only schedulable if KEP-5004 is
    configured, i.e. some DeviceClass sets ``spec.extendedResourceName``.

    Returns the resource key to request (possibly remapped to the
    DeviceClass-declared name). Raises ResourcesUnavailableError with
    actionable guidance if the cluster is DRA-only and no mapping exists.
    """
    if not detect_dra(context):
        return resource_key
    # pylint: disable-next=import-outside-toplevel
    from sky.provision.kubernetes import utils as kubernetes_utils

    # If any node still advertises the key via the device plugin, the
    # request is schedulable without KEP-5004.
    for node in kubernetes_utils.get_kubernetes_nodes(context=context):
        try:
            if int(node.status.allocatable.get(resource_key, 0)) > 0:
                return resource_key
        except (TypeError, ValueError):
            continue
    mapping = get_extended_resource_mapping(context)
    if resource_key in mapping:
        return resource_key
    if mapping:
        # KEP-5004 is configured under a different extended resource name;
        # request that name instead.
        remapped_key = sorted(mapping.keys())[0]
        logger.info(f'Cluster advertises GPUs via DRA; requesting extended '
                    f'resource {remapped_key!r} (mapped to DeviceClass '
                    f'{mapping[remapped_key]!r}) instead of {resource_key!r}.')
        return remapped_key
    with ux_utils.print_exception_no_traceback():
        raise exceptions.ResourcesUnavailableError(
            'This Kubernetes cluster advertises GPUs via DRA (Dynamic '
            'Resource Allocation) only, and no DeviceClass has '
            '`spec.extendedResourceName` set, so SkyPilot pods requesting '
            f'{resource_key!r} cannot be scheduled. To use this cluster '
            'with SkyPilot, enable the `DRAExtendedResource` feature gate '
            '(Kubernetes >= 1.34; beta in 1.35) on the kube-apiserver and '
            'kube-scheduler, and set `spec.extendedResourceName: '
            f'{resource_key}` on the GPU DeviceClass. See '
            'https://kubernetes.io/docs/concepts/scheduling-eviction/'
            'dynamic-resource-allocation/ for details.')

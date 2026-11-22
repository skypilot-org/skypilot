"""Reserve cloud resource names before provisioning can adopt resources."""
import contextlib
import hashlib
import json
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky.utils import common_utils
from sky.utils import locks
from sky.utils import yaml_utils

if typing.TYPE_CHECKING:
    from sky.backends import cloud_vm_ray_backend


def candidates(display_name: str, cloud: clouds.Cloud) -> List[str]:
    """Keep live mappings; offer bounded alternatives only for new mappings."""
    handle = global_user_state.get_handle_from_cluster_name(display_name)
    if (handle is not None and
            cloud.is_same_cloud(handle.launched_resources.cloud)):
        return [handle.cluster_name_on_cloud]

    max_length = cloud.max_cluster_name_length()
    names = [common_utils.make_cluster_name_on_cloud(display_name, max_length)]
    prefix = common_utils.make_cluster_name_on_cloud(display_name,
                                                     max_length=None,
                                                     add_user_hash=False)
    user_hash = common_utils.get_user_hash()
    # Retain the user suffix, including on clouds with short name limits.
    hash_length = 8 if max_length is None else min(
        8, max_length - len(user_hash) - 3)
    assert hash_length > 0, (display_name, max_length)
    for attempt in range(8):
        digest = hashlib.sha256(
            f'{display_name}:{user_hash}:{attempt}'.encode()).hexdigest()
        suffix = f'-{digest[:hash_length]}-{user_hash}'
        shortened = (prefix if max_length is None else
                     prefix[:max_length - len(suffix)].rstrip('-'))
        names.append(shortened + suffix)
    return names


def _namespace(cloud: Optional[clouds.Cloud], provider: Dict[str, Any],
               owner: Optional[List[str]]) -> Tuple[Optional[str], ...]:
    if cloud is None:
        return (None,)
    if isinstance(cloud, clouds.Azure):
        subscription = provider.get('subscription_id')
        resource_group = provider.get('resource_group')
        return ('azure', subscription.lower() if subscription else None,
                resource_group.lower() if resource_group else None)
    if isinstance(cloud, clouds.GCP):
        return ('gcp', provider.get('project_id'))
    if isinstance(cloud, clouds.Kubernetes):
        # Context aliases may refer to the same Kubernetes cluster. The
        # recorded cluster UID identifies it when available. An omitted
        # namespace is resolved from kubeconfig, so keep it unknown here.
        return ('kubernetes', provider.get('namespace'),
                provider.get('cluster_uid'))
    if isinstance(cloud, clouds.AWS):
        return ('aws', owner[-1] if owner else None, provider.get('region'))
    return (cloud.canonical_name(),)


def _provider_config(cluster_yaml: Optional[str]) -> Dict[str, Any]:
    if cluster_yaml is None:
        return {}
    content = global_user_state.get_cluster_yaml_str(cluster_yaml)
    return yaml_utils.safe_load(content).get('provider', {}) if content else {}


def _find_conflict(cluster_name: str, cloud_name: str,
                   namespace: Tuple[Optional[str], ...]) -> Optional[str]:
    """Return a conflicting live record, treating missing scope as unknown."""
    for record in global_user_state.get_cluster_name_reservations(
            cloud_name, cluster_name):
        handle = record['handle']
        resources = getattr(handle, 'launched_resources', None)
        existing_scope = _namespace(getattr(resources, 'cloud', None),
                                    _provider_config(handle.cluster_yaml),
                                    record['owner'])
        if not any(left is not None and right is not None and left != right
                   for left, right in zip(namespace, existing_scope)):
            return record['name']
    return None


def check_kubernetes_teardown(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle') -> None:
    """Refuse teardown when another live record could own the same resources."""
    resources = getattr(handle, 'launched_resources', None)
    cloud = getattr(resources, 'cloud', None)
    if not isinstance(cloud, clouds.Kubernetes):
        return
    conflict = _find_conflict(
        handle.cluster_name, handle.cluster_name_on_cloud,
        _namespace(cloud, _provider_config(handle.cluster_yaml), None))
    if conflict is not None:
        raise exceptions.ClusterNameCollisionError(
            f'Cannot tear down {handle.cluster_name!r}: another SkyPilot '
            'cluster record may own the same Kubernetes resources. Contact '
            'your SkyPilot administrator to resolve the duplicate records.')


@contextlib.contextmanager
def reserve(cluster_name: str, config: Dict[str, Any], cloud: clouds.Cloud,
            owner: Optional[List[str]]) -> Iterator[None]:
    """Reject another live cluster's cloud name while its INIT row is saved.

    The caller must keep this context open until add_or_update_cluster()
    commits the initial handle. Existing INIT and STOPPED rows reserve names;
    terminated history does not. The lock covers namespaces conservatively so
    missing legacy account metadata cannot let concurrent callers bypass it.
    This protects launches sharing one SkyPilot state database.

    Args:
        cluster_name: Logical cluster being launched or resumed.
        config: Resolved configuration, including persisted launch fields.
        cloud: The cloud being provisioned.
        owner: Native cloud identity for the provision attempt.

    Raises:
        ClusterNameCollisionError: Another live logical cluster owns the name.
        ExecutionPausedError: A concurrent launch is reserving the same name.
    """
    cloud_name: str = config['cluster_name']
    provider: Dict[str, Any] = config['provider']
    namespace = _namespace(cloud, provider, owner)
    lock_key = json.dumps([namespace[0], cloud_name])
    lock_id = 'cloud-name-' + hashlib.sha256(lock_key.encode()).hexdigest()
    lock = locks.get_lock(lock_id)
    try:
        lock.acquire(blocking=not common_utils.is_in_request_context())
    except locks.LockTimeout as error:
        raise exceptions.ExecutionPausedError(
            f'Cloud name {cloud_name!r} is being reserved by another launch.',
            hint='Waiting for the concurrent cluster reservation to finish.',
            retry_wait_seconds=1,
            continue_condition=locks.LockAcquirableCondition(
                lock_id)) from error
    try:
        conflict = _find_conflict(cluster_name, cloud_name, namespace)
        if conflict is not None:
            raise exceptions.ClusterNameCollisionError(
                f'Cluster {cluster_name!r} maps to cloud name {cloud_name!r}, '
                f'which is already used by cluster {conflict!r}. '
                'Choose a different cluster name.')
        yield
    finally:
        lock.release()

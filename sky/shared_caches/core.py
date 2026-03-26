"""Core business logic for shared cache management."""
import copy
from typing import Any, Dict, List, Optional, Set

import filelock

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes as kubernetes_adaptor
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import volume as volume_utils

logger = sky_logging.init_logger(__name__)

_CONFIG_LOCK_TIMEOUT_SECONDS = 10


def _update_kubernetes_config(modifier_fn) -> Dict[str, Any]:
    """Update the kubernetes configuration in the config file.

    Uses file locking to prevent race conditions, following the same pattern
    as _update_workspaces_config in sky/workspaces/core.py.

    Args:
        modifier_fn: A function that takes the current config dict and
            modifies it in-place.

    Returns:
        The updated config.
    """
    lock_path = skypilot_config.get_skypilot_config_lock_path()
    try:
        with filelock.FileLock(lock_path, _CONFIG_LOCK_TIMEOUT_SECONDS):
            current_config = skypilot_config.to_dict()
            modifier_fn(current_config)
            # Validate the entire config against the schema
            common_utils.validate_schema(
                current_config,
                schemas.get_config_schema(),
                'Invalid SkyPilot config after modification.',
                skip_none=True)
            skypilot_config.update_api_server_config_no_lock(current_config)
            return current_config
    except filelock.Timeout as e:
        raise RuntimeError(
            f'Failed to update shared cache configuration due to a timeout '
            f'when trying to acquire the lock at {lock_path}. This may '
            'indicate another SkyPilot process is currently updating the '
            'configuration. Please try again or manually remove the lock '
            f'file if you believe it is stale.') from e


def _get_shared_caches_for_path(
    config: Dict[str, Any],
    context: Optional[str],
) -> List[Dict[str, Any]]:
    """Get shared_caches list from config at the given path.

    Args:
        config: The full SkyPilot config dict.
        context: K8s context name, or None for top-level kubernetes config.

    Returns:
        The shared_caches list (may be empty).
    """
    k8s_config = config.get('kubernetes', {})
    if context is not None:
        ctx_configs = k8s_config.get('context_configs', {})
        ctx_config = ctx_configs.get(context, {})
        return ctx_config.get('shared_caches', [])
    return k8s_config.get('shared_caches', [])


def _set_shared_caches_for_path(
    config: Dict[str, Any],
    context: Optional[str],
    shared_caches: List[Dict[str, Any]],
) -> None:
    """Set shared_caches list in config at the given path.

    Args:
        config: The full SkyPilot config dict (modified in-place).
        context: K8s context name, or None for top-level kubernetes config.
        shared_caches: The new shared_caches list.
    """
    if 'kubernetes' not in config:
        config['kubernetes'] = {}
    k8s_config = config['kubernetes']

    if context is not None:
        if 'context_configs' not in k8s_config:
            k8s_config['context_configs'] = {}
        if context not in k8s_config['context_configs']:
            k8s_config['context_configs'][context] = {}
        k8s_config['context_configs'][context]['shared_caches'] = shared_caches
    else:
        k8s_config['shared_caches'] = shared_caches


@usage_lib.entrypoint
def list_shared_caches() -> List[Dict[str, Any]]:
    """List all shared cache entries from the config.

    Returns:
        Flat list of cache entries, each annotated with context and volume info:
        [{context: str|None, spec: {...}, cache_paths: [...],
          volume_type: str|None, volume_size: str|None}]
    """
    config = skypilot_config.to_dict()
    k8s_config = config.get('kubernetes', {})

    # Build a lookup of volume info by name
    volume_info: Dict[str, Dict[str, Any]] = {}
    for vol in global_user_state.get_volumes():
        handle: models.VolumeConfig = vol['handle']
        volume_info[vol['name']] = {
            'type': handle.type,
            'size': handle.size,
        }

    result = []

    def _make_entry(context: Optional[str], entry: Dict[str,
                                                        Any]) -> Dict[str, Any]:
        spec = entry.get('spec', {})
        vol_name = spec.get('name', '')
        vol = volume_info.get(vol_name, {})
        return {
            'context': context,
            'spec': spec,
            'cache_paths': entry.get('cache_paths', []),
            'volume_type': vol.get('type'),
            'volume_size': vol.get('size'),
        }

    # Top-level shared_caches (applies to all contexts)
    for entry in k8s_config.get('shared_caches', []):
        result.append(_make_entry(None, entry))

    # Per-context shared_caches
    context_configs = k8s_config.get('context_configs', {})
    for ctx_name, ctx_config in context_configs.items():
        for entry in ctx_config.get('shared_caches', []):
            result.append(_make_entry(ctx_name, entry))

    return result


@usage_lib.entrypoint
def upsert_shared_cache(
    context: Optional[str],
    spec: Dict[str, Any],
    cache_paths: List[str],
) -> List[Dict[str, Any]]:
    """Add or update a shared cache entry.

    Args:
        context: K8s context name, or None for all contexts.
        spec: Cache spec dict with at least 'name' key.
        cache_paths: List of cache mount paths.

    Returns:
        The updated list of all shared caches.
    """
    volume_name = spec.get('name')
    if not volume_name:
        raise ValueError('spec.name is required')
    if not cache_paths:
        raise ValueError('cache_paths must not be empty')

    new_entry = {
        'spec': copy.deepcopy(spec),
        'cache_paths': list(cache_paths),
    }

    def modifier(config: Dict[str, Any]) -> None:
        existing = _get_shared_caches_for_path(config, context)
        # Deep copy to avoid mutating the read-only config
        caches = copy.deepcopy(existing)

        # Check for cache path overlap with other entries
        _validate_no_path_overlap(config, context, volume_name, cache_paths)

        # Find existing entry by spec.name and replace, or append
        found = False
        for i, entry in enumerate(caches):
            if entry.get('spec', {}).get('name') == volume_name:
                caches[i] = new_entry
                found = True
                break
        if not found:
            caches.append(new_entry)

        _set_shared_caches_for_path(config, context, caches)

    _update_kubernetes_config(modifier)
    return list_shared_caches()


def _validate_no_path_overlap(
    config: Dict[str, Any],
    context: Optional[str],
    volume_name: str,
    new_paths: List[str],
) -> None:
    """Validate that new cache paths don't overlap with existing ones.

    Only checks within the same context scope (same context or top-level).
    """
    existing = _get_shared_caches_for_path(config, context)
    existing_paths: Set[str] = set()
    for entry in existing:
        # Skip the entry being updated
        if entry.get('spec', {}).get('name') == volume_name:
            continue
        for p in entry.get('cache_paths', []):
            existing_paths.add(p)

    for path in new_paths:
        if path in existing_paths:
            raise ValueError(
                f'Cache path {path!r} overlaps with an existing shared cache '
                f'entry in the same scope.')


@usage_lib.entrypoint
def delete_shared_cache(
    context: Optional[str],
    volume_name: str,
) -> List[Dict[str, Any]]:
    """Delete a shared cache entry.

    Args:
        context: K8s context name, or None for top-level config.
        volume_name: The spec.name of the cache entry to remove.

    Returns:
        The updated list of all shared caches.
    """

    def modifier(config: Dict[str, Any]) -> None:
        existing = _get_shared_caches_for_path(config, context)
        caches = [
            e for e in copy.deepcopy(existing)
            if e.get('spec', {}).get('name') != volume_name
        ]
        if len(caches) == len(existing):
            raise ValueError(
                f'Shared cache with volume name {volume_name!r} not found '
                f'in context {context!r}.')
        _set_shared_caches_for_path(config, context, caches)

    _update_kubernetes_config(modifier)
    return list_shared_caches()


@usage_lib.entrypoint
def list_k8s_contexts() -> List[str]:
    """List available Kubernetes context names."""
    try:
        return kubernetes_utils.get_all_kube_context_names()
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to list Kubernetes contexts: {e}')
        return []


@usage_lib.entrypoint
def list_storage_classes(context: Optional[str] = None) -> List[Dict[str, Any]]:
    """List storage classes for a Kubernetes context.

    Args:
        context: K8s context name. None uses the default context.

    Returns:
        List of storage class info dicts with name, provisioner, etc.
    """
    try:
        storage_api = kubernetes_adaptor.storage_api(context)
        sc_list = storage_api.list_storage_class()
        result = []
        for sc in sc_list.items:
            result.append({
                'name': sc.metadata.name,
                'provisioner': sc.provisioner,
                'allow_volume_expansion': sc.allow_volume_expansion or False,
            })
        return result
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            f'Failed to list storage classes for context {context}: {e}')
        return []


@usage_lib.entrypoint
def list_rwm_volumes() -> List[Dict[str, Any]]:
    """List existing SkyPilot volumes suitable for shared caches.

    Includes ReadWriteMany PVC volumes and hostPath volumes.

    Returns:
        List of volume info dicts suitable for dropdown selection.
    """
    all_volumes = global_user_state.get_volumes()
    result = []
    for vol in all_volumes:
        handle: models.VolumeConfig = vol['handle']
        # Include hostPath volumes (always suitable for shared caches)
        if handle.type == volume_utils.VolumeType.HOSTPATH.value:
            result.append({
                'name': vol['name'],
                'name_on_cloud': handle.name_on_cloud,
                'size': handle.size,
                'cloud': handle.cloud,
                'region': handle.region,
                'type': handle.type,
            })
            continue
        # Include RWX PVC volumes
        access_mode = handle.config.get('access_mode', '')
        if access_mode == volume_utils.VolumeAccessMode.READ_WRITE_MANY.value:
            result.append({
                'name': vol['name'],
                'name_on_cloud': handle.name_on_cloud,
                'size': handle.size,
                'cloud': handle.cloud,
                'region': handle.region,
                'type': handle.type,
            })
    return result

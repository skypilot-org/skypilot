"""Shared helpers for debug dump data serialization.

These helpers are used by both sky.utils.debug_utils (API server side) and
sky.jobs.utils (controller side) to serialize cluster records and events.
Extracted to avoid a circular import:
  debug_utils -> jobs.server.core -> jobs.utils -> debug_utils
"""
import copy
import datetime
from typing import Any, Dict, List, Optional, Tuple

from sky import global_user_state
from sky.utils import config_utils

# Sensitive config paths to redact in debug dumps, following the same
# pattern as provision/common.py:ProvisionConfig.get_redacted_config().
_SENSITIVE_CONFIG_KEYS: List[Tuple[str, ...]] = [
    ('api_server', 'endpoint'),
    ('api_server', 'service_account_token'),
]


def redact_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of config with sensitive values replaced by '<redacted>'.

    Used by both the client (sdk.py) and the server (debug_utils.py) when
    including SkyPilot config in debug dumps.
    """
    config_copy = config_utils.Config(copy.deepcopy(config))
    for field_path in _SENSITIVE_CONFIG_KEYS:
        val = config_copy.get_nested(field_path, default_value=None)
        if val is not None:
            config_copy.set_nested(field_path, '<redacted>')
    return dict(**config_copy)


def epoch_to_human(epoch: Optional[float]) -> Optional[str]:
    """Convert epoch timestamp to human-readable ISO format."""
    if epoch is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(epoch).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def serialize_cluster_record(cluster_record: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a cluster DB record to a JSON-friendly dict.

    Shared by the API server dump (_dump_cluster_info in debug_utils.py) and
    the controller manifest (_collect_cluster_debug_manifest in jobs/utils.py).
    """
    handle = cluster_record.get('handle')
    handle_info: Dict[str, Any] = {}
    if handle:
        handle_info = {
            'cluster_name': getattr(handle, 'cluster_name', None),
            'cluster_name_on_cloud': getattr(handle, 'cluster_name_on_cloud',
                                             None),
            'head_ip': getattr(handle, 'head_ip', None),
            'launched_nodes': getattr(handle, 'launched_nodes', None),
            'launched_resources': str(
                getattr(handle, 'launched_resources', None)),
            'stable_internal_external_ips': getattr(
                handle, 'stable_internal_external_ips', None),
            'stable_ssh_ports': getattr(handle, 'stable_ssh_ports', None),
            'docker_user': getattr(handle, 'docker_user', None),
            'ssh_user': getattr(handle, 'ssh_user', None),
        }

    launched_at = cluster_record.get('launched_at')
    status_updated_at = cluster_record.get('status_updated_at')
    return {
        'name': cluster_record.get('name'),
        'cluster_hash': cluster_record.get('cluster_hash'),
        'status': str(cluster_record.get('status')),
        'launched_at': launched_at,
        'launched_at_human': epoch_to_human(launched_at),
        'autostop': cluster_record.get('autostop'),
        'to_down': cluster_record.get('to_down'),
        'cluster_ever_up': cluster_record.get('cluster_ever_up'),
        'status_updated_at': status_updated_at,
        'status_updated_at_human': epoch_to_human(status_updated_at),
        'config_hash': cluster_record.get('config_hash'),
        'workspace': cluster_record.get('workspace'),
        'is_managed': cluster_record.get('is_managed'),
        'user_hash': cluster_record.get('user_hash'),
        'user_name': cluster_record.get('user_name'),
        'last_use': cluster_record.get('last_use'),
        'owner': cluster_record.get('owner'),
        'metadata': cluster_record.get('metadata'),
        'last_creation_command': cluster_record.get('last_creation_command'),
        'last_creation_yaml': cluster_record.get('last_creation_yaml'),
        'last_event': cluster_record.get('last_event'),
        'handle': handle_info,
    }


def get_cluster_events_data(cluster_hash: str) -> List[Dict[str, Any]]:
    """Get cluster events for all event types.

    Returns a list of dicts with 'event_type' and 'events' keys for non-empty
    event types. Shared by the API server dump and the controller manifest.
    """
    results: List[Dict[str, Any]] = []
    for event_type in list(global_user_state.ClusterEventType):
        events = global_user_state.get_cluster_events(cluster_name=None,
                                                      cluster_hash=cluster_hash,
                                                      event_type=event_type,
                                                      include_timestamps=True)
        if events:
            results.append({
                'event_type': event_type.value.lower(),
                'events': events,
            })
    return results

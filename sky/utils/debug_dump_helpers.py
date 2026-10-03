"""Shared helpers for debug dump data serialization.

These helpers are used by both sky.utils.debug_utils (API server side) and
sky.jobs.utils (controller side) to serialize cluster records and events.
Extracted to avoid a circular import:
  debug_utils -> jobs.server.core -> jobs.utils -> debug_utils
"""
import copy
import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from sky import global_user_state
from sky import task as task_lib
from sky.utils import config_utils
from sky.utils import yaml_utils

REDACTED_VALUE = '<redacted>'

# Sensitive config paths to redact in debug dumps, following the same
# pattern as provision/common.py:ProvisionConfig.get_redacted_config().
_SENSITIVE_CONFIG_KEYS: List[Tuple[str, ...]] = [
    ('api_server', 'endpoint'),
    ('api_server', 'service_account_token'),
]

# Config paths holding connection URIs: keep the value's shape (host and db
# name are diagnostic signal) but mask any password component.
_URI_CONFIG_KEYS: List[Tuple[str, ...]] = [
    ('db',),
]

# Env var names whose values are always redacted in debug dumps, even though
# the name does not match _SENSITIVE_ENV_VAR_NAME_RE (e.g. URI- or
# AUTH-suffixed names).
_SENSITIVE_ENV_VAR_NAMES = frozenset({
    'SKYPILOT_DB_CONNECTION_URI',
    'SKYPILOT_INITIAL_BASIC_AUTH',
    'SKYPILOT_SERVICE_ACCOUNT_TOKEN',
    'SKYPILOT_DOCKER_PASSWORD',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_SESSION_TOKEN',
    'AWS_ACCESS_KEY_ID',
    'AZURE_CLIENT_SECRET',
})

# Any env var whose NAME matches this pattern has its value redacted in debug
# dumps. Deliberately broad: a false positive (redacting a non-secret value
# whose name merely contains e.g. 'KEY') only costs a little diagnostic
# signal, while a false negative leaks a credential into a shareable dump.
_SENSITIVE_ENV_VAR_NAME_RE = re.compile(
    r'TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|PASSWD', re.IGNORECASE)

# Password component of URI-shaped values, userinfo style:
# scheme://user:password@host
_URI_USERINFO_PASSWORD_RE = re.compile(r'(://[^/@:\s]*):[^/@\s]*@')
# Password passed as a query/connection parameter: ...password=... (also
# matches libpq-style 'password=...' keyword/value connection strings).
_URI_QUERY_PASSWORD_RE = re.compile(r'((?:password|passwd|pwd)=)[^&\s]+',
                                    re.IGNORECASE)


def is_sensitive_env_var(name: str) -> bool:
    """Whether an env var's value must be redacted in debug dumps."""
    return (name in _SENSITIVE_ENV_VAR_NAMES or
            _SENSITIVE_ENV_VAR_NAME_RE.search(name) is not None)


def mask_uri_password(value: str) -> str:
    """Mask the password component of a URI-shaped value.

    Handles both userinfo passwords (scheme://user:password@host) and
    password query/connection parameters (...?password=...). Values with no
    password component are returned unchanged.
    """
    value = _URI_USERINFO_PASSWORD_RE.sub(rf'\1:{REDACTED_VALUE}@', value)
    value = _URI_QUERY_PASSWORD_RE.sub(rf'\g<1>{REDACTED_VALUE}', value)
    return value


def redact_env_vars(env_vars: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of env_vars safe to include in a debug dump.

    Names are kept (which vars are set is diagnostic signal). Values of
    credential-shaped names are replaced with '<redacted>', and any other
    string value that looks like a URI carrying a password has the password
    component masked. Used by both the client (sdk.py) and the server
    (debug_utils.py) for every environment section in a debug dump.
    """
    redacted: Dict[str, Any] = {}
    for name, value in env_vars.items():
        if is_sensitive_env_var(name):
            # Keep falsy values (e.g. set-but-empty vars) as-is: there is
            # nothing to leak, and the emptiness itself is diagnostic signal.
            redacted[name] = REDACTED_VALUE if value else value
        elif isinstance(value, str):
            redacted[name] = mask_uri_password(value)
        else:
            redacted[name] = value
    return redacted


def redact_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of config with sensitive values replaced by '<redacted>'.

    Used by both the client (sdk.py) and the server (debug_utils.py) when
    including SkyPilot config in debug dumps.
    """
    config_copy = config_utils.Config(copy.deepcopy(config))
    for field_path in _SENSITIVE_CONFIG_KEYS:
        val = config_copy.get_nested(field_path, default_value=None)
        if val is not None:
            config_copy.set_nested(field_path, REDACTED_VALUE)
    for field_path in _URI_CONFIG_KEYS:
        val = config_copy.get_nested(field_path, default_value=None)
        if isinstance(val, str):
            config_copy.set_nested(field_path, mask_uri_password(val))
    return dict(**config_copy)


def epoch_to_human(epoch: Optional[float]) -> Optional[str]:
    """Convert epoch timestamp to human-readable ISO format."""
    if epoch is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(epoch).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def redact_task_yaml(yaml_str: str) -> str:
    """Parse a task/dag YAML string and redact secrets and credentials.

    Shared by the API server dump (debug_utils.py) and the controller
    manifest (jobs/utils.py).
    """
    try:
        docs = list(yaml_utils.safe_load_all(yaml_str))
    except Exception:  # pylint: disable=broad-except
        return '<parse error, redacted>'
    for doc in docs:
        if isinstance(doc, dict):
            task_lib.redact_task_yaml_dict(doc)
    return yaml_utils.dump_yaml_str(docs)


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
        'last_creation_yaml':
            (redact_task_yaml(cluster_record['last_creation_yaml'])
             if cluster_record.get('last_creation_yaml') is not None else None),
        'last_event': cluster_record.get('last_event'),
        'handle': handle_info,
    }


def serialize_cluster_history_record(
        history_record: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a cluster history record to a JSON-friendly dict.

    The input is a record returned by
    global_user_state.get_clusters_from_history.
    """
    launched_at = history_record.get('launched_at')
    resources = history_record.get('resources')
    status = history_record.get('status')
    return {
        'name': history_record.get('name'),
        'cluster_hash': history_record.get('cluster_hash'),
        'status': str(status) if status is not None else None,
        'launched_at': launched_at,
        'launched_at_human': epoch_to_human(launched_at),
        'duration': history_record.get('duration'),
        'num_nodes': history_record.get('num_nodes'),
        'resources': str(resources) if resources is not None else None,
        'usage_intervals': history_record.get('usage_intervals'),
        'user_hash': history_record.get('user_hash'),
        'user_name': history_record.get('user_name'),
        'workspace': history_record.get('workspace'),
        'last_event': history_record.get('last_event'),
        'node_names': history_record.get('node_names'),
        'last_creation_command': history_record.get('last_creation_command'),
        'last_creation_yaml':
            (redact_task_yaml(history_record['last_creation_yaml'])
             if history_record.get('last_creation_yaml') is not None else None),
    }


def get_cluster_dump_data(cluster_name: str) -> List[Tuple[str, Any]]:
    """Collect JSON-serializable dump data for a cluster.

    Returns (relative_filename, content) pairs covering the live cluster
    record (if the cluster still exists), its cluster history records, and
    cluster events. Cluster history and events outlive the cluster row, so
    terminated clusters (e.g. a finished managed job's cluster) still
    produce data. Shared by the API server dump (_dump_cluster_info in
    debug_utils.py) and the controller manifest
    (_collect_cluster_debug_manifest in jobs/utils.py).
    """
    data: List[Tuple[str, Any]] = []
    cluster_record = global_user_state.get_cluster_from_name(cluster_name)
    # A single name can map to multiple history records when the name is
    # reused across launches.
    history_records = global_user_state.get_clusters_from_history(
        cluster_names=[cluster_name])
    if cluster_record is not None:
        data.append(
            ('cluster_info.json', serialize_cluster_record(cluster_record)))
    if history_records:
        data.append(('cluster_history.json', [
            serialize_cluster_history_record(record)
            for record in history_records
        ]))

    # Events are keyed by cluster hash. Collect from the live cluster's
    # hash AND every history hash (deduplicated — the live cluster has a
    # history record with the same hash): when a name has been reused, the
    # current cluster and its terminated predecessors all have events
    # worth dumping.
    event_hashes = []
    if cluster_record is not None and cluster_record.get('cluster_hash'):
        event_hashes.append(cluster_record['cluster_hash'])
    for record in history_records:
        record_hash = record.get('cluster_hash')
        if record_hash and record_hash not in event_hashes:
            event_hashes.append(record_hash)
    # Suffix event files with the hash only when the name maps to several
    # clusters, so the common single-cluster case keeps stable filenames.
    use_hash_suffix = len(event_hashes) > 1
    for cluster_hash in event_hashes:
        suffix = f'.{cluster_hash[:8]}' if use_hash_suffix else ''
        for event_data in get_cluster_events_data(cluster_hash):
            data.append((f'events_{event_data["event_type"]}{suffix}.json',
                         event_data['events']))
    return data


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

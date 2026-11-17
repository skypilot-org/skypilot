"""Server-side idle detection for clusters, from an external metrics source.

Autostop on the cluster only sees SkyPilot jobs and SSH sessions. This asks a
source the admin configures (``idle_detection`` in the server config) whether
each UP cluster has been idle for a whole window, and tells its owner.
"""
import json
import os
import re
import time
from typing import Any, Callable, Dict, Optional

import requests

from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import leader_election
from sky.utils import resources_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

OPT_OUT_LABEL = 'skypilot-idle-detection'
OPT_OUT_VALUE = 'skip'

_LOCK_ID = 'idle_detection'
_STATE_KEY = 'idle_detection'
_LAST_PASS_KEY = 'idle_detection_last_pass'
_SOURCE_TIMEOUT_SECONDS = 30
_WEBHOOK_TIMEOUT_SECONDS = 10


def _read_headers(source_config: Dict[str, Any]) -> Dict[str, str]:
    path = source_config.get('headers_file')
    if path is None:
        return {}
    headers = {}
    with open(os.path.expanduser(path), encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                name, value = line.split(':', 1)
                headers[name.strip()] = value.strip()
    return headers


def _cluster_fields(record: Dict[str, Any],
                    window_seconds: int) -> Dict[str, Any]:
    handle = record['handle']
    cluster_info = handle.cached_cluster_info
    node_names = record['node_names']
    return {
        'cluster': record['name'],
        'cluster_on_cloud': handle.cluster_name_on_cloud,
        'user': record['user_name'],
        'workspace': record['workspace'],
        'instance_ids':
            (list(cluster_info.instances) if cluster_info is not None else []),
        'node_names': node_names.split(',') if node_names else [],
        'node_ips': handle.cached_internal_ips or [],
        'window_seconds': window_seconds,
    }


def _render_query(template: str, fields: Dict[str, Any]) -> str:
    values = {k: v for k, v in fields.items() if k != 'window_seconds'}
    values['window'] = f'{fields["window_seconds"]}s'

    def substitute(match: 're.Match[str]') -> str:
        name = match.group(1)
        if name not in values:
            return match.group(0)
        value = values[name]
        if isinstance(value, list):
            value = '|'.join(re.escape(item) for item in value)
        if not value:
            # An empty matcher could select the series of other clusters.
            raise ValueError(f'{match.group(0)} is empty for this cluster.')
        # PromQL string literals take the same backslash escapes as JSON.
        return json.dumps(value)[1:-1]

    return re.sub(r'\{(\w+)\}', substitute, template)


def _ask_prometheus(source_config: Dict[str, Any], headers: Dict[str, str],
                    fields: Dict[str, Any]) -> Optional[str]:
    response = requests.get(
        source_config['url'],
        params={'query': _render_query(source_config['query'], fields)},
        headers=headers,
        timeout=_SOURCE_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()['data']
    if data['resultType'] != 'vector':
        raise ValueError('The query must return an instant vector, got '
                         f'{data["resultType"]}.')
    values = [float(sample['value'][1]) for sample in data['result']]
    threshold = source_config['threshold']
    if values and all(value < threshold for value in values):
        return f'{len(values)} series, max {max(values):g} < {threshold:g}'
    return None


def _ask_http(source_config: Dict[str, Any], headers: Dict[str, str],
              fields: Dict[str, Any]) -> Optional[str]:
    response = requests.post(source_config['url'],
                             json=fields,
                             headers=headers,
                             timeout=_SOURCE_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    if body['idle'] is True:
        return body.get('reason') or 'reported idle by the source'
    return None


_SOURCES: Dict[str, Callable[..., Optional[str]]] = {
    'prometheus': _ask_prometheus,
    'http': _ask_http,
}


def _notify(config: Dict[str, Any], record: Dict[str, Any], reason: str,
            idle_since: int) -> None:
    response = requests.post(config['notify']['webhook'],
                             json={
                                 'event': 'idle_warning',
                                 'cluster': record['name'],
                                 'user': record['user_name'],
                                 'workspace': record['workspace'],
                                 'reason': reason,
                                 'action': 'notify',
                                 'idle_since': idle_since,
                                 'deadline': None,
                             },
                             timeout=_WEBHOOK_TIMEOUT_SECONDS)
    response.raise_for_status()
    global_user_state.add_cluster_event(
        record['name'], status_lib.ClusterStatus.UP,
        f'Idle for the last {config["window"]} ({reason}). Owner notified.',
        global_user_state.ClusterEventType.STATUS_CHANGE)


def _save_state(record: Dict[str, Any], state: Optional[Dict[str,
                                                             Any]]) -> None:
    metadata = {k: v for k, v in record['metadata'].items() if k != _STATE_KEY}
    if state is not None:
        metadata[_STATE_KEY] = state
    global_user_state.set_cluster_info(record['name'], metadata)


def _is_exempt(record: Dict[str, Any]) -> bool:
    labels = record['handle'].launched_resources.labels or {}
    return (
        controller_utils.Controllers.from_name(record['name']) is not None or
        labels.get(OPT_OUT_LABEL) == OPT_OUT_VALUE or
        # get_clusters() fills in the daemon's own user for a cluster with
        # no recorded owner; there is nobody to notify.
        record['user_name'] in (None, constants.SKYPILOT_SYSTEM_USER_ID))


def _check_cluster(record: Dict[str, Any], config: Dict[str, Any],
                   headers: Dict[str, str], window: int, watching_since: int,
                   now: int, leading: Callable[[], bool],
                   counts: Dict[str, int]) -> None:
    previous = record['metadata'].get(_STATE_KEY)
    if record['status'] != status_lib.ClusterStatus.UP:
        if previous is not None:
            _save_state(record, None)
        return
    if _is_exempt(record):
        return

    # The daemon judges a cluster only after watching it stay UP for a whole
    # window: the source averages whatever samples exist, so a young cluster
    # would read idle. Provisioning rewrites launched_at; watching_since
    # changes when the daemon resumes after a pause.
    if (previous is None or previous['watching_since'] != watching_since or
            previous['launched_at'] != record['launched_at']):
        state = {
            'launched_at': record['launched_at'],
            'watching_since': watching_since,
            'up_since': now,
        }
    else:
        state = dict(previous)

    if now - state['up_since'] >= window:
        counts['checked'] += 1
        (source_name, source_config), = config['source'].items()
        reason = None
        try:
            reason = _SOURCES[source_name](source_config, headers,
                                           _cluster_fields(record, window))
        except Exception as e:  # pylint: disable=broad-except
            counts['errors'] += 1
            logger.warning(
                f'Could not tell whether cluster {record["name"]!r} is idle; '
                f'treating it as active: {common_utils.format_exception(e)}')
        # The source may have taken longer to answer than leadership lasts.
        if not leading():
            return
        if reason is None:
            state.pop('idle_since', None)
            state.pop('notified_at', None)
        else:
            counts['idle'] += 1
            state.setdefault('idle_since', now)
            if now - state.get('notified_at', now - window) >= window:
                # The answer describes the cluster as it was when this pass
                # read it; it may have been stopped or relaunched since.
                current = global_user_state.get_cluster_from_name(
                    record['name'],
                    include_user_info=False,
                    summary_response=True)
                if (current is None or
                        current['status'] != status_lib.ClusterStatus.UP or
                        current['launched_at'] != record['launched_at']):
                    return
                try:
                    _notify(config, record, reason, state['idle_since'])
                    state['notified_at'] = now
                    counts['notified'] += 1
                except Exception as e:  # pylint: disable=broad-except
                    counts['errors'] += 1
                    logger.warning(
                        f'Could not notify the owner of idle cluster '
                        f'{record["name"]!r}; will retry on the next pass: '
                        f'{common_utils.format_exception(e)}')
    if state != previous:
        _save_state(record, state)


def run_pass(interval: int) -> None:
    """Checks every UP cluster once.

    Args:
        interval: Seconds the daemon sleeps between passes, to tell a pause
            in the daemon from its normal rhythm.
    """
    config = skypilot_config.get_nested(('idle_detection',), None)
    if config is None:
        return
    # One API server acts per pass: the per-cluster state is read, changed and
    # written back as a whole.
    elector = leader_election.get_elector(_LOCK_ID)
    if not elector.try_acquire():
        return
    try:
        window = resources_utils.parse_time_seconds(config['window'])
        (source_config,) = config['source'].values()
        headers = _read_headers(source_config)
        now = int(time.time())
        # After a pause in the daemon nobody watched the clusters stay UP, so
        # every observation window starts over. Measured from the end of the
        # last pass: a slow pass is not a pause.
        last_pass = json.loads(
            global_user_state.get_system_config(_LAST_PASS_KEY) or 'null')
        if (last_pass is not None and
                now - last_pass['finished'] <= 2 * interval):
            watching_since = last_pass['watching_since']
        else:
            watching_since = now
        renewed_at = time.monotonic()

        def leading() -> bool:
            nonlocal renewed_at
            if time.monotonic() - renewed_at >= elector.renew_interval_seconds:
                if not elector.renew():
                    return False
                renewed_at = time.monotonic()
            return True

        counts = {'checked': 0, 'idle': 0, 'notified': 0, 'errors': 0}
        for record in global_user_state.get_clusters(
                exclude_managed_clusters=True):
            if not leading():
                break
            try:
                _check_cluster(record, config, headers, window, watching_since,
                               now, leading, counts)
            except Exception as e:  # pylint: disable=broad-except
                counts['errors'] += 1
                logger.warning(
                    f'Idle detection failed for cluster {record["name"]!r}: '
                    f'{common_utils.format_exception(e)}')
        if not leading():
            logger.warning('Another API server took over idle detection; '
                           'ending this pass.')
            return
        global_user_state.set_system_config(
            _LAST_PASS_KEY,
            json.dumps({
                'finished': int(time.time()),
                'watching_since': watching_since,
            }))
        # A source that fails for every cluster makes them all look active,
        # which is indistinguishable from a busy fleet without this line.
        summary = ' '.join(f'{name}={count}' for name, count in counts.items())
        log = logger.warning if counts['errors'] else logger.info
        log(f'Idle detection pass: {summary}')
    finally:
        elector.release()

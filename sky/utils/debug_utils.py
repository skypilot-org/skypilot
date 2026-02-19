"""Debug dump utilities for troubleshooting SkyPilot issues."""
import datetime
import json
import os
import pathlib
import platform
import shutil
import time
from typing import Any, Dict, List, Optional, Set, TypedDict
import zipfile

from sky import global_user_state
from sky import sky_logging
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.skylet import constants as skylet_constants
from sky.utils import common
from sky.utils import tempstore

logger = sky_logging.init_logger(__name__)

# Persistent location for debug dumps
DEBUG_DUMP_DIR = '~/.sky/debug_dumps'

# System daemon request IDs to always include in debug dumps.
# Built from INTERNAL_REQUEST_DAEMONS (background refresh daemons) plus the
# on-boot check request.
SYSTEM_REQUEST_IDS = [d.id for d in daemons.INTERNAL_REQUEST_DAEMONS
                     ] + [server_constants.ON_BOOT_CHECK_REQUEST_ID]


class DebugDumpContext(TypedDict):
    """The context for a debug dump."""
    request_ids: Set[str]
    cluster_names: Set[str]
    managed_job_ids: Set[int]


def _epoch_to_human(epoch: Optional[float]) -> Optional[str]:
    """Convert epoch timestamp to human-readable ISO format."""
    if epoch is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(epoch).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _get_requests_from_clusters(debug_dump_context: DebugDumpContext) -> None:
    """Get all request IDs associated with the given clusters."""
    if not debug_dump_context['cluster_names']:
        return
    logger.debug(
        f'Getting requests for {len(debug_dump_context["cluster_names"])} '
        f'clusters')
    for cluster_name in debug_dump_context['cluster_names']:
        try:
            requests = requests_lib.get_request_tasks(
                requests_lib.RequestTaskFilter(cluster_names=[cluster_name],
                                               fields=['request_id']))
            debug_dump_context['request_ids'] |= {
                request.request_id for request in requests
            }
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get requests for cluster '
                           f'{cluster_name}: {e}')


def _get_requests_from_managed_jobs(
        debug_dump_context: DebugDumpContext) -> None:
    """Parse request database to find requests related to managed jobs."""
    # pylint: disable=import-outside-toplevel
    from sky.jobs.server import core as managed_jobs_core

    if not debug_dump_context['managed_job_ids']:
        return
    logger.debug(
        f'Getting requests for {len(debug_dump_context["managed_job_ids"])} '
        f'managed jobs')

    # Fetch job details to enable matching by name and user
    job_names: Set[str] = set()
    job_user_hashes: Set[str] = set()
    try:
        jobs, _, _, _ = managed_jobs_core.queue_v2(
            refresh=False, job_ids=list(debug_dump_context['managed_job_ids']))
        for job in jobs:
            name = job.get('job_name')
            if name:
                job_names.add(name)
            user_hash = job.get('user_hash')
            if user_hash:
                job_user_hashes.add(user_hash)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to fetch managed job details: {e}')

    # Request names related to managed jobs (excludes read-only queue)
    managed_job_request_names = [
        request_names.RequestName.JOBS_LAUNCH.value,
        request_names.RequestName.JOBS_CANCEL.value,
        request_names.RequestName.JOBS_LOGS.value,
    ]

    try:
        # Get all requests with managed job-related names
        requests = requests_lib.get_request_tasks(
            requests_lib.RequestTaskFilter(
                include_request_names=managed_job_request_names,
                fields=['request_id', 'request_body']))

        for request in requests:
            try:
                body = request.request_body
                if body is None:
                    continue
                matched = False
                # Match by direct job ID
                job_id = getattr(body, 'job_id', None)
                job_ids = getattr(body, 'job_ids', None)
                if (job_id is not None and
                        job_id in debug_dump_context['managed_job_ids']):
                    matched = True
                elif (job_ids is not None and
                      any(jid in debug_dump_context['managed_job_ids']
                          for jid in job_ids)):
                    matched = True
                # Match cancel-by-name
                elif getattr(body, 'name', None) in job_names:
                    matched = True
                # Match cancel-all-users (affects all jobs)
                elif getattr(body, 'all_users', False):
                    matched = True
                # Match cancel-all (affects only the requesting
                # user's jobs, so include if user owns a target job)
                elif getattr(body, 'all', False):
                    cancel_user = getattr(body, 'env_vars', {}).get(
                        skylet_constants.USER_ID_ENV_VAR)
                    if cancel_user and cancel_user in job_user_hashes:
                        matched = True
                if matched:
                    debug_dump_context['request_ids'].add(request.request_id)
            except Exception:  # pylint: disable=broad-except
                logger.warning(f'Failed to parse request '
                               f'{request.request_id}')
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get requests for managed jobs: {e}')


def _get_clusters_from_requests(debug_dump_context: DebugDumpContext) -> None:
    """Get cluster names from the given request IDs."""
    if not debug_dump_context['request_ids']:
        return
    logger.debug(
        f'Getting clusters for {len(debug_dump_context["request_ids"])} '
        f'requests')
    for request_id in debug_dump_context['request_ids']:
        try:
            request = requests_lib.get_request(request_id,
                                               fields=['cluster_name'])
            if request is not None and request.cluster_name is not None:
                debug_dump_context['cluster_names'].add(request.cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get cluster for request '
                           f'{request_id}: {e}')


def _get_clusters_from_managed_jobs(
        debug_dump_context: DebugDumpContext) -> None:
    """Get underlying cluster names from managed jobs.

    In non-consolidation mode, the actual job clusters live on the remote
    controller and aren't available on the API server. We just include the
    jobs controller itself; _dump_managed_job_info handles per-job details.
    """
    if not debug_dump_context['managed_job_ids']:
        return
    debug_dump_context['cluster_names'].add(common.JOB_CONTROLLER_NAME)


def _populate_recent_context(debug_dump_context: DebugDumpContext,
                             hours: float) -> None:
    """Populate context with resources active within the given time window."""
    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.jobs.server import core as managed_jobs_core

    logger.debug(f'Populating context with resources from last {hours} hours')
    cutoff_time = time.time() - (hours * 3600)

    # Get recent requests (cluster names are handled by
    # _get_clusters_from_requests during cross-linking)
    try:
        requests = requests_lib.get_request_tasks(
            requests_lib.RequestTaskFilter(
                fields=['request_id', 'finished_at']))
        for request in requests:
            finished_at = request.finished_at or time.time()
            if finished_at >= cutoff_time:
                debug_dump_context['request_ids'].add(request.request_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent requests: {e}')

    # Get recent clusters
    try:
        clusters = global_user_state.get_clusters()
        for cluster in clusters:
            status_updated_at = cluster.get('status_updated_at') or 0
            launched_at = cluster.get('launched_at') or 0
            if status_updated_at >= cutoff_time or launched_at >= cutoff_time:
                cluster_name = cluster.get('name')
                if cluster_name:
                    debug_dump_context['cluster_names'].add(cluster_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent clusters: {e}')

    # Get recent managed jobs via queue_v2 (handles remote controllers
    # via gRPC/SSH, unlike direct DB access which only works in
    # consolidation mode).
    try:
        jobs, _, _, _ = managed_jobs_core.queue_v2(refresh=False)
        for job in jobs:
            submitted_at = job.get('submitted_at') or 0
            end_at = job.get('end_at') or time.time()
            if submitted_at >= cutoff_time or end_at >= cutoff_time:
                job_id = job.get('job_id')
                if job_id is not None:
                    debug_dump_context['managed_job_ids'].add(job_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent managed jobs: {e}')

    logger.debug(f'Found {len(debug_dump_context["request_ids"])} requests, '
                 f'{len(debug_dump_context["cluster_names"])} clusters, '
                 f'{len(debug_dump_context["managed_job_ids"])} managed jobs '
                 f'from recent activity')


def _dump_server_info(dump_dir: str,
                      errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect server metadata."""
    logger.debug('Entering _dump_server_info')
    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    import sky
    from sky import check as sky_check
    from sky import skypilot_config

    server_info: Dict[str, Any] = {
        'skypilot_version': sky.__version__,
        'skypilot_commit': getattr(sky, '__commit__', 'unknown'),
        'api_version': server_constants.API_VERSION,
        'dump_timestamp': time.time(),
        'dump_timestamp_human': datetime.datetime.now().isoformat(),
        'python_version': platform.python_version(),
        'os_platform': platform.platform(),
        'db_backend':
            ('postgresql'
             if os.environ.get('SKYPILOT_DB_CONNECTION_URI') else 'sqlite'),
    }

    # Add server uptime using the boot check request's created_at timestamp.
    # This is shared across all uvicorn workers (stored in the DB), unlike
    # a module-level variable which would be per-worker.
    try:
        boot_request = requests_lib.get_request(
            server_constants.ON_BOOT_CHECK_REQUEST_ID, fields=['created_at'])
        if boot_request is not None and boot_request.created_at is not None:
            server_info['server_start_time'] = boot_request.created_at
            server_info['server_start_time_human'] = _epoch_to_human(
                boot_request.created_at)
            server_info['server_uptime_seconds'] = round(
                time.time() - boot_request.created_at, 2)
    except Exception as e:  # pylint: disable=broad-except
        server_info['server_uptime_error'] = str(e)

    # Add config info
    try:
        server_info['config'] = {
            'jobs_controller_consolidation_mode': skypilot_config.get_nested(
                ('jobs', 'controller', 'consolidation_mode'), False),
        }
    except Exception as e:  # pylint: disable=broad-except
        server_info['config_error'] = str(e)
        if errors is not None:
            errors.append({
                'component': 'server_info',
                'resource': 'config',
                'error': str(e)
            })

    # Add environment variables
    server_info['environment'] = {
        'SKYPILOT_DEBUG': os.environ.get('SKYPILOT_DEBUG', ''),
        'SKYPILOT_DEV': os.environ.get('SKYPILOT_DEV', ''),
    }

    # Add cloud status
    try:
        cloud_status = sky_check.check(quiet=True)
        # Convert to serializable format
        enabled_clouds = []
        for cloud, enabled in cloud_status.items():
            enabled_clouds.append({
                'cloud': str(cloud),
                'enabled': enabled,
            })
        server_info['enabled_clouds'] = enabled_clouds
    except Exception as e:  # pylint: disable=broad-except
        server_info['cloud_status_error'] = str(e)
        if errors is not None:
            errors.append({
                'component': 'server_info',
                'resource': 'cloud_status',
                'error': str(e)
            })

    server_info_path = os.path.join(dump_dir, 'server_info.json')
    with open(server_info_path, 'w', encoding='utf-8') as f:
        json.dump(server_info, f, indent=2, default=str)
    logger.debug('Exiting _dump_server_info')


def _dump_request_id_info(
        request_ids: Set[str],
        dump_dir: str,
        errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect request logs and metadata."""
    if not request_ids:
        logger.debug('No requests to dump')
        return
    logger.debug(f'Entering _dump_request_id_info for '
                 f'{len(request_ids)} requests')

    requests_dir = os.path.join(dump_dir, 'requests')
    os.makedirs(requests_dir, exist_ok=True)

    for request_id in request_ids:
        request_dir = os.path.join(requests_dir, request_id)
        os.makedirs(request_dir, exist_ok=True)

        # Get request metadata from DB
        try:
            request = requests_lib.get_request(request_id)
            if request is not None:
                request_info: Dict[str, Any] = {
                    'request_id': request.request_id,
                    'name': request.name,
                    'status': request.status.value if request.status else None,
                    'created_at': request.created_at,
                    'created_at_human': _epoch_to_human(request.created_at),
                    'finished_at': request.finished_at,
                    'finished_at_human': _epoch_to_human(request.finished_at),
                    'cluster_name': request.cluster_name,
                    'user_id': request.user_id,
                    'status_msg': request.status_msg,
                    'schedule_type': (request.schedule_type.value
                                      if request.schedule_type else None),
                    'request_body': str(request.request_body),
                }

                # Include error info if present
                try:
                    error = request.get_error()
                    if error:
                        request_info['error'] = {
                            'type': error.get('type'),
                            'message': error.get('message'),
                        }
                except Exception:  # pylint: disable=broad-except
                    pass

                request_info_path = os.path.join(request_dir,
                                                 'request_info.json')
                with open(request_info_path, 'w', encoding='utf-8') as f:
                    json.dump(request_info, f, indent=2, default=str)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get info for request {request_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'requests',
                    'resource': request_id,
                    'error': str(e)
                })

        # Copy request log file
        try:
            log_path = (pathlib.Path(
                requests_lib.REQUEST_LOG_PATH_PREFIX).expanduser() /
                        f'{request_id}.log')
            if log_path.exists():
                shutil.copy2(log_path, os.path.join(request_dir, 'request.log'))
                logger.debug(f'Copied request log for {request_id}')
            else:
                logger.debug(f'Request log not found for {request_id}: '
                             f'{log_path}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to copy log for request {request_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'requests',
                    'resource': f'{request_id}/log',
                    'error': str(e)
                })

    logger.debug('Exiting _dump_request_id_info')


def _dump_cluster_info(cluster_names: Set[str],
                       dump_dir: str,
                       errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect cluster state and events."""
    if not cluster_names:
        logger.debug('No clusters to dump')
        return
    logger.debug(f'Entering _dump_cluster_info for '
                 f'{len(cluster_names)} clusters')

    clusters_dir = os.path.join(dump_dir, 'clusters')
    os.makedirs(clusters_dir, exist_ok=True)

    for cluster_name in cluster_names:
        cluster_dir = os.path.join(clusters_dir, cluster_name)
        os.makedirs(cluster_dir, exist_ok=True)

        # Get cluster info from DB
        cluster_record = None
        try:
            cluster_record = global_user_state.get_cluster_from_name(
                cluster_name)
            if cluster_record is not None:
                # Serialize handle carefully - exclude non-serializable parts
                handle = cluster_record.get('handle')
                handle_info: Dict[str, Any] = {}
                if handle:
                    handle_info = {
                        'cluster_name': getattr(handle, 'cluster_name', None),
                        'cluster_name_on_cloud': getattr(
                            handle, 'cluster_name_on_cloud', None),
                        'head_ip': getattr(handle, 'head_ip', None),
                        'launched_nodes': getattr(handle, 'launched_nodes',
                                                  None),
                        'launched_resources': str(
                            getattr(handle, 'launched_resources', None)),
                    }

                launched_at = cluster_record.get('launched_at')
                status_updated_at = cluster_record.get('status_updated_at')
                cluster_info: Dict[str, Any] = {
                    'name': cluster_record.get('name'),
                    'cluster_hash': cluster_record.get('cluster_hash'),
                    'status': str(cluster_record.get('status')),
                    'launched_at': launched_at,
                    'launched_at_human': _epoch_to_human(launched_at),
                    'autostop': cluster_record.get('autostop'),
                    'to_down': cluster_record.get('to_down'),
                    'cluster_ever_up': cluster_record.get('cluster_ever_up'),
                    'status_updated_at': status_updated_at,
                    'status_updated_at_human':
                        _epoch_to_human(status_updated_at),
                    'config_hash': cluster_record.get('config_hash'),
                    'workspace': cluster_record.get('workspace'),
                    'is_managed': cluster_record.get('is_managed'),
                    'user_hash': cluster_record.get('user_hash'),
                    'user_name': cluster_record.get('user_name'),
                    'handle': handle_info,
                }

                cluster_info_path = os.path.join(cluster_dir,
                                                 'cluster_info.json')
                with open(cluster_info_path, 'w', encoding='utf-8') as f:
                    json.dump(cluster_info, f, indent=2, default=str)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get info for cluster '
                           f'{cluster_name}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'clusters',
                    'resource': cluster_name,
                    'error': str(e)
                })

        # Get cluster events
        try:
            cluster_hash = (cluster_record.get('cluster_hash')
                            if cluster_record else None)
            if cluster_hash:
                for event_type in [
                        global_user_state.ClusterEventType.DEBUG,
                        global_user_state.ClusterEventType.STATUS_CHANGE,
                        global_user_state.ClusterEventType.TERMINAL
                ]:
                    try:
                        events = global_user_state.get_cluster_events(
                            cluster_name=None,
                            cluster_hash=cluster_hash,
                            event_type=event_type,
                            include_timestamps=True)
                        if events:
                            event_type_lower = event_type.value.lower()
                            event_file = f'events_{event_type_lower}.json'
                            event_path = os.path.join(cluster_dir, event_file)
                            with open(event_path, 'w', encoding='utf-8') as f:
                                json.dump(events, f, indent=2, default=str)
                    except Exception as e:  # pylint: disable=broad-except
                        logger.warning(f'Failed to get {event_type.value} '
                                       f'events for cluster {cluster_name}: '
                                       f'{e}')
                        if errors is not None:
                            errors.append({
                                'component': 'clusters',
                                'resource': f'{cluster_name}/events_{event_type.value}',
                                'error': str(e)
                            })
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get events for cluster '
                           f'{cluster_name}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'clusters',
                    'resource': f'{cluster_name}/events',
                    'error': str(e)
                })

        # Get associated requests
        try:
            requests = requests_lib.get_request_tasks(
                requests_lib.RequestTaskFilter(
                    cluster_names=[cluster_name],
                    fields=['request_id', 'name', 'status', 'created_at']))
            associated_requests = [{
                'request_id': r.request_id,
                'name': r.name,
                'status': r.status.value if r.status else None,
                'created_at': r.created_at,
                'created_at_human': _epoch_to_human(r.created_at),
            } for r in requests]

            assoc_path = os.path.join(cluster_dir, 'associated_requests.json')
            with open(assoc_path, 'w', encoding='utf-8') as f:
                json.dump(associated_requests, f, indent=2, default=str)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get associated requests for cluster '
                           f'{cluster_name}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'clusters',
                    'resource': f'{cluster_name}/associated_requests',
                    'error': str(e)
                })

    logger.debug('Exiting _dump_cluster_info')


def _dump_managed_job_info(
        managed_job_ids: Set[int],
        dump_dir: str,
        errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect managed job state and logs."""
    # Import here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    from sky.jobs import constants as job_constants
    from sky.jobs import state as managed_job_state
    from sky.jobs.server import core as managed_jobs_core

    if not managed_job_ids:
        logger.debug('No managed jobs to dump')
        return
    logger.debug(f'Entering _dump_managed_job_info for '
                 f'{len(managed_job_ids)} managed jobs')

    jobs_dir = os.path.join(dump_dir, 'managed_jobs')
    os.makedirs(jobs_dir, exist_ok=True)

    for job_id in managed_job_ids:
        job_dir = os.path.join(jobs_dir, str(job_id))
        os.makedirs(job_dir, exist_ok=True)

        # Get job info via queue_v2 (handles remote controllers via
        # gRPC/SSH, unlike direct DB access which only works in
        # consolidation mode).
        try:
            jobs, _, _, _ = managed_jobs_core.queue_v2(refresh=False,
                                                       job_ids=[job_id])
            if jobs:
                # Iterate over ALL returned tasks, not just the first one
                for task_idx, job in enumerate(jobs):
                    # Convert non-serializable fields
                    job_info = {
                        k: (str(v) if not isinstance(v,
                                                     (str, int, float, bool,
                                                      type(None), list, dict))
                            else v) for k, v in job.items()
                    }

                    suffix = f'_task{task_idx}' if len(jobs) > 1 else ''
                    job_info_path = os.path.join(job_dir,
                                                 f'job_info{suffix}.json')
                    with open(job_info_path, 'w', encoding='utf-8') as f:
                        json.dump(job_info, f, indent=2, default=str)

                    # Copy job log file if available
                    local_log_file = job.get('local_log_file')
                    if local_log_file and os.path.exists(local_log_file):
                        log_name = f'run{suffix}.log'
                        shutil.copy2(local_log_file,
                                     os.path.join(job_dir, log_name))
                        logger.debug(f'Copied log file for job {job_id} task '
                                     f'{task_idx}')
                    else:
                        logger.debug(
                            f'Log file not found for job {job_id} task '
                            f'{task_idx}: {local_log_file}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get info for job {job_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': str(job_id),
                    'error': str(e)
                })

        # Get job events (best-effort from local DB).
        # Note: In non-consolidation mode, the spot_jobs DB lives on the
        # remote controller VM, so events may be empty here. This is
        # expected behavior - events are only available when running in
        # consolidation mode where the DB is local.
        try:
            events = managed_job_state.get_job_events(job_id, limit=1000)
            if events:
                # Convert to serializable format
                serializable_events = [{
                    'spot_job_id': e.get('spot_job_id'),
                    'task_id': e.get('task_id'),
                    'new_status': str(e.get('new_status')),
                    'code': e.get('code'),
                    'reason': e.get('reason'),
                    'timestamp': str(e.get('timestamp')),
                    'timestamp_human': _epoch_to_human(e.get('timestamp')),
                } for e in events]

                events_path = os.path.join(job_dir, 'job_events.json')
                with open(events_path, 'w', encoding='utf-8') as f:
                    json.dump(serializable_events, f, indent=2, default=str)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get events for job {job_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': f'{job_id}/events',
                    'error': str(e)
                })

        # Copy controller logs
        try:
            controller_logs_dir = pathlib.Path(
                job_constants.JOBS_CONTROLLER_LOGS_DIR).expanduser()
            if controller_logs_dir.exists():
                copied_any = False
                # Per-job controller log: {job_id}.log
                for log_file in controller_logs_dir.glob(f'{job_id}.log'):
                    if log_file.is_file():
                        shutil.copy2(log_file,
                                     os.path.join(job_dir, log_file.name))
                        logger.debug(f'Copied controller log: {log_file.name}')
                        copied_any = True
                # Controller system/process logs: controller_*.log
                for log_file in controller_logs_dir.glob('controller_*.log'):
                    if log_file.is_file():
                        shutil.copy2(log_file,
                                     os.path.join(job_dir, log_file.name))
                        logger.debug(f'Copied controller log: {log_file.name}')
                        copied_any = True
                if not copied_any:
                    logger.debug(f'No controller logs found for job {job_id}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to copy controller logs for job '
                           f'{job_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': f'{job_id}/controller_logs',
                    'error': str(e)
                })

    logger.debug('Exiting _dump_managed_job_info')


def create_debug_dump(
    request_ids: Optional[List[str]] = None,
    cluster_names: Optional[List[str]] = None,
    managed_job_ids: Optional[List[int]] = None,
    recent_hours: Optional[float] = None,
    client_info: Optional[Dict[str, Any]] = None,
) -> pathlib.Path:
    """Create a debug dump for troubleshooting.

    Args:
        request_ids: List of request IDs to include in the dump.
        cluster_names: List of cluster names to include in the dump.
        managed_job_ids: List of managed job IDs to include in the dump.
        recent_hours: If specified, include all resources active within
            this many hours.
        client_info: Optional client-side info to include in the dump.

    Returns:
        Path to the created zip file.
    """
    start_time = time.time()
    logger.debug('Starting debug dump creation')
    logger.debug(f'Initial inputs: request_ids={request_ids}, '
                 f'cluster_names={cluster_names}, '
                 f'managed_job_ids={managed_job_ids}, '
                 f'recent_hours={recent_hours}')

    # Error collector for surfacing issues in the dump
    errors: List[Dict[str, str]] = []

    debug_dump_context = DebugDumpContext(
        request_ids=set(request_ids or []),
        cluster_names=set(cluster_names or []),
        managed_job_ids=set(managed_job_ids or []),
    )

    # Populate from recent activity if requested
    if recent_hours is not None:
        _populate_recent_context(debug_dump_context, recent_hours)

    # Collect all related resources (cross-linking)
    logger.debug('Cross-linking related resources')
    _get_requests_from_clusters(debug_dump_context)
    _get_requests_from_managed_jobs(debug_dump_context)
    _get_clusters_from_requests(debug_dump_context)
    _get_clusters_from_managed_jobs(debug_dump_context)

    # Always include system daemon requests
    debug_dump_context['request_ids'].update(SYSTEM_REQUEST_IDS)

    logger.debug(f'After cross-linking: '
                 f'{len(debug_dump_context["request_ids"])} requests, '
                 f'{len(debug_dump_context["cluster_names"])} clusters, '
                 f'{len(debug_dump_context["managed_job_ids"])} managed jobs')

    # Create persistent output directory
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dump_base_dir = pathlib.Path(DEBUG_DUMP_DIR).expanduser()
    dump_base_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f'Debug dump output directory: {dump_base_dir}')

    # Clean up dumps older than 1 hour
    for old_dump in dump_base_dir.glob('debug_dump_*.zip'):
        try:
            if old_dump.stat().st_mtime < time.time() - 3600:
                old_dump.unlink(missing_ok=True)
                logger.debug(f'Cleaned up old debug dump: {old_dump.name}')
        except OSError:
            pass

    # Use temp dir for building the dump, then zip to persistent location
    with tempstore.tempdir() as temp_dir:
        dump_dir = os.path.join(temp_dir, f'debug_dump_{timestamp}')
        os.makedirs(dump_dir)
        logger.debug(f'Building dump in temp directory: {dump_dir}')

        _dump_server_info(dump_dir, errors=errors)
        _dump_request_id_info(debug_dump_context['request_ids'],
                              dump_dir,
                              errors=errors)
        _dump_cluster_info(debug_dump_context['cluster_names'],
                           dump_dir,
                           errors=errors)
        _dump_managed_job_info(debug_dump_context['managed_job_ids'],
                               dump_dir,
                               errors=errors)

        # Write client info if provided
        if client_info:
            logger.debug('Writing client info')
            client_info_path = os.path.join(dump_dir, 'client_info.json')
            with open(client_info_path, 'w', encoding='utf-8') as f:
                json.dump(client_info, f, indent=2, default=str)

        # Write errors file
        errors_path = os.path.join(dump_dir, 'errors.json')
        with open(errors_path, 'w', encoding='utf-8') as f:
            json.dump(errors, f, indent=2, default=str)

        # Write summary file
        elapsed_time = time.time() - start_time
        summary: Dict[str, Any] = {
            'requested': {
                'request_ids': list(request_ids) if request_ids else [],
                'cluster_names': list(cluster_names) if cluster_names else [],
                'managed_job_ids': list(managed_job_ids)
                                   if managed_job_ids else [],
                'recent_hours': recent_hours,
            },
            'collected': {
                'request_count': len(debug_dump_context['request_ids']),
                'cluster_count': len(debug_dump_context['cluster_names']),
                'managed_job_count': len(debug_dump_context['managed_job_ids']),
                'request_ids': sorted(debug_dump_context['request_ids']),
                'cluster_names': sorted(debug_dump_context['cluster_names']),
                'managed_job_ids': sorted(debug_dump_context['managed_job_ids']
                                         ),
            },
            'timing': {
                'elapsed_seconds': round(elapsed_time, 2),
                'timestamp': timestamp,
            },
            'warnings': errors,
        }
        summary_path = os.path.join(dump_dir, 'summary.json')
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)

        # Log total dump size before zipping
        total_dump_size = sum(f.stat().st_size
                              for f in pathlib.Path(dump_dir).rglob('*')
                              if f.is_file())
        logger.debug(f'Total dump size before zipping: {total_dump_size} bytes')

        # Create zip file in PERSISTENT location (outside temp dir)
        zip_filename = f'debug_dump_{timestamp}.zip'
        zip_file_path = dump_base_dir / zip_filename
        logger.debug(f'Creating zip file: {zip_file_path}')

        file_count = 0
        with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(dump_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zipf.write(file_path, arcname)
                    file_count += 1

        logger.debug(f'Debug dump created with {file_count} files: '
                     f'{zip_file_path}')

    return zip_file_path

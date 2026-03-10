"""Debug dump utilities for troubleshooting SkyPilot issues."""
import datetime
import json
import logging
import os
import pathlib
import platform
import shutil
import time
from typing import Any, Dict, List, Optional, Set, TypedDict
import zipfile

import sky
from sky import check as sky_check
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.jobs import utils as managed_job_utils
from sky.jobs.server import core as managed_jobs_core
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.skylet import constants as skylet_constants
from sky.utils import common
from sky.utils import controller_utils
from sky.utils import debug_dump_helpers
from sky.utils import message_utils
from sky.utils import subprocess_utils
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
    errors: List[Dict[str, str]]


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
            debug_dump_context['errors'].append({
                'component': 'cross_link',
                'resource': f'requests_from_cluster/{cluster_name}',
                'error': str(e)
            })


def _get_requests_from_managed_jobs(
        debug_dump_context: DebugDumpContext) -> None:
    """Parse request database to find requests related to managed jobs."""
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
        debug_dump_context['errors'].append({
            'component': 'cross_link',
            'resource': 'managed_job_details',
            'error': str(e)
        })

    # Request names related to managed jobs (excludes read-only queue).
    # DB stores names with 'sky.' prefix (see server_constants).
    prefix = server_constants.REQUEST_NAME_PREFIX
    managed_job_request_names = [
        prefix + request_names.RequestName.JOBS_LAUNCH.value,
        prefix + request_names.RequestName.JOBS_CANCEL.value,
        prefix + request_names.RequestName.JOBS_LOGS.value,
    ]

    try:
        # Get all requests with managed job-related names
        requests = requests_lib.get_request_tasks(
            requests_lib.RequestTaskFilter(
                include_request_names=managed_job_request_names,
                fields=['request_id', 'request_body']))

        for request in requests:
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
                cancel_user = getattr(body, 'env_vars',
                                      {}).get(skylet_constants.USER_ID_ENV_VAR)
                if cancel_user and cancel_user in job_user_hashes:
                    matched = True
            if matched:
                debug_dump_context['request_ids'].add(request.request_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get requests for managed jobs: {e}')
        debug_dump_context['errors'].append({
            'component': 'cross_link',
            'resource': 'requests_from_managed_jobs',
            'error': str(e)
        })


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
            debug_dump_context['errors'].append({
                'component': 'cross_link',
                'resource': f'clusters_from_request/{request_id}',
                'error': str(e)
            })


def _get_managed_jobs_from_requests(
        debug_dump_context: DebugDumpContext) -> None:
    """Extract managed job IDs from request bodies.

    If any request in the context is a managed job request (launch, cancel,
    logs), extract the job IDs from its body and add them to the context.
    """
    if not debug_dump_context['request_ids']:
        return
    logger.debug(f'Getting managed jobs for '
                 f'{len(debug_dump_context["request_ids"])} requests')

    # DB stores names with 'sky.' prefix (see server_constants).
    prefix = server_constants.REQUEST_NAME_PREFIX
    managed_job_request_names = {
        prefix + request_names.RequestName.JOBS_LAUNCH.value,
        prefix + request_names.RequestName.JOBS_CANCEL.value,
        prefix + request_names.RequestName.JOBS_LOGS.value,
    }

    for request_id in debug_dump_context['request_ids']:
        try:
            request = requests_lib.get_request(request_id,
                                               fields=['name', 'request_body'])
            if request is None or request.name not in managed_job_request_names:
                continue
            body = request.request_body
            if body is None:
                continue
            job_id = getattr(body, 'job_id', None)
            if job_id is not None:
                debug_dump_context['managed_job_ids'].add(job_id)
            job_ids = getattr(body, 'job_ids', None)
            if job_ids is not None:
                debug_dump_context['managed_job_ids'].update(job_ids)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get managed job info for '
                           f'request {request_id}: {e}')
            debug_dump_context['errors'].append({
                'component': 'cross_link',
                'resource': f'managed_jobs_from_request/{request_id}',
                'error': str(e)
            })


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
    logger.debug(f'Populating context with resources from last {hours} hours')
    cutoff_time = time.time() - (hours * 3600)

    # Get recent requests (cluster names are handled by
    # _get_clusters_from_requests during cross-linking)
    try:
        requests = requests_lib.get_request_tasks(
            requests_lib.RequestTaskFilter(finished_after=cutoff_time,
                                           fields=['request_id']))
        for request in requests:
            debug_dump_context['request_ids'].add(request.request_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent requests: {e}')
        debug_dump_context['errors'].append({
            'component': 'recent_context',
            'resource': 'requests',
            'error': str(e)
        })

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
        debug_dump_context['errors'].append({
            'component': 'recent_context',
            'resource': 'clusters',
            'error': str(e)
        })

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
        debug_dump_context['errors'].append({
            'component': 'recent_context',
            'resource': 'managed_jobs',
            'error': str(e)
        })

    logger.debug(f'Found {len(debug_dump_context["request_ids"])} requests, '
                 f'{len(debug_dump_context["cluster_names"])} clusters, '
                 f'{len(debug_dump_context["managed_job_ids"])} managed jobs '
                 f'from recent activity')


def _dump_server_info(dump_dir: str,
                      errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect server metadata."""
    logger.debug('Entering _dump_server_info')
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
            server_info[
                'server_start_time_human'] = debug_dump_helpers.epoch_to_human(
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
        'SKYPILOT_DB_CONNECTION_URI': bool(
            os.environ.get('SKYPILOT_DB_CONNECTION_URI')),
        skylet_constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING: os.environ.get(
            skylet_constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING, ''),
        server_constants.OAUTH2_PROXY_ENABLED_ENV_VAR: os.environ.get(
            server_constants.OAUTH2_PROXY_ENABLED_ENV_VAR, ''),
        server_constants.OAUTH2_PROXY_BASE_URL_ENV_VAR: os.environ.get(
            server_constants.OAUTH2_PROXY_BASE_URL_ENV_VAR, ''),
        'SKYPILOT_API_SERVER_STORAGE_ENABLED': os.environ.get(
            'SKYPILOT_API_SERVER_STORAGE_ENABLED', ''),
        'SKYPILOT_ROLLING_UPDATE_ENABLED': os.environ.get(
            'SKYPILOT_ROLLING_UPDATE_ENABLED', ''),
        'SKYPILOT_DISABLE_BASIC_AUTH_MIDDLEWARE': os.environ.get(
            'SKYPILOT_DISABLE_BASIC_AUTH_MIDDLEWARE', ''),
        'SKY_API_SERVER_METRICS_ENABLED': os.environ.get(
            'SKY_API_SERVER_METRICS_ENABLED', ''),
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
                    'created_at_human': debug_dump_helpers.epoch_to_human(
                        request.created_at),
                    'finished_at': request.finished_at,
                    'finished_at_human': debug_dump_helpers.epoch_to_human(
                        request.finished_at),
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

        # Copy debug log file (only exists when
        # ENABLE_REQUEST_DEBUG_LOGGING is enabled)
        try:
            debug_log_path = pathlib.Path(
                sky_logging.DEBUG_LOG_DIR) / f'{request_id}.log'
            if debug_log_path.exists():
                shutil.copy2(debug_log_path,
                             os.path.join(request_dir, 'request_debug.log'))
                logger.debug(f'Copied debug log for {request_id}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to copy debug log for request {request_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'requests',
                    'resource': f'{request_id}/request_debug.log',
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
                cluster_info = debug_dump_helpers.serialize_cluster_record(
                    cluster_record)
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
                for event_data in debug_dump_helpers.get_cluster_events_data(
                        cluster_hash):
                    event_file = f'events_{event_data["event_type"]}.json'
                    event_path = os.path.join(cluster_dir, event_file)
                    with open(event_path, 'w', encoding='utf-8') as f:
                        json.dump(event_data['events'],
                                  f,
                                  indent=2,
                                  default=str)
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
                'created_at_human': debug_dump_helpers.epoch_to_human(
                    r.created_at),
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
    if not managed_job_ids:
        logger.debug('No managed jobs to dump')
        return
    logger.debug(f'Entering _dump_managed_job_info for '
                 f'{len(managed_job_ids)} managed jobs')

    jobs_dir = os.path.join(dump_dir, 'managed_jobs')
    os.makedirs(jobs_dir, exist_ok=True)

    # Phase 1: Queue info from queue_v2 (works in both consolidation and
    # non-consolidation modes via existing gRPC/SSH plumbing)
    _dump_managed_job_queue_info(managed_job_ids, jobs_dir, errors)

    # Phase 2: Controller-side debug data (controller logs, events,
    # run logs, cluster info) via new gRPC RPC / CodeGen fallback
    _collect_controller_debug_data(list(managed_job_ids), dump_dir, errors)

    logger.debug('Exiting _dump_managed_job_info')


def _dump_managed_job_queue_info(
        managed_job_ids: Set[int],
        jobs_dir: str,
        errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect managed job info from queue_v2.

    This works in both consolidation and non-consolidation modes.
    """
    for job_id in managed_job_ids:
        job_dir = os.path.join(jobs_dir, str(job_id))
        os.makedirs(job_dir, exist_ok=True)

        try:
            jobs, _, _, _ = managed_jobs_core.queue_v2(refresh=False,
                                                       job_ids=[job_id])
            if jobs:
                for task_idx, job in enumerate(jobs):
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
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get info for job {job_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': str(job_id),
                    'error': str(e)
                })


def _collect_controller_debug_data(
        job_ids: List[int],
        dump_dir: str,
        errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Collect controller-side debug data via CodeGen manifest + rsync.

    Phase 1: Run CodeGen on the controller to get a manifest containing:
      - inline_data: small DB-derived JSON (written directly to disk)
      - file_paths: remote paths of large log files (downloaded via rsync)
    Phase 2: Use the controller handle's command runners to rsync
             each listed file.

    Works in both consolidation mode (LocalResourcesHandle → runs locally)
    and non-consolidation mode (remote controller via SSH).
    """
    # Get controller handle
    try:
        handle = backend_utils.is_controller_accessible(
            controller=controller_utils.Controllers.JOBS_CONTROLLER,
            stopped_message='Jobs controller is not running.',
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Jobs controller not accessible, skipping '
                       f'controller debug data: {e}')
        if errors is not None:
            errors.append({
                'component': 'managed_jobs',
                'resource': 'controller_access',
                'error': str(e)
            })
        return

    # Phase 1: Get manifest via CodeGen
    manifest = None
    try:
        code = managed_job_utils.ManagedJobCodeGen.get_debug_dump_manifest(
            job_ids)
        backend = CloudVmRayBackend()
        returncode, stdout, stderr = backend.run_on_head(handle,
                                                         code,
                                                         stream_logs=False,
                                                         require_outputs=True,
                                                         separate_stderr=True)
        subprocess_utils.handle_returncode(
            returncode, code,
            'Failed to collect debug dump manifest from controller.', stderr)
        manifest = message_utils.decode_payload(stdout)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to collect controller debug manifest '
                       f'via CodeGen: {e}')
        if errors is not None:
            errors.append({
                'component': 'managed_jobs',
                'resource': 'controller_manifest',
                'error': str(e)
            })
        return

    if manifest is None:
        return

    # Write inline data (small DB-derived JSON)
    for item in manifest.get('inline_data', []):
        relative_path = item.get('relative_path', '')
        if (os.path.isabs(relative_path) or
                '..' in relative_path.split(os.sep)):
            logger.warning('Skipping unsafe relative_path in manifest: '
                           f'{relative_path}')
            continue
        try:
            file_path = os.path.join(dump_dir, relative_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(item['content'])
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to write inline data '
                           f'{relative_path}: {e}')

    # Phase 2: Rsync large log files from controller
    file_path_entries = manifest.get('file_paths', [])
    if file_path_entries:
        try:
            runners = handle.get_command_runners()
            runner = runners[0]

            def _rsync_file(file_info):
                remote_path = file_info['remote_path']
                relative_path = file_info['relative_path']
                if (os.path.isabs(relative_path) or
                        '..' in relative_path.split(os.sep)):
                    logger.warning('Skipping unsafe relative_path in '
                                   f'manifest: {relative_path}')
                    return
                local_path = os.path.join(dump_dir, relative_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                try:
                    runner.rsync(
                        source=remote_path,
                        target=local_path,
                        up=False,
                        stream_logs=False,
                    )
                except exceptions.CommandError as e:
                    if e.returncode == exceptions.RSYNC_FILE_NOT_FOUND_CODE:
                        logger.debug(f'Remote file not found: {remote_path}')
                    else:
                        logger.warning(f'Failed to rsync {remote_path}: {e}')
                        if errors is not None:
                            errors.append({
                                'component': 'managed_jobs',
                                'resource': f'rsync/{relative_path}',
                                'error': str(e)
                            })

            subprocess_utils.run_in_parallel(_rsync_file, file_path_entries)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to rsync controller debug files: {e}')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': 'controller_rsync',
                    'error': str(e)
                })

    # Propagate controller-side errors
    if errors is not None:
        for err in manifest.get('errors', []):
            errors.append(err)

    logger.debug(
        f'Collected {len(manifest.get("inline_data", []))} inline files '
        f'and {len(file_path_entries)} rsynced files from controller')


def _build_debug_dump(
    dump_dir: str,
    debug_dump_context: DebugDumpContext,
    recent_hours: Optional[float],
    client_info: Optional[Dict[str, Any]],
) -> None:
    """Build the debug dump contents in dump_dir.

    Populates context via cross-linking, then dumps all sections
    (server info, requests, clusters, managed jobs, client info,
    errors, summary).
    """
    # Snapshot original inputs before cross-linking modifies the sets
    requested = {
        'request_ids': sorted(debug_dump_context['request_ids']),
        'cluster_names': sorted(debug_dump_context['cluster_names']),
        'managed_job_ids': sorted(debug_dump_context['managed_job_ids']),
        'recent_hours': recent_hours,
    }

    # Populate from recent activity if requested
    if recent_hours is not None:
        _populate_recent_context(debug_dump_context, recent_hours)

    # Collect all related resources (cross-linking)
    logger.debug('Cross-linking related resources')
    _get_requests_from_clusters(debug_dump_context)
    _get_requests_from_managed_jobs(debug_dump_context)
    _get_managed_jobs_from_requests(debug_dump_context)
    _get_clusters_from_requests(debug_dump_context)
    _get_clusters_from_managed_jobs(debug_dump_context)

    # Always include system daemon requests
    debug_dump_context['request_ids'].update(SYSTEM_REQUEST_IDS)

    logger.debug(f'After cross-linking: '
                 f'{len(debug_dump_context["request_ids"])} requests, '
                 f'{len(debug_dump_context["cluster_names"])} clusters, '
                 f'{len(debug_dump_context["managed_job_ids"])} managed jobs')

    # Dump all sections
    errors = debug_dump_context['errors']
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
    else:
        logger.debug('No client info provided')

    # Write errors file
    errors_path = os.path.join(dump_dir, 'errors.json')
    with open(errors_path, 'w', encoding='utf-8') as f:
        json.dump(errors, f, indent=2, default=str)

    # Write summary file
    summary: Dict[str, Any] = {
        'requested': requested,
        'collected': {
            'request_count': len(debug_dump_context['request_ids']),
            'cluster_count': len(debug_dump_context['cluster_names']),
            'managed_job_count': len(debug_dump_context['managed_job_ids']),
            'request_ids': sorted(debug_dump_context['request_ids']),
            'cluster_names': sorted(debug_dump_context['cluster_names']),
            'managed_job_ids': sorted(debug_dump_context['managed_job_ids']),
        },
        'errors': errors,
    }
    summary_path = os.path.join(dump_dir, 'summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)


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
    logger.debug('Starting debug dump creation')
    logger.debug(f'Initial inputs: request_ids={request_ids}, '
                 f'cluster_names={cluster_names}, '
                 f'managed_job_ids={managed_job_ids}, '
                 f'recent_hours={recent_hours}')

    debug_dump_context = DebugDumpContext(
        request_ids=set(request_ids or []),
        cluster_names=set(cluster_names or []),
        managed_job_ids=set(managed_job_ids or []),
        errors=[],
    )

    # Create persistent output directory
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
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

    # Build dump in temp dir, then zip to persistent location
    with tempstore.tempdir() as temp_dir:
        dump_dir = os.path.join(temp_dir, f'debug_dump_{timestamp}')
        os.makedirs(dump_dir)
        logger.debug(f'Building dump in temp directory: {dump_dir}')

        # Attach a file handler to capture debug-level logs into the
        # dump. The logger's effective level is already DEBUG (inherited
        # from root 'sky' logger), so we only need to set the handler
        # level — no logger level changes needed.
        debug_handler = logging.FileHandler(
            os.path.join(dump_dir, 'debug_dump.log'))
        debug_handler.setFormatter(sky_logging.FORMATTER)
        debug_handler.setLevel(logging.DEBUG)
        logger.addHandler(debug_handler)
        try:
            _build_debug_dump(dump_dir, debug_dump_context, recent_hours,
                              client_info)
        finally:
            logger.removeHandler(debug_handler)
            debug_handler.flush()
            debug_handler.close()

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

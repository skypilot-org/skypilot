"""Debug dump utilities for troubleshooting SkyPilot issues."""
import collections
import datetime
import hashlib
import json
import logging
import os
import pathlib
import platform
import posixpath
import re
import shutil
import time
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict
import zipfile

import sky
from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.backends import backend_utils
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.jobs import utils as managed_job_utils
from sky.jobs.server import core as managed_jobs_core
from sky.provision.kubernetes import debug as kubernetes_debug
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server import constants as server_constants
from sky.server import daemons
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.skylet import constants as skylet_constants
from sky.utils import command_runner
from sky.utils import common
from sky.utils import controller_utils
from sky.utils import debug_dump_helpers
from sky.utils import message_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import tempstore
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def _full_traceback() -> str:
    """Capture the full traceback, bypassing any tracebacklimit."""
    with ux_utils.enable_traceback():
        return traceback.format_exc()


# Persistent location for debug dumps
DEBUG_DUMP_DIR = '~/.sky/debug_dumps'

# Env var names whose values should be redacted (show bool presence only).
# Used for both server_info environment and request body sanitization.
_SENSITIVE_ENV_VARS = {
    'SKYPILOT_DB_CONNECTION_URI',
    'SKYPILOT_INITIAL_BASIC_AUTH',
    'SKYPILOT_SERVICE_ACCOUNT_TOKEN',
    'SKYPILOT_DOCKER_PASSWORD',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_SESSION_TOKEN',
    'AWS_ACCESS_KEY_ID',
    'AZURE_CLIENT_SECRET',
}

# Maps request name → field names containing task/dag YAML to redact.
# Empty tuple means include body verbatim (no YAML fields).
# Requests not in this dict have their body excluded entirely.
_REQUEST_BODY_ALLOWLIST: Dict[str, Tuple[str, ...]] = {
    # Category 1: verbatim (metadata only — cluster names, job IDs, flags, etc.)
    'sky.check': (),
    'sky.enabled_clouds': (),
    'sky.enabled_clouds_batch': (),
    'sky.stop': (),
    'sky.down': (),
    'sky.start': (),
    'sky.autostop': (),
    'sky.status': (),
    'sky.endpoints': (),
    'sky.cost_report': (),
    'sky.cluster_events': (),
    'sky.queue': (),
    'sky.job_status': (),
    'sky.cancel': (),
    'sky.logs': (),
    'sky.download_logs': (),
    'sky.hook_logs': (),
    'sky.jobs.queue': (),
    'sky.jobs.queue_v2': (),
    'sky.jobs.cancel': (),
    'sky.jobs.logs': (),
    'sky.jobs.wait': (),
    'sky.jobs.download_logs': (),
    'sky.jobs.pool_down': (),
    'sky.jobs.pool_status': (),
    'sky.jobs.pool_logs': (),
    'sky.jobs.pool_sync_down_logs': (),
    'sky.jobs.events': (),
    'sky.serve.down': (),
    'sky.serve.logs': (),
    'sky.serve.sync_down_logs': (),
    'sky.serve.status': (),
    'sky.serve.terminate_replica': (),
    'sky.storage_ls': (),
    'sky.storage_delete': (),
    'sky.volume_list': (),
    'sky.volume_delete': (),
    'sky.volume_apply': (),
    'sky.local_up': (),
    'sky.local_down': (),
    'sky.ssh_node_pools.up': (),
    'sky.ssh_node_pools.down': (),
    'sky.api_cancel': (),
    'sky.all_contexts': (),
    'sky.create_debug_dump': (),
    'sky.kubernetes_label_gpus': (),
    'sky.realtime_kubernetes_gpu_availability': (),
    'sky.kubernetes_node_info': (),
    'sky.status_kubernetes': (),
    'sky.realtime_slurm_gpu_availability': (),
    'sky.slurm_node_info': (),
    'sky.list_accelerators': (),
    'sky.list_accelerator_counts': (),
    'sky.workspaces.delete': (),
    'sky.workspaces.get': (),
    'sky.workspaces.get_config': (),
    'sky.workspaces.batch_add_users': (),
    'sky.workspaces.batch_remove_users': (),
    'sky.recipes.list': (),
    'sky.recipes.get': (),
    'sky.recipes.delete': (),
    'sky.recipes.pin': (),
    # Internal daemons
    'sky.status-refresh': (),
    'sky.volume-refresh': (),
    'sky.managed-job-status-refresh': (),
    'sky.sky-serve-status-refresh': (),
    'sky.pool-status-refresh': (),
    'sky.server-heartbeat': (),
    'sky.expired-token-cleanup': (),
    # Category 2: redact task/dag YAML fields before including
    'sky.launch': ('task',),
    'sky.exec': ('task',),
    'sky.optimize': ('dag',),
    'sky.jobs.launch': ('task',),
    'sky.jobs.pool_apply': ('task',),
    'sky.serve.up': ('task',),
    'sky.serve.update': ('task',),
}

# System daemon request IDs to always include in debug dumps.
# Built from INTERNAL_REQUEST_DAEMONS (background refresh daemons) plus the
# on-boot check request.
SYSTEM_REQUEST_IDS = [d.id for d in daemons.INTERNAL_REQUEST_DAEMONS
                     ] + [server_constants.ON_BOOT_CHECK_REQUEST_ID]

# Request names for managed job mutations (excludes read-only queue).
# Used by both _get_requests_from_managed_jobs and
# _get_managed_jobs_from_requests.
_MANAGED_JOB_REQUEST_NAMES = frozenset({
    server_constants.REQUEST_NAME_PREFIX +
    request_names.RequestName.JOBS_LAUNCH.value,
    server_constants.REQUEST_NAME_PREFIX +
    request_names.RequestName.JOBS_CANCEL.value,
    server_constants.REQUEST_NAME_PREFIX +
    request_names.RequestName.JOBS_LOGS.value,
})


class DebugDumpContext(TypedDict):
    """The context for a debug dump."""
    request_ids: Set[str]
    cluster_names: Set[str]
    managed_job_ids: Set[int]
    # Provenance sidecars: requests added by a cross-link helper because
    # they reference a job (resp. cluster). When we later iterate
    # request_ids to expand the context further, we skip these to break
    # the job→request→job and cluster→request→cluster cycles. Without
    # these, an over-broad matcher (body.name, body.all_users, body.all,
    # or any cluster touching many requests) drags unrelated resources
    # into the dump.
    request_ids_via_job: Set[str]
    request_ids_via_cluster: Set[str]
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
            new_ids = {request.request_id for request in requests}
            if new_ids:
                logger.debug(f'Cross-link: cluster {cluster_name!r} -> '
                             f'{len(new_ids)} requests: {sorted(new_ids)}')
            # Only tag IDs that weren't already in the context. Otherwise
            # a user-seeded or recent-context request would inherit the
            # via_cluster restriction and get skipped in
            # _get_clusters_from_requests.
            newly_added = new_ids - debug_dump_context['request_ids']
            debug_dump_context['request_ids'] |= new_ids
            debug_dump_context['request_ids_via_cluster'] |= newly_added
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get requests for cluster '
                           f'{cluster_name}: {e}')
            debug_dump_context['errors'].append({
                'component': 'cross_link',
                'resource': f'requests_from_cluster/{cluster_name}',
                'error': str(e),
                'traceback': _full_traceback()
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
            refresh=False,
            job_ids=list(debug_dump_context['managed_job_ids']),
            all_users=True)
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
            'error': str(e),
            'traceback': _full_traceback()
        })

    try:
        # Get all requests with managed job-related names
        requests = requests_lib.get_request_tasks(
            requests_lib.RequestTaskFilter(
                include_request_names=list(_MANAGED_JOB_REQUEST_NAMES),
                fields=['request_id', 'name', 'request_body', 'return_value']))

        for request in requests:
            match_reason: Optional[str] = None
            # Match by request body fields (job_id, job_ids, name, etc.)
            body = request.request_body
            if body is not None:
                job_id = getattr(body, 'job_id', None)
                job_ids = getattr(body, 'job_ids', None)
                if (job_id is not None and
                        job_id in debug_dump_context['managed_job_ids']):
                    match_reason = f'body.job_id={job_id}'
                elif (job_ids is not None and
                      any(jid in debug_dump_context['managed_job_ids']
                          for jid in job_ids)):
                    match_reason = f'body.job_ids={job_ids}'
                # Match cancel-by-name
                elif getattr(body, 'name', None) in job_names:
                    match_reason = (
                        f'body.name={getattr(body, "name", None)!r}')
                # Match cancel-all-users (affects all jobs)
                elif getattr(body, 'all_users', False):
                    match_reason = 'body.all_users=True'
                # Match cancel-all (affects only the requesting
                # user's jobs, so include if user owns a target job)
                elif getattr(body, 'all', False):
                    cancel_user = getattr(body, 'env_vars', {}).get(
                        skylet_constants.USER_ID_ENV_VAR)
                    if cancel_user and cancel_user in job_user_hashes:
                        match_reason = (f'body.all=True (user={cancel_user})')
            # For jobs.launch, also match by return_value job_id
            jobs_launch_name = (server_constants.REQUEST_NAME_PREFIX +
                                request_names.RequestName.JOBS_LAUNCH.value)
            if (not match_reason and request.name == jobs_launch_name):
                rv = request.return_value
                if isinstance(rv, dict):
                    resp_job_id = rv.get('job_id')
                    if isinstance(resp_job_id, list):
                        resp_jobs = resp_job_id
                    else:
                        resp_jobs = [resp_job_id]
                    for job_id in resp_jobs:
                        if (job_id is not None and job_id
                                in debug_dump_context['managed_job_ids']):
                            match_reason = (f'return_value.job_id={job_id}')
                            break
            if match_reason:
                logger.debug(f'Cross-link: managed jobs -> request '
                             f'{request.request_id} ({request.name}) '
                             f'via {match_reason}')
                # Only tag IDs that weren't already in the context.
                # Otherwise a user-seeded or recent-context request
                # would inherit the via_job restriction and get skipped
                # in _get_managed_jobs_from_requests.
                if (request.request_id
                        not in debug_dump_context['request_ids']):
                    debug_dump_context['request_ids_via_job'].add(
                        request.request_id)
                debug_dump_context['request_ids'].add(request.request_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get requests for managed jobs: {e}')
        debug_dump_context['errors'].append({
            'component': 'cross_link',
            'resource': 'requests_from_managed_jobs',
            'error': str(e),
            'traceback': _full_traceback()
        })


def _get_clusters_from_requests(debug_dump_context: DebugDumpContext) -> None:
    """Get cluster names from the given request IDs.

    Skips requests that were themselves added because they reference a
    cluster — that's a same-type re-expansion (cluster -> request ->
    cluster) and would let a cluster that touched many requests drag
    every other cluster touched by those requests into the dump.
    """
    # Requests added by _get_requests_from_clusters must not re-seed
    # cluster_names here. Other origins (user seed, recent context,
    # _get_requests_from_managed_jobs) remain free to expand.
    request_ids = (debug_dump_context['request_ids'] -
                   debug_dump_context['request_ids_via_cluster'])
    if not request_ids:
        return
    logger.debug(f'Getting clusters for {len(request_ids)} requests')
    for request_id in request_ids:
        try:
            request = requests_lib.get_request(request_id,
                                               fields=['cluster_name'])
            if request is not None and request.cluster_name is not None:
                logger.debug(f'Cross-link: request {request_id} -> '
                             f'cluster {request.cluster_name!r}')
                debug_dump_context['cluster_names'].add(request.cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get cluster for request '
                           f'{request_id}: {e}')
            debug_dump_context['errors'].append({
                'component': 'cross_link',
                'resource': f'clusters_from_request/{request_id}',
                'error': str(e),
                'traceback': _full_traceback()
            })


def _get_managed_jobs_from_requests(
        debug_dump_context: DebugDumpContext) -> None:
    """Extract managed job IDs from request bodies.

    If any request in the context is a managed job request (launch, cancel,
    logs), extract the job IDs from its body and add them to the context.

    This runs before any cluster -> request expansion, so only user-seeded
    and recent-context requests are expanded into jobs. In particular,
    requests found via a cluster never seed jobs: every sky.jobs.* request
    carries the jobs controller cluster name, so allowing that chain would
    turn any dump touching the controller cluster into a dump of every
    managed job.

    Skips requests that were themselves added because they reference a
    job — that's a same-type re-expansion (job -> request -> job) which
    would let an over-broad matcher (body.name, body.all_users, body.all)
    drag every sibling job of a batch-style request into the dump.
    """
    # Requests added by _get_requests_from_managed_jobs must not re-seed
    # managed_job_ids here. (With the current cross-link ordering that
    # helper runs after this one, so the subtraction is defensive.)
    request_ids = (debug_dump_context['request_ids'] -
                   debug_dump_context['request_ids_via_job'])
    if not request_ids:
        return
    logger.debug(f'Getting managed jobs for {len(request_ids)} requests')

    for request_id in request_ids:
        try:
            request = requests_lib.get_request(
                request_id, fields=['name', 'request_body', 'return_value'])
            if (request is None or
                    request.name not in _MANAGED_JOB_REQUEST_NAMES):
                continue
            body = request.request_body
            if body is not None:
                job_id = getattr(body, 'job_id', None)
                if job_id is not None:
                    logger.debug(f'Cross-link: request {request_id} -> '
                                 f'managed job {job_id} via body.job_id')
                    debug_dump_context['managed_job_ids'].add(job_id)
                job_ids = getattr(body, 'job_ids', None)
                if job_ids is not None:
                    logger.debug(f'Cross-link: request {request_id} -> '
                                 f'managed jobs {job_ids} via body.job_ids')
                    debug_dump_context['managed_job_ids'].update(job_ids)
            # For jobs.launch, the job ID is in the response, not the
            # request body.
            jobs_launch_name = (server_constants.REQUEST_NAME_PREFIX +
                                request_names.RequestName.JOBS_LAUNCH.value)
            if request.name == jobs_launch_name:
                rv = request.return_value
                if isinstance(rv, dict):
                    resp_job_id = rv.get('job_id')
                    if isinstance(resp_job_id, list):
                        debug_dump_context['managed_job_ids'].update(
                            resp_job_id)
                        logger.debug(f'Linked request {request_id} '
                                     f'to managed jobs {resp_job_id} '
                                     f'via return_value')
                    elif resp_job_id is not None:
                        debug_dump_context['managed_job_ids'].add(resp_job_id)
                        logger.debug(f'Linked request {request_id} '
                                     f'to managed job {resp_job_id} '
                                     f'via return_value')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get managed job info for '
                           f'request {request_id}: {e}')
            debug_dump_context['errors'].append({
                'component': 'cross_link',
                'resource': f'managed_jobs_from_request/{request_id}',
                'error': str(e),
                'traceback': _full_traceback()
            })


def _managed_job_cluster_names_from_records(
        job_records: List[Dict[str, Any]]) -> Dict[int, Set[str]]:
    """Map job IDs to the underlying cluster name(s) of their tasks.

    A job assigned to a pool records the pool worker it runs on in
    current_cluster_name. Non-pool jobs use a deterministic per-task
    cluster name (a multi-task pipeline launches one cluster per task,
    so a job can have several). Mirrors the resolution in
    jobs.utils.queue_v2 and jobs.server.core._get_job_cluster_names.

    job_records are queue_v2 records: one record per task, so a
    multi-task job contributes one name per task.
    """
    cluster_names: Dict[int, Set[str]] = collections.defaultdict(set)
    for job in job_records:
        job_id = job.get('job_id')
        if job_id is None:
            continue
        current_cluster_name = job.get('current_cluster_name')
        if current_cluster_name:
            cluster_names[job_id].add(current_cluster_name)
            continue
        if job.get('pool'):
            # Pool job not yet assigned to a worker — no cluster yet.
            continue
        task_name = job.get('task_name')
        if not task_name:
            continue
        cluster_names[job_id].add(
            managed_job_utils.generate_managed_job_cluster_name(
                task_name, job_id))
    return cluster_names


def _get_managed_jobs_from_clusters(
        debug_dump_context: DebugDumpContext) -> None:
    """Get managed job IDs whose underlying cluster is in the context.

    This must run FIRST in _build_debug_dump — before the recent-activity
    scan and before any other cross-link expansion — so the cluster names
    it sees are exactly the ones the user explicitly requested. That keeps
    the cluster -> job expansion intentional: a pool worker is shared by
    many jobs, so mapping it back to all of its jobs is only reasonable
    when the user asked about that cluster specifically. Clusters that
    enter the context later — via the recent-activity scan, job ->
    cluster, or request -> cluster — are never expanded into jobs.
    """
    if not debug_dump_context['cluster_names']:
        return
    logger.debug(
        f'Getting managed jobs for '
        f'{len(debug_dump_context["cluster_names"])} requested clusters')
    try:
        jobs, _, _, _ = managed_jobs_core.queue_v2(refresh=False,
                                                   all_users=True)
        job_cluster_names = _managed_job_cluster_names_from_records(jobs)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get managed jobs for clusters: {e}')
        debug_dump_context['errors'].append({
            'component': 'cross_link',
            'resource': 'managed_jobs_from_clusters',
            'error': str(e),
            'traceback': _full_traceback()
        })
        return
    for job_id, names in job_cluster_names.items():
        matched = names & debug_dump_context['cluster_names']
        if matched:
            logger.debug(f'Cross-link: cluster(s) {sorted(matched)} -> '
                         f'managed job {job_id}')
            debug_dump_context['managed_job_ids'].add(job_id)


def _get_job_clusters_from_managed_jobs(
        debug_dump_context: DebugDumpContext) -> None:
    """Get the underlying per-job cluster names from managed jobs.

    Only meaningful in consolidation mode, where job clusters are recorded
    in this API server's own state. In non-consolidation mode they live on
    the remote controller and are collected via the controller-side
    manifest in _dump_managed_job_info instead.

    Must run BEFORE _get_requests_from_clusters so that requests
    referencing these clusters are cross-linked into the dump. The jobs
    controller cluster is deliberately NOT added here — see
    _get_clusters_from_managed_jobs for why it must come after request
    expansion.
    """
    if not debug_dump_context['managed_job_ids']:
        return
    if not managed_job_utils.is_consolidation_mode():
        return
    job_ids = list(debug_dump_context['managed_job_ids'])
    logger.debug(f'Getting job clusters for {len(job_ids)} managed jobs')
    try:
        jobs, _, _, _ = managed_jobs_core.queue_v2(refresh=False,
                                                   job_ids=job_ids,
                                                   all_users=True)
        job_cluster_names = _managed_job_cluster_names_from_records(jobs)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get job clusters for managed jobs: {e}')
        debug_dump_context['errors'].append({
            'component': 'cross_link',
            'resource': 'job_clusters_from_managed_jobs',
            'error': str(e),
            'traceback': _full_traceback()
        })
        return
    for job_id, names in job_cluster_names.items():
        for name in sorted(names):
            logger.debug(f'Cross-link: managed job {job_id} -> '
                         f'cluster {name!r}')
            debug_dump_context['cluster_names'].add(name)


def _get_clusters_from_managed_jobs(
        debug_dump_context: DebugDumpContext) -> None:
    """Add the jobs controller cluster for any managed jobs in the context.

    This must stay AFTER _get_requests_from_clusters in the cross-link
    order: every sky.jobs.* request stores the controller cluster name in
    its cluster_name column, so expanding requests from the controller
    cluster would pull every managed-jobs request ever made (and,
    transitively, unrelated resources) into any dump that touches a single
    job. The controller cluster is included for its cluster record and
    events only.

    Per-job clusters are handled by _get_job_clusters_from_managed_jobs.
    """
    if not debug_dump_context['managed_job_ids']:
        return
    logger.debug(f'Cross-link: {len(debug_dump_context["managed_job_ids"])} '
                 f'managed jobs -> adding jobs controller cluster '
                 f'{common.JOB_CONTROLLER_NAME!r}')
    debug_dump_context['cluster_names'].add(common.JOB_CONTROLLER_NAME)


def _populate_recent_context(debug_dump_context: DebugDumpContext,
                             minutes: float) -> None:
    """Populate context with resources active within the given time window."""
    logger.debug(
        f'Populating context with resources from last {minutes} minutes')
    cutoff_time = time.time() - (minutes * 60)

    # Get recent requests (cluster names are handled by
    # _get_clusters_from_requests during cross-linking)
    try:
        requests = requests_lib.get_request_tasks(
            requests_lib.RequestTaskFilter(finished_after=cutoff_time,
                                           fields=['request_id',
                                                   'finished_at']))
        for request in requests:
            logger.debug(f'Recent: including request {request.request_id} '
                         f'(finished_at={request.finished_at},'
                         f' cutoff={cutoff_time:.0f})')
            debug_dump_context['request_ids'].add(request.request_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent requests: {e}')
        debug_dump_context['errors'].append({
            'component': 'recent_context',
            'resource': 'requests',
            'error': str(e),
            'traceback': _full_traceback()
        })

    # Get recent clusters
    try:
        clusters = global_user_state.get_clusters()
        for cluster in clusters:
            status_updated_at = cluster.get('status_updated_at') or 0
            launched_at = cluster.get('launched_at') or 0
            if status_updated_at >= cutoff_time or launched_at >= cutoff_time:
                cluster_name = cluster.get('name')
                if cluster_name and controller_utils.Controllers.from_name(
                        cluster_name) is not None:
                    # Skip controller clusters: every sky.jobs.* /
                    # sky.serve.* request stores its controller's cluster
                    # name, so letting a controller into the context here
                    # would make _get_requests_from_clusters pull every
                    # such request ever made into any recent-activity
                    # dump. The jobs controller's cluster record is still
                    # dumped whenever managed jobs are in the context —
                    # _get_clusters_from_managed_jobs adds it after
                    # request expansion — and explicitly requesting a
                    # controller with -c still works.
                    logger.debug(f'Recent: skipping controller cluster '
                                 f'{cluster_name!r}')
                    continue
                if cluster_name:
                    reasons = []
                    if status_updated_at >= cutoff_time:
                        reasons.append(
                            f'status_updated_at={status_updated_at:.0f}')
                    if launched_at >= cutoff_time:
                        reasons.append(f'launched_at={launched_at:.0f}')
                    logger.debug(f'Recent: including cluster {cluster_name!r} '
                                 f'({", ".join(reasons)} >= '
                                 f'cutoff {cutoff_time:.0f})')
                    debug_dump_context['cluster_names'].add(cluster_name)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent clusters: {e}')
        debug_dump_context['errors'].append({
            'component': 'recent_context',
            'resource': 'clusters',
            'error': str(e),
            'traceback': _full_traceback()
        })

    # Get recent managed jobs via queue_v2 (handles remote controllers
    # via gRPC/SSH, unlike direct DB access which only works in
    # consolidation mode).
    try:
        jobs, _, _, _ = managed_jobs_core.queue_v2(refresh=False,
                                                   all_users=True)
        for job in jobs:
            submitted_at = job.get('submitted_at') or 0
            end_at = job.get('end_at') or time.time()
            if submitted_at >= cutoff_time or end_at >= cutoff_time:
                job_id = job.get('job_id')
                if job_id is not None:
                    reasons = []
                    if submitted_at >= cutoff_time:
                        reasons.append(f'submitted_at={submitted_at:.0f}')
                    if end_at >= cutoff_time:
                        reasons.append(f'end_at={end_at:.0f}')
                    logger.debug(f'Recent: including managed job {job_id} '
                                 f'({", ".join(reasons)} >= '
                                 f'cutoff {cutoff_time:.0f})')
                    debug_dump_context['managed_job_ids'].add(job_id)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get recent managed jobs: {e}')
        debug_dump_context['errors'].append({
            'component': 'recent_context',
            'resource': 'managed_jobs',
            'error': str(e),
            'traceback': _full_traceback()
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
        server_info['jobs_controller_consolidation_mode'] = (
            managed_job_utils.is_consolidation_mode())
        server_info['config'] = debug_dump_helpers.redact_config(
            dict(skypilot_config.get_server_config()))
    except Exception as e:  # pylint: disable=broad-except
        server_info['config_error'] = str(e)
        if errors is not None:
            errors.append({
                'component': 'server_info',
                'resource': 'config',
                'error': str(e),
                'traceback': _full_traceback()
            })

    # Add all SKYPILOT_*/SKY_* environment variables, redacting sensitive ones
    env = {}
    for k, v in sorted(os.environ.items()):
        if k.startswith(('SKYPILOT_', 'SKY_')):
            env[k] = bool(v) if k in _SENSITIVE_ENV_VARS else v
    server_info['environment'] = env

    # Add cloud status (keyed by workspace name, each mapping cloud names
    # to a list of capability strings — already JSON-serializable).
    try:
        server_info['enabled_clouds'] = sky_check.check(quiet=True)
    except Exception as e:  # pylint: disable=broad-except
        server_info['cloud_status_error'] = str(e)
        if errors is not None:
            errors.append({
                'component': 'server_info',
                'resource': 'cloud_status',
                'error': str(e),
                'traceback': _full_traceback()
            })

    server_info_path = os.path.join(dump_dir, 'server_info.json')
    with open(server_info_path, 'w', encoding='utf-8') as f:
        json.dump(server_info, f, indent=2, default=str)
    logger.debug('Exiting _dump_server_info')


def _sanitize_request_body(request) -> Optional[Dict[str, Any]]:
    """Sanitize a request body for inclusion in a debug dump.

    Returns None if the request type is not in the allowlist or has no body.
    For allowed requests, redacts sensitive env vars and task/dag YAML fields.
    """
    task_fields = _REQUEST_BODY_ALLOWLIST.get(request.name)
    if task_fields is None:
        return None
    body = request.request_body
    if body is None:
        return None
    try:
        data = body.model_dump()
    except Exception:  # pylint: disable=broad-except
        return None
    # Redact sensitive env var values
    env_vars = data.get('env_vars')
    if isinstance(env_vars, dict):
        for k in env_vars:
            if k in _SENSITIVE_ENV_VARS:
                env_vars[k] = '<redacted>'
    # Redact task/dag YAML fields
    for field in task_fields:
        if field in data and isinstance(data[field], str):
            data[field] = debug_dump_helpers.redact_task_yaml(data[field])
    return data


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
                    'request_body': _sanitize_request_body(request),
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
                logger.debug(
                    f'Dumped request {request_id} '
                    f'(name={request.name}, '
                    f'status='
                    f'{request.status.value if request.status else None})')
            else:
                logger.debug(f'Request {request_id} not found in DB')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get info for request {request_id}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'requests',
                    'resource': request_id,
                    'error': str(e),
                    'traceback': _full_traceback()
                })

        # Copy request log file
        try:
            log_path = (pathlib.Path(
                server_constants.REQUEST_LOG_PATH_PREFIX).expanduser() /
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
                    'error': str(e),
                    'traceback': _full_traceback()
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
                    'error': str(e),
                    'traceback': _full_traceback()
                })

    logger.debug('Exiting _dump_request_id_info')


# Short connection timeout for the skylet-log-path resolution command. The
# debug dump may run against many clusters, some of which are still
# provisioning or otherwise unreachable; we want such a node to fail fast
# rather than hang the dump waiting to connect.
_SKYLET_LOG_RESOLVE_CONNECT_TIMEOUT = 10

# Total wall-clock timeout for the skylet-log rsync. Reachability is already
# gated by the resolve step above (connect_timeout), so this is a backstop
# for a connected-but-stalled transfer (flaky network, oversized rotated
# log): generous enough not to trip on a healthy node + small log, tight
# enough that one bad node can't hang the whole dump.
_SKYLET_LOG_RSYNC_TIMEOUT = 60


def _resolve_remote_skylet_log_path(runner: Any, cluster_name: str) -> str:
    """Resolve the absolute skylet log path on the head node.

    Skylet writes its log to ``$SKY_RUNTIME_DIR/.sky/skylet.log``, where
    SKY_RUNTIME_DIR defaults to ``$HOME`` (see
    runtime_utils.get_runtime_dir_path, used by skylet/attempt_skylet.py to
    place the log). The runtime dir can be
    relocated off ``$HOME`` -- Slurm moves it off the NFS home, and devspaces
    override it via the pod env -- and not every command runner exposes that
    location as a Python attribute. Rather than special-casing each provider, we
    resolve the path on the remote node using the same env var, in the same
    ``source_bashrc`` environment that instance_setup uses to start skylet (see
    start_skylet_on_head_node), so it is correct for every runner.

    Bounded by a short connect timeout so an unreachable node (e.g. a cluster
    still provisioning) fails fast instead of hanging the dump. Falls back to
    ``~/.sky/skylet.log`` if the resolution command fails; rsync then handles a
    missing file best-effort. posixpath (not os.path) is used for the fallback
    because this is a remote *nix path resolved on the cluster.
    """
    default_path = posixpath.join('~', skylet_constants.SKYLET_LOG_FILE)
    # Mirror the shell form of the runtime dir (constants.SKY_RUNTIME_DIR) so
    # the remote shell expands the same env var skylet read.
    cmd = (f'echo "${{{skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY}:-$HOME}}/'
           f'{skylet_constants.SKYLET_LOG_FILE}"')
    try:
        returncode, stdout, _ = runner.run(
            cmd,
            require_outputs=True,
            stream_logs=False,
            source_bashrc=True,
            connect_timeout=_SKYLET_LOG_RESOLVE_CONNECT_TIMEOUT)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to resolve skylet log path on cluster '
                     f'{cluster_name!r}, falling back to {default_path!r}: {e}')
        return default_path
    # sourcing bashrc can emit banner/warning lines before our echo; the path is
    # the last non-empty line.
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if returncode != 0 or not lines:
        logger.debug(f'Could not resolve skylet log path on cluster '
                     f'{cluster_name!r} (rc={returncode}), falling back to '
                     f'{default_path!r}')
        return default_path
    return lines[-1]


def _collect_cluster_skylet_log(
        cluster_name: str,
        cluster_dir: str,
        handle: Any,
        errors: Optional[List[Dict[str, str]]] = None,
        status: Optional['status_lib.ClusterStatus'] = None) -> None:
    """Rsync the head node's skylet log into the cluster dump dir.

    Skylet runs only on the head node (see
    instance_setup.start_skylet_on_head_node), so we pull it off the first
    command runner (runners[0] is always the head; ClusterInfo.ip_tuples()
    guarantees head-first ordering). The log path is resolved on the remote node
    (see _resolve_remote_skylet_log_path) to honor a relocated SKY_RUNTIME_DIR.

    Best-effort: the dump is never aborted. For an INIT cluster, collection
    failures are *expected* (the node may not be reachable or provisioned yet),
    so they are logged at debug level rather than recorded as dump errors --
    otherwise a fleet with some always-launching clusters would fill ``errors``
    with benign connection-refused noise. For other statuses a failure is
    genuinely worth surfacing, so it is recorded in ``errors``.
    """
    # INIT clusters are expected to sometimes be unreachable; don't treat a
    # collection failure as a real dump error in that case.
    expected_failure = (status == status_lib.ClusterStatus.INIT)

    def _record_failure(message: str, exc: BaseException) -> None:
        # str(exc) is empty for some exceptions (e.g. FetchClusterInfoError);
        # fall back to the type name so the entry is never blank.
        detail = str(exc) or type(exc).__name__
        if expected_failure:
            logger.debug(f'{message} (expected for {status} cluster '
                         f'{cluster_name!r}): {detail}')
            return
        logger.warning(f'{message}: {detail}')
        if errors is not None:
            errors.append({
                'component': 'clusters',
                'resource': f'{cluster_name}/skylet_log',
                'error': detail,
                'traceback': _full_traceback()
            })

    try:
        runners = handle.get_command_runners()
    except Exception as e:  # pylint: disable=broad-except
        _record_failure(
            f'Failed to get command runners for cluster {cluster_name}', e)
        return

    if not runners:
        logger.debug(f'No command runners for cluster {cluster_name!r}; '
                     f'skipping skylet log')
        return

    runner = runners[0]  # Head node; skylet runs only there.
    remote_path = _resolve_remote_skylet_log_path(runner, cluster_name)
    target = os.path.join(cluster_dir, 'skylet.log')
    try:
        runner.rsync(source=remote_path,
                     target=target,
                     up=False,
                     stream_logs=False,
                     timeout=_SKYLET_LOG_RSYNC_TIMEOUT)
        logger.debug(f'Collected skylet log for cluster {cluster_name!r}')
    except exceptions.CommandError as e:
        if e.returncode == exceptions.RSYNC_FILE_NOT_FOUND_CODE:
            logger.debug(f'No skylet log found on cluster {cluster_name!r}')
        else:
            _record_failure(
                f'Failed to rsync skylet log for cluster {cluster_name}', e)
    except Exception as e:  # pylint: disable=broad-except
        _record_failure(
            f'Failed to collect skylet log for cluster {cluster_name}', e)


def _kube_coordinates_for_handle(
        handle: Any) -> Optional[Tuple[Optional[str], str]]:
    """Resolve a cluster handle's (kube context, namespace), or None.

    Returns None for clusters that aren't on Kubernetes (or whose runners can't
    be resolved). For a Kubernetes cluster every command runner is a
    KubernetesCommandRunner carrying its pod's (context, namespace); they're
    cluster-wide identical, so runners[0] suffices. get_command_runners()
    rebuilds from the cached cluster_info (no live API call), so this is cheap
    and safe even when the context is defunct.
    """
    launched_resources = getattr(handle, 'launched_resources', None)
    cloud = getattr(launched_resources, 'cloud', None)
    if not isinstance(cloud, clouds.Kubernetes):
        return None

    runners = handle.get_command_runners()
    k8s_runners: List[Any] = [
        r for r in runners
        if isinstance(r, command_runner.KubernetesCommandRunner)
    ]
    if not k8s_runners:
        return None
    runner = k8s_runners[0]
    assert isinstance(runner, command_runner.KubernetesCommandRunner), runner
    # .context/.namespace are set via tuple-unpacking mypy can't track.
    return runner.context, runner.namespace  # type: ignore[attr-defined]


def _sanitize_context_name(context: Optional[str]) -> str:
    """Map a kube context to a filesystem-safe directory name.

    The in-cluster context (``None``) maps to ``in-cluster``. Otherwise we
    replace path/colon/whitespace chars and append a short hash of the raw name
    so two contexts that sanitize to the same string don't collide.
    """
    if context is None:
        return 'in-cluster'
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', context)
    digest = hashlib.sha1(context.encode('utf-8')).hexdigest()[:8]
    return f'{safe}-{digest}'


def _collect_cluster_kubernetes_resources(
        cluster_name: str,
        cluster_dir: str,
        handle: Any,
        errors: Optional[List[Dict[str, str]]] = None) -> None:
    """Snapshot a Kubernetes cluster's per-cluster k8s objects into the dump.

    For clusters on Kubernetes, capture the pods, their events, the resources
    SkyPilot created (Services, etc.), and this cluster's Kueue Workload -- the
    same things you'd reach for with ``kubectl get -o yaml`` when debugging.
    No-op for clusters on other clouds.

    These calls are all namespace-scoped, so they work under SkyPilot's minimal
    (namespace-only) RBAC. Cluster-WIDE objects shared across SkyPilot clusters
    (GPU-metrics pods, the Kueue quota config) are collected once per context by
    ``_dump_kube_contexts_info`` instead; we drop a ``context.json`` here so a
    reader can find them.

    Unlike the skylet log (which is rsynced off a reachable node and so only
    works on UP clusters), these come from the kube API server, which answers
    even when the cluster is broken -- pod events on a failed launch are exactly
    what we want -- so the caller doesn't gate this on cluster status.

    Best-effort: every failure is recorded in ``errors`` and never aborts the
    dump.
    """
    try:
        coords = _kube_coordinates_for_handle(handle)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to get command runners for cluster '
                       f'{cluster_name}: {e}')
        if errors is not None:
            errors.append({
                'component': 'clusters',
                'resource': f'{cluster_name}/kubernetes',
                'error': str(e),
                'traceback': _full_traceback()
            })
        return

    if coords is None:
        logger.debug(
            f'Cluster {cluster_name!r} is not on Kubernetes (or has no '
            f'k8s runners); skipping k8s resource dump')
        return

    context, namespace = coords
    output_dir = os.path.join(cluster_dir, 'kubernetes')

    k8s_errors = kubernetes_debug.dump_cluster_resources(
        context=context,
        namespace=namespace,
        cluster_name_on_cloud=handle.cluster_name_on_cloud,
        output_dir=output_dir)

    # Drop a mapping file pointing at the per-context dump of this cluster's
    # cluster-wide objects. Best-effort -- never let it abort the dump.
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, 'context.json'),
                  'w',
                  encoding='utf-8') as f:
            json.dump(
                {
                    'context': context,
                    'namespace': namespace,
                    'cluster_name_on_cloud': handle.cluster_name_on_cloud,
                    'context_dir': f'kubernetes_contexts/'
                                   f'{_sanitize_context_name(context)}',
                },
                f,
                indent=2,
                default=str)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to write context.json for {cluster_name!r}: {e}')

    if errors is not None:
        for err in k8s_errors:
            errors.append({
                'component': 'clusters',
                'resource': f'{cluster_name}/{err["resource"]}',
                'error': err['error'],
                'traceback': err['traceback'],
            })


def _dump_kube_contexts_info(dump_dir: str,
                             errors: Optional[List[Dict[str,
                                                        str]]] = None) -> None:
    """Dump cluster-WIDE k8s objects once per allowed kube context.

    The GPU-metrics pods (Prometheus server, DCGM exporter) and the non-Workload
    Kueue objects (ClusterQueues / LocalQueues / ResourceFlavors / Topologies)
    are shared across all SkyPilot clusters on a kube context, so we fetch them
    once per context into ``kubernetes_contexts/<sanitized-context>/``.

    Source of truth is ``Kubernetes.existing_allowed_contexts()`` -- the same
    set ``sky check`` uses -- *not* contexts derived from the dumped clusters. A
    context with no SkyPilot clusters (e.g. a freshly onboarded tenant) is still
    scraped, so its GPU-metrics / Kueue config can be debugged before anything
    runs there. ``None`` in the list means in-cluster auth (see
    _sanitize_context_name).

    We additionally always include the API server's own in-cluster context
    (when running in-cluster), even if ``existing_allowed_contexts()`` drops it
    -- which it does when ``allowed_contexts`` is an explicit list that omits
    it, or ``SKYPILOT_ALL_KUBERNETES_CONTEXTS_INCLUDES_IN_CLUSTER=false`` hides
    it as a compute target. Its GPU-manager / Kueue config is worth dumping.

    Robustness (tenants can have defunct contexts that time out): every call is
    5s-bounded, the per-context fetch fast-fails on the first connection error,
    and contexts are fetched in parallel -- so N dead contexts cost ~5s, not
    ~5s*calls*N. Best-effort: errors are recorded, never aborts the dump.
    """
    try:
        contexts = clouds.Kubernetes.existing_allowed_contexts(silent=True)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to list allowed Kubernetes contexts: {e}')
        if errors is not None:
            errors.append({
                'component': 'kubernetes_contexts',
                'resource': 'allowed_contexts',
                'error': str(e),
                'traceback': _full_traceback(),
            })
        return

    # Always dump the API server's own in-cluster context, even when it's been
    # excluded as a compute target (explicit allowed_contexts list, or
    # SKYPILOT_ALL_KUBERNETES_CONTEXTS_INCLUDES_IN_CLUSTER=false). The dedupe
    # below drops it if existing_allowed_contexts() already surfaced it.
    if kubernetes_utils.is_incluster_config_available():
        contexts = list(contexts) + [kubernetes.in_cluster_context_name()]

    # Dedupe defensively while preserving order (None = in-cluster is allowed).
    unique_contexts = list(dict.fromkeys(contexts))
    if not unique_contexts:
        return

    contexts_root = os.path.join(dump_dir, 'kubernetes_contexts')
    os.makedirs(contexts_root, exist_ok=True)

    def _dump_one(context: Optional[str]) -> List[Dict[str, str]]:
        # run_in_parallel re-raises the first exception, so swallow everything
        # into the returned error list.
        try:
            output_dir = os.path.join(contexts_root,
                                      _sanitize_context_name(context))
            return kubernetes_debug.dump_context_resources(
                context=context, output_dir=output_dir)
        except Exception as e:  # pylint: disable=broad-except
            return [{
                'resource': 'kubernetes_contexts',
                'error': str(e),
                'traceback': _full_traceback(),
            }]

    num_threads = min(len(unique_contexts), 8)
    results = subprocess_utils.run_in_parallel(_dump_one, unique_contexts,
                                               num_threads)

    if errors is not None:
        for context, ctx_errors in zip(unique_contexts, results):
            sanitized = _sanitize_context_name(context)
            for err in ctx_errors:
                errors.append({
                    'component': 'kubernetes_contexts',
                    'resource': f'{sanitized}/{err["resource"]}',
                    'error': err['error'],
                    'traceback': err['traceback'],
                })


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

        # Get cluster info, history, and events. Cluster history and
        # events outlive the cluster row, so terminated clusters still
        # produce data here.
        try:
            dump_data = debug_dump_helpers.get_cluster_dump_data(cluster_name)
            for filename, content in dump_data:
                file_path = os.path.join(cluster_dir, filename)
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(content, f, indent=2, default=str)
            if dump_data:
                logger.debug(f'Dumped cluster {cluster_name!r} '
                             f'({len(dump_data)} files)')
            else:
                logger.debug(f'Cluster {cluster_name!r} not found in DB or '
                             f'cluster history')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get info for cluster '
                           f'{cluster_name}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'clusters',
                    'resource': cluster_name,
                    'error': str(e),
                    'traceback': _full_traceback()
                })

        # Copy the provision log if available. The path is recorded in
        # cluster history, so this also works for terminated clusters.
        try:
            provision_log_path = (
                global_user_state.get_cluster_history_provision_log_path(
                    cluster_name))
            if provision_log_path:
                provision_log = pathlib.Path(provision_log_path).expanduser()
                if provision_log.is_file():
                    shutil.copy2(provision_log,
                                 os.path.join(cluster_dir, 'provision.log'))
                    logger.debug(
                        f'Copied provision log for cluster {cluster_name!r}')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to copy provision log for cluster '
                           f'{cluster_name}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'clusters',
                    'resource': f'{cluster_name}/provision_log',
                    'error': str(e),
                    'traceback': _full_traceback()
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
                    'error': str(e),
                    'traceback': _full_traceback()
                })

        # Live cluster record for the skylet-log and Kubernetes sections
        # below. None for terminated clusters, which have no reachable
        # node or handle (their history/events were dumped above).
        cluster_record = None
        try:
            cluster_record = global_user_state.get_cluster_from_name(
                cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get cluster record for '
                           f'{cluster_name}: {e}')
            if errors is not None:
                errors.append({
                    'component': 'clusters',
                    'resource': f'{cluster_name}/cluster_record',
                    'error': str(e),
                    'traceback': _full_traceback()
                })

        # Pull the skylet log from the head node. We attempt this for both UP
        # and INIT clusters: an INIT cluster may be degraded (failed setup,
        # partial provisioning) but still have a reachable node with a skylet
        # log, which is exactly when the log is most useful. The only status we
        # skip is STOPPED, which has no reachable node. A not-yet-provisioned
        # cluster simply fails the rsync, which is handled best-effort.
        status = cluster_record.get('status') if cluster_record else None
        handle = cluster_record.get('handle') if cluster_record else None

        if status != status_lib.ClusterStatus.STOPPED and handle is not None:
            _collect_cluster_skylet_log(cluster_name, cluster_dir, handle,
                                        errors, status)
        else:
            logger.debug(f'Skipping skylet log for cluster {cluster_name!r} '
                         f'(status={status})')

        # For Kubernetes clusters, also snapshot the related k8s objects (pods,
        # events, Services, Kueue Workload, ...). Gated only on having a handle,
        # not on status: these come from the kube API server, so they're
        # reachable -- and most useful -- even when the cluster isn't UP.
        if handle is not None:
            _collect_cluster_kubernetes_resources(cluster_name, cluster_dir,
                                                  handle, errors)

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
    Makes a single batched queue_v2 call for all job IDs.
    """
    try:
        all_records, _, _, _ = managed_jobs_core.queue_v2(
            refresh=False, job_ids=list(managed_job_ids), all_users=True)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to fetch managed job queue info: {e}')
        if errors is not None:
            errors.append({
                'component': 'managed_jobs',
                'resource': 'queue_v2_batch',
                'error': str(e),
                'traceback': _full_traceback()
            })
        return

    # Group records by job_id (multi-task jobs return multiple records).
    jobs_by_id: Dict[int, list] = collections.defaultdict(list)
    for record in all_records:
        jobs_by_id[record.get('job_id')].append(record)

    for job_id in managed_job_ids:
        job_dir = os.path.join(jobs_dir, str(job_id))
        os.makedirs(job_dir, exist_ok=True)

        tasks = jobs_by_id.get(job_id, [])
        if not tasks:
            logger.debug(f'Managed job {job_id} not found in queue')
            continue

        for task_idx, job in enumerate(tasks):
            job_info = {
                k: (str(v) if not isinstance(v,
                                             (str, int, float, bool, type(None),
                                              list, dict)) else v)
                for k, v in job.items()
            }
            suffix = f'_task{task_idx}' if len(tasks) > 1 else ''
            job_info_path = os.path.join(job_dir, f'job_info{suffix}.json')
            with open(job_info_path, 'w', encoding='utf-8') as f:
                json.dump(job_info, f, indent=2, default=str)
        logger.debug(f'Dumped managed job {job_id} ({len(tasks)} task(s))')


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
                'error': str(e),
                'traceback': _full_traceback()
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
                'error': str(e),
                'traceback': _full_traceback()
            })
        return

    if manifest is None:
        return

    # Write inline data (small DB-derived JSON)
    dump_dir_resolved = pathlib.Path(dump_dir).resolve()
    for item in manifest.get('inline_data', []):
        relative_path = item.get('relative_path', '')
        target = (dump_dir_resolved / relative_path).resolve()
        try:
            target.relative_to(dump_dir_resolved)
        except ValueError as e:
            logger.error('Skipping unsafe relative_path in manifest: '
                         f'{relative_path} ({e})')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': f'inline/{relative_path}',
                    'error': f'Path traversal: {relative_path} ({e})',
                })
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item['content'], encoding='utf-8')
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
                target = (dump_dir_resolved / relative_path).resolve()
                try:
                    target.relative_to(dump_dir_resolved)
                except ValueError as e:
                    logger.error('Skipping unsafe relative_path in '
                                 f'manifest: {relative_path} ({e})')
                    if errors is not None:
                        errors.append({
                            'component': 'managed_jobs',
                            'resource': f'rsync/{relative_path}',
                            'error': f'Path traversal: {relative_path} ({e})',
                        })
                    return
                local_path = str(target)
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
                                'error': str(e),
                                'traceback': _full_traceback()
                            })

            subprocess_utils.run_in_parallel(_rsync_file, file_path_entries)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to rsync controller debug files: {e}')
            if errors is not None:
                errors.append({
                    'component': 'managed_jobs',
                    'resource': 'controller_rsync',
                    'error': str(e),
                    'traceback': _full_traceback()
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
    recent_minutes: Optional[float],
    client_info: Optional[Dict[str, Any]],
    requested: Dict[str, Any],
) -> None:
    """Build the debug dump contents in dump_dir.

    Populates context via cross-linking, then dumps all sections
    (server info, requests, clusters, managed jobs, client info,
    errors, summary).
    """
    # Populate the context and cross-link related resources. Each helper
    # runs exactly once, and the order is load-bearing:
    # 1. Expand clusters -> jobs BEFORE anything else adds cluster names
    #    to the context (the recent-activity scan, job -> cluster,
    #    request -> cluster), so only user-requested clusters expand into
    #    jobs. A pool worker maps to every job that ever ran on it, so a
    #    later-added worker (e.g. via a pool job's job -> cluster link,
    #    or the recent scan) must not fan back out into all of its jobs.
    # 2. Discover the remaining managed jobs (recent scan, seeded/recent
    #    requests) so the job -> cluster expansion sees the full job set.
    # 3. Add the jobs' own clusters BEFORE expanding clusters into
    #    requests, so requests referencing those clusters are included.
    # 4. Expand clusters -> requests and jobs -> requests.
    # 5. Map requests -> clusters last (requests found via a cluster are
    #    skipped there; see _get_clusters_from_requests). The jobs
    #    controller cluster is also added last: every sky.jobs.* request
    #    carries the controller cluster name, so expanding requests from
    #    it would pull every managed-jobs request into the dump (see
    #    _get_clusters_from_managed_jobs).
    # Note requests found via a cluster are never expanded into jobs:
    # _get_managed_jobs_from_requests runs before
    # _get_requests_from_clusters. Combined with the via_cluster skip in
    # _get_clusters_from_requests, a cluster shared by many requests
    # (e.g. the controller) cannot drag unrelated jobs or clusters in.
    logger.debug('Cross-linking related resources')
    _get_managed_jobs_from_clusters(debug_dump_context)
    if recent_minutes is not None:
        _populate_recent_context(debug_dump_context, recent_minutes)
    _get_managed_jobs_from_requests(debug_dump_context)
    _get_job_clusters_from_managed_jobs(debug_dump_context)
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

    # Dump all sections
    errors = debug_dump_context['errors']
    _dump_server_info(dump_dir, errors=errors)
    # Cluster-wide k8s objects (GPU-metrics pods, Kueue quota config), fetched
    # once per allowed kube context (source of truth: existing_allowed_contexts,
    # so a context with no SkyPilot clusters is still captured).
    _dump_kube_contexts_info(dump_dir, errors=errors)
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
    recent_minutes: Optional[float] = None,
    client_info: Optional[Dict[str, Any]] = None,
) -> pathlib.Path:
    """Create a debug dump for troubleshooting.

    Args:
        request_ids: List of request IDs or prefixes to include in the
            dump. Prefixes are resolved to all matching request IDs.
        cluster_names: List of cluster names to include in the dump.
        managed_job_ids: List of managed job IDs to include in the dump.
        recent_minutes: If specified, include all resources active within
            this many minutes.
        client_info: Optional client-side info to include in the dump.

    Returns:
        Path to the created zip file.
    """
    logger.debug('Starting debug dump creation')
    logger.debug(f'Initial inputs: request_ids={request_ids}, '
                 f'cluster_names={cluster_names}, '
                 f'managed_job_ids={managed_job_ids}, '
                 f'recent_minutes={recent_minutes}')

    # Resolve request ID prefixes to full IDs (same pattern as
    # sky api status in server.py)
    resolved_request_ids: Set[str] = set()
    if request_ids:
        for rid in request_ids:
            matches = requests_lib.get_requests_with_prefix(
                rid, fields=['request_id'])
            if not matches:
                logger.warning(f'No requests found matching prefix {rid!r}')
                continue
            for match in matches:
                resolved_request_ids.add(match.request_id)

    debug_dump_context = DebugDumpContext(
        request_ids=resolved_request_ids,
        cluster_names=set(cluster_names or []),
        managed_job_ids=set(managed_job_ids or []),
        # User-seeded and recent-context requests have no provenance
        # restriction; only requests added by cross-link helpers
        # populate these sidecars (see DebugDumpContext docstring).
        request_ids_via_job=set(),
        request_ids_via_cluster=set(),
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

        # Attach a file handler to capture debug-level logs into the dump
        # itself. We attach to the root 'sky' logger so that logs from all sky.*
        # modules are captured, not just sky.utils.debug_utils.  Also attach to
        # sky.provision which has propagate=False.  This mirrors
        # sky_logging.add_debug_log_handler().
        debug_handler = logging.FileHandler(
            os.path.join(dump_dir, 'debug_dump.log'))
        debug_handler.setFormatter(sky_logging.FORMATTER)
        debug_handler.setLevel(logging.DEBUG)
        sky_root_logger = logging.getLogger('sky')
        provision_logger = logging.getLogger('sky.provision')
        try:
            sky_root_logger.addHandler(debug_handler)
            provision_logger.addHandler(debug_handler)
            # Pass original user inputs so "requested" reflects what the
            # user asked for, even if some IDs didn't resolve.
            original_requested = {
                'request_ids': sorted(request_ids or []),
                'cluster_names': sorted(cluster_names or []),
                'managed_job_ids': sorted(managed_job_ids or []),
                'recent_minutes': recent_minutes,
            }
            _build_debug_dump(dump_dir,
                              debug_dump_context,
                              recent_minutes,
                              client_info,
                              requested=original_requested)
        finally:
            sky_root_logger.removeHandler(debug_handler)
            provision_logger.removeHandler(debug_handler)
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

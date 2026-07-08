"""Role-aware body filters for the SkyPilot API server.

This module provides per-endpoint shims that mutate incoming request
bodies when the caller has the strictly-read-only `viewer` role.  The
viewer endpoint allowlist (in `sky.users.rbac`) is enough to keep
viewers off write endpoints, but a handful of "ambiguous" endpoints
have body fields that swing the action between read and write:

  * `POST /status`: `include_credentials` returns SSH private keys;
    `refresh` queries clouds and mutates state.db.
  * `POST /jobs/queue`, `/jobs/queue/v2`, `/jobs/logs`,
    `/jobs/download_logs`: `refresh` restarts the jobs controller.
  * `GET /volumes`: `refresh` queries cloud volume state.

For viewers, these fields are forced to their read-only / no-side-
effect values *before* the handler runs.  Non-viewer callers see no
behaviour change.

The shim is wired into FastAPI as a `Depends()` dependency rather
than a middleware so it can mutate the parsed pydantic body
in-place; standard Starlette middlewares run before body parsing.
"""

import fastapi

from sky.server.requests import payloads
from sky.users import permission
from sky.users import rbac
from sky.utils import common as common_lib


def _is_viewer(request: fastapi.Request) -> bool:
    """Return True if the authenticated caller has the viewer role.

    Uses the in-memory Casbin enforcer state (no DB roundtrip),
    matching the perf pattern in
    `PermissionService.check_endpoint_permission`.
    """
    auth_user = getattr(request.state, 'auth_user', None)
    if auth_user is None:
        return False
    # Trust the in-memory grouping policy; same source the middleware
    # already consulted to gate this request to here.
    enforcer = permission.permission_service._ensure_enforcer()  # pylint: disable=protected-access
    roles = enforcer.get_roles_for_user(auth_user.id)
    # Admin wins over viewer when both roles are present.
    return (rbac.RoleName.VIEWER.value in roles and
            rbac.RoleName.ADMIN.value not in roles)


def force_viewer_status_body(
    request: fastapi.Request,
    status_body: payloads.StatusBody = fastapi.Body(
        default_factory=payloads.StatusBody),
) -> payloads.StatusBody:
    """Strip side-effecting fields from `POST /status` for viewers.

    Forces:
      * `refresh = NONE` — viewers cannot trigger cloud refresh or DB
        mutations like cluster status updates.
      * `include_credentials = False` — viewers cannot retrieve SSH
        private keys (which would also write the keys to disk if
        missing, see backend_utils.create_ssh_key_files_from_db).
    """
    if _is_viewer(request):
        status_body.refresh = common_lib.StatusRefreshMode.NONE
        status_body.include_credentials = False
    return status_body


def force_viewer_jobs_queue_body(
    request: fastapi.Request,
    jobs_queue_body: payloads.JobsQueueBody,
) -> payloads.JobsQueueBody:
    """Strip `refresh` from `/jobs/queue` for viewers."""
    if _is_viewer(request):
        jobs_queue_body.refresh = False
    return jobs_queue_body


def force_viewer_jobs_queue_v2_body(
    request: fastapi.Request,
    jobs_queue_body_v2: payloads.JobsQueueV2Body,
) -> payloads.JobsQueueV2Body:
    """Strip `refresh` from `/jobs/queue/v2` for viewers."""
    if _is_viewer(request):
        jobs_queue_body_v2.refresh = False
    return jobs_queue_body_v2


def force_viewer_jobs_logs_body(
    request: fastapi.Request,
    jobs_logs_body: payloads.JobsLogsBody,
) -> payloads.JobsLogsBody:
    """Strip `refresh` from `/jobs/logs` for viewers."""
    if _is_viewer(request):
        jobs_logs_body.refresh = False
    return jobs_logs_body


def force_viewer_jobs_download_logs_body(
    request: fastapi.Request,
    jobs_download_logs_body: payloads.JobsDownloadLogsBody,
) -> payloads.JobsDownloadLogsBody:
    """Strip `refresh` from `/jobs/download_logs` for viewers."""
    if _is_viewer(request):
        jobs_download_logs_body.refresh = False
    return jobs_download_logs_body


def force_viewer_volume_refresh(
    request: fastapi.Request,
    refresh: bool = False,
) -> bool:
    """Strip `refresh` from `GET /volumes` (a query param, not a body)."""
    if _is_viewer(request):
        return False
    return refresh

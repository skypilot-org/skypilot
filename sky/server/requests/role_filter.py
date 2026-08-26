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

from typing import Optional

import fastapi

from sky.server.requests import payloads
from sky.users import permission
from sky.users import rbac
from sky.utils import common as common_lib


def request_owner_scope(request: fastapi.Request) -> Optional[str]:
    """The user_id that request-tracking queries must be scoped to.

    Returns ``None`` (meaning "no scope — see every user's requests") when:
      * the API server has no per-user identity for the caller
        (``auth_user`` is unset) — either a single-user/local server, or a
        deployment where auth is terminated upstream (e.g. basic auth at the
        ingress) so no RBAC applies; or
      * the caller is an admin.
    Otherwise returns the caller's own user id, so a non-admin only ever
    sees or acts on their own requests.

    This is the single place that decides request-object visibility; every
    ``/api/{get,stream,status,cancel,completion}`` handler routes its scope
    through here.
    """
    auth_user = getattr(request.state, 'auth_user', None)
    if auth_user is None:
        return None
    # In-memory lookup (no DB roundtrip), matching `_is_viewer`.
    roles = permission.permission_service.roles_in_memory(auth_user.id)
    if rbac.RoleName.ADMIN.value in roles:
        return None
    return auth_user.id


def force_caller_scope_cancel_body(
    request: fastapi.Request,
    request_cancel_body: payloads.RequestCancelBody,
) -> payloads.RequestCancelBody:
    """Bind `POST /api/cancel` to the caller unless they are an admin.

    A non-admin caller may only cancel their own requests, regardless of the
    ``user_id`` they put in the body. Admins (and no-auth servers) keep the
    client-supplied value, so ``--all-users`` (``user_id=None`` => every
    request) still works for them. The strictly-read-only viewer role may not
    cancel anything at all.
    """
    if _is_viewer(request):
        raise fastapi.HTTPException(
            status_code=403, detail='The viewer role cannot cancel requests.')
    scope = request_owner_scope(request)
    if scope is not None:
        request_cancel_body.user_id = scope
    return request_cancel_body


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
    roles = permission.permission_service.roles_in_memory(auth_user.id)
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

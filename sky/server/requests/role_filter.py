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

A second, related family of shims lives here: gates that *reject* a
body field the caller is not entitled to set, rather than rewriting it.
`force_caller_scope_cancel_body` (own requests only) and
`reject_all_users_{cancel,jobs_cancel}_body` (the ``--all-users`` flag on
mutating endpoints, see `rbac.restrict_all_users_mutations`) are of that
kind. They belong here for the same reason: the decision needs the
caller's role, which only the dispatch context has.

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


def all_users_mutations_restricted(request: fastapi.Request) -> bool:
    """Whether this caller is barred from `--all-users` on mutating endpoints.

    True only when the operator has set ``rbac.restrict_all_users_mutations``
    *and* the caller is a restricted principal. "Restricted" reuses
    `request_owner_scope`, which returns None for exactly the two callers the
    rest of RBAC exempts: an admin, and a server with no per-user identity for
    the caller (single-user/local, or auth terminated upstream).

    Also surfaced to clients on ``GET /api/health`` so the CLI can reject
    ``-u`` up front for the down/stop/autostop commands, which expand
    ``--all-users`` into per-cluster requests client-side and so never send the
    flag to the server.
    """
    if not rbac.restrict_all_users_mutations():
        return False
    return request_owner_scope(request) is not None


def _reject_all_users(request: fastapi.Request, all_users: bool,
                      operation: str) -> None:
    """Raises 403 if `all_users` is set and this caller may not use it."""
    if not all_users:
        return
    if not all_users_mutations_restricted(request):
        return
    raise fastapi.HTTPException(
        status_code=403,
        detail=(f'--all-users/-u is not allowed for {operation}: this API '
                'server restricts all-users operations to admins '
                '(rbac.restrict_all_users_mutations). Target your own '
                'resources instead, or ask an administrator.'))


def reject_all_users_cancel_body(
    request: fastapi.Request,
    cancel_body: payloads.CancelBody,
) -> payloads.CancelBody:
    """Gate `POST /cancel` (`sky cancel -u`) on the all-users restriction."""
    _reject_all_users(request, cancel_body.all_users,
                      'cancelling jobs on a cluster')
    return cancel_body


def reject_all_users_jobs_cancel_body(
    request: fastapi.Request,
    jobs_cancel_body: payloads.JobsCancelBody,
) -> payloads.JobsCancelBody:
    """Gate `POST /jobs/cancel` (`sky jobs cancel -u`) on the restriction."""
    _reject_all_users(request, jobs_cancel_body.all_users,
                      'cancelling managed jobs')
    return jobs_cancel_body


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

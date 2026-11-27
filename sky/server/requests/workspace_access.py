"""Which access level a request needs on the caller's active workspace.

Every request runs with an *active workspace* and is gated on it by
``workspaces_core.reject_request_for_unauthorized_workspace``. Two levels
exist:

- ``read``  — "may I use this workspace as my context". Enough for a request
  that only looks at state. A non-member of a workspace whose ``read_access``
  is ``all`` has this level.
- ``write`` — "may I create resources in this workspace". Required by the
  requests that stamp the active workspace onto a *new* resource.

The level is derived from the **endpoint**, in two ordered rules:

0. An endpoint in ``rbac._ALWAYS_WRITE_ENDPOINTS`` (the create endpoints —
   launch, jobs launch, serve up/update, volume apply) always
   needs ``write``, regardless of any viewer-allowlist declaration. This
   short-circuit (``permission.is_read_only_endpoint`` checks
   ``rbac.is_always_write_endpoint`` first) is what stops a wildcard viewer
   entry from relaxing a create endpoint to read.
1. Otherwise: an endpoint declared read-only for the strictly-read-only
   ``viewer`` role (`sky/users/rbac.py::_DEFAULT_VIEWER_ALLOWLIST`,
   ``BasePlugin.viewer_allowlist``, and the operator's
   ``rbac.roles.viewer.permissions.allowlist``) needs only ``read``; anything
   else needs ``write``.

Mutating an *existing* resource is deliberately not what this classifies:
that must be gated on the resource's own workspace (see
``workspaces_core.check_cluster_write_permission``), not on the caller's
active workspace, which may be a different workspace entirely.
"""
import contextvars
from typing import Optional, Tuple

from sky import sky_logging
from sky.users import permission
from sky.workspaces import constants as workspace_constants

logger = sky_logging.init_logger(__name__)

# The (path, method) of the HTTP request currently being dispatched, recorded
# by `APIVersionMiddleware`. A ContextVar is how the existing
# `client_api_version` capture bridges the same gap: the endpoint is only
# known in the FastAPI dispatch context, while the classification is consumed
# by `executor.prepare_request_async` further down the same call chain.
_request_endpoint: contextvars.ContextVar[Optional[Tuple[str, str]]] = (
    contextvars.ContextVar('request_endpoint', default=None))


def set_request_endpoint(path: str, method: str) -> None:
    """Records the endpoint of the request being dispatched."""
    _request_endpoint.set((path, method))


def get_request_endpoint() -> Optional[Tuple[str, str]]:
    """The endpoint of the request being dispatched, if any."""
    return _request_endpoint.get()


def for_current_request() -> str:
    """The access level this request needs on the caller's active workspace.

    Must be called in the FastAPI dispatch context (where the endpoint
    ContextVar is visible), i.e. from `executor.prepare_request_async`. The
    result is stamped onto the request body so the worker process — which
    cannot see the ContextVar — can enforce it.

    Returns:
        `WORKSPACE_ACTION_READ` or `WORKSPACE_ACTION_WRITE`.
    """
    endpoint = get_request_endpoint()
    if endpoint is None:
        # Not dispatched from an HTTP endpoint: internal daemon ticks and
        # direct callers. Daemons run as the system user, which passes either
        # level; fail safe for anything else.
        return workspace_constants.WORKSPACE_ACTION_WRITE
    path, method = endpoint
    try:
        read_only = permission.permission_service.is_read_only_endpoint(
            path, method)
    except Exception as e:  # pylint: disable=broad-except
        # Classification must never be the thing that fails a request; the
        # workspace gate itself will report a real permission problem.
        logger.warning(f'Failed to classify {method} {path} for workspace '
                       f'access, assuming write: {e}')
        return workspace_constants.WORKSPACE_ACTION_WRITE
    if read_only:
        return workspace_constants.WORKSPACE_ACTION_READ
    return workspace_constants.WORKSPACE_ACTION_WRITE

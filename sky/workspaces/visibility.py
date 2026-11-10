"""Which infra targets (Kubernetes/SSH contexts, Slurm clusters) a user sees.

A user may read the targets allowed by the workspaces they can read: those
they are a member of, plus those opened up with ``read_access: all``. Admins
and requests without an authenticated user are unscoped.
"""
import dataclasses
import json
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set

import fastapi

from sky import clouds
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes as kubernetes_adaptor
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.server import common as server_common
from sky.users import permission
from sky.users import rbac
from sky.utils import common_utils
from sky.workspaces import constants as workspace_constants
from sky.workspaces import core as workspaces_core

logger = sky_logging.init_logger(__name__)

# cloud -> the workspace key that lists its allowed targets.
_ALLOWED_KEYS = {
    'kubernetes': 'allowed_contexts',
    'ssh': 'allowed_node_pools',
    'slurm': 'allowed_clusters',
}


@dataclasses.dataclass(frozen=True)
class InfraScope:
    """What one request may read. ``None`` means unscoped.

    An empty set means the user sees nothing; it must never be treated as
    "not given", or it would widen to everything.
    """
    # Kubernetes contexts plus SSH node pool (``ssh-*``) contexts: both are
    # kubeconfig contexts and the Kubernetes code paths accept either.
    kubernetes: Optional[FrozenSet[str]]
    slurm: Optional[FrozenSet[str]]

    @property
    def unscoped(self) -> bool:
        return self.kubernetes is None


UNSCOPED = InfraScope(kubernetes=None, slurm=None)


def _workspace_targets(workspace: str, cloud: str) -> Set[str]:
    # Evaluated under the workspace so its override, the global fallback,
    # `all` and targets that no longer exist resolve exactly as they do for a
    # launch in that workspace.
    try:
        with skypilot_config.local_active_workspace_ctx(workspace):
            if cloud == 'kubernetes':
                targets = clouds.Kubernetes.existing_allowed_contexts(
                    silent=True)
            elif cloud == 'ssh':
                targets = clouds.SSH.existing_allowed_contexts(silent=True)
            else:
                targets = clouds.Slurm.existing_allowed_clusters(silent=True)
        return {t for t in targets if t is not None}
    except Exception as e:  # pylint: disable=broad-except
        # Fail closed for this workspace rather than failing every request.
        logger.warning(f'Failed to list {cloud} targets of workspace '
                       f'{workspace}: {e}')
        return set()


def visible_for_workspaces(workspaces: Iterable[str], cloud: str) -> Set[str]:
    """The union of ``cloud``'s allowed targets over ``workspaces``.

    Pure: no auth and no config refresh, so the executor can use it too.
    """
    key = _ALLOWED_KEYS[cloud]
    configs = skypilot_config.get_nested(('workspaces',), default_value={})
    # The result depends only on a workspace's own value and `disabled`, so
    # evaluate one workspace per distinct pair (each evaluation re-parses the
    # kubeconfig or ~/.slurm/config): there are many workspaces, few values.
    representatives: Dict[str, str] = {}
    for workspace in workspaces:
        cloud_config: Dict[str, Any] = ((configs or {}).get(workspace) or
                                        {}).get(cloud) or {}
        if cloud_config.get('disabled', False):
            continue
        group = json.dumps(cloud_config.get(key, '<global>'),
                           sort_keys=True,
                           default=str)
        representatives.setdefault(group, workspace)
    targets: Set[str] = set()
    for workspace in representatives.values():
        targets |= _workspace_targets(workspace, cloud)
    return targets


def _is_admin(user_id: str) -> bool:
    try:
        roles = permission.permission_service.get_user_roles(user_id)
    except Exception as e:  # pylint: disable=broad-except
        # Fail closed: a role lookup error scopes the user, never widens.
        logger.warning(f'Failed to look up roles of {user_id}; scoping: {e}')
        return False
    return roles == [rbac.RoleName.ADMIN.value]


def scope_for(auth_user: Optional[models.User],
              need_kubernetes: bool = True,
              need_slurm: bool = True) -> InfraScope:
    """The scope of a request by ``auth_user``. API-server process only.

    ``auth_user`` must come from ``request.state``: only there can "no
    authenticated user" be told apart from a real one. Blocking; call it off
    the event loop.
    """
    if auth_user is None:
        return UNSCOPED
    # This process does not get the executor's per-request config reload;
    # without it a revoked target stays visible.
    server_common.refresh_workspace_state_for_sync_handler()
    if _is_admin(auth_user.id):
        return UNSCOPED
    workspaces = list(workspaces_core.workspaces_for_user(auth_user.id))
    kubernetes: FrozenSet[str] = frozenset()
    slurm: FrozenSet[str] = frozenset()
    if need_kubernetes:
        kubernetes = frozenset(
            visible_for_workspaces(workspaces, 'kubernetes') |
            visible_for_workspaces(workspaces, 'ssh'))
    if need_slurm:
        slurm = frozenset(visible_for_workspaces(workspaces, 'slurm'))
    return InfraScope(kubernetes=kubernetes, slurm=slurm)


def not_found(kind: str) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=404,
        detail={
            'error': workspace_constants.INFRA_TARGET_NOT_FOUND,
            'message': f'{kind} not found.',
        })


def require_kubernetes_context(scope: InfraScope, context: str) -> None:
    if scope.kubernetes is not None and context not in scope.kubernetes:
        raise not_found('Kubernetes context')


def require_slurm_cluster(scope: InfraScope, cluster: str) -> None:
    if scope.slurm is not None and cluster not in scope.slurm:
        raise not_found('Slurm cluster')


def current_kubernetes_context() -> Optional[str]:
    """The context a request without one falls back to.

    Mirrors ``get_kubernetes_node_info`` and the Kubernetes catalog.
    """
    context = kubernetes_utils.get_current_kube_config_context_name()
    if context is None and kubernetes_utils.is_incluster_config_available():
        context = kubernetes_adaptor.in_cluster_context_name()
    return context


def slurm_cluster_names() -> List[str]:
    """The Slurm clusters of every workspace the current user can read.

    Runs in the executor, under the request's user.
    """
    user = common_utils.get_current_user()
    workspaces = workspaces_core.workspaces_for_user(user.id)
    return sorted(visible_for_workspaces(workspaces, 'slurm'))

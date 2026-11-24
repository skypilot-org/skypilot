"""Fine-grained permissions and admin-defined roles.

The built-in roles (admin, user, viewer) decide access by endpoint path. This
module adds a second layer for operations that need finer control: a
*permission* names one operation (``node.cordon``), an admin-defined *role* is a
set of permissions, and a *role binding* gives a role to users within a scope
(for example, a list of Kubernetes contexts).

Configured under ``rbac`` in the server config::

    rbac:
      custom_roles:
        node-operator:
          description: Cordon and uncordon nodes.
          permissions: [node.cordon, node.uncordon]
      role_bindings:
        - role: node-operator
          users: [alice@example.com]
          contexts: [my-gpu-cluster]

Permissions are declared in code (by core modules or by plugins through
``BasePlugin.permissions``) with the kind of scope they apply to and the
endpoints that perform them. Each operation is checked twice from the same
declaration: the RBAC middleware lets a request through when the caller holds
the permission in *some* scope (the request body is not parsed there), and the
function that performs the operation checks the exact scope (see
``sky.users.permission.requires``).

To make a new resource kind governable: add a ``ScopeKind`` and its binding key
in ``_SCOPE_KEYS`` (and the matching ``role_bindings`` schema property), then
declare permissions of that kind. Roles, bindings and enforcement are shared.

This module is pure: it holds the data model, the registry and the evaluation of
the config. It does not look up roles or the current user; ``permission.py``
does, so this module stays importable from anywhere.
"""
import dataclasses
import enum
import threading
import typing
from typing import (Any, Dict, FrozenSet, Iterable, List, Mapping, Optional,
                    Sequence, Tuple)

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.utils.db import sqlalchemy_deps

if typing.TYPE_CHECKING:
    import casbin.util as casbin_util
else:
    casbin_util = adaptors_common.LazyImport(
        'casbin.util',
        import_error_message=sqlalchemy_deps.SQLALCHEMY_INSTALL_MSG)

logger = sky_logging.init_logger(__name__)

# Built-in role names a custom role may not reuse. Kept here rather than
# imported from `rbac` so this module has no server-side dependencies.
_RESERVED_ROLE_NAMES = frozenset({'admin', 'user', 'viewer'})


class ScopeKind(enum.Enum):
    """What a permission's grant is limited to."""
    # Applies everywhere; a binding grants it without naming a scope.
    GLOBAL = 'global'
    # An infra context: a Kubernetes context or a Slurm cluster. A binding must
    # list the contexts explicitly; it never grants every context implicitly.
    CONTEXT = 'context'


# The `role_bindings` key that lists the scopes of each kind.
_SCOPE_KEYS: Dict[ScopeKind, str] = {
    ScopeKind.CONTEXT: 'contexts',
}


@dataclasses.dataclass(frozen=True)
class Scope:
    """The resource an operation acts on, for a permission check."""
    kind: ScopeKind
    name: Optional[str] = None

    @classmethod
    def context(cls, name: str) -> 'Scope':
        return cls(ScopeKind.CONTEXT, name)


GLOBAL_SCOPE = Scope(ScopeKind.GLOBAL)


@dataclasses.dataclass(frozen=True)
class Permission:
    """One operation that a role can grant.

    Attributes:
        name: ``<resource>.<verb>``, e.g. ``node.cordon``. Unique.
        scope_kind: What a grant of this permission is limited to.
        description: Shown to admins composing roles.
        endpoints: ``(path, method)`` pairs that perform this operation. Paths
            use the casbin ``keyMatch2`` syntax of the other RBAC rules. The
            RBAC middleware decides these endpoints by this permission alone,
            in place of the caller's built-in role.
    """
    name: str
    scope_kind: ScopeKind
    description: str = ''
    endpoints: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self):
        resource, _, verb = self.name.partition('.')
        if not resource or not verb:
            raise ValueError(f'Permission name {self.name!r} must look like '
                             '<resource>.<verb>.')
        # Requests are matched on the uppercase method.
        object.__setattr__(
            self, 'endpoints',
            tuple((path, method.upper()) for path, method in self.endpoints))


class _Registry:
    """The permissions this process knows about. Per-process, like the viewer
    allowlist: every process that checks permissions builds it at startup."""

    def __init__(self):
        self._lock = threading.Lock()
        self._permissions: Dict[str, Permission] = {}

    def register(self, permission: Permission) -> None:
        with self._lock:
            existing = self._permissions.get(permission.name)
            if existing is not None and existing != permission:
                raise ValueError(
                    f'Permission {permission.name!r} is already registered '
                    'with a different definition.')
            self._permissions[permission.name] = permission

    def get(self, name: str) -> Optional[Permission]:
        return self._permissions.get(name)

    def all(self) -> List[Permission]:
        return list(self._permissions.values())

    def for_endpoint(self, path: str, method: str) -> List[Permission]:
        """The permissions whose endpoints match this request.

        A HEAD request also matches GET endpoints: the server answers HEAD for
        every GET route, so it must not slip past a GET declaration.
        """
        methods = {method, 'GET'} if method == 'HEAD' else {method}
        return [
            perm for perm in list(self._permissions.values()) if any(
                ep_method in methods and casbin_util.key_match2(path, ep_path)
                for ep_path, ep_method in perm.endpoints)
        ]

    def clear(self) -> None:
        """For tests."""
        with self._lock:
            self._permissions = {}


registry = _Registry()


def register(permissions: Iterable[Permission]) -> None:
    for perm in permissions:
        registry.register(perm)


# ---------------------------------------------------------------------------
# Config: custom roles and role bindings.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RoleBinding:
    role: str
    users: FrozenSet[str]
    # Scope names per kind, from the binding's scope keys.
    scopes: Mapping[ScopeKind, FrozenSet[str]]


def get_custom_roles() -> Dict[str, FrozenSet[str]]:
    """Custom role name -> permission names, from `rbac.custom_roles`."""
    configured = skypilot_config.get_nested(
        ('rbac', 'custom_roles'), default_value=None) or {}
    roles: Dict[str, FrozenSet[str]] = {}
    for name, spec in configured.items():
        name = str(name)
        if name.lower() in _RESERVED_ROLE_NAMES:
            logger.warning(f'Custom role {name!r} reuses a built-in role name; '
                           'ignoring it.')
            continue
        roles[name] = frozenset((spec or {}).get('permissions') or [])
    return roles


def get_role_bindings() -> List[RoleBinding]:
    configured = skypilot_config.get_nested(
        ('rbac', 'role_bindings'), default_value=None) or []
    bindings = []
    for spec in configured:
        scopes = {
            kind: frozenset(spec.get(key) or [])
            for kind, key in _SCOPE_KEYS.items()
        }
        bindings.append(
            RoleBinding(role=str(spec['role']),
                        users=frozenset(spec.get('users') or []),
                        scopes=scopes))
    return bindings


def _binding_grants(binding: RoleBinding, permission: Permission,
                    scope: Optional[Scope]) -> bool:
    """Whether this binding's scopes cover `scope` for `permission`.

    `scope=None` asks whether the binding grants the permission anywhere.
    """
    if permission.scope_kind == ScopeKind.GLOBAL:
        return True
    names = binding.scopes.get(permission.scope_kind, frozenset())
    if scope is None:
        return bool(names)
    if scope.kind != permission.scope_kind:
        return False
    return scope.name in names


def bound_users_match(binding: RoleBinding, user_id: str, name_matches) -> bool:
    """Whether the binding names this user, by id or by username.

    `name_matches(entry)` decides whether a username entry identifies the user
    (the caller resolves names, which needs the user table, and must never let
    a name a user chose for themselves, such as a service account's, match).
    """
    if user_id in binding.users:
        return True
    return any(name_matches(entry) for entry in binding.users)


def holds(user_id: str,
          permission: Permission,
          scope: Optional[Scope],
          name_matches,
          *,
          roles: Optional[Mapping[str, FrozenSet[str]]] = None,
          bindings: Optional[Sequence[RoleBinding]] = None) -> bool:
    """Whether a custom-role binding grants `permission` to this user.

    Only custom roles: built-in roles are decided by the caller (admin holds
    every permission). `scope=None` asks whether the user holds the permission
    in any scope, which is what the RBAC middleware needs.
    """
    if roles is None:
        roles = get_custom_roles()
    if bindings is None:
        bindings = get_role_bindings()
    for binding in bindings:
        if permission.name not in roles.get(binding.role, frozenset()):
            continue
        if not _binding_grants(binding, permission, scope):
            continue
        if bound_users_match(binding, user_id, name_matches):
            return True
    return False


def validate_config(config: Mapping[str, Any]) -> List[str]:
    """Problems with `rbac.custom_roles` / `rbac.role_bindings` in `config`.

    Checks only what the schema cannot: references between the two sections
    and to registered permissions. Returned rather than raised, so a server
    with a stale role still starts; the affected grants are simply not given.
    """
    rbac_config = config.get('rbac') or {}
    roles = rbac_config.get('custom_roles') or {}
    problems = []
    for name, spec in roles.items():
        if str(name).lower() in _RESERVED_ROLE_NAMES:
            problems.append(f'custom role {name!r} reuses a built-in name')
        for perm_name in (spec or {}).get('permissions') or []:
            if registry.get(perm_name) is None:
                problems.append(f'custom role {name!r}: unknown permission '
                                f'{perm_name!r}')
    for i, binding in enumerate(rbac_config.get('role_bindings') or []):
        role = binding.get('role')
        if role not in roles:
            problems.append(f'role_bindings[{i}]: unknown role {role!r}')
            continue
        for perm_name in (roles[role] or {}).get('permissions') or []:
            perm = registry.get(perm_name)
            if perm is None or perm.scope_kind == ScopeKind.GLOBAL:
                continue
            key = _SCOPE_KEYS[perm.scope_kind]
            if not binding.get(key):
                problems.append(
                    f'role_bindings[{i}]: role {role!r} has {perm_name!r}, '
                    f'which needs `{key}`; it grants nothing without it')
    return problems

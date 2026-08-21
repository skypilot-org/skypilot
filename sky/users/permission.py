"""Permission service for SkyPilot API Server."""
import contextlib
import hashlib
import logging
import os
import queue
import threading
import time
from typing import Dict, Generator, List, Optional, Set, Tuple

import casbin
from casbin import util as casbin_util
import sqlalchemy_adapter

from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.skylet import constants
from sky.users import rbac
from sky.utils import common
from sky.utils import common_utils
from sky.utils import locks
from sky.utils.db import db_utils
from sky.utils.db import kv_cache
from sky.workspaces import constants as workspace_constants
from sky.workspaces import utils as workspaces_utils

logging.getLogger('casbin.policy').setLevel(sky_logging.ERROR)
logging.getLogger('casbin.role').setLevel(sky_logging.ERROR)
logging.getLogger('casbin.model').setLevel(sky_logging.ERROR)
logging.getLogger('casbin.rbac').setLevel(sky_logging.ERROR)
logger = sky_logging.init_logger(__name__)

# Distributed lock id guarding casbin policy writes.
POLICY_UPDATE_LOCK_ID = 'casbin-policy-update'
POLICY_UPDATE_LOCK_TIMEOUT_SECONDS = 20
# Retry the (short-held) policy lock every 100ms instead of the Postgres lock's
# 1s default, so a contended waiter on the hot path (login seeds a role, each
# reconcile calls update_role / workspace policy writes) doesn't sleep up to ~1s
# after the holder releases. See sky/utils/locks.py PostgresLock.acquire.
POLICY_UPDATE_LOCK_POLL_INTERVAL_SECONDS = 0.1

# Upper bound for `_read_only_endpoint_cache`. The key is the real request path,
# which can carry user-supplied dynamic segments (e.g. `/ssh_node_pools/{name}/
# down`, plugin `:id` routes), so the key space grows with traffic and the cache
# is otherwise only cleared on allowlist rebuild (boot / config reload). Reset
# the whole cache once it exceeds this, trading a rare cold miss (58 regexes)
# for a hard memory bound in a long-lived server process.
_READ_ONLY_ENDPOINT_CACHE_MAX = 4096

# How long a worker remembers "this principal has no role I recognize" before
# paying for another policy load. Bounds the cost of the permanent case (a
# genuinely role-less principal) without letting the transient one (a role
# assigned on another worker moments ago) stay wrong for long.
_NO_ROLE_PROBE_TTL_SECONDS = 30
# Upper bound on that memo, cleared wholesale on overflow like
# `_read_only_endpoint_cache`. Keys are authenticated user ids, so the space is
# bounded by real principals rather than by traffic.
_NO_ROLE_PROBE_CACHE_MAX = 4096
# How often a worker repeats the denial warning for the same principal. Long
# enough that a client retrying in a loop cannot flood the log, short enough
# that the operator sees the condition is ongoing.
_DENIED_LOG_TTL_SECONDS = 300

# Share of a worker's wall time it may spend reloading the policy for
# principals it does not recognize. The per-principal memo does not bound this
# (it expires, and one contended provisioning strands many principals at once),
# and each reload holds the enforcer's write lock. Expressed as a duty cycle so
# it self-tunes to policy size: after a reload takes D, the next waits
# D * (1/duty - 1).
_PROBE_DUTY_CYCLE = 0.1

# Cap on repairs queued but not yet run. The per-principal claim bounds the
# rate; this bounds a burst, so one SCIM push that strands hundreds cannot grow
# an unbounded queue behind a single worker thread.
_REPAIR_MAX_IN_FLIGHT = 64

# Materialized once: the role set is a static enum, and this is consulted on
# every request.
_SUPPORTED_ROLES = frozenset(rbac.get_supported_roles())


def _take_ttl_permit(cache: Dict[str, float], key: str, ttl: float,
                     cap: int) -> bool:
    """Take `key`'s permit for the work behind it, if the last one has expired.

    One permit per key per `ttl`, consumed by taking it: a caller that asks
    twice for one request gets False the second time. The shape behind every
    per-principal rate limit here -- a probe's policy reload, a seed attempt, a
    denial log line.

    Over the cap it drops what has expired, and if that is not enough the oldest
    half of what remains -- so a live permit can be revoked early under
    sustained pressure. Deliberately not the whole cache: these entries all
    expire, so clearing them wholesale would let one eviction send every
    principal back through the work the limit exists to space out.
    """
    now = time.time()
    if now - cache.get(key, 0.0) < ttl:
        return False
    if len(cache) >= cap:
        # Snapshot with list() and remove with pop(): several auth-executor
        # threads reach this at once, and iterating a dict another thread is
        # writing raises, as does deleting a key it already removed. Losing
        # that race should change which entries go, not fail the request.
        for cached, at in list(cache.items()):
            if now - at >= ttl:
                cache.pop(cached, None)
        if len(cache) >= cap:
            # Everything is live and we are still over: drop the oldest half,
            # which costs those principals one extra pass, not all of them.
            oldest = sorted(list(cache.items()), key=lambda kv: kv[1])
            for cached, _ in oldest[:cap // 2]:
                cache.pop(cached, None)
    cache[key] = now
    return True


def _system_user_roles() -> Dict[str, str]:
    """The server's own identities and the role each of them has.

    Fixed, not a default: `sky.users.server` refuses to delete these users or
    change their role, so the policy can only agree or be wrong about them.
    """
    return {
        common.SERVER_ID: rbac.RoleName.ADMIN.value,
        constants.SKYPILOT_SYSTEM_USER_ID: rbac.RoleName.ADMIN.value,
        constants.SKYPILOT_SYSTEM_VIEWER_USER_ID: rbac.RoleName.VIEWER.value,
    }


def _recognized_roles(roles: List[str]) -> List[str]:
    """The subset of `roles` the policy system actually knows about.

    An unrecognized name is not a weaker role, it is no role: nothing in the
    blocklist mentions it, so `enforce` permits it everywhere. Databases
    predating the update-role validation can still hold one.
    """
    return [role for role in roles if role in _SUPPORTED_ROLES]


def _system_role(user_id: str) -> Optional[str]:
    """The role this identity has by definition, or None if it is not one.

    Checked before the policy, so the server can authorize its own calls
    whatever the policy holds -- and without a reload.
    """
    return _system_user_roles().get(user_id)


_enforcer_instance: Optional['PermissionService'] = None

# KV cache constants for workspace permission checks.
_WORKSPACE_PERM_CACHE_PREFIX = 'perm:ws:'
_WORKSPACE_PERM_CACHE_KEY_SEP = ':'
# Long TTL as safety net; primary freshness is explicit invalidation on update.
_WORKSPACE_PERM_CACHE_TTL_SECONDS = 60 * 60  # 1h


class PermissionService:
    """Permission service for SkyPilot API Server."""

    def __init__(self):
        self.enforcer: Optional[casbin.SyncedEnforcer] = None
        self._lock = threading.Lock()
        # Viewer role's endpoint allowlist, materialised at boot.
        self._viewer_allowlist: List[tuple] = []
        # Endpoints that only read, for workspace-access purposes: the viewer
        # allowlist plus the entries the viewer role cannot express. Also
        # materialised at boot. See `rbac.get_read_only_endpoints`.
        self._read_only_endpoints: List[tuple] = []
        # Per-(path, method) memo for `is_read_only_endpoint`, which runs on
        # every request in the main event loop. Cleared whenever the allowlist
        # is rebuilt (`_build_viewer_allowlist_no_lock`).
        self._read_only_endpoint_cache: Dict[Tuple[str, str], bool] = {}
        # user_id -> when this worker last reloaded on their behalf. See
        # `_probe_unknown_principal`.
        self._no_role_probe_cache: Dict[str, float] = {}
        # user_id -> when this worker last tried to seed a missing role. See
        self._seed_attempt_cache: Dict[str, float] = {}
        # user_id -> when this worker last logged a denial for them. See
        # `_log_denied_principal`.
        self._denied_log_cache: Dict[str, float] = {}
        # Principals this worker has already seen hold a role. Read on the
        # event loop by `probably_has_role`, which must not touch the enforcer.
        self._role_seen: Set[str] = set()
        # When this worker may next reload on an unrecognized principal's
        # behalf, and the lock guarding it. See `_probe_unknown_principal`.
        self._probe_cooldown_until: float = 0.0
        self._probe_lock = threading.Lock()
        # Queued repairs, the worker draining them, and the lock over both --
        # started on first use so importing this module (the CLI does) never
        # starts a thread. See `_schedule_role_repair`.
        self._repair_queue: 'queue.Queue[str]' = queue.Queue()
        self._repair_in_flight: Set[str] = set()
        self._repair_worker: Optional[threading.Thread] = None
        self._repair_lock = threading.Lock()
        # When the in-flight cap warning was last emitted. A timestamp, not one
        # of the TTL caches: the condition is global, so there is nothing to key
        # by and no cache to bound. Read and written under `_repair_lock`.
        self._cap_warned_at: float = 0.0

    def initialize(self):
        self._lazy_initialize(full_initialize=True)

    def _lazy_initialize(self, full_initialize: bool = False):
        if self.enforcer is not None:
            return
        with self._lock:
            if self.enforcer is not None:
                return
            global _enforcer_instance
            if _enforcer_instance is None:
                engine = global_user_state.initialize_and_get_db()
                if full_initialize:
                    db_utils.add_all_tables_to_db_sqlalchemy(
                        sqlalchemy_adapter.Base.metadata, engine)
                adapter = sqlalchemy_adapter.Adapter(
                    engine, db_class=sqlalchemy_adapter.CasbinRule)
                model_path = os.path.join(os.path.dirname(__file__),
                                          'model.conf')
                # Use SyncedEnforcer for thread safety. It uses a
                # read-write lock internally: concurrent reads (enforce,
                # get_roles_for_user) take a shared read lock, while
                # writes (load_policy, add_policy) take an exclusive
                # write lock. This prevents the RuntimeError from
                # concurrent iteration/mutation of RoleManager.all_roles.
                enforcer = casbin.SyncedEnforcer(model_path, adapter)
                self.enforcer = enforcer
                # Only set the enforcer instance once the enforcer
                # is successfully initialized, if we change it and then fail
                # we will set it to None and all subsequent calls will fail.
                _enforcer_instance = self
                if full_initialize:
                    with _policy_lock():
                        self._maybe_initialize_policies()
                        self._maybe_initialize_basic_auth_user()
            else:
                assert _enforcer_instance is not None
                self.enforcer = _enforcer_instance.enforcer
            # The viewer allowlist is in-process state (not stored in
            # casbin). It MUST be populated in every process that handles
            # requests.
            self._build_viewer_allowlist_no_lock()

    def _ensure_enforcer(self) -> casbin.SyncedEnforcer:
        """Ensure enforcer is initialized and return it."""
        self._lazy_initialize()
        assert self.enforcer is not None, (
            'Enforcer should be initialized after _lazy_initialize()')
        return self.enforcer

    def _get_plugin_rbac_rules(self):
        """Get RBAC rules from loaded plugins.

        Returns:
            Dictionary of plugin RBAC rules, or empty dict if plugins module
            is not available or no rules are defined.
        """
        try:
            # pylint: disable=import-outside-toplevel
            from sky.server import plugins as server_plugins
            return server_plugins.get_plugin_rbac_rules()
        except ImportError:
            # Plugin module not available (e.g., not running as server)
            logger.debug(
                'Plugin module not available, skipping plugin RBAC rules')
            return {}
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get plugin RBAC rules: {e}')
            return {}

    def _get_plugin_viewer_allowlist(self) -> List[dict]:
        """Get viewer-allowlist entries from loaded plugins.

        Lazily populates the module-level plugin allowlist cache if
        it's empty — this matters in uvicorn worker processes which
        re-import `sky.server.plugins` from scratch and would otherwise
        see an empty cache (only the main server process calls
        `load_plugin_viewer_allowlist()` at startup).

        Returns:
            List of `{path, method}` records, or empty list if plugins
            module is not available or no rules are defined.
        """
        try:
            # pylint: disable=import-outside-toplevel
            from sky.server import plugins as server_plugins
            cached = server_plugins.get_plugin_viewer_allowlist()
            if cached:
                return cached
            # Cache empty — could be either "no plugin entries" or
            # "loader hasn't run in this process". Try to populate it;
            # `load_plugin_viewer_allowlist` is side-effect-free
            # (instantiates each plugin but doesn't call install) and
            # idempotent.
            try:
                return server_plugins.load_plugin_viewer_allowlist()
            except AttributeError:
                return cached
        except ImportError:
            logger.debug('Plugin module not available, '
                         'skipping plugin viewer allowlist')
            return []
        except AttributeError:
            # Old plugin module that doesn't export this loader.
            logger.debug('Plugin module does not expose '
                         'get_plugin_viewer_allowlist; skipping')
            return []
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get plugin viewer allowlist: {e}')
            return []

    def _build_viewer_allowlist_no_lock(self) -> None:
        """Build the endpoint allowlists from defaults + plugin entries.

        Populates both `self._viewer_allowlist` (viewer role) and
        `self._read_only_endpoints` (workspace-access classification), which
        share the same plugin lookup.

        Read-only with respect to casbin/DB state — no policy lock
        required. Safe to call from any process (main server or uvicorn
        worker); the result is per-process in-memory state.
        """
        plugin_viewer_allow = self._get_plugin_viewer_allowlist()
        self._viewer_allowlist = [(rule['path'], rule['method'])
                                  for rule in rbac.get_viewer_allowlist(
                                      plugin_allowlist=plugin_viewer_allow)]
        self._read_only_endpoints = [(rule['path'], rule['method'])
                                     for rule in rbac.get_read_only_endpoints(
                                         plugin_allowlist=plugin_viewer_allow)]
        self._read_only_endpoint_cache = {}
        logger.debug(f'Viewer allowlist has {len(self._viewer_allowlist)} '
                     f'entries, read-only endpoints '
                     f'{len(self._read_only_endpoints)}')

    def _maybe_initialize_basic_auth_user(self) -> None:
        """Initialize basic auth user if it is enabled.

        Caller holds `_policy_lock()`.
        """
        basic_auth = os.environ.get(constants.SKYPILOT_INITIAL_BASIC_AUTH)
        if not basic_auth:
            return
        username, password = basic_auth.split(':', 1)
        if username and password:
            # MD5 only derives a stable user id from the (non-secret)
            # username; the password is checked separately. Not a security use.
            user_hash = hashlib.md5(username.encode(), usedforsecurity=False
                                   ).hexdigest()[:common_utils.USER_HASH_LENGTH]
            user_info = global_user_state.get_user(user_hash)
            if user_info:
                logger.debug(f'Basic auth user {username} already exists')
                return
            global_user_state.add_or_update_user(
                models.User(id=user_hash,
                            name=username,
                            password=password,
                            user_type=models.UserType.BASIC.value))
            enforcer = self._ensure_enforcer()
            # Same reason as `_maybe_initialize_policies`: `save_policy()`
            # rewrites the whole table from this model, which predates the
            # lock unless refreshed.
            self._load_policy_no_lock()
            enforcer.add_grouping_policy(user_hash, rbac.RoleName.ADMIN.value)
            enforcer.save_policy()
            logger.info(f'Basic auth user {username} initialized')

    def _maybe_initialize_policies(self) -> None:
        """Initialize policies if they don't already exist.

        Caller holds `_policy_lock()`.
        """
        logger.debug(f'Initializing policies in process: {os.getpid()}')

        # The model was loaded by the enforcer's constructor, before the lock
        # was acquired -- and waiting for it can take seconds. Anything another
        # replica wrote in that window is missing here, and this method both
        # ends in a full-table `save_policy()` and deletes whatever it
        # considers redundant, so it would erase those writes rather than
        # merely miss them.
        self._load_policy_no_lock()

        policy_updated = False

        # Check if policies are already initialized by looking for existing
        # permission policies in the enforcer
        enforcer = self._ensure_enforcer()
        # Convert existing policies to set of tuples for O(1) lookups
        existing_policies = {tuple(p) for p in enforcer.get_policy()}

        # Get plugin RBAC rules dynamically
        plugin_rules = self._get_plugin_rbac_rules()

        # Viewer allowlist is built in `_lazy_initialize` (called above
        # via `_ensure_enforcer`) so worker processes that never reach
        # this method still get it. Operator-config changes to
        # `rbac.roles.viewer.permissions.allowlist` still require a
        # server restart — same semantics as the existing blocklist
        # for the user role.

        # If we already have policies for the expected roles, skip
        # initialization
        role_permissions = rbac.get_role_permissions(plugin_rules=plugin_rules)
        expected_policies = []
        for role, permissions in role_permissions.items():
            if permissions.get('permissions'
                              ) and 'blocklist' in permissions['permissions']:
                blocklist = permissions['permissions']['blocklist']
                for item in blocklist:
                    expected_policies.append(
                        (role, item['path'], item['method']))

        # Add workspace policy
        workspace_policy_permissions = rbac.get_workspace_policy_permissions()
        logger.debug(f'Workspace policy permissions from config: '
                     f'{workspace_policy_permissions}')

        for workspace_name, users in workspace_policy_permissions.items():
            for user in users:
                expected_policies.append((user, workspace_name, '*'))
        # Check if all expected policies already exist and find missing ones
        missing_policies = [
            p for p in expected_policies if p not in existing_policies
        ]
        # Find policies to remove
        expected_policies_set = set(expected_policies)
        redundant_policies = [
            p for p in existing_policies if p not in expected_policies_set
        ]
        if missing_policies:
            # Add missing policies
            logger.debug(f'Found {len(missing_policies)} missing policies, '
                         'initializing...')
            for p in missing_policies:
                logger.debug(f'Adding policy: {p}')
                enforcer.add_policy(*p)
                policy_updated = True
            logger.debug('Missing policies added successfully')

        if redundant_policies:
            # Remove redundant policies
            logger.debug(f'Found {len(redundant_policies)} redundant policies, '
                         'cleaning up...')
            for p in redundant_policies:
                logger.debug(f'Removing policy: {p}')
                enforcer.remove_policy(*p)
                policy_updated = True
            logger.debug('Redundant policies removed successfully')

        if not missing_policies and not redundant_policies:
            logger.debug('Policies already in sync, skipping initialization')

        # Always ensure users have default roles (this is idempotent)
        # Get users who already have roles (g policies) to avoid redundant calls
        users_with_roles = {tuple(g)[0] for g in enforcer.get_grouping_policy()}
        all_users = global_user_state.get_all_users()
        for existing_user in all_users:
            if existing_user.id in _system_user_roles():
                # Their explicit role is seeded below. Letting the default land
                # first would win, because the seed only fills a gap.
                continue
            if str(existing_user.id) not in users_with_roles:
                logger.debug(f'Adding role for user: {existing_user.name}'
                             f'({existing_user.id})')
                user_added = self._add_user_if_not_exists_no_lock(
                    existing_user.id)
                policy_updated = policy_updated or user_added
        for system_user_id, system_user_role in _system_user_roles().items():
            global_user_state.add_or_update_user(
                models.User(id=system_user_id,
                            name=system_user_id,
                            user_type=models.UserType.SYSTEM.value))
            if system_user_id not in users_with_roles:
                logger.debug(f'Adding role for system user: {system_user_id} '
                             f'({system_user_role})')
                user_added = self._add_user_if_not_exists_no_lock(
                    system_user_id, system_user_role)
                policy_updated = policy_updated or user_added
        if policy_updated:
            enforcer.save_policy()

    def add_user_if_not_exists(self,
                               user_id: str,
                               role: Optional[str] = None) -> None:
        """Add user role relationship. `role` overrides the default role."""
        self._lazy_initialize()
        with _policy_lock():
            # Refresh before deciding: the callee reads this process's
            # in-memory model, which may predate another worker's write, and
            # would then add a *second* role for a user who already has one.
            # Deliberately here rather than in the callee, which
            # `_maybe_initialize_policies` calls once per user in a loop.
            self._load_policy_no_lock()
            self._add_user_if_not_exists_no_lock(user_id, role)

    def _add_user_if_not_exists_no_lock(self,
                                        user_id: str,
                                        role: Optional[str] = None) -> bool:
        """Add user role relationship without lock.

        Returns:
            True if the user was added, False otherwise.
        """
        enforcer = self._ensure_enforcer()
        user_roles = enforcer.get_roles_for_user(user_id)
        if not user_roles:
            enforcer.add_grouping_policy(user_id, role or
                                         rbac.get_default_role())
            return True
        return False

    def delete_user(self, user_id: str) -> None:
        """Remove every policy naming a user: their role and workspace grants.

        User ids are derived from the username, so a delete followed by a
        recreate reuses the id -- anything left behind is silently inherited by
        the next holder. Both row types are keyed by user id at field 0, and a
        filtered removal also clears duplicates, which a loop over the
        deduplicated role list cannot reach.
        """
        with _policy_lock():
            self._load_policy_no_lock()
            enforcer = self._ensure_enforcer()
            removed_roles = enforcer.remove_filtered_grouping_policy(0, user_id)
            # Private-workspace membership is a `p` row (user, workspace, '*').
            # Role names also sit at field 0 of `p` rows (the blocklist rules),
            # so match the '*' action as well instead of field 0 alone: no user
            # id is a role name today, but taking out a role's whole blocklist
            # would leave that role unrestricted rather than merely wrong. ''
            # matches any value, in the model and in the sqlalchemy adapter.
            removed_grants = enforcer.remove_filtered_policy(
                0, user_id, '', '*')
            if not removed_roles and not removed_grants:
                # Nothing to persist, and nothing to invalidate.
                return
            enforcer.save_policy()
            self.invalidate_user_permission_cache(user_id)

    def update_role(self, user_id: str, new_role: str) -> None:
        """Replace every role held by a user with `new_role`."""
        with _policy_lock():
            self._load_policy_no_lock()
            enforcer = self._ensure_enforcer()
            if enforcer.get_roles_for_user(user_id) == [new_role]:
                return
            # Replace, don't add: a user is only ever meant to hold one role,
            # and a leftover `admin` would survive a demotion -- silently
            # restoring what the demotion took away, since the blocklist check
            # lets admin win over the other role.
            enforcer.remove_filtered_grouping_policy(0, user_id)
            enforcer.add_grouping_policy(user_id, new_role)
            enforcer.save_policy()
            # Always invalidate: even a first role assignment can grant
            # workspace access that was previously denied and cached.
            self.invalidate_user_permission_cache(user_id)

    def get_user_roles(self, user_id: str) -> List[str]:
        """Get the roles directly assigned to a user.

        Roles do not inherit from one another: the only grouping policies
        this module ever writes are `(user_id, role)`, never `(role, role)`,
        and `get_roles_for_user` does not expand transitively anyway (that
        would be `get_implicit_roles_for_user`). Every user therefore has
        exactly zero or one role in practice, since `update_role` replaces
        rather than adds. Callers deciding *authorization* should not treat a
        second role as additive — see `sky.users.server._caller_is_admin`.

        Args:
            user: The user ID to get roles for.

        Returns:
            A list of role names that the user has.
        """
        system_role = _system_role(user_id)
        if system_role is not None:
            # Skips the reload as well, which the server's own identities would
            # otherwise pay on every internal call.
            return [system_role]
        self._load_policy_no_lock()
        enforcer = self._ensure_enforcer()
        return enforcer.get_roles_for_user(user_id)

    def roles_in_memory(self, user_id: str) -> List[str]:
        """This process's current view of a principal's roles, no reload.

        For decisions on the request path, which cannot afford the database
        roundtrip `get_user_roles` makes. Resolves identities the same way, so
        a caller that reads this cannot reach a different conclusion about a
        principal than one that reads `get_user_roles`.
        """
        system_role = _system_role(user_id)
        if system_role is not None:
            return [system_role]
        return self._ensure_enforcer().get_roles_for_user(user_id)

    def get_users_for_role(self, role: str) -> List[str]:
        """Get all users for a role."""
        self._load_policy_no_lock()
        enforcer = self._ensure_enforcer()
        return enforcer.get_users_for_role(role)

    def get_accessible_workspace_names(
            self,
            user_id: str,
            workspace_names: Set[str],
            action: str = workspace_constants.WORKSPACE_ACTION_WRITE
    ) -> Set[str]:
        """Return workspace names the user can access (batch, O(1) enforcer).

        Use instead of check_workspace_permission in a loop when filtering
        many workspaces, to avoid N enforcer calls.

        Args:
            action: 'write' (default) returns only workspaces the user can
                mutate (the member '*' grants) -- the historical meaning of
                "accessible", and the right answer for any caller that offers
                the list as a place to act. 'read' additionally includes
                workspaces that are read-only-visible to non-members (evaluated
                live from config, not a materialized grant); pass it explicitly
                for visibility-only uses such as resource listings and
                ``GET /workspaces``.
        """
        writable = self._writable_workspace_names(user_id, workspace_names)
        if writable is None:
            # Server-off or admin: full access to everything requested.
            return workspace_names
        if action == workspace_constants.WORKSPACE_ACTION_READ:
            return writable | self._read_only_visible(workspace_names)
        return writable

    def get_workspace_access_sets(
            self, user_id: str,
            workspace_names: Set[str]) -> Tuple[Set[str], Set[str]]:
        """Return ``(readable, writable)`` workspace names in one policy scan.

        Equivalent to calling `get_accessible_workspace_names` with
        ``action='read'`` and ``action='write'``, but scans the casbin policy
        once instead of twice. Used by callers that need both sets: the hot
        ``GET /workspaces`` path (dashboard polling, what-to-show vs what-is-
        writable) and ``GET /users/me/workspace`` (accessible vs read-only).
        """
        writable = self._writable_workspace_names(user_id, workspace_names)
        if writable is None:
            # Server-off or admin: everything requested is both readable and
            # writable. Same object twice is fine -- callers only test
            # membership / iterate, never mutate.
            return workspace_names, workspace_names
        readable = writable | self._read_only_visible(workspace_names)
        return readable, writable

    def _writable_workspace_names(
            self, user_id: str,
            workspace_names: Set[str]) -> Optional[Set[str]]:
        """Workspace names the user can mutate (member '*' grants), one scan.

        Returns None to mean "all of ``workspace_names``" (server-off or admin),
        so callers short-circuit without materializing the set.

        NOTE: this only matches direct (user_id, workspace, '*') and wildcard
        ('*', workspace, '*') policies. It does NOT traverse casbin role
        hierarchies (the g() function in the model matcher). If role-based
        workspace grants are ever added, this method must be updated to use
        enforcer.enforce() per workspace or expand roles via
        enforcer.get_implicit_permissions_for_user().
        """
        if os.getenv(constants.ENV_VAR_IS_SKYPILOT_SERVER) is None:
            return None
        roles = self.get_user_roles(user_id)
        if rbac.RoleName.ADMIN.value in roles:
            return None
        enforcer = self._ensure_enforcer()
        writable = set()
        for rule in enforcer.get_policy():
            if len(rule) >= 3 and rule[2] == '*' and (rule[0] == user_id or
                                                      rule[0] == '*'):
                if rule[1] in workspace_names:
                    writable.add(rule[1])
        return writable

    @staticmethod
    def _read_only_visible(workspace_names: Set[str]) -> Set[str]:
        """Requested workspaces that are read-only-visible to non-members.

        Evaluated live from config (not a materialized casbin grant) so changes
        take effect immediately. Mirrors the read branch of
        check_workspace_permission.
        """
        return (workspaces_utils.get_read_only_workspace_names() &
                workspace_names)

    def _probe_unknown_principal(self, user_id: str) -> List[str]:
        """Reload once for a principal with no role here, and re-read it.

        Only the database distinguishes "assigned elsewhere" from "never
        seeded", so pay for one load -- bounded per principal by TTL and per
        worker by `_PROBE_DUTY_CYCLE`. A failed reload answers "no roles" (the
        caller denies): raising would leave the middleware with a bare 500.
        """
        roles = lambda: self._ensure_enforcer().get_roles_for_user(user_id)
        if not _take_ttl_permit(self._no_role_probe_cache, user_id,
                                _NO_ROLE_PROBE_TTL_SECONDS,
                                _NO_ROLE_PROBE_CACHE_MAX):
            return roles()
        with self._probe_lock:
            if time.time() < self._probe_cooldown_until:
                # Another principal's probe is paying for the whole worker
                # right now. Theirs refreshes the model this one reads.
                return roles()
            self._probe_cooldown_until = float('inf')
        started = time.time()
        try:
            self._load_policy_no_lock()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Policy reload for unrecognized principal '
                           f'{user_id} failed; treating them as role-less: '
                           f'{common_utils.format_exception(e)}')
            return []
        finally:
            elapsed = time.time() - started
            with self._probe_lock:
                self._probe_cooldown_until = time.time() + elapsed * (
                    1.0 / _PROBE_DUTY_CYCLE - 1.0)
        return roles()

    def _schedule_role_repair(self, user_id: str) -> None:
        """Queue a seed for a principal the policy holds no role for.

        Off the request because the caller runs inside the auth executor's 5s
        deadline while the policy lock waits up to 20s: seeding inline would
        answer 503 under the very contention that strands principals. This
        request stays denied; the next one finds a role.
        """
        with self._repair_lock:
            # Replaced first, because whether the worker died is orthogonal to
            # whether this principal gets queued. Every return below leaves the
            # queue holding repairs nobody reads, and a full in-flight set --
            # which is what a dead worker produces -- sends every caller into
            # one of those returns. A restart placed after them never runs, and
            # the cap warning below stays the only symptom, forever. Only a
            # BaseException can get the drain loop into this state.
            if (self._repair_worker is not None and
                    not self._repair_worker.is_alive()):
                logger.warning('The role repair worker died; replacing it.')
                self._start_repair_worker()
            # Already queued before the cap: otherwise a principal that is
            # waiting its turn gets logged as one that was turned away.
            if user_id in self._repair_in_flight:
                return
            if len(self._repair_in_flight) >= _REPAIR_MAX_IN_FLIGHT:
                # Rate-limited like the denial log, and for the same reason:
                # this is the line that tells an operator repairs are being
                # dropped, so a burst must not repeat it per request.
                now = time.time()
                if now - self._cap_warned_at >= _DENIED_LOG_TTL_SECONDS:
                    self._cap_warned_at = now
                    logger.warning(
                        f'Not queueing a role repair for {user_id}: '
                        f'{_REPAIR_MAX_IN_FLIGHT} already pending. Something '
                        f'is stranding principals faster than they can be '
                        f'repaired.')
                return
            # Claimed last: a claim spent while the queue was full would make
            # this principal wait out a TTL for a repair that never ran.
            if not self.claim_role_seed_attempt(user_id):
                return
            self._repair_in_flight.add(user_id)
            try:
                if self._repair_worker is None:
                    self._start_repair_worker()
            except Exception:
                # Rolled back, or a failed thread start strands this principal
                # for the life of the process: the entry below is what dedups
                # later attempts, and only the drain worker removes it -- and
                # there is no worker.
                self._repair_in_flight.discard(user_id)
                raise
        self._repair_queue.put(user_id)

    def _start_repair_worker(self) -> None:
        """Attach a drain thread. The caller holds `_repair_lock`."""
        self._repair_worker = threading.Thread(target=self._drain_role_repairs,
                                               name='role-repair',
                                               daemon=True)
        self._repair_worker.start()

    def _drain_role_repairs(self) -> None:
        """Run queued repairs one at a time, forever.

        One worker on purpose: each repair takes the distributed policy lock,
        and the contention that strands principals is what running them in
        parallel would feed. A daemon thread, so a repair waiting on that lock
        cannot delay interpreter exit -- the lock is released either way when
        the process dies.
        """
        while True:
            user_id = self._repair_queue.get()
            try:
                self._run_role_repair(user_id)
            finally:
                # The queue owns the in-flight bookkeeping, not the repair
                # itself: a caller that stubs the repair body would otherwise
                # leave the principal marked as queued forever.
                with self._repair_lock:
                    self._repair_in_flight.discard(user_id)
                self._repair_queue.task_done()

    def _run_role_repair(self, user_id: str) -> None:
        """Body of a queued repair. Never raises: nothing awaits it."""
        try:
            if not self.role_seed_missing(user_id):
                return
            logger.warning(f'User {user_id} has no role; seeding one now. '
                           f'Their original seed did not complete.')
            seed_new_user_role(user_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Queued role repair for {user_id} failed; a later '
                           f'request retries: '
                           f'{common_utils.format_exception(e)}')

    def _log_denied_principal(self, user_id: str, path: str,
                              method: str) -> None:
        """Report a denial for a principal with no recognized role.

        Rate-limited per principal per worker: the state is persistent, so an
        unrated log would repeat for every request such a principal makes. The
        point of denying rather than confining is that this is loud, so it must
        actually reach the operator without drowning the log.
        """
        if not _take_ttl_permit(self._denied_log_cache, user_id,
                                _DENIED_LOG_TTL_SECONDS,
                                _NO_ROLE_PROBE_CACHE_MAX):
            return
        logger.warning(
            f'Denying {method} {path} for user {user_id}: the policy holds no '
            f'role for them, so nothing grants access. Their role seed did not '
            f'complete; assign a role to restore access.')

    def probably_has_role(self, user_id: str) -> bool:
        """Cheap, non-blocking: has this worker ever seen a role for them?

        A set lookup, deliberately not `enforcer.get_roles_for_user`: this runs
        on the event loop, and that takes a lock a concurrent `load_policy()`
        holds for the length of a full reload. False means "not known to have
        one", not "has none" -- confirm off the loop.
        """
        return user_id in self._role_seen

    def remember_role_seen(self, user_id: str) -> None:
        """Record that this principal holds a role, for `probably_has_role`."""
        if len(self._role_seen) >= _NO_ROLE_PROBE_CACHE_MAX:
            # Half, not all: clearing sends every principal back through a
            # confirmation off the loop at the same moment. No timestamps in a
            # set, so which half is arbitrary.
            for stale in list(self._role_seen)[:_NO_ROLE_PROBE_CACHE_MAX // 2]:
                self._role_seen.discard(stale)
        self._role_seen.add(user_id)

    def role_seed_missing(self, user_id: str) -> bool:
        """Whether the policy holds no role for this principal, so one is owed.

        A query, not a claim -- ask `claim_role_seed_attempt` for permission to
        act on the answer. False for a principal holding an *unrecognized*
        role: seeding only ever fills a gap, so acting there would take the
        distributed lock and repair nothing, and replacing a role nobody
        recognizes is an administrative act rather than something a login does
        behind the operator's back. `check_endpoint_permission` denies them.

        Also feeds `probably_has_role`'s memo, so the loop-side guard can stay a
        set lookup.

        Blocking (may reload the policy). Call from a worker thread.
        """
        if _system_role(user_id) is not None:
            return False
        enforcer = self._ensure_enforcer()
        if enforcer.get_roles_for_user(user_id):
            self.remember_role_seen(user_id)
            return False
        if self._probe_unknown_principal(user_id):
            # Only this worker was behind; the role exists.
            self.remember_role_seen(user_id)
            return False
        return True

    def claim_role_seed_attempt(self, user_id: str) -> bool:
        """Take this worker's per-principal permission to attempt a seed.

        True at most once per TTL per principal, and it *consumes* that
        permission -- so a caller that asks twice for one request gets False the
        second time and silently skips the repair. Rate-limiting the attempt
        rather than only the probe matters because a seed that keeps failing on
        a contended policy lock would otherwise retry on every request and feed
        the contention that caused it.

        Cheap and non-blocking: in-memory only.
        """
        return _take_ttl_permit(self._seed_attempt_cache, user_id,
                                _NO_ROLE_PROBE_TTL_SECONDS,
                                _NO_ROLE_PROBE_CACHE_MAX)

    def queue_role_repair(self, user_id: str) -> None:
        """Hand a role-less principal to the off-request repair queue.

        Cheap and non-blocking -- an in-memory claim and a queue put -- so an
        async caller may do this inline. That is the point: the seed takes the
        distributed policy lock, which waits up to
        `POLICY_UPDATE_LOCK_TIMEOUT_SECONDS`, and the contention that strands
        principals is exactly when a login would be waiting for it. This
        request is unaffected either way; the next one finds a role.

        Never raises, so callers on the login and provisioning paths need no
        guard of their own: queueing cannot fail for lock reasons, but starting
        the drain thread can (`RuntimeError` when the process is out of
        threads), and a repair nobody is waiting for must not turn a login into
        a 500. The next caller retries.
        """
        try:
            self._schedule_role_repair(user_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Could not queue a role repair for {user_id}; a '
                           f'later request retries: '
                           f'{common_utils.format_exception(e)}')

    def check_endpoint_permission(self, user_id: str, path: str,
                                  method: str) -> bool:
        """Check permission.

        Return True to BLOCK the request (RBAC middleware turns truthy
        return into 403). Return False to allow.

        Admin / user roles use the Casbin blocklist semantics:
        True iff a `(role, path, method)` policy matches.

        Viewer role uses an in-memory allowlist:
        True (block) unless the (path, method) matches an entry in
        `self._viewer_allowlist`.

        A principal holding no role this model recognizes is denied, the way
        every mainstream authorization system treats an unbound principal.
        Under blocklist semantics an unknown or absent role matches no policy
        at all, which `enforce` reads as permitted everywhere -- so the
        unconfigured case must not be the permissive one.
        """
        # We intentionally don't load the policy here, as it is a hot path, and
        # we don't support updating the policy.
        # We don't hold the lock for checking permission, as it is read only and
        # it is a hot path in every request. It is ok to have a stale policy,
        # as long as it is eventually consistent.
        # self._load_policy_no_lock()
        enforcer = self._ensure_enforcer()
        system_role = _system_role(user_id)
        if system_role is not None:
            # Judged by the role the identity carries, so the server can
            # authorize its own calls whatever the policy holds. Evaluated
            # against the *role* rather than the id: casbin's `g` is reflexive,
            # and this way an operator's blocklist for that role still applies.
            if system_role == rbac.RoleName.VIEWER.value:
                return not self._is_viewer_allowed(path, method)
            return enforcer.enforce(system_role, path, method)
        # Read roles from in-memory enforcer state. Do NOT use
        # self.get_user_roles(...) here — that does a DB roundtrip via
        # _load_policy_no_lock and would put a query on the request hot
        # path.
        roles = enforcer.get_roles_for_user(user_id)
        if not _recognized_roles(roles):
            # Reload before denying them: a role assigned moments ago on
            # another worker has not reached this model, and refusing a new
            # admin every admin endpoint until something else happens to
            # reload is its own outage.
            roles = self._probe_unknown_principal(user_id)
            if not _recognized_roles(roles):
                if not roles:
                    # Off-request, so the write never lands inside the auth
                    # deadline: this request stays denied, the next one is not.
                    # Only for an empty role: seeding fills a gap, and a name
                    # nobody recognizes is not one -- queueing it would spin,
                    # and spend a slot the genuinely role-less need.
                    #
                    # Through the guarded entry point: this runs inside the RBAC
                    # middleware, where an exception is a bare 500 rather than a
                    # denial.
                    self.queue_role_repair(user_id)
                self._log_denied_principal(user_id, path, method)
                return True
        # Recognized, by either route: remember it for the loop-side guard.
        # Without this the memo was only ever written by a repair, so
        # `probably_has_role` answered False for every healthy principal until
        # one had run -- and the login paths that consult it queued a pointless
        # repair for each of them after every restart, which could fire the
        # in-flight cap warning on ordinary warm-up.
        self.remember_role_seen(user_id)
        # Admin wins over viewer when a user holds both — viewer's
        # default-deny semantics shouldn't restrict an admin.
        if (rbac.RoleName.VIEWER.value in roles and
                rbac.RoleName.ADMIN.value not in roles):
            return not self._is_viewer_allowed(path, method)
        # `enforce` resolves the id through casbin's `g`, so a system identity
        # whose row never landed matches nothing -- which under blocklist
        # semantics is "allowed", the same answer its admin role gives.
        return enforcer.enforce(user_id, path, method)

    def _is_viewer_allowed(self, path: str, method: str) -> bool:
        """Test (path, method) against the viewer allowlist."""
        return self._matches_endpoint(self._viewer_allowlist, path, method)

    def is_read_only_endpoint(self, path: str, method: str) -> bool:
        """Whether this endpoint only reads, for workspace-access purposes.

        Consumed by `sky.server.requests.workspace_access` to decide whether a
        request needs read or write access to the caller's active workspace.
        Derived from `rbac.get_read_only_endpoints`, so plugin endpoints are
        classified by the declaration plugins already maintain for the viewer
        role.

        First checks `rbac.is_always_write_endpoint`: an always-write (create)
        endpoint returns False here regardless of the read-only declaration —
        this is what makes a wildcard viewer entry unable to relax a create
        endpoint to read.

        Returns False for anything not declared read-only — the caller treats
        that as "needs write", which is the fail-safe direction.
        """
        # Ensures the allowlists are materialised in this process; a no-op
        # after the first call.
        self._lazy_initialize()
        key = (path, method)
        cached = self._read_only_endpoint_cache.get(key)
        if cached is not None:
            return cached
        # Always-write (create) endpoints can never be read, regardless of the
        # viewer allowlist -- this also overrides a wildcard viewer entry that
        # happens to match one (see `rbac._ALWAYS_WRITE_ENDPOINTS`).
        if rbac.is_always_write_endpoint(path, method):
            result = False
        else:
            result = self._matches_endpoint(self._read_only_endpoints, path,
                                            method)
        # Bound the cache: the key includes dynamic path segments, so drop the
        # whole cache (rather than grow unbounded) once it gets too large. The
        # replacement is atomic under the GIL, so concurrent readers are safe.
        if len(self._read_only_endpoint_cache) >= _READ_ONLY_ENDPOINT_CACHE_MAX:
            self._read_only_endpoint_cache = {}
        self._read_only_endpoint_cache[key] = result
        return result

    @staticmethod
    def _matches_endpoint(entries: List[tuple], path: str, method: str) -> bool:
        """Test (path, method) against a list of (path pattern, method)."""
        for allow_path, allow_method in entries:
            if allow_method != method:
                continue
            # casbin_util.key_match2: arg1 is the request key, arg2 is
            # the policy pattern. Pattern supports `:name` placeholders
            # and `*` wildcards.
            if casbin_util.key_match2(path, allow_path):
                return True
        return False

    def _load_policy_no_lock(self):
        """Load policy from storage."""
        enforcer = self._ensure_enforcer()
        enforcer.load_policy()

    def load_policy(self):
        """Load policy from storage with lock."""
        with _policy_lock():
            self._load_policy_no_lock()

    def _workspace_perm_cache_key(self, workspace_name: str,
                                  user_id: str) -> str:
        """Build a KV cache key for a workspace permission entry."""
        return (f'{_WORKSPACE_PERM_CACHE_PREFIX}'
                f'{workspace_name}'
                f'{_WORKSPACE_PERM_CACHE_KEY_SEP}'
                f'{user_id}')

    def invalidate_workspace_permission_cache(self,
                                              workspace_name: str) -> None:
        """Invalidate all cached permission entries for a workspace."""
        prefix = (f'{_WORKSPACE_PERM_CACHE_PREFIX}'
                  f'{workspace_name}'
                  f'{_WORKSPACE_PERM_CACHE_KEY_SEP}')
        kv_cache.delete_cache_entries_by_prefix(prefix)

    def invalidate_user_permission_cache(self, user_id: str) -> None:
        """Invalidate all cached permission entries for a user."""
        kv_cache.delete_cache_entries_by_prefix_suffix(
            prefix=_WORKSPACE_PERM_CACHE_PREFIX,
            suffix=f'{_WORKSPACE_PERM_CACHE_KEY_SEP}{user_id}')

    def check_workspace_permission(
            self,
            user_id: str,
            workspace_name: str,
            action: str = workspace_constants.WORKSPACE_ACTION_WRITE) -> bool:
        """Check workspace permission.

        This method checks if a user has permission to access a specific
        workspace. Membership (write) is granted by the member '*' policy
        (a direct grant for private workspaces, the ('*', ws, '*') wildcard for
        public ones) and its result is cached in a DB-backed KV cache so all
        server/executor processes share one view. Read-only visibility is not
        cached -- see the 'read' branch below.

        Args:
            action: 'write' (default) checks membership -- write and read are
                both granted by the member '*' policy. 'read' additionally
                passes if the workspace is read-only-visible to non-members
                (evaluated live from config), so a non-member can read (but not
                mutate) a read-only workspace. Write implies read.
        """
        if os.getenv(constants.ENV_VAR_IS_SKYPILOT_SERVER) is None:
            # When it is not on API server, we allow all users to access all
            # workspaces, as the workspace check has been done on API server.
            return True

        # Member/write access (admin or the member '*' grant). This is the
        # cached, casbin-backed part; it is action-agnostic (the '*' grant
        # covers both read and write).
        if self._check_member_permission(user_id, workspace_name):
            return True

        # Read-only visibility is evaluated live from the current config
        # (per-workspace read_access, falling back to the org-wide
        # workspace_config.read_access) rather than a materialized casbin
        # grant, so a change takes effect on the next request without a policy
        # re-sync or cache invalidation. Never cached.
        if (action == workspace_constants.WORKSPACE_ACTION_READ and
                workspaces_utils.is_read_only_workspace(workspace_name)):
            return True

        return False

    def _check_member_permission(self, user_id: str,
                                 workspace_name: str) -> bool:
        """Whether the user is a member of (can write) the workspace.

        Admin, or the casbin member '*' grant (which also covers public
        workspaces via the ('*', ws, '*') policy). Result is cached in the
        DB-backed KV cache so all server/executor processes share one view and
        the casbin enforce is off the hot path.
        """
        cache_key = self._workspace_perm_cache_key(workspace_name, user_id)
        cached = kv_cache.get_cache_entry(cache_key)
        if cached is not None:
            return cached == '1'

        role = self.get_user_roles(user_id)
        if rbac.RoleName.ADMIN.value in role:
            result = True
        else:
            # Actions are matched exactly by the casbin model (r.act == p.act,
            # no wildcard). The member policies use the '*' action, which grants
            # write (and, implicitly, read).
            enforcer = self._ensure_enforcer()
            result = enforcer.enforce(user_id, workspace_name, '*')

        logger.debug(f'Workspace member check: user={user_id}, '
                     f'workspace={workspace_name}, result={result}')

        # Cache the result; failures are non-critical.
        try:
            kv_cache.add_or_update_cache_entry(
                cache_key, '1' if result else '0',
                time.time() + _WORKSPACE_PERM_CACHE_TTL_SECONDS)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to cache workspace permission: {e}')

        return result

    def check_service_account_token_permission(self, user_id: str,
                                               token_owner_id: str,
                                               action: str) -> bool:
        """Check service account token permission.

        This method checks if a user has permission to perform an action on
        a service account token owned by another user.

        Args:
            user_id: The ID of the user requesting the action
            token_owner_id: The ID of the user who owns the token
            action: The action being performed (e.g., 'delete', 'view')

        Returns:
            True if the user has permission, False otherwise
        """
        del action

        user_roles = self.get_user_roles(user_id)
        # Admin can manage any token — check this first so a user
        # holding both admin and viewer isn't blocked by the viewer rule.
        if rbac.RoleName.ADMIN.value in user_roles:
            return True

        # Viewers cannot manage ANY service-account tokens.
        if rbac.RoleName.VIEWER.value in user_roles:
            return False

        # Users can always manage their own tokens
        if user_id == token_owner_id:
            return True

        # Regular users cannot manage tokens owned by others
        return False

    def add_workspace_policy(self, workspace_name: str,
                             users: List[str]) -> None:
        """Add workspace policy.

        Args:
            workspace_name: Name of the workspace
            users: List of user IDs that should have access.
                   For public workspaces, this should be ['*'].
                   For private workspaces, this should be specific user IDs.
        """
        with _policy_lock():
            # Reload from the DB inside the lock before mutating: save_policy()
            # rewrites the whole casbin_rule table from this enforcer's
            # in-memory view, so a stale view (e.g. missing another workspace's
            # policies added by a different worker) would clobber those rows on
            # save. update_workspace_policy already does this; mirror it here.
            self._load_policy_no_lock()
            enforcer = self._ensure_enforcer()
            for user in users:
                logger.debug(f'Adding workspace policy: user={user}, '
                             f'workspace={workspace_name}')
                enforcer.add_policy(user, workspace_name, '*')
            enforcer.save_policy()
            # Invalidate stale cached denials (e.g. from checks between a
            # workspace deletion and its re-creation with the same name).
            self.invalidate_workspace_permission_cache(workspace_name)

    def update_workspace_policy(self, workspace_name: str,
                                users: List[str]) -> None:
        """Update workspace policy.

        Args:
            workspace_name: Name of the workspace
            users: List of user IDs that should have access.
                   For public workspaces, this should be ['*'].
                   For private workspaces, this should be specific user IDs.
        """
        with _policy_lock():
            self._load_policy_no_lock()
            enforcer = self._ensure_enforcer()
            # Remove all existing policies for this workspace
            enforcer.remove_filtered_policy(1, workspace_name)
            # Add new policies
            for user in users:
                logger.debug(f'Updating workspace policy: user={user}, '
                             f'workspace={workspace_name}')
                enforcer.add_policy(user, workspace_name, '*')
            enforcer.save_policy()
            # Invalidate cached permission entries after the policy is
            # persisted so other processes re-compute permissions on next
            # check.
            self.invalidate_workspace_permission_cache(workspace_name)

    def resync_workspace_policies_for_new_user(self, user_id: str) -> None:
        """Grant a newly-created user any workspace access owed by config.

        Private workspaces list their members in ``allowed_users`` (a mix
        of user_ids and usernames). Those entries are resolved to casbin
        policies at server startup and on workspace-config updates, but an
        entry can only resolve once a matching user record exists, and a
        user record is first created on login. An admin who adds a user to
        ``allowed_users`` before that user has ever logged in therefore
        produces an entry that resolves to nothing at sync time.

        This method re-resolves the config from the perspective of a single
        newly-created user: for each private workspace whose
        ``allowed_users`` names this user (by id or unique username), it
        adds the missing ``(user_id, workspace_name, '*')`` policy. It is
        scoped to this one user and does not rebuild all policies.

        Idempotent and concurrency-safe: policy writes are guarded by the
        distributed policy lock and ``add_policy`` skips duplicates, so
        concurrent replicas / workers converge on the same result.

        Lock ordering matters: a workspace update (`update_workspace_fn`)
        takes the config lock exclusively, then the policy lock nested
        inside it. This method takes the same two locks in the same order
        — config lock (shared, we only read) first, then the policy lock —
        so the two paths cannot deadlock, and holding the config lock
        across the policy write means the matches can never be computed
        from a config snapshot that predates an admin's removal of this
        user: whichever side takes the config lock second sees the other's
        result. On a config-lock timeout the grant is SKIPPED (never
        computed from a stale config); the zero-accessible retry in
        `workspaces.core.resolve_workspace_for_user` picks it up later.
        The pre-lock computation is only a cheap early exit so user
        creation skips the distributed locks entirely when no private
        workspace names the user.
        """
        if os.getenv(constants.ENV_VAR_IS_SKYPILOT_SERVER) is None:
            return

        # Lazy imports to avoid a circular import: `sky.users.resolver`
        # imports this module.
        # pylint: disable=import-outside-toplevel
        from sky.users import resolver as user_resolver

        def _matching_workspaces() -> List[str]:
            workspaces = skypilot_config.get_nested(('workspaces',),
                                                    default_value={})
            private_workspaces = {
                workspace_name: workspace_config
                for workspace_name, workspace_config in workspaces.items()
                if workspace_config.get('private', False) and
                workspace_config.get('allowed_users')
            }
            if not private_workspaces:
                # Skip building the UserResolver (a full users-table read)
                # when there is nothing to match against.
                return []
            resolver = user_resolver.UserResolver()
            # Every form of this user that could appear in an allowed_users
            # list: the user_id itself plus the unique username, if any.
            user_entries = set(resolver.entries_for(user_id))
            return [
                workspace_name for workspace_name, workspace_config in
                private_workspaces.items() if user_entries.intersection(
                    workspace_config.get('allowed_users', []))
            ]

        if not _matching_workspaces():
            return

        added_any = False
        try:
            with skypilot_config.get_skypilot_config_lock(
                    POLICY_UPDATE_LOCK_TIMEOUT_SECONDS, shared_lock=True):
                # Fresh read inside the config lock (see docstring). Call
                # reload_config directly: safe_reload_config would try to
                # re-acquire the (non-reentrant) config lock we now hold.
                skypilot_config.reload_config()
                matching_workspaces = _matching_workspaces()
                if not matching_workspaces:
                    return
                with _policy_lock():
                    self._load_policy_no_lock()
                    enforcer = self._ensure_enforcer()
                    for workspace_name in matching_workspaces:
                        # add_policy returns False if the policy already
                        # exists.
                        if enforcer.add_policy(user_id, workspace_name, '*'):
                            logger.info(
                                f'Granting user {user_id} access to private '
                                f'workspace {workspace_name!r} on user '
                                'creation (matched allowed_users after the '
                                'user record was created).')
                            added_any = True
                    if added_any:
                        enforcer.save_policy()
        except locks.LockTimeout:
            logger.warning(
                f'Timed out acquiring the config lock; skipping the '
                f'workspace policy re-sync for user {user_id}. It will be '
                'retried on their next workspace resolution.')
            return
        if added_any:
            self.invalidate_user_permission_cache(user_id)

    def remove_workspace_policy(self, workspace_name: str) -> None:
        """Remove workspace policy."""
        with _policy_lock():
            # Reload from the DB inside the lock before mutating: save_policy()
            # rewrites the whole table from this process's in-memory model, so
            # without this a stale worker deleting a workspace also erases
            # every policy another worker wrote since this one last loaded.
            # Same reason as `add_workspace_policy` / `update_workspace_policy`.
            self._load_policy_no_lock()
            enforcer = self._ensure_enforcer()
            enforcer.remove_filtered_policy(1, workspace_name)
            enforcer.save_policy()
            # Invalidate cached permission entries after the policy is
            # persisted so other processes re-compute permissions on next
            # check.
            self.invalidate_workspace_permission_cache(workspace_name)


@contextlib.contextmanager
def _policy_lock() -> Generator[None, None, None]:
    """Context manager for policy update lock."""
    try:
        with locks.get_lock(
                POLICY_UPDATE_LOCK_ID,
                POLICY_UPDATE_LOCK_TIMEOUT_SECONDS,
                poll_interval=POLICY_UPDATE_LOCK_POLL_INTERVAL_SECONDS):
            yield
    except locks.LockTimeout as e:
        raise RuntimeError('Failed to update policy due to a timeout when '
                           'trying to acquire the casbin policy lock. This '
                           'may indicate another SkyPilot process is currently '
                           'updating the policy. Please try again.') from e


# Singleton instance of PermissionService for other modules to use.
permission_service = PermissionService()


def seed_new_user_role(user_id: str, role: Optional[str] = None) -> None:
    """Reload config, then set up policies for a newly-created user.

    Assigns `role` (the default role when omitted) and grants any
    private-workspace access that the config's `allowed_users` lists owe this
    user (see `resync_workspace_policies_for_new_user` for why this can only
    happen once the user record exists).

    Refreshes the in-memory config first so a runtime change to
    `rbac.default_role` or `workspaces` is honored without a server restart:
    the main API-server process (auth middlewares, sync handlers) bypasses the
    executor's per-request config reload. `add_user_if_not_exists` is a no-op if
    the user already has a role.

    Blocking (config file/DB read + policy lock). Async callers MUST offload it
    via `asyncio.to_thread` so it does not block the event loop.
    """
    skypilot_config.safe_reload_config()
    permission_service.add_user_if_not_exists(user_id, role)
    # This worker just gave them a role; the loop-side guard should not have to
    # learn that from a repair.
    permission_service.remember_role_seen(user_id)
    try:
        permission_service.resync_workspace_policies_for_new_user(user_id)
    except Exception as e:  # pylint: disable=broad-except
        # Don't fail the user's first request over a transient grant
        # failure (lock timeout, DB error): the grant is retried on the
        # zero-accessible-workspaces path in
        # `workspaces.core.resolve_workspace_for_user`, which re-syncs
        # once more before denying access.
        logger.error('Failed to grant private-workspace access for new '
                     f'user {user_id}; will retry on their next workspace '
                     f'resolution: {common_utils.format_exception(e)}')

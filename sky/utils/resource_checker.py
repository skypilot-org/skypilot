"""Resource checking utilities for finding active clusters and managed jobs."""

import collections
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sky import exceptions
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import context_utils

logger = sky_logging.init_logger(__name__)

_Rows = List[Dict[str, Any]]

# Lists live resources of one kind in the given workspace ids, owned by the
# given user hashes (no filter when None), as dicts with 'name', 'workspace'
# (an id) and 'user_hash'.
ActiveResourceLister = Callable[[Optional[List[str]], Optional[List[str]]],
                                List[Dict[str, Any]]]
# Kind shown in messages ('volume', 'endpoint', ...) -> lister.
_ACTIVE_RESOURCE_LISTERS: Dict[str, ActiveResourceLister] = {}
# Serve replicas, pool workers and managed-job clusters.
_CONTROLLER_LAUNCHED_CLUSTER = 'service/pool/job cluster'
# A controller relaunches a replica or worker that is torn down by hand.
_CONTROLLER_LAUNCHED_HINT = (
    f' A {_CONTROLLER_LAUNCHED_CLUSTER} goes away when its service, pool or '
    'managed job is terminated.')
_MAX_NAMES_PER_KIND = 10


def register_active_resource_lister(kind: str,
                                    fn: ActiveResourceLister) -> None:
    """Register a lister of live resources that block workspace changes.

    Its resources then block deleting a workspace, narrowing it, and removing
    or demoting their owner, like clusters and managed jobs do. Called during
    server startup (plugin loading, single-threaded), so no lock is needed.

    The lister runs on each such change, so it must be cheap: one DB query, no
    cloud or Kubernetes calls. It should filter by both arguments in that
    query: user delete and role demotion pass the user hashes with every
    workspace. The filter only saves work, since callers filter the rows
    again. If it raises, the change is refused.
    """
    _ACTIVE_RESOURCE_LISTERS[kind] = fn


def _workspace_of(resource: Dict[str, Any]) -> Optional[str]:
    return resource.get('workspace', constants.SKYPILOT_DEFAULT_WORKSPACE)


def _format_names(names: List[str]) -> str:
    if len(names) <= _MAX_NAMES_PER_KIND:
        return ', '.join(names)
    shown = ', '.join(names[:_MAX_NAMES_PER_KIND])
    return f'{shown} and {len(names) - _MAX_NAMES_PER_KIND} more'


def _describe_resources(clusters: _Rows, jobs: _Rows,
                        others: _Rows) -> List[str]:
    """One 'N active <kind>(s): names' part per kind with resources."""
    parts = []
    if clusters:
        parts.append(f'{len(clusters)} active cluster(s): ' +
                     _format_names([c['name'] for c in clusters]))
    if jobs:
        parts.append(f'{len(jobs)} active managed job(s): ' +
                     _format_names([str(j['job_id']) for j in jobs]))
    by_kind: Dict[str, List[str]] = {}
    for resource in others:
        by_kind.setdefault(resource['kind'], []).append(resource['name'])
    for kind, names in by_kind.items():
        parts.append(f'{len(names)} active {kind}(s): {_format_names(names)}')
    return parts


def check_no_active_resources_for_users(
        user_operations: List[Tuple[str, str]]) -> None:
    """Check if users have active clusters or managed jobs.

    Args:
        user_operations: List of tuples (user_id, operation) where
            operation is 'update' or 'delete'.

    Raises:
        ValueError: If any user has active clusters or managed jobs.
            The error message will include all users with issues.
    """
    if not user_operations:
        return

    def filter_by_user(user_id: str):
        return lambda resource: resource.get('user_hash') == user_id

    _check_active_resources(
        user_operations, filter_by_user, 'user',
        ResourceSnapshot.fetch_for_users([u for u, _ in user_operations]))


def check_no_active_resources_for_workspaces(
        workspace_operations: List[Tuple[str, str]],
        resources: Optional['ResourceSnapshot'] = None) -> None:
    """Check if workspaces have active clusters or managed jobs.

    Args:
        workspace_operations: List of tuples (workspace_name, operation) where
            operation is 'update' or 'delete'.
        resources: Optional shared ``ResourceSnapshot`` covering at least
            these workspaces. Fetched for them when not provided.

    Raises:
        ValueError: If any workspace has active clusters or managed jobs.
            The error message will include all workspaces with issues.
    """
    if not workspace_operations:
        return

    def filter_by_workspace(workspace_name: str):
        return lambda resource: _workspace_of(resource) == workspace_name

    if resources is None:
        resources = ResourceSnapshot.fetch_for_workspaces(
            [name for name, _ in workspace_operations])
    _check_active_resources(workspace_operations, filter_by_workspace,
                            'workspace', resources)


def _check_active_resources(resource_operations: List[Tuple[str, str]],
                            filter_factory: Callable[[str],
                                                     Callable[[Dict[str, Any]],
                                                              bool]],
                            resource_type: str,
                            resources: 'ResourceSnapshot') -> None:
    """Check if resource entities have active resources.

    Args:
        resource_operations: List of tuples (resource_name, operation) where
            operation is 'update' or 'delete'.
        filter_factory: Function that takes a resource_name and returns a filter
            function for resources.
        resource_type: Type of resource being checked ('user' or 'workspace').
        resources: The active resources to check against.

    Raises:
        ValueError: If any resource has active resources.
    """
    # Collect all error messages instead of raising immediately
    error_messages = []

    # Check each resource against the fetched data
    for resource_name, operation in resource_operations:
        resource_filter = filter_factory(resource_name)
        resource_errors = _describe_resources(
            [c for c in resources.clusters if resource_filter(c)],
            [j for j in resources.managed_jobs if resource_filter(j)],
            [o for o in resources.others if resource_filter(o)])

        # If this resource has issues, add to overall error messages
        if resource_errors:
            resource_error_summary = ' and '.join(resource_errors)
            if resource_type == 'user':
                # resource_name is user_id
                user_info = global_user_state.get_user(resource_name)
                if user_info and user_info.name:
                    resource_name = user_info.name
            elif resource_type == 'workspace':
                resource_name = skypilot_config.get_workspace_name(
                    resource_name)
            error_messages.append(
                f'Cannot {operation} {resource_type} {resource_name!r} '
                f'because it has {resource_error_summary}.')

    # If we collected any errors, raise them all together
    if error_messages:
        if len(error_messages) == 1:
            # Single resource error
            full_message = error_messages[
                0] + ' Please terminate these resources first.'
        else:
            # Multiple resource errors
            full_message = (f'Cannot proceed due to active resources in '
                            f'{len(error_messages)} {resource_type}(s):\n' +
                            '\n'.join(f'• {msg}' for msg in error_messages) +
                            '\nPlease terminate these resources first.')
        if _CONTROLLER_LAUNCHED_CLUSTER in full_message:
            full_message += _CONTROLLER_LAUNCHED_HINT
        raise ValueError(full_message)


def check_users_workspaces_active_resources(
    user_ids: List[str],
    workspace_names: List[str],
    resources: Optional['ResourceSnapshot'] = None,
    owners: Optional[List[str]] = None,
) -> Tuple[str, List[str], Dict[str, str]]:
    """Check if all the active resources in workspaces belong to the user_ids.
       If not, return the error message.

    Args:
        user_ids: List of user_id.
        workspace_names: List of workspaces to check.
        resources: Optional shared ``ResourceSnapshot`` covering at
            least the given workspaces. Batch callers should construct
            one ``ResourceSnapshot.fetch_for_workspaces`` and pass it
            through; the per-workspace slice is taken from the
            snapshot's workspace index. When not provided, a transient
            snapshot scoped to ``workspace_names`` is fetched
            internally.
        owners: If given, only resources owned by these users count, e.g.
            the users being removed.

    Returns:
        resource_error_summary: str
        missed_users_names: List[str]
        missed_user_dict: Dict[str, str]
    """
    if resources is None:
        resources = ResourceSnapshot.fetch_for_workspaces(workspace_names)
    # The snapshot may cover a superset of workspaces; a single linear filter
    # keeps the relative order of the source lists.
    workspace_set = set(workspace_names)
    missed_users = set()

    def missed(resource_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for resource in resource_list:
            if _workspace_of(resource) not in workspace_set:
                continue
            user_hash = resource.get('user_hash')
            if (user_hash and user_hash not in user_ids and
                (owners is None or user_hash in owners)):
                missed_users.add(user_hash)
                result.append(resource)
        return result

    resource_errors = _describe_resources(missed(resources.clusters),
                                          missed(resources.managed_jobs),
                                          missed(resources.others))
    resource_error_summary = ' and '.join(resource_errors)
    missed_users_names = []
    missed_user_dict = {}
    if missed_users:
        all_users = global_user_state.get_all_users()
        for user in all_users:
            if user.id in missed_users:
                missed_users_names.append(user.name if user.name else user.id)
                missed_user_dict[user.id] = user.name if user.name else user.id
    return resource_error_summary, missed_users_names, missed_user_dict


def _get_active_resources_for_workspaces(
    workspace_names: List[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get active clusters, managed jobs and other resources for workspaces.

    Args:
        workspace_names: List of workspace_name.

    Returns:
        all_clusters: List[Dict[str, Any]]
        all_managed_jobs: List[Dict[str, Any]]
        all_others: List[Dict[str, Any]]
    """
    if not workspace_names:
        return [], [], []

    def filter_by_workspaces(workspace_names: List[str]):
        return lambda resource: _workspace_of(resource) in workspace_names

    return _get_active_resources_by_names(workspace_names, filter_by_workspaces,
                                          workspace_names)


def _get_active_resources_by_names(
    resource_names: List[str],
    filter_factory: Callable[[List[str]], Callable[[Dict[str, Any]], bool]],
    workspace_names: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get active resources matching ``filter_factory(resource_names)``.

    Args:
        resource_names: List of resource_name.
        filter_factory: Function that takes a resource_name and returns a filter
            function for resources.
        workspace_names: If given, only these workspaces are fetched.

    Returns:
        all_clusters: List[Dict[str, Any]]
        all_managed_jobs: List[Dict[str, Any]]
        all_others: List[Dict[str, Any]]
    """
    all_clusters, all_managed_jobs, all_others = _get_active_resources(
        workspace_names)
    resource_filter = filter_factory(resource_names)
    return ([c for c in all_clusters if resource_filter(c)
            ], [j for j in all_managed_jobs if resource_filter(j)],
            [o for o in all_others if resource_filter(o)])


class ResourceSnapshot:
    """A point-in-time view of active resources.

    Holds the raw ``(clusters, managed_jobs, others)`` lists fetched from the
    cluster / managed-jobs sources and the registered listers, and builds
    per-user and per-workspace indices lazily on first access. Batch callers
    should construct one snapshot at the top of the operation and pass it
    through helpers; each ``for_user`` / ``for_workspace`` lookup is then O(1)
    instead of re-filtering the full lists per call.
    """

    def __init__(
        self,
        clusters: List[Dict[str, Any]],
        managed_jobs: List[Dict[str, Any]],
        others: Optional[List[Dict[str, Any]]] = None,
        loader: Optional[Callable[[], 'ResourceSnapshot']] = None,
    ):
        self._clusters = clusters
        self._managed_jobs = managed_jobs
        self._others = others or []
        # Fetches the three lists on first use (see deferred_for_workspaces).
        self._loader = loader
        self._load_error: Optional[Exception] = None
        self._by_workspace: Optional[Dict[str, Tuple[List[Dict[str, Any]],
                                                     ...]]] = None
        self._by_user: Optional[Dict[str, Tuple[List[Dict[str, Any]],
                                                ...]]] = None

    @classmethod
    def fetch_for_users(cls, user_ids: List[str]) -> 'ResourceSnapshot':
        """Fetch active resources; the listers return only these users'."""
        return cls(*_get_active_resources(user_hashes=user_ids))

    @classmethod
    def fetch_for_workspaces(cls,
                             workspace_names: List[str]) -> 'ResourceSnapshot':
        """Fetch active resources filtered to the given workspaces."""
        return cls(*_get_active_resources_for_workspaces(workspace_names))

    @classmethod
    def deferred_for_workspaces(
            cls, workspace_names: List[str]) -> 'ResourceSnapshot':
        """Like ``fetch_for_workspaces``, but fetched on first use.

        For batch callers where most changes may need no check at all.
        """
        return cls([], [],
                   loader=lambda: cls.fetch_for_workspaces(workspace_names))

    def _load(self) -> None:
        # A failed fetch fails every later use too, so a batch caller that
        # catches it per workspace never checks against empty lists.
        if self._load_error is not None:
            raise self._load_error
        if self._loader is None:
            return
        try:
            loaded = self._loader()
        except Exception as e:  # pylint: disable=broad-except
            self._load_error = e
            raise
        self._loader = None
        self._clusters = loaded.clusters
        self._managed_jobs = loaded.managed_jobs
        self._others = loaded.others

    @property
    def clusters(self) -> List[Dict[str, Any]]:
        self._load()
        return self._clusters

    @property
    def managed_jobs(self) -> List[Dict[str, Any]]:
        self._load()
        return self._managed_jobs

    @property
    def others(self) -> List[Dict[str, Any]]:
        """Resources from the registered listers, each with a 'kind'."""
        self._load()
        return self._others

    def _index(
        self, key: Callable[[Dict[str, Any]], Optional[str]]
    ) -> Dict[str, Tuple[List[Dict[str, Any]], ...]]:
        index: Dict[str, Tuple[List[Dict[str, Any]], ...]] = (
            collections.defaultdict(lambda: ([], [], [])))
        for i, resources in enumerate(
            (self.clusters, self.managed_jobs, self.others)):
            for resource in resources:
                k = key(resource)
                if k:
                    index[k][i].append(resource)
        return dict(index)

    def for_user(self, user_id: str) -> Tuple[List[Dict[str, Any]], ...]:
        """Return ``(clusters, jobs, others)`` owned by ``user_id``.

        Index is built lazily on first call.
        """
        if self._by_user is None:
            self._by_user = self._index(lambda r: r.get('user_hash'))
        return self._by_user.get(user_id, ([], [], []))

    def for_workspace(self,
                      workspace_name: str) -> Tuple[List[Dict[str, Any]], ...]:
        """Return ``(clusters, jobs, others)`` in the given workspace.

        Index is built lazily on first call.
        """
        if self._by_workspace is None:
            self._by_workspace = self._index(_workspace_of)
        return self._by_workspace.get(workspace_name, ([], [], []))


def load_fresh_workspaces() -> Dict[str, Any]:
    """Reload the skypilot config from disk and return the workspaces dict.

    Exposed so batch callers can do the reload + read once and pass the
    workspaces dict to multiple ``check_user_role_demotion`` calls.
    Individual callers should rely on the default ``workspaces=None`` of
    ``check_user_role_demotion`` instead.
    """
    skypilot_config.safe_reload_config()
    return skypilot_config.get_nested(('workspaces',), default_value={})


def check_user_role_demotion(
        user: models.User,
        workspaces: Optional[Dict[str, Any]] = None,
        resources: Optional[ResourceSnapshot] = None,
        workspaces_allowed_users: Optional[Dict[str, Set[str]]] = None) -> None:
    """Check whether an admin can be safely demoted to a regular user.

    After demotion the user loses implicit access to all private workspaces
    where they are not listed in ``allowed_users``. This function ensures
    the user has no active clusters or managed jobs in private workspaces
    they would lose access to.

    Args:
        user: The user being demoted. Used for both the access check
            (``user.id`` against each workspace's ``allowed_users``) and
            the error message (``user.name`` preferred, falling back to
            ``user.id``).
        workspaces: Optional pre-fetched workspaces config (from
            ``load_fresh_workspaces()``). When called in a batch loop, the
            caller should fetch this once and pass it in to avoid the
            per-call ``safe_reload_config`` + YAML read overhead.
        resources: Optional shared ``ResourceSnapshot``. Batch callers
            should build one ``ResourceSnapshot.fetch_for_users()`` at the top
            of the operation and pass it through, so the per-user lookup
            is O(1) (via the snapshot's user index) instead of an
            O(C+J) scan per user. When not provided, a transient
            snapshot is fetched internally.
        workspaces_allowed_users: Optional pre-resolved map of
            ``workspace_name -> set(allowed_user_ids)`` for private
            workspaces. Batch callers should resolve each private
            workspace's ``allowed_users`` once (via
            ``UserResolver.resolve_workspaces_allowed_users``) and pass
            the map. Without this, each invocation re-calls
            ``get_workspace_users`` for every private workspace, and each
            of those re-fetches ``get_all_users()`` from the DB.

    Raises:
        ValueError: If the user has active clusters or managed jobs in
            private workspaces they would lose access to.
    """
    # Imports done lazily to avoid circular imports with permission/workspaces.
    # pylint: disable=import-outside-toplevel
    from sky.workspaces import utils as workspaces_utils

    user_id = user.id
    user_display = user.name or user.id

    if workspaces is None:
        # Single-call path. /users/update and /users/batch_update are sync
        # FastAPI handlers and don't go through the executor's
        # reload_for_new_request pipeline; without this reload they would
        # read the global config snapshot from server startup and miss any
        # allowed_users writes from a recent /workspaces/batch_add_users.
        workspaces = load_fresh_workspaces()
    if not workspaces:
        return

    inaccessible_workspaces: List[str] = []
    for workspace_name, workspace_config in workspaces.items():
        if not workspace_config.get('private', False):
            continue
        # Prefer the pre-resolved set (batch path) to avoid re-calling
        # get_workspace_users -> get_all_users for every workspace.
        if (workspaces_allowed_users is not None and
                workspace_name in workspaces_allowed_users):
            allowed_user_ids = workspaces_allowed_users[workspace_name]
        else:
            allowed_user_ids = set(
                workspaces_utils.get_workspace_users(workspace_config))
        if user_id in allowed_user_ids:
            continue
        inaccessible_workspaces.append(workspace_name)

    if not inaccessible_workspaces:
        return

    # Resolve the demoted user's clusters / jobs via the snapshot's
    # per-user index. Build a transient snapshot for the single-user
    # path; batch callers pass a shared one.
    if resources is None:
        resources = ResourceSnapshot.fetch_for_users([user_id])
    user_resources = resources.for_user(user_id)

    workspace_set = set(inaccessible_workspaces)
    # workspace -> (clusters, jobs, others), in first-seen order.
    workspace_resources: Dict[str, Tuple[List[Dict[str, Any]], ...]] = {}
    for i, resource_list in enumerate(user_resources):
        for resource in resource_list:
            ws = _workspace_of(resource)
            if ws not in workspace_set:
                continue
            workspace_resources.setdefault(ws, ([], [], []))[i].append(resource)

    if not workspace_resources:
        return

    error_lines = []
    for ws, res in workspace_resources.items():
        joined = ' and '.join(_describe_resources(*res))
        error_lines.append(
            f'  - workspace {skypilot_config.get_workspace_name(ws)!r}: '
            f'{joined}')

    raise ValueError(
        f'Cannot demote user {user_display!r} from admin to user: user '
        f'{user_display!r} has active resources in private workspaces '
        f'where {user_display!r} is not in allowed_users:\n' +
        '\n'.join(error_lines) +
        f'\nPlease either terminate these resources or add {user_display!r} '
        'to the allowed_users of those workspaces first.')


def _list_volumes(workspace_names: Optional[List[str]],
                  user_hashes: Optional[List[str]]) -> List[Dict[str, Any]]:
    # Ephemeral volumes belong to a cluster, which is counted already.
    volumes = global_user_state.get_volume_owners(
        is_ephemeral=False,
        workspaces_filter=(set(workspace_names)
                           if workspace_names is not None else None))
    if user_hashes is None:
        return volumes
    return [v for v in volumes if v['user_hash'] in user_hashes]


def _service_lister(pool: bool) -> ActiveResourceLister:

    def lister(workspace_names: Optional[List[str]],
               user_hashes: Optional[List[str]]) -> List[Dict[str, Any]]:
        # Only a consolidated controller's services table is local. Elsewhere
        # the services, and their replica and worker clusters, live in the
        # controller's own DB and are not checked.
        if not serve_utils.is_consolidation_mode(pool=pool):
            return []
        services = serve_state.get_live_services_in_workspaces(
            set(workspace_names) if workspace_names is not None else None)
        return [
            s for s in services if s['pool'] == pool and
            (user_hashes is None or s['user_hash'] in user_hashes)
        ]

    return lister


register_active_resource_lister('volume', _list_volumes)
register_active_resource_lister('service', _service_lister(pool=False))
register_active_resource_lister('pool', _service_lister(pool=True))


def _get_active_resources(
    workspace_names: Optional[List[str]] = None,
    user_hashes: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get all active clusters, managed jobs and other resources.

    Args:
        workspace_names: If given, clusters and the registered listers are
            limited to these workspaces. Managed jobs are always fetched
            whole.
        user_hashes: If given, only these users' resources are returned.
            The listers filter in their query; clusters and managed jobs are
            fetched whole and filtered here.

    Returns:
        all_clusters: List[Dict[str, Any]], clusters not launched by a
            controller.
        all_managed_jobs: List[Dict[str, Any]]
        all_others: List[Dict[str, Any]], each with a 'kind': clusters
            launched by a controller (serve replicas, pool workers) and the
            resources of every registered lister.
    """

    def get_all_clusters() -> List[Dict[str, Any]]:
        return global_user_state.get_clusters(
            workspaces_filter=(set(workspace_names)
                               if workspace_names is not None else None),
            summary_response=True)

    def get_all_managed_jobs() -> List[Dict[str, Any]]:
        # pylint: disable=import-outside-toplevel
        from sky.jobs.server import core as managed_jobs_core
        try:
            filtered_jobs, _, _, _, _ = managed_jobs_core.queue_v2(
                refresh=False,
                skip_finished=True,
                all_users=True,
                fields=['job_id', 'user_hash', 'workspace'])
            return filtered_jobs
        except exceptions.ClusterNotUpError:
            logger.warning('All jobs should be finished.')
            return []

    def list_kind(kind: str,
                  lister: ActiveResourceLister) -> List[Dict[str, Any]]:
        # Unlike the jobs fetch, a lister failure says nothing about whether
        # resources exist, so it refuses the change.
        try:
            resources = lister(workspace_names, user_hashes)
        except Exception as e:  # pylint: disable=broad-except
            raise ValueError(f'Could not check for active {kind}(s): '
                             f'{common_utils.format_exception(e)}') from e
        return [dict(r, kind=kind) for r in resources]

    # Each lister is its own query; run serially on a remote database they
    # outlast the jobs fetch. 4 workers stay within the default per-process
    # DB connection budget.
    with context_utils.ContextPropagatingThreadPool(max_workers=4) as executor:
        clusters_future = executor.submit(get_all_clusters)
        jobs_future = executor.submit(get_all_managed_jobs)
        lister_futures = [
            executor.submit(list_kind, kind, lister)
            for kind, lister in list(_ACTIVE_RESOURCE_LISTERS.items())
        ]

        all_clusters = clusters_future.result()
        all_managed_jobs = jobs_future.result()
        all_others = [r for f in lister_futures for r in f.result()]

    # Clusters launched by a controller: a managed job's cluster is also
    # listed as its job; serve replicas and pool workers only here.
    controller_launched = [{
        'name': c['name'],
        'workspace': c.get('workspace'),
        'user_hash': c.get('user_hash'),
        'kind': _CONTROLLER_LAUNCHED_CLUSTER,
    } for c in all_clusters if c.get('is_managed')]
    all_clusters = [c for c in all_clusters if not c.get('is_managed')]
    if user_hashes is not None:
        users = set(user_hashes)
        all_clusters = [c for c in all_clusters if c.get('user_hash') in users]
        all_managed_jobs = [
            j for j in all_managed_jobs if j.get('user_hash') in users
        ]
        controller_launched = [
            c for c in controller_launched if c['user_hash'] in users
        ]
    return all_clusters, all_managed_jobs, controller_launched + all_others

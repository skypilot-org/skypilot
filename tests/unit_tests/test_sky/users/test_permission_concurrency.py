"""Test concurrent access to PermissionService does not cause RuntimeError.
"""
# pylint: disable=protected-access,redefined-outer-name
import concurrent.futures
import os
import threading

import casbin
import pytest
import sqlalchemy
import sqlalchemy_adapter

from sky.users import permission
from sky.users import rbac


@pytest.fixture
def permission_service(tmp_path):
    """Create a PermissionService with a SyncedEnforcer."""
    old_instance = permission._enforcer_instance
    permission._enforcer_instance = None
    try:
        db_path = str(tmp_path / 'test_casbin.db')
        engine = sqlalchemy.create_engine(
            f'sqlite:///{db_path}',
            connect_args={'check_same_thread': False},
            poolclass=sqlalchemy.pool.StaticPool)
        sqlalchemy_adapter.Base.metadata.create_all(engine)
        adapter = sqlalchemy_adapter.Adapter(
            engine, db_class=sqlalchemy_adapter.CasbinRule)

        model_path = os.path.join(os.path.dirname(permission.__file__),
                                  'model.conf')
        enforcer = casbin.SyncedEnforcer(model_path, adapter)

        service = permission.PermissionService()
        service.enforcer = enforcer
        permission._enforcer_instance = service

        for i in range(500):
            enforcer.add_grouping_policy(f'user_{i}', rbac.RoleName.USER.value)
        for i in range(500):
            enforcer.add_grouping_policy(f'admin_{i}',
                                         rbac.RoleName.ADMIN.value)
        enforcer.save_policy()

        yield service
    finally:
        permission._enforcer_instance = old_instance


def test_concurrent_permission_service_no_race(permission_service):
    """Verify SyncedEnforcer's read-write lock prevents the race.

    Concurrent get_user_roles and get_users_for_role calls should not
    raise RuntimeError because SyncedEnforcer serializes load_policy
    (write lock) against role queries (read lock).

    We mock the DB adapter to be instant so threads actually overlap
    on the critical section (build_role_links + to_string) rather than
    serializing on SQLite.
    """
    errors = []
    stop = threading.Event()
    num_threads = 4
    barrier = threading.Barrier(num_threads)
    query_counter = 0
    query_lock = threading.Lock()

    # Snapshot the policy data so we can reload without hitting the DB.
    # Access the internal enforcer's model for policy snapshot.
    enforcer = permission_service.enforcer
    internal_model = enforcer._e.model  # pylint: disable=protected-access
    saved_policies = []
    for sec in internal_model.model:
        for ptype in internal_model.model[sec]:
            ast = internal_model.model[sec][ptype]
            if hasattr(ast, 'policy'):
                for rule in ast.policy:
                    saved_policies.append((sec, ptype, list(rule)))

    def fast_adapter_load(model):
        """Reload policy into model without DB round-trip."""
        for sec, ptype, rule in saved_policies:
            model.add_policy(sec, ptype, rule)

    def worker(thread_id):  # pylint: disable=unused-argument
        nonlocal query_counter
        barrier.wait()
        while not stop.is_set():
            try:
                for role in rbac.get_supported_roles():
                    permission_service.get_users_for_role(role)
                # Query for unknown user IDs to trigger _get_role's
                # get-or-create, which adds to all_roles.
                with query_lock:
                    my_counter = query_counter
                    query_counter += 5
                for i in range(my_counter, my_counter + 5):
                    permission_service.get_user_roles(f'unknown_{i}')
            except RuntimeError as e:
                if 'dictionary changed size during iteration' in str(e):
                    errors.append(e)
                    stop.set()
                    return
                raise

    # Patch the adapter to skip DB reads, removing the serialization
    # bottleneck so threads actually race on build_role_links/to_string.
    # pylint: disable=protected-access
    adapter = enforcer._e.adapter
    original_load = adapter.load_policy
    adapter.load_policy = fast_adapter_load
    try:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=num_threads) as executor:
            futures = [
                executor.submit(worker, tid) for tid in range(num_threads)
            ]
            stop.wait(timeout=10)
            stop.set()
            for f in futures:
                f.result(timeout=5)
    finally:
        adapter.load_policy = original_load

    assert len(errors) == 0, (
        f'SyncedEnforcer should prevent the race, but got: {errors[0]}')

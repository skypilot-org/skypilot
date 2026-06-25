"""Tests that the cluster history WRITE path persists user_hash and
launched_resources, and that they survive `sky down` so the dashboard's
history view (cost report) can show the real User and Resources for a
terminated cluster.

This exercises the real recording functions
(``global_user_state.add_or_update_cluster`` /
``global_user_state.remove_cluster``) rather than seeding rows by hand, so it
covers the WRITE path, not just the READ path.
"""
from sky import clouds as sky_clouds
from sky import core
from sky import global_user_state
from sky import models
from sky import resources as resources_lib
from sky.backends import cloud_vm_ray_backend
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


def _make_handle(cluster_name, launched_resources, num_nodes=1):
    """Build a realistic CloudVmRayResourceHandle, as the launch path does."""
    return cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name=cluster_name,
        cluster_name_on_cloud=cluster_name,
        cluster_yaml=None,
        launched_nodes=num_nodes,
        launched_resources=launched_resources,
    )


def _k8s_resources(instance_type, accelerators=None):
    return resources_lib.Resources(
        cloud=sky_clouds.Kubernetes(),
        instance_type=instance_type,
        accelerators=accelerators,
    )


def _record_launch_lifecycle(cluster_name, launched_resources, num_nodes=1):
    """Mimic the real launch recording sequence in
    cloud_vm_ray_backend.CloudVmRayBackend:
      - INIT record (ready=False) at provisioning start
      - UP record (ready=True) at post-provision
    Both pass the handle that already carries launched_resources.
    """
    handle = _make_handle(cluster_name, launched_resources, num_nodes)
    # Provisioning start: INIT.
    global_user_state.add_or_update_cluster(
        cluster_name,
        cluster_handle=handle,
        requested_resources={launched_resources},
        ready=False,
    )
    # Post-provision: UP.
    global_user_state.add_or_update_cluster(
        cluster_name,
        cluster_handle=handle,
        requested_resources={launched_resources},
        ready=True,
    )


def _history_record(cluster_name):
    for r in global_user_state.get_clusters_from_history():
        if r['name'] == cluster_name:
            return r
    return None


def _cost_report_record(cluster_name):
    for r in core.cost_report(dashboard_summary_response=True,
                              exclude_managed_clusters=True):
        if r['name'] == cluster_name:
            return r
    return None


def test_history_persists_user_hash_and_resources_after_terminate(
        tmp_path, monkeypatch):
    """The history row keeps user_hash + launched_resources after `sky down`,
    and cost_report surfaces a real user and a real resources string."""
    _fresh_db(tmp_path, monkeypatch)

    user = common_utils.get_current_user()
    # Make the user name resolvable so user_name is populated (not just hash).
    global_user_state.add_or_update_user(
        models.User(id=user.id, name='test-user'))

    res = _k8s_resources('4CPU--16GB')
    _record_launch_lifecycle('reel-rl', res, num_nodes=1)

    # Terminate the cluster (sky down): deletes the clusters-table row but must
    # preserve the cluster_history row.
    global_user_state.remove_cluster('reel-rl', terminate=True)

    # The active cluster list no longer has it.
    assert global_user_state.get_cluster_from_name('reel-rl') is None

    # History still has the record with the real stored data.
    hist = _history_record('reel-rl')
    assert hist is not None
    assert hist['status'] is None  # terminated -> no clusters-table status
    assert hist['user_hash'] == user.id
    assert hist['user_name'] == 'test-user'
    assert hist['resources'] is not None  # launched_resources unpickled

    # The dashboard summary (cost_report) keeps user_hash and produces a real
    # resources string for the terminated cluster.
    report = _cost_report_record('reel-rl')
    assert report is not None
    assert report['user_hash'] == user.id
    assert report['user_name'] == 'test-user'
    # Real formatted resources string, NOT the '-' fallback.
    assert report['resources_str'] not in (None, '', '-')
    assert report['resources_str'].startswith('1x')
    assert 'cpus=4' in report['resources_str']


def test_cost_report_resources_string_for_gpu_kubernetes(tmp_path, monkeypatch):
    """A multi-node GPU Kubernetes cluster formats into a real resources
    string in the dashboard summary after termination."""
    _fresh_db(tmp_path, monkeypatch)

    user = common_utils.get_current_user()
    global_user_state.add_or_update_user(
        models.User(id=user.id, name='gpu-user'))

    res = _k8s_resources('8CPU--32GB--L4:1', accelerators={'L4': 1})
    _record_launch_lifecycle('gpu-job', res, num_nodes=2)
    global_user_state.remove_cluster('gpu-job', terminate=True)

    report = _cost_report_record('gpu-job')
    assert report is not None
    assert report['user_hash'] == user.id
    assert report['resources_str'] not in (None, '', '-')
    assert report['resources_str'].startswith('2x')
    assert 'L4:1' in report['resources_str']


def test_cost_report_user_falls_back_to_hash_when_user_row_missing(
        tmp_path, monkeypatch):
    """When the user row is genuinely gone, the User column shows the stored
    hash rather than an empty cell."""
    _fresh_db(tmp_path, monkeypatch)

    user = common_utils.get_current_user()
    # Deliberately do NOT add a user row -> user_name resolves to None.

    res = _k8s_resources('4CPU--16GB')
    _record_launch_lifecycle('orphan', res)
    global_user_state.remove_cluster('orphan', terminate=True)

    report = _cost_report_record('orphan')
    assert report is not None
    # Hash is still persisted and surfaced.
    assert report['user_hash'] == user.id
    # user_name falls back to the hash for display instead of being empty.
    assert report['user_name'] == user.id
    # Resources still format from the stored launched_resources.
    assert report['resources_str'].startswith('1x')

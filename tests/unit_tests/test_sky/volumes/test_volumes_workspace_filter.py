"""Listing volumes must not cross a workspace boundary.

Clusters and managed jobs are both filtered to the caller's accessible
workspaces; these tests pin the same behavior for volumes.
"""

from unittest import mock

import pytest
import sqlalchemy

from sky import global_user_state
from sky import models
from sky.utils import status_lib
from sky.volumes.server import core as volumes_core
from sky.workspaces import constants as workspace_constants

_ACTIVE_WORKSPACE = 'sky.global_user_state.skypilot_config.get_active_workspace'


@pytest.fixture()
def isolated_database(tmp_path):
    """A real database: these tests are about the query, not about mocks."""
    global_user_state._db_manager._engine = sqlalchemy.create_engine(
        f'sqlite:///{tmp_path / "state.db"}')
    global_user_state.create_table(global_user_state._db_manager.get_engine())
    yield
    global_user_state._db_manager._engine = None


def _add(name: str, workspace: str, is_ephemeral: bool = False) -> None:
    """Adds a volume owned by ``workspace``.

    add_volume records the workspace that is active at creation time, so the
    only way to seed a table spanning workspaces is to move the active one.
    """
    with mock.patch(_ACTIVE_WORKSPACE, return_value=workspace):
        global_user_state.add_volume(
            name,
            models.VolumeConfig(
                name=name,
                cloud='kubernetes',
                type='k8s-pvc',
                region='my-context',
                zone=None,
                size='1Gi',
                config={},
                name_on_cloud=f'{name}-on-cloud',
            ),
            status_lib.VolumeStatus.READY,
            is_ephemeral=is_ephemeral,
        )


def _seed_two_workspaces() -> None:
    _add('vol-a', workspace='ws-a')
    _add('vol-b', workspace='ws-b')


def test_filters_to_the_given_workspaces(isolated_database):
    _seed_two_workspaces()

    records = global_user_state.get_volumes(workspaces_filter={'ws-a'})

    assert [r['name'] for r in records] == ['vol-a']


def test_no_filter_returns_every_workspace(isolated_database):
    """Callers that must see the whole table -- the refresh daemon and the
    duplicate-backend-resource check -- pass no filter."""
    _seed_two_workspaces()

    records = global_user_state.get_volumes()

    assert sorted(r['name'] for r in records) == ['vol-a', 'vol-b']


def test_empty_filter_returns_nothing(isolated_database):
    """An empty set means "no accessible workspaces", not "no filter": a
    truthiness check here would show such a caller everything."""
    _seed_two_workspaces()

    assert global_user_state.get_volumes(workspaces_filter=set()) == []


def test_composes_with_ephemerality(isolated_database):
    _add('vol-a-persistent', workspace='ws-a', is_ephemeral=False)
    _add('vol-a-ephemeral', workspace='ws-a', is_ephemeral=True)
    _add('vol-b-persistent', workspace='ws-b', is_ephemeral=False)

    records = global_user_state.get_volumes(is_ephemeral=False,
                                            workspaces_filter={'ws-a'})

    assert [r['name'] for r in records] == ['vol-a-persistent']


def test_volume_list_only_returns_accessible_workspaces(isolated_database):
    """End to end through volume_list: a caller who can read only ws-a must
    not be told that ws-b's volume exists, nor who owns it."""
    _seed_two_workspaces()

    with mock.patch.object(volumes_core.workspaces_core,
                           'get_accessible_workspace_names',
                           return_value={'ws-a'}) as accessible:
        records = volumes_core.volume_list()

    assert [r.name for r in records] == ['vol-a']
    # READ, not WRITE: a workspace that is only read-only-visible to a
    # non-member still has its volumes listed, exactly as its clusters and
    # jobs already are.
    accessible.assert_called_once_with(
        action=workspace_constants.WORKSPACE_ACTION_READ)


def test_refresh_is_scoped_to_the_accessible_workspaces(isolated_database):
    """A caller who reads one workspace must not drive a full-table reconcile.

    volume_refresh groups its cloud calls by (context, namespace) taken from
    the volumes it was handed, so refreshing rows the caller cannot see means
    touching contexts reachable only from another workspace.
    """
    _seed_two_workspaces()

    with mock.patch.object(volumes_core.workspaces_core,
                           'get_accessible_workspace_names',
                           return_value={'ws-a'}):
        with mock.patch.object(volumes_core,
                               'volume_refresh') as volume_refresh:
            volumes_core.volume_list(refresh=True)

    volume_refresh.assert_called_once_with(volume_names=['vol-a'])

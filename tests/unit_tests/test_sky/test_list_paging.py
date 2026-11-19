"""Unit tests for `limit` and cursors on the cluster, storage, volume and
recipe lists."""
import asyncio
import textwrap
from typing import List, Optional
from unittest import mock

import pytest
import sqlalchemy
from sqlalchemy import orm

from sky import core
from sky import global_user_state
from sky import models
from sky.recipes import db as recipes_db
from sky.recipes.utils import RecipeType
from sky.server import server
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils.db import db_utils
from sky.volumes.server import server as volumes_server


@pytest.fixture
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


class _ClusterInfo:

    def get_node_names(self):
        return ['node-a']


class _Resources:
    priority = None
    priority_class = None


class _Handle:
    launched_resources = _Resources()
    cached_cluster_info = _ClusterInfo()


class _StorageHandle:
    sky_stores: dict = {}


def _add_cluster(name: str, launched_at: Optional[int]) -> None:
    global_user_state.add_or_update_cluster(cluster_name=name,
                                            cluster_handle=_Handle(),
                                            requested_resources=set(),
                                            ready=True)
    with orm.Session(global_user_state._db_manager.get_engine()) as session:
        session.execute(global_user_state.cluster_table.update().where(
            global_user_state.cluster_table.c.name == name).values(
                launched_at=launched_at))
        session.commit()


def _cluster_names(**kwargs) -> List[str]:
    return [r['name'] for r in global_user_state.get_clusters(**kwargs)]


class TestClusterPages:

    def _seed(self):
        _add_cluster('b-old', 100)
        _add_cluster('a-new', 300)
        _add_cluster('c-new', 300)
        _add_cluster('d-mid', 200)
        _add_cluster('e-unknown', None)

    def test_newest_launch_first_then_name(self, _fresh_db):
        self._seed()
        assert _cluster_names() == [
            'a-new', 'c-new', 'd-mid', 'b-old', 'e-unknown'
        ]

    def test_pages_follow_the_cursor(self, _fresh_db):
        self._seed()
        assert _cluster_names(limit=2) == ['a-new', 'c-new']
        assert _cluster_names(limit=2,
                              after=(300, 'c-new')) == ['d-mid', 'b-old']
        # A cluster without a launch time sorts as launched at 0.
        assert _cluster_names(limit=2, after=(100, 'b-old')) == ['e-unknown']
        assert _cluster_names(limit=2, after=(0, 'e-unknown')) == []

    def test_a_tie_continues_by_name(self, _fresh_db):
        self._seed()
        assert _cluster_names(limit=1, after=(300, 'a-new')) == ['c-new']

    def test_pages_keep_the_name_filter(self, _fresh_db):
        self._seed()
        assert _cluster_names(cluster_names=['b-old', 'c-new', 'e-unknown'],
                              limit=2,
                              after=(300, 'c-new')) == ['b-old', 'e-unknown']


class TestStoragePages:

    def test_pages_by_name(self, _fresh_db):
        for name in ('s-c', 's-a', 's-b'):
            global_user_state.add_or_update_storage(
                name, _StorageHandle(), status_lib.StorageStatus.READY)

        def names(**kwargs):
            return [r['name'] for r in global_user_state.get_storage(**kwargs)]

        assert names() == ['s-a', 's-b', 's-c']
        assert names(limit=2) == ['s-a', 's-b']
        assert names(limit=2, after_name='s-b') == ['s-c']


@pytest.fixture
def _volume_db(tmp_path):
    global_user_state._db_manager._engine = sqlalchemy.create_engine(
        f'sqlite:///{tmp_path / "state.db"}')
    global_user_state.create_table(global_user_state._db_manager.get_engine())
    yield
    global_user_state._db_manager._engine = None


def _add_volume(name: str) -> None:
    global_user_state.add_volume(
        name,
        models.VolumeConfig(name=name,
                            cloud='kubernetes',
                            type='k8s-pvc',
                            region='my-context',
                            zone=None,
                            size='1Gi',
                            config={},
                            name_on_cloud=f'{name}-on-cloud'),
        status_lib.VolumeStatus.READY)


class TestVolumePages:

    def _names(self, **kwargs):
        return [r['name'] for r in global_user_state.get_volumes(**kwargs)]

    def test_pages_by_name(self, _volume_db):
        for name in ('vol-c', 'vol-a', 'vol-b'):
            _add_volume(name)
        assert self._names() == ['vol-a', 'vol-b', 'vol-c']
        assert self._names(limit=2) == ['vol-a', 'vol-b']
        assert self._names(limit=2, after_name='vol-b') == ['vol-c']

    def test_pages_across_name_batches(self, _volume_db, monkeypatch):
        for name in ('vol-c', 'vol-a', 'vol-b', 'vol-d'):
            _add_volume(name)
        monkeypatch.setattr(global_user_state, '_CLUSTER_IN_QUERY_CHUNK_SIZE',
                            1)
        named = ['vol-d', 'vol-b', 'vol-a', 'vol-c']
        assert self._names(volume_names=named, limit=2) == ['vol-a', 'vol-b']
        assert self._names(volume_names=named, limit=2,
                           after_name='vol-b') == ['vol-c', 'vol-d']


_RECIPE = textwrap.dedent("""
    resources:
      cpus: 2
    run: echo hello
    """).strip()


@pytest.fixture
def _recipes_db(tmp_path, monkeypatch):
    engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path}/recipes.db')
    recipes_db.Base.metadata.create_all(engine)
    monkeypatch.setattr(recipes_db._db_manager, '_engine', engine)
    yield engine


class TestRecipePages:

    def test_a_page_is_by_name_not_pinned_first(self, _recipes_db):
        for name in ('r-c', 'r-a', 'r-b'):
            recipes_db.create_recipe(name=name,
                                     content=_RECIPE,
                                     recipe_type=RecipeType.CLUSTER,
                                     user_id='u')
        recipes_db.toggle_pin('r-c', True)

        def names(**kwargs):
            return [r.name for r in recipes_db.list_recipes(**kwargs)]

        assert names() == ['r-c', 'r-a', 'r-b']
        assert names(limit=2) == ['r-a', 'r-b']
        assert names(limit=2, after_name='r-b') == ['r-c']


class TestPageChecks:

    @pytest.mark.parametrize('limit,has_cursor,message', [
        (0, False, 'at least 1'),
        (None, True, 'requires limit'),
    ])
    def test_refusals(self, limit, has_cursor, message):
        with pytest.raises(ValueError, match=message):
            common_utils.check_page(limit, has_cursor)

    def test_accepted(self):
        common_utils.check_page(None, False)
        common_utils.check_page(5, True)

    def test_status_cursor_takes_both_fields(self):
        with pytest.raises(ValueError, match='go together'):
            core.status(limit=5, after_name='c')
        with pytest.raises(ValueError, match='requires limit'):
            core.status(after_launched_at=1, after_name='c')

    def test_status_passes_the_page(self):
        with mock.patch.object(core.backend_utils,
                               'get_clusters',
                               return_value=[]) as get_clusters:
            core.status(limit=5, after_launched_at=7, after_name='c')
        assert get_clusters.call_args.kwargs['limit'] == 5
        assert get_clusters.call_args.kwargs['after'] == (7, 'c')


def _request():
    request = mock.Mock()
    request.state.request_id = 'req-1'
    request.state.auth_user = None
    return request


class TestRoutes:

    def test_storage_ls_takes_the_page(self, monkeypatch):
        schedule = mock.AsyncMock()
        monkeypatch.setattr(executor, 'schedule_request_async', schedule)
        asyncio.run(server.storage_ls(_request(), limit=2, after_name='s-a'))
        body = schedule.call_args.kwargs['request_body']
        assert isinstance(body, payloads.StorageListBody)
        assert body.to_kwargs()['limit'] == 2
        assert body.to_kwargs()['after_name'] == 's-a'

    def test_volume_list_takes_the_page(self, monkeypatch):
        schedule = mock.AsyncMock()
        monkeypatch.setattr(executor, 'schedule_request_async', schedule)
        asyncio.run(
            volumes_server.volume_list(_request(),
                                       refresh=False,
                                       names=None,
                                       limit=2,
                                       after_name='vol-a'))
        body = schedule.call_args.kwargs['request_body']
        assert body.limit == 2 and body.after_name == 'vol-a'

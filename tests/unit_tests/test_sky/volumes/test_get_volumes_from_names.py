"""Looking up a few volumes must not read the whole volume table."""

import pytest
import sqlalchemy

from sky import global_user_state
from sky import models
from sky.utils import status_lib


@pytest.fixture()
def isolated_database(tmp_path):
    """A real database: these tests are about the query, not about mocks."""
    global_user_state._db_manager._engine = sqlalchemy.create_engine(
        f'sqlite:///{tmp_path / "state.db"}')
    global_user_state.create_table(global_user_state._db_manager.get_engine())
    yield
    global_user_state._db_manager._engine = None


def _add(name: str, is_ephemeral: bool = False) -> None:
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


def test_returns_only_the_named_volumes(isolated_database):
    for name in ('vol-a', 'vol-b', 'vol-c'):
        _add(name)

    records = global_user_state.get_volumes_from_names(['vol-a', 'vol-c'])

    assert sorted(r['name'] for r in records) == ['vol-a', 'vol-c']


def _comparable(record):
    """The record minus its handle, which is a pydantic model whose __eq__
    recurses on nested config."""
    record = dict(record)
    handle = record.pop('handle')
    record['handle_name_on_cloud'] = handle.name_on_cloud
    return record


def test_shape_matches_get_volumes(isolated_database):
    """Callers switching between the two accessors must not have to check
    which keys they now have."""
    _add('vol-a')

    from_names = global_user_state.get_volumes_from_names(['vol-a'])[0]
    from_all = global_user_state.get_volumes()[0]

    assert _comparable(from_names) == _comparable(from_all)


def test_get_volume_by_name_shape_matches_too(isolated_database):
    """get_volume_by_name used to omit is_ephemeral, so a caller that
    filtered on it against that record silently dropped the filter."""
    _add('vol-a', is_ephemeral=True)

    by_name = global_user_state.get_volume_by_name('vol-a')

    assert by_name['is_ephemeral'] is True
    assert _comparable(by_name) == _comparable(
        global_user_state.get_volumes()[0])


def test_unknown_names_are_absent_not_an_error(isolated_database):
    _add('vol-a')

    records = global_user_state.get_volumes_from_names(['vol-a', 'nope'])

    assert [r['name'] for r in records] == ['vol-a']


def test_empty_input_does_not_query(isolated_database):
    _add('vol-a')

    assert global_user_state.get_volumes_from_names([]) == []


@pytest.mark.parametrize('is_ephemeral', [True, False])
def test_filters_by_ephemerality_like_get_volumes(isolated_database,
                                                  is_ephemeral):
    _add('vol-persistent', is_ephemeral=False)
    _add('vol-ephemeral', is_ephemeral=True)
    names = ['vol-persistent', 'vol-ephemeral']

    records = global_user_state.get_volumes_from_names(
        names, is_ephemeral=is_ephemeral)

    expected = 'vol-ephemeral' if is_ephemeral else 'vol-persistent'
    assert [r['name'] for r in records] == [expected]


def test_more_names_than_one_chunk(isolated_database, monkeypatch):
    """The IN list is chunked to stay under SQLite's bound-parameter cap, so
    a lookup spanning chunks must still return every match."""
    monkeypatch.setattr(global_user_state, '_CLUSTER_IN_QUERY_CHUNK_SIZE', 2)
    names = [f'vol-{i}' for i in range(5)]
    for name in names:
        _add(name)

    records = global_user_state.get_volumes_from_names(names)

    assert sorted(r['name'] for r in records) == sorted(names)

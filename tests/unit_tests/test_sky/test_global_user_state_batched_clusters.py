"""Unit tests for the batched cluster-lookup helpers in global_user_state.

These helpers exist so pool dashboard (and any future caller iterating many
replicas) can avoid the per-name DB round-trip that would otherwise show up
as a double N+1 inside ReplicaInfo.to_info_dict.
"""
from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file (mirrors the helper in
    test_global_user_state_cluster_events.py)."""
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


class _MinimalHandle:
    """Just enough for global_user_state.add_or_update_cluster to pickle."""
    launched_resources = None


def _add_cluster(name: str) -> None:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=False,
    )


def test_get_clusters_from_names_empty_input_returns_empty(
        tmp_path, monkeypatch):
    """Empty input must NOT hit the DB and must return {} so callers can
    safely pass empty lists from comprehensions."""
    _fresh_db(tmp_path, monkeypatch)
    # No mocks needed — the function returns before touching the engine.
    assert not global_user_state.get_clusters_from_names([])


def test_get_clusters_from_names_returns_record_per_name(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('alive-1')
    _add_cluster('alive-2')

    result = global_user_state.get_clusters_from_names(['alive-1', 'alive-2'])

    assert set(result.keys()) == {'alive-1', 'alive-2'}
    assert result['alive-1'] is not None
    assert result['alive-2'] is not None
    assert result['alive-1']['name'] == 'alive-1'
    assert result['alive-2']['name'] == 'alive-2'
    # summary_response defaults to True, so these extra fields are absent.
    assert 'last_creation_yaml' not in result['alive-1']
    assert 'last_creation_command' not in result['alive-1']


def test_get_clusters_from_names_missing_names_become_none(
        tmp_path, monkeypatch):
    """Names that don't exist in the cluster table must appear in the result
    mapped to None — callers (e.g. _get_service_status) rely on this to know
    which replicas need a handle fallback lookup."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('alive-1')

    result = global_user_state.get_clusters_from_names(
        ['alive-1', 'gone-1', 'gone-2'])

    assert result['alive-1'] is not None
    assert result['gone-1'] is None
    assert result['gone-2'] is None


def test_get_clusters_from_names_summary_false_includes_extras(
        tmp_path, monkeypatch):
    """summary_response=False mirrors the single-name path and surfaces
    the extra last_creation_* columns."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('alive-1')

    result = global_user_state.get_clusters_from_names(['alive-1'],
                                                       summary_response=False)

    assert 'last_creation_yaml' in result['alive-1']
    assert 'last_creation_command' in result['alive-1']


def test_get_handles_from_cluster_names_chunks_large_input(
        tmp_path, monkeypatch):
    """`get_handles_from_cluster_names` shares the IN-chunking knob with
    `get_clusters_from_names`. Same setup: shrink chunk size to 2, insert 5
    clusters, verify all five names resolve through the loop."""
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(global_user_state, '_CLUSTER_IN_QUERY_CHUNK_SIZE', 2)
    names = [f'c-{i}' for i in range(5)]
    for name in names:
        _add_cluster(name)
    # Missing names just don't appear in the result (this helper doesn't
    # None-fill, matching its existing contract).
    result = global_user_state.get_handles_from_cluster_names(
        set(names) | {'missing'})

    assert set(result.keys()) == set(names)
    for name in names:
        # Each handle round-trips through pickle.loads, so we only assert it
        # came back as the right type rather than identity.
        assert isinstance(result[name], _MinimalHandle), name


def test_get_clusters_from_names_chunks_large_input(tmp_path, monkeypatch):
    """Names beyond a single batch must still be resolved. Shrink the chunk
    size to 2 so we exercise the loop with only 5 clusters."""
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(global_user_state, '_CLUSTER_IN_QUERY_CHUNK_SIZE', 2)
    names = [f'c-{i}' for i in range(5)]
    for name in names:
        _add_cluster(name)
    # Also include a missing name to confirm the None-fill behavior survives
    # chunking.
    queried = names + ['missing']

    result = global_user_state.get_clusters_from_names(queried)

    assert set(result.keys()) == set(queried)
    for name in names:
        assert result[name] is not None, name
        assert result[name]['name'] == name
    assert result['missing'] is None


def test_get_clusters_from_names_matches_single_helper(tmp_path, monkeypatch):
    """Batched record for an existing cluster must match the single-name
    helper field-for-field (modulo summary_response/include_user_info-only
    keys), so callers can swap one for the other without subtle diffs."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('alive-1')

    batched = global_user_state.get_clusters_from_names(
        ['alive-1'], include_user_info=False, summary_response=True)['alive-1']
    single = global_user_state.get_cluster_from_name('alive-1',
                                                     include_user_info=False,
                                                     summary_response=True)

    # handle is a freshly unpickled instance each call (no __eq__ defined on
    # _MinimalHandle), so identity comparison fails. Verify the rest match
    # field-for-field, and the handle type is at least the same.
    assert batched is not None and single is not None
    assert set(batched.keys()) == set(single.keys())
    for key in batched:
        if key == 'handle':
            assert type(batched[key]) is type(single[key])  # noqa: E721
        else:
            assert batched[key] == single[key], f'mismatch on {key}'

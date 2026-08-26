"""Unit tests for system_config accessors in global_user_state."""
from sqlalchemy import orm

from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file.

    Same construction as `sky/global_user_state.py` (including
    `post_init_fn`), against a location derived from `SKY_RUNTIME_DIR`.
    """
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


def _row(config_key):
    engine = global_user_state._db_manager.get_engine()
    with orm.Session(engine) as session:
        return session.query(global_user_state.system_config_table).filter_by(
            config_key=config_key).first()


def test_get_or_set_inserts_when_missing(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    assert global_user_state.get_or_set_system_config('k', 'v1') == 'v1'
    assert global_user_state.get_system_config('k') == 'v1'


def test_get_or_set_never_overwrites(tmp_path, monkeypatch):
    """The whole point: the loser of a race adopts the winner's value.

    Overwriting a signing secret here is unrecoverable -- every token already
    issued stops verifying.
    """
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.get_or_set_system_config('k', 'first')
    before = _row('k')

    assert global_user_state.get_or_set_system_config('k', 'second') == 'first'

    after = _row('k')
    assert after.config_value == 'first'
    assert after.updated_at == before.updated_at
    assert after.created_at == before.created_at


def test_set_system_config_still_overwrites(tmp_path, monkeypatch):
    """`set_system_config` keeps its upsert semantics for other callers."""
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_system_config('k', 'first')
    global_user_state.set_system_config('k', 'second')
    assert global_user_state.get_system_config('k') == 'second'


def test_get_or_set_is_per_key(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.get_or_set_system_config('a', '1')
    assert global_user_state.get_or_set_system_config('b', '2') == '2'
    assert global_user_state.get_system_config('a') == '1'


def test_count_service_account_tokens(tmp_path, monkeypatch):
    """The orphan-token alarm counts rows instead of loading them."""
    _fresh_db(tmp_path, monkeypatch)
    assert global_user_state.count_service_account_tokens() == 0

    global_user_state.add_service_account_token(token_id='t1',
                                                token_name='ci',
                                                token_hash='h1',
                                                creator_user_hash='alice',
                                                service_account_user_id='sa-1',
                                                expires_at=None)
    assert global_user_state.count_service_account_tokens() == 1

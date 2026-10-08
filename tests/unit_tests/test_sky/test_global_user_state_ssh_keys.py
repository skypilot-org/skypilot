"""Unit tests for ssh_key accessors in global_user_state."""
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


def _row(user_hash):
    engine = global_user_state._db_manager.get_engine()
    with orm.Session(engine) as session:
        return session.query(global_user_state.ssh_key_table).filter_by(
            user_hash=user_hash).first()


def test_get_or_set_inserts_when_missing(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    assert global_user_state.get_or_set_ssh_keys('u', 'pub1',
                                                 'priv1') == ('pub1', 'priv1')
    assert global_user_state.get_ssh_keys('u') == ('pub1', 'priv1', True)


def test_get_or_set_never_overwrites(tmp_path, monkeypatch):
    """The whole point: the loser of a race adopts the winner's pair.

    API servers sharing a database but not the local key directory can race
    to bootstrap the same user's key pair; a loser that kept its own pair
    would fail SSH into clusters launched from the winner.
    """
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_ssh_keys('u', 'pub-first', 'priv-first')

    assert global_user_state.get_or_set_ssh_keys(
        'u', 'pub-second', 'priv-second') == ('pub-first', 'priv-first')

    row = _row('u')
    assert row.ssh_public_key == 'pub-first'
    assert row.ssh_private_key == 'priv-first'


def test_set_ssh_keys_still_overwrites(tmp_path, monkeypatch):
    """`set_ssh_keys` keeps its upsert semantics for existing callers."""
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_ssh_keys('u', 'pub-first', 'priv-first')
    global_user_state.set_ssh_keys('u', 'pub-second', 'priv-second')
    assert global_user_state.get_ssh_keys('u') == ('pub-second', 'priv-second',
                                                   True)


def test_get_or_set_is_per_user(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.get_or_set_ssh_keys('a', 'pub-a', 'priv-a')
    assert global_user_state.get_or_set_ssh_keys('b', 'pub-b',
                                                 'priv-b') == ('pub-b',
                                                               'priv-b')
    assert global_user_state.get_ssh_keys('a') == ('pub-a', 'priv-a', True)

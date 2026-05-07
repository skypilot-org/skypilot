"""Unit tests for check_results accessors in global_user_state."""
from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file.

    SkyPilot derives the SQLite path from `SKY_RUNTIME_DIR` (default `~`) +
    `.sky/state.db`.  Override the env var and reset the cached
    DatabaseManager so a fresh engine + tables are created against the new
    location.  Mirror the production construction in
    `sky/global_user_state.py` (including `post_init_fn`) so the engine is
    set up faithfully.
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
    return tmp_path / '.sky' / 'state.db'


def test_get_returns_empty_when_no_row(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    assert global_user_state.get_cached_check_results('default') == {}


def test_set_then_get_full_run(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    results = {
        'AWS': {
            '': {
                'enabled': True,
                'reason': 'enabled.'
            }
        },
        'Kubernetes': {
            'ctx-a': {
                'enabled': True,
                'reason': 'enabled.'
            },
            'ctx-b': {
                'enabled': False,
                'reason': 'Forbidden'
            },
        },
    }
    global_user_state.set_check_results(results,
                                        workspace='default',
                                        is_full_workspace_run=True)
    assert global_user_state.get_cached_check_results('default') == results


def test_full_run_replaces_entire_row(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_check_results(
        {
            'AWS': {
                '': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            },
            'GCP': {
                '': {
                    'enabled': False,
                    'reason': 'no creds'
                }
            }
        },
        workspace='default',
        is_full_workspace_run=True)
    # Full run that drops GCP entirely.
    global_user_state.set_check_results(
        {'AWS': {
            '': {
                'enabled': True,
                'reason': 'enabled.'
            }
        }},
        workspace='default',
        is_full_workspace_run=True)
    assert global_user_state.get_cached_check_results('default') == {
        'AWS': {
            '': {
                'enabled': True,
                'reason': 'enabled.'
            }
        },
    }


def test_scoped_run_merges_other_clouds(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_check_results(
        {
            'AWS': {
                '': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            },
            'Kubernetes': {
                'ctx-a': {
                    'enabled': True,
                    'reason': 'enabled.'
                }
            }
        },
        workspace='default',
        is_full_workspace_run=True)
    # Single-cloud run for AWS only - must preserve Kubernetes.
    global_user_state.set_check_results(
        {'AWS': {
            '': {
                'enabled': False,
                'reason': 'creds expired'
            }
        }},
        workspace='default',
        is_full_workspace_run=False)
    assert global_user_state.get_cached_check_results('default') == {
        'AWS': {
            '': {
                'enabled': False,
                'reason': 'creds expired'
            }
        },
        'Kubernetes': {
            'ctx-a': {
                'enabled': True,
                'reason': 'enabled.'
            }
        },
    }


def test_scoped_run_preserves_sibling_contexts_within_cloud(
        tmp_path, monkeypatch):
    """A scoped run that touches a subset of contexts within a cloud
    must not drop the leaves for sibling contexts that weren't
    re-probed.  This is the case for per-context lookups on
    multi-context Kubernetes clouds — a single-context recheck would
    otherwise clobber every other context's status under the cloud
    key, making them appear "not enabled" until the next full run.
    """
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_check_results(
        {
            'Kubernetes': {
                'ctx-a': {
                    'enabled': True,
                    'reason': 'enabled.'
                },
                'ctx-b': {
                    'enabled': True,
                    'reason': 'enabled.'
                },
                'ctx-c': {
                    'enabled': True,
                    'reason': 'enabled.'
                },
            }
        },
        workspace='default',
        is_full_workspace_run=True)
    # Scoped run that only re-probes ctx-b — ctx-a and ctx-c must
    # survive intact, and ctx-b's leaf must update.
    global_user_state.set_check_results(
        {'Kubernetes': {
            'ctx-b': {
                'enabled': False,
                'reason': 'Forbidden'
            }
        }},
        workspace='default',
        is_full_workspace_run=False)
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': {
            'ctx-a': {
                'enabled': True,
                'reason': 'enabled.'
            },
            'ctx-b': {
                'enabled': False,
                'reason': 'Forbidden'
            },
            'ctx-c': {
                'enabled': True,
                'reason': 'enabled.'
            },
        },
    }


def test_workspace_isolation(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    global_user_state.set_check_results(
        {'AWS': {
            '': {
                'enabled': True,
                'reason': 'a'
            }
        }},
        workspace='ws-a',
        is_full_workspace_run=True)
    global_user_state.set_check_results(
        {'AWS': {
            '': {
                'enabled': True,
                'reason': 'b'
            }
        }},
        workspace='ws-b',
        is_full_workspace_run=True)
    assert global_user_state.get_cached_check_results('ws-a') == {
        'AWS': {
            '': {
                'enabled': True,
                'reason': 'a'
            }
        },
    }
    assert global_user_state.get_cached_check_results('ws-b') == {
        'AWS': {
            '': {
                'enabled': True,
                'reason': 'b'
            }
        },
    }

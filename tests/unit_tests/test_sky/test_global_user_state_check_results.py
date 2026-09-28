"""Unit tests for check_results accessors in global_user_state."""
import pytest

from sky import global_user_state
from sky import models
from sky.clouds import cloud
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


@pytest.mark.parametrize(
    'slurm_config,expected',
    [
        ({}, False),
        ({
            'submit_as_user': True
        }, True),
        ({
            'cluster_configs': {
                'cluster-a': {
                    'submit_as_user': True
                }
            }
        }, True),
        ({
            'cluster_configs': {
                'cluster-a': {
                    'submit_as_user': False
                }
            }
        }, False),
    ],
)
def test_detects_submit_as_user_config(monkeypatch, slurm_config, expected):
    monkeypatch.setattr(global_user_state.skypilot_config, 'get_nested',
                        lambda *args, **kwargs: slurm_config)
    assert global_user_state._slurm_submit_as_user_enabled() is expected


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


def test_check_results_and_enabled_clouds_are_user_scoped(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    current_user = [models.User(id='alice-id', name='alice@example.com')]
    monkeypatch.setattr(global_user_state, '_slurm_submit_as_user_enabled',
                        lambda: True)
    monkeypatch.setattr(global_user_state.common_utils, 'get_current_user',
                        lambda: current_user[0])
    alice_results = {
        'Slurm': {
            'cluster-a': {
                'enabled': True,
                'reason': 'alice home is shared'
            }
        }
    }
    global_user_state.set_check_results(alice_results,
                                        workspace='default',
                                        is_full_workspace_run=True)
    global_user_state.set_enabled_clouds(['Slurm'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')

    current_user[0] = models.User(id='bob-id', name='bob@example.com')
    assert global_user_state.get_cached_check_results('default') == {}
    assert global_user_state.get_cached_enabled_clouds(
        cloud.CloudCapability.COMPUTE, workspace='default') == []

    bob_results = {
        'Slurm': {
            'cluster-a': {
                'enabled': True,
                'reason': 'bob home is not shared'
            }
        }
    }
    global_user_state.set_check_results(bob_results,
                                        workspace='default',
                                        is_full_workspace_run=True)

    current_user[0] = models.User(id='alice-id', name='alice@example.com')
    assert global_user_state.get_cached_check_results(
        'default') == alice_results
    assert [
        repr(c) for c in global_user_state.get_cached_enabled_clouds(
            cloud.CloudCapability.COMPUTE, workspace='default')
    ] == ['Slurm']


def test_shared_credential_clouds_are_visible_to_every_user(
        tmp_path, monkeypatch):
    """Regression test for #10588.

    With Slurm submit-as-user enabled, only Slurm's check result depends on
    the requesting user. Clouds whose credentials live on the API server
    (e.g. Kubernetes) must stay visible to users who never ran `sky check`.
    """
    _fresh_db(tmp_path, monkeypatch)
    current_user = [models.User(id='alice-id', name='alice@example.com')]
    monkeypatch.setattr(global_user_state, '_slurm_submit_as_user_enabled',
                        lambda: True)
    monkeypatch.setattr(global_user_state.common_utils, 'get_current_user',
                        lambda: current_user[0])
    k8s_result = {'ctx-a': {'enabled': True, 'reason': 'enabled.'}}
    alice_slurm = {'cluster-a': {'enabled': True, 'reason': 'alice ok'}}
    global_user_state.set_check_results(
        {
            'Kubernetes': k8s_result,
            'Slurm': alice_slurm
        },
        workspace='default',
        is_full_workspace_run=True)
    global_user_state.set_enabled_clouds(['Kubernetes', 'Slurm'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')

    # Bob has never run `sky check`: he sees the shared cloud, not Slurm.
    current_user[0] = models.User(id='bob-id', name='bob@example.com')
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': k8s_result
    }
    assert [
        repr(c) for c in global_user_state.get_cached_enabled_clouds(
            cloud.CloudCapability.COMPUTE, workspace='default')
    ] == ['Kubernetes']

    # Bob's full run: Slurm is not usable for him. The Slurm result goes to
    # his row only; the Kubernetes result is shared with everyone.
    bob_slurm = {'cluster-a': {'enabled': False, 'reason': 'no account'}}
    global_user_state.set_check_results(
        {
            'Kubernetes': k8s_result,
            'Slurm': bob_slurm
        },
        workspace='default',
        is_full_workspace_run=True)
    global_user_state.set_enabled_clouds(['Kubernetes'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': k8s_result,
        'Slurm': bob_slurm
    }
    assert [
        repr(c) for c in global_user_state.get_cached_enabled_clouds(
            cloud.CloudCapability.COMPUTE, workspace='default')
    ] == ['Kubernetes']

    # Alice keeps her own Slurm result and enablement.
    current_user[0] = models.User(id='alice-id', name='alice@example.com')
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': k8s_result,
        'Slurm': alice_slurm
    }
    assert [
        repr(c) for c in global_user_state.get_cached_enabled_clouds(
            cloud.CloudCapability.COMPUTE, workspace='default')
    ] == ['Kubernetes', 'Slurm']


def test_scoped_slurm_run_does_not_touch_shared_row_when_user_scoped(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    current_user = [models.User(id='alice-id', name='alice@example.com')]
    monkeypatch.setattr(global_user_state, '_slurm_submit_as_user_enabled',
                        lambda: True)
    monkeypatch.setattr(global_user_state.common_utils, 'get_current_user',
                        lambda: current_user[0])
    k8s_result = {'ctx-a': {'enabled': True, 'reason': 'enabled.'}}
    global_user_state.set_check_results({'Kubernetes': k8s_result},
                                        workspace='default',
                                        is_full_workspace_run=True)

    current_user[0] = models.User(id='bob-id', name='bob@example.com')
    bob_slurm = {'cluster-a': {'enabled': True, 'reason': 'bob ok'}}
    global_user_state.set_check_results({'Slurm': bob_slurm},
                                        workspace='default',
                                        is_full_workspace_run=False)
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': k8s_result,
        'Slurm': bob_slurm
    }

    current_user[0] = models.User(id='alice-id', name='alice@example.com')
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': k8s_result
    }


def test_check_results_remain_workspace_scoped_when_submit_user_disabled(
        tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    current_user = [models.User(id='alice-id', name='alice@example.com')]
    monkeypatch.setattr(global_user_state, '_slurm_submit_as_user_enabled',
                        lambda: False)
    monkeypatch.setattr(global_user_state.common_utils, 'get_current_user',
                        lambda: current_user[0])
    results = {'AWS': {'': {'enabled': True, 'reason': 'enabled'}}}
    global_user_state.set_check_results(results,
                                        workspace='default',
                                        is_full_workspace_run=True)

    current_user[0] = models.User(id='bob-id', name='bob@example.com')
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


def _enabled(workspace='default'):
    return [
        repr(c) for c in global_user_state.get_cached_enabled_clouds(
            cloud.CloudCapability.COMPUTE, workspace=workspace)
    ]


def _submit_as_user(monkeypatch, enabled, user_id='alice-id'):
    state = {'enabled': enabled, 'user': user_id}
    monkeypatch.setattr(global_user_state, '_slurm_submit_as_user_enabled',
                        lambda: state['enabled'])
    monkeypatch.setattr(
        global_user_state.common_utils, 'get_current_user',
        lambda: models.User(id=state['user'], name=state['user']))
    return state


def test_first_time_user_needs_user_scoped_refresh(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    state = _submit_as_user(monkeypatch, True)
    global_user_state.set_enabled_clouds(['Kubernetes', 'Slurm'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')
    assert not global_user_state.is_user_scoped_enabled_clouds_cache_missing(
        cloud.CloudCapability.COMPUTE, 'default')

    # Bob sees Kubernetes from the shared row, so the cached list is not
    # empty, but his Slurm enablement has never been checked.
    state['user'] = 'bob-id'
    assert _enabled() == ['Kubernetes']
    assert global_user_state.is_user_scoped_enabled_clouds_cache_missing(
        cloud.CloudCapability.COMPUTE, 'default')

    # Once his check has written a (possibly empty) row, no more refreshes.
    global_user_state.set_enabled_clouds(
        ['Kubernetes'],
        cloud.CloudCapability.COMPUTE,
        workspace='default',
        previously_enabled_clouds=['Kubernetes'])
    assert not global_user_state.is_user_scoped_enabled_clouds_cache_missing(
        cloud.CloudCapability.COMPUTE, 'default')

    state['enabled'] = False
    assert not global_user_state.is_user_scoped_enabled_clouds_cache_missing(
        cloud.CloudCapability.COMPUTE, 'default')


def test_turning_submit_as_user_off_keeps_shared_slurm(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    state = _submit_as_user(monkeypatch, False)
    global_user_state.set_enabled_clouds(['Kubernetes', 'Slurm'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')
    slurm = {'cluster-a': {'enabled': True, 'reason': 'shared creds'}}
    k8s = {'ctx-a': {'enabled': True, 'reason': 'enabled.'}}
    global_user_state.set_check_results({
        'Kubernetes': k8s,
        'Slurm': slurm
    },
                                        workspace='default',
                                        is_full_workspace_run=True)

    # A user without Slurm access checks while submit-as-user is on.
    state['enabled'] = True
    state['user'] = 'bob-id'
    global_user_state.set_enabled_clouds(['Kubernetes'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')
    global_user_state.set_check_results(
        {
            'Kubernetes': k8s,
            'Slurm': {
                'cluster-a': {
                    'enabled': False,
                    'reason': 'no account'
                }
            }
        },
        workspace='default',
        is_full_workspace_run=True)
    assert _enabled() == ['Kubernetes']

    # Turning the flag off again restores the pre-flag shared state.
    state['enabled'] = False
    assert sorted(_enabled()) == ['Kubernetes', 'Slurm']
    assert global_user_state.get_cached_check_results('default') == {
        'Kubernetes': k8s,
        'Slurm': slurm
    }


@pytest.mark.parametrize('submit_as_user', [False, True])
def test_set_enabled_clouds_applies_delta_to_current_row(
        tmp_path, monkeypatch, submit_as_user):
    _fresh_db(tmp_path, monkeypatch)
    state = _submit_as_user(monkeypatch, submit_as_user)
    global_user_state.set_enabled_clouds(['Kubernetes'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default')
    # Bob reads the enabled clouds to start `sky check slurm`...
    state['user'] = 'bob-id'
    bob_read = _enabled()
    assert bob_read == ['Kubernetes']
    # ...an admin's `sky check aws` commits in the meantime...
    state['user'] = 'admin-id'
    global_user_state.set_enabled_clouds(['Kubernetes', 'AWS'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default',
                                         previously_enabled_clouds=_enabled())
    # ...and Bob's write, based on his stale read, must not drop AWS.
    state['user'] = 'bob-id'
    global_user_state.set_enabled_clouds(bob_read + ['Slurm'],
                                         cloud.CloudCapability.COMPUTE,
                                         workspace='default',
                                         previously_enabled_clouds=bob_read)
    assert sorted(_enabled()) == ['AWS', 'Kubernetes', 'Slurm']

    # A delta still removes what the caller disabled.
    global_user_state.set_enabled_clouds(
        ['Kubernetes', 'Slurm'],
        cloud.CloudCapability.COMPUTE,
        workspace='default',
        previously_enabled_clouds=['AWS', 'Kubernetes', 'Slurm'])
    assert sorted(_enabled()) == ['Kubernetes', 'Slurm']


def test_legacy_per_user_rows_are_read_through(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    state = _submit_as_user(monkeypatch, True)
    key = global_user_state._get_enabled_clouds_key(
        cloud.CloudCapability.COMPUTE, 'default')
    engine = global_user_state._db_manager.get_engine()
    with global_user_state.orm.Session(engine) as session:
        # The layout written before shared and user rows were split.
        global_user_state._upsert_config_value(session, f'{key}_alice-id',
                                               ['Kubernetes', 'Slurm'])
        session.commit()

    assert _enabled() == ['Kubernetes', 'Slurm']
    assert not global_user_state.is_user_scoped_enabled_clouds_cache_missing(
        cloud.CloudCapability.COMPUTE, 'default')

    # The first write promotes the shared clouds into the workspace row, so
    # other users see them too.
    global_user_state.set_enabled_clouds(
        ['Kubernetes', 'Slurm'],
        cloud.CloudCapability.COMPUTE,
        workspace='default',
        previously_enabled_clouds=['Kubernetes', 'Slurm'])
    state['user'] = 'bob-id'
    assert _enabled() == ['Kubernetes']


def test_user_rows_do_not_collide_with_workspace_rows(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _submit_as_user(monkeypatch, True)
    alice_slurm = {'cluster-a': {'enabled': True, 'reason': 'alice ok'}}
    global_user_state.set_check_results({'Slurm': alice_slurm},
                                        workspace='team',
                                        is_full_workspace_run=True)
    other = {'AWS': {'': {'enabled': True, 'reason': 'enabled.'}}}
    global_user_state.set_check_results(other,
                                        workspace='team_alice-id',
                                        is_full_workspace_run=True)
    assert global_user_state.get_cached_check_results('team') == {
        'Slurm': alice_slurm
    }
    assert global_user_state.get_cached_check_results('team_alice-id') == other


@pytest.mark.parametrize('submit_as_user', [False, True])
@pytest.mark.parametrize('corrupt', ['null', '[]', '"x"', '{not json'])
def test_corrupt_rows_read_as_empty(tmp_path, monkeypatch, submit_as_user,
                                    corrupt):
    _fresh_db(tmp_path, monkeypatch)
    _submit_as_user(monkeypatch, submit_as_user)
    engine = global_user_state._db_manager.get_engine()
    keys = [
        global_user_state._get_check_results_key('default'),
        global_user_state._get_enabled_clouds_key(cloud.CloudCapability.COMPUTE,
                                                  'default'),
    ]
    if submit_as_user:
        keys += [global_user_state._user_scoped_key(k) for k in keys]
    with global_user_state.orm.Session(engine) as session:
        for key in keys:
            session.execute(global_user_state.config_table.insert().values(
                key=key, value=corrupt))
        session.commit()
    assert global_user_state.get_cached_check_results('default') == {}
    if corrupt != '[]':
        assert _enabled() == []
    # Scoped writes replace the corrupt row instead of crashing.
    aws = {'AWS': {'': {'enabled': True, 'reason': 'enabled.'}}}
    global_user_state.set_check_results(aws,
                                        workspace='default',
                                        is_full_workspace_run=False)
    assert global_user_state.get_cached_check_results('default') == aws

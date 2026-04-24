"""Failing tests for the generalized lifecycle-hooks framework (PR1).

Target surface from termination_hook_design.md + termination_hook_impl.md:

- `resources.hooks: [{run, events?, timeout?}]` where `events` is
  optional and defaults to `[autostop, preemption, down]`.
- Schema rejects empty `events`, duplicates, unknown event names,
  non-positive timeouts, unknown keys.
- Controller resources (`jobs.controller.resources`,
  `serve.controller.resources`) reject `hooks`.
- `Resources._hooks` round-trips through YAML + pickle.
- Pickle migration `_VERSION` 32 → 33 routes master's
  `AutostopConfig.hook` / `hook_timeout` into the new `_hooks` list.
- Legacy `autostop.hook` YAML parses with a one-line stderr
  deprecation warning and routes into `_hooks`.
- `hook_executor` exposes `try_claim_teardown`, `run`, constants,
  and a per-event log path layout.
- `SKYLET_LIB_VERSION` bumps to 7.

These tests intentionally fail on master until PR1's implementation
commits land.
"""
import threading

import pytest

from sky.resources import Resources
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import schemas

DEFAULT_TIMEOUT = constants.DEFAULT_AUTOSTOP_HOOK_TIMEOUT_SECONDS
ALL_EVENTS = ['autostop', 'preemption', 'down']


def _validate(resources_config):
    """Run the resources schema validator; raises on invalid."""
    common_utils.validate_schema(resources_config,
                                 schemas.get_resources_schema(),
                                 'Invalid resources YAML: ')


# ---------------------------------------------------------------------------
# Schema validation — accept forms
# ---------------------------------------------------------------------------


def test_schema_accepts_hook_with_no_events_key():
    """`events` is optional; omission = [autostop, preemption, down]."""
    _validate({'hooks': [{'run': 'echo hi'}]})


def test_schema_accepts_single_event():
    _validate({'hooks': [{'run': 'echo hi', 'events': ['autostop']}]})


def test_schema_accepts_all_events_and_timeout():
    _validate({
        'hooks': [{
            'run': 'save.sh',
            'events': ['autostop', 'preemption', 'down'],
            'timeout': 120,
        }],
    })


def test_schema_accepts_multiple_hook_entries():
    _validate({
        'hooks': [
            {
                'run': 'a.sh'
            },
            {
                'run': 'b.sh',
                'events': ['preemption'],
                'timeout': 45
            },
        ],
    })


# ---------------------------------------------------------------------------
# Schema validation — reject forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('bad_hook', [
    {},
    {
        'events': ['autostop']
    },
    {
        'run': ''
    },
    {
        'run': 'x',
        'events': []
    },
    {
        'run': 'x',
        'events': ['autostop', 'autostop']
    },
    {
        'run': 'x',
        'events': ['reboot']
    },
    {
        'run': 'x',
        'timeout': 0
    },
    {
        'run': 'x',
        'timeout': -1
    },
    {
        'run': 'x',
        'extra_key': True
    },
])
def test_schema_rejects_malformed_hook(bad_hook):
    with pytest.raises(Exception):
        _validate({'hooks': [bad_hook]})


# ---------------------------------------------------------------------------
# Resources._hooks storage — round-trip + default-fill
# ---------------------------------------------------------------------------


def test_resources_default_fills_events_when_omitted():
    (r,) = list(Resources.from_yaml_config({'hooks': [{'run': 'echo x'}]}))
    assert r.hooks is not None and len(r.hooks) == 1
    assert sorted(r.hooks[0]['events']) == sorted(ALL_EVENTS)


def test_resources_round_trip_preserves_hooks():
    hooks = [
        {
            'run': 'a',
            'events': ['autostop'],
            'timeout': 60
        },
        {
            'run': 'b',
            'events': ['autostop', 'down']
        },
    ]
    (r,) = list(Resources.from_yaml_config({'hooks': hooks}))
    out = r.to_yaml_config()
    assert out.get('hooks'), out
    # Each original entry is present; default-fill for 'events' may apply
    # when the user omitted it (not applicable in this test's inputs).
    assert len(out['hooks']) == 2
    assert out['hooks'][0]['run'] == 'a'
    assert sorted(out['hooks'][0]['events']) == ['autostop']
    assert out['hooks'][0]['timeout'] == 60


def test_resources_copy_preserves_hooks():
    (r,) = list(
        Resources.from_yaml_config(
            {'hooks': [{
                'run': 'x',
                'events': ['down']
            }]}))
    r2 = r.copy()
    assert r2.hooks == r.hooks


def test_resources_copy_override_replaces_hooks():
    (r,) = list(
        Resources.from_yaml_config(
            {'hooks': [{
                'run': 'x',
                'events': ['down']
            }]}))
    new_hooks = [{'run': 'y', 'events': ['autostop']}]
    r2 = r.copy(hooks=new_hooks)
    # copy(hooks=...) may or may not re-default-fill; compare on run+events.
    assert len(r2.hooks) == 1
    assert r2.hooks[0]['run'] == 'y'
    assert sorted(r2.hooks[0]['events']) == ['autostop']


def test_resources_no_hooks_yields_none_or_empty():
    (r,) = list(Resources.from_yaml_config({}))
    assert not r.hooks
    assert 'hooks' not in (r.to_yaml_config() or {})


# ---------------------------------------------------------------------------
# Legacy autostop.hook routing + deprecation warning
# ---------------------------------------------------------------------------


def test_legacy_autostop_hook_routes_into_hooks(capsys):
    (r,) = list(
        Resources.from_yaml_config({
            'autostop': {
                'idle_minutes': 10,
                'hook': 'echo legacy',
                'hook_timeout': 42,
            },
        }))
    assert r.hooks and len(r.hooks) == 1
    entry = r.hooks[0]
    assert entry['run'] == 'echo legacy'
    assert sorted(entry['events']) == ['autostop']
    assert entry['timeout'] == 42

    # Legacy attrs scrubbed from AutostopConfig.
    ac = r.autostop_config
    assert getattr(ac, 'hook', None) is None
    assert getattr(ac, 'hook_timeout', None) is None

    # Deprecation warning on stderr.
    err = capsys.readouterr().err
    assert 'autostop.hook' in err
    assert 'deprecated' in err.lower()


def test_legacy_autostop_hook_default_timeout():
    (r,) = list(
        Resources.from_yaml_config({
            'autostop': {
                'idle_minutes': 5,
                'hook': 'echo legacy',
            },
        }))
    assert r.hooks and r.hooks[0]['timeout'] == DEFAULT_TIMEOUT


def test_legacy_and_explicit_hooks_both_preserved():
    (r,) = list(
        Resources.from_yaml_config({
            'autostop': {
                'idle_minutes': 5,
                'hook': 'legacy.sh',
            },
            'hooks': [{
                'run': 'modern.sh',
                'events': ['down']
            }],
        }))
    runs = sorted(h['run'] for h in r.hooks)
    assert runs == ['legacy.sh', 'modern.sh']


# ---------------------------------------------------------------------------
# Pickle migration v32 → v33
# ---------------------------------------------------------------------------


def test_pickle_migration_routes_legacy_hook_attrs():
    """A Resources pickled at _VERSION=32 with AutostopConfig.hook set
    must rehydrate with hook in _hooks and attrs stripped."""
    (r,) = list(Resources.from_yaml_config({'autostop': {'idle_minutes': 1}}))
    state = r.__dict__.copy()
    ac = state['_autostop_config']
    ac.hook = 'legacy.sh'
    ac.hook_timeout = 99
    state.pop('_hooks', None)
    state['_version'] = 32

    fresh = Resources.__new__(Resources)
    fresh.__setstate__(state)

    assert fresh.hooks and fresh.hooks[0]['run'] == 'legacy.sh'
    assert fresh.hooks[0]['timeout'] == 99
    assert sorted(fresh.hooks[0]['events']) == ['autostop']
    assert getattr(fresh.autostop_config, 'hook', None) is None
    assert getattr(fresh.autostop_config, 'hook_timeout', None) is None


def test_resources_version_bumped_to_33():
    assert Resources._VERSION >= 33


# ---------------------------------------------------------------------------
# Controller rejection
# ---------------------------------------------------------------------------


def test_controller_schema_rejects_hooks():
    """`jobs.controller.resources` and `serve.controller.resources`
    must reject the `hooks` field at schema-validation time."""
    config = {
        'jobs': {
            'controller': {
                'resources': {
                    'cpus': 4,
                    'hooks': [{
                        'run': 'x',
                        'events': ['autostop']
                    }],
                }
            }
        }
    }
    with pytest.raises(Exception):
        common_utils.validate_schema(config, schemas.get_config_schema(),
                                     'Invalid sky config: ')

    config2 = {
        'serve': {
            'controller': {
                'resources': {
                    'cpus': 4,
                    'hooks': [{
                        'run': 'x'
                    }],
                }
            }
        }
    }
    with pytest.raises(Exception):
        common_utils.validate_schema(config2, schemas.get_config_schema(),
                                     'Invalid sky config: ')


# ---------------------------------------------------------------------------
# hook_executor: CAS + per-event logs
# ---------------------------------------------------------------------------


@pytest.fixture
def hook_executor(tmp_path, monkeypatch):
    """Fresh hook_executor per test, with claim file + log dir redirected."""
    from sky.skylet import hook_executor as he
    monkeypatch.setattr(he, 'HOOK_LOG_DIR', str(tmp_path))
    monkeypatch.setattr(he, 'CLAIM_FILE', str(tmp_path / '.teardown_claim'))
    yield he
    # Best-effort cleanup.
    try:
        import os
        os.unlink(str(tmp_path / '.teardown_claim'))
    except FileNotFoundError:
        pass


def test_try_claim_teardown_first_in_wins(hook_executor):
    assert hook_executor.try_claim_teardown('autostop') is True
    assert hook_executor.try_claim_teardown('preemption') is False
    assert hook_executor.try_claim_teardown('down') is False


def test_try_claim_teardown_thread_safety(hook_executor):
    winners = []

    def _claim(evt):
        if hook_executor.try_claim_teardown(evt):
            winners.append(evt)

    threads = [
        threading.Thread(target=_claim, args=('autostop',)),
        threading.Thread(target=_claim, args=('preemption',)),
        threading.Thread(target=_claim, args=('down',)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1


def test_hook_executor_log_path_per_event(hook_executor):
    assert hook_executor._log_path_for('autostop').endswith('/autostop.log')
    assert hook_executor._log_path_for('preemption').endswith('/preemption.log')
    assert hook_executor._log_path_for('down').endswith('/down.log')


def test_hook_executor_filters_by_event(hook_executor, monkeypatch):
    calls = []

    def _fake_run(script, log_path, timeout):
        calls.append(script)
        return 0

    monkeypatch.setattr(hook_executor, '_run_script', _fake_run)
    hooks = [
        {
            'run': 'a',
            'events': ['autostop']
        },
        {
            'run': 'b',
            'events': ['preemption']
        },
        {
            'run': 'c',
            'events': ['autostop', 'down']
        },
    ]
    hook_executor.run('autostop', hooks)
    assert calls == ['a', 'c']


def test_hook_executor_sequential(hook_executor, monkeypatch):
    order = []
    monkeypatch.setattr(hook_executor, '_run_script',
                        lambda s, l, t: order.append(s) or 0)
    hooks = [{'run': str(i), 'events': ['autostop']} for i in range(3)]
    hook_executor.run('autostop', hooks)
    assert order == ['0', '1', '2']


def test_hook_executor_failure_continues(hook_executor, monkeypatch):
    called = []

    def _fake(script, log_path, timeout):
        called.append(script)
        return 1 if script == 'boom' else 0

    monkeypatch.setattr(hook_executor, '_run_script', _fake)
    hook_executor.run('autostop', [
        {
            'run': 'boom',
            'events': ['autostop']
        },
        {
            'run': 'ok',
            'events': ['autostop']
        },
    ])
    assert called == ['boom', 'ok']


def test_hook_executor_empty_and_no_match_are_noops(hook_executor, monkeypatch):
    called = []
    monkeypatch.setattr(hook_executor, '_run_script',
                        lambda *a, **k: called.append(a) or 0)
    hook_executor.run('autostop', [])
    hook_executor.run('autostop', None)
    hook_executor.run('down', [{'run': 'x', 'events': ['autostop']}])
    assert not called


# ---------------------------------------------------------------------------
# Skylet version
# ---------------------------------------------------------------------------


def test_skylet_lib_version_bumped_to_7():
    assert constants.SKYLET_LIB_VERSION >= 7

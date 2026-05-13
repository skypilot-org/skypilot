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

from sky.clouds import kubernetes as k8s_cloud
from sky.resources import Resources
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import schemas

DEFAULT_TIMEOUT = constants.DEFAULT_AUTOSTOP_HOOK_TIMEOUT_SECONDS
ALL_EVENTS = ['autostop', 'preemption', 'down']


def _validate(hooks_payload):
    """Run the task-YAML schema validator on `{config: {hooks: ...}}`.

    The schema lives at task.yaml's `config.hooks:`. We wrap the
    caller's payload (which is already a `{'hooks': [...]}` dict) as
    `{'config': payload}` and validate the whole task YAML.
    """
    task_config = {'config': hooks_payload}
    common_utils.validate_schema(task_config, schemas.get_task_schema(),
                                 'Invalid task YAML: ')


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


# ---------------------------------------------------------------------------
# K8s preemption-grace rendering
# ---------------------------------------------------------------------------


def test_preemption_grace_is_sum_of_timeouts():
    """hook_executor runs hooks sequentially, so the rendered grace
    period must be the SUM of preemption-hook timeouts, not the max.
    """

    timeout = k8s_cloud._compute_preemption_hook_timeout([
        {
            'run': 'a',
            'events': ['preemption'],
            'timeout': 30
        },
        {
            'run': 'b',
            'events': ['preemption'],
            'timeout': 45
        },
        {
            'run': 'c',
            'events': ['preemption'],
            'timeout': 25
        },
    ])
    assert timeout == 100


def test_preemption_grace_ignores_non_preemption_events():

    timeout = k8s_cloud._compute_preemption_hook_timeout([
        {
            'run': 'a',
            'events': ['autostop'],
            'timeout': 30
        },
        {
            'run': 'b',
            'events': ['down'],
            'timeout': 45
        },
        {
            'run': 'c',
            'events': ['preemption'],
            'timeout': 60
        },
    ])
    assert timeout == 60


def test_preemption_grace_none_when_no_preemption_hook():

    assert k8s_cloud._compute_preemption_hook_timeout(None) is None
    assert k8s_cloud._compute_preemption_hook_timeout([]) is None
    assert k8s_cloud._compute_preemption_hook_timeout([{
        'run': 'a',
        'events': ['autostop'],
        'timeout': 30
    }]) is None


def test_preemption_grace_default_timeout_when_unset(capsys):
    """An entry without explicit ``timeout`` falls back to the default,
    which exceeds the autoscaler cap and therefore returns the cap."""

    timeout = k8s_cloud._compute_preemption_hook_timeout([
        {
            'run': 'a',
            'events': ['preemption']
        },
    ])
    assert timeout == k8s_cloud._PREEMPTION_GRACE_CAP_SECONDS


def test_preemption_grace_capped_at_autoscaler_limit(capsys):
    """Sum > cap → cap is returned + a stderr warning fires (C7)."""
    timeout = k8s_cloud._compute_preemption_hook_timeout([
        {
            'run': 'a',
            'events': ['preemption'],
            'timeout': 400
        },
        {
            'run': 'b',
            'events': ['preemption'],
            'timeout': 400
        },
    ])
    assert timeout == k8s_cloud._PREEMPTION_GRACE_CAP_SECONDS
    captured = capsys.readouterr()
    assert 'cluster-autoscaler' in captured.err.lower()
    assert '800s' in captured.err


def test_preemption_grace_no_cap_when_under_limit():
    """Sum ≤ cap → exact sum, no warning."""
    timeout = k8s_cloud._compute_preemption_hook_timeout([
        {
            'run': 'a',
            'events': ['preemption'],
            'timeout': 60
        },
        {
            'run': 'b',
            'events': ['preemption'],
            'timeout': 60
        },
    ])
    assert timeout == 120


# ---------------------------------------------------------------------------
# config.hooks YAML routing — task.from_yaml_config picks up hooks under
# `config:` and forwards them to Resources._hooks. The deprecated
# `resources.hooks:` form still parses but emits a stderr warning.
# ---------------------------------------------------------------------------


def test_task_yaml_config_hooks_lands_on_resources(tmp_path):
    """Canonical form: `config.hooks:` at the top level."""
    from sky.task import Task
    yaml_str = ('name: test\n'
                'config:\n'
                '  hooks:\n'
                '    - run: echo from-config-hooks\n'
                '      events: [autostop]\n'
                '      timeout: 30\n'
                'resources:\n'
                '  cpus: 2\n')
    p = tmp_path / 'task.yaml'
    p.write_text(yaml_str)
    task = Task.from_yaml(str(p))
    (r,) = list(task.resources)
    assert r.hooks and len(r.hooks) == 1
    assert r.hooks[0]['run'] == 'echo from-config-hooks'
    assert sorted(r.hooks[0]['events']) == ['autostop']
    assert r.hooks[0]['timeout'] == 30


def test_task_yaml_resources_hooks_routes_with_warning(tmp_path, capsys):
    """Deprecated form: `resources.hooks:` still works + stderr warns."""
    from sky.task import Task
    yaml_str = ('name: test\n'
                'resources:\n'
                '  cpus: 2\n'
                '  hooks:\n'
                '    - run: echo from-resources-hooks\n'
                '      events: [down]\n')
    p = tmp_path / 'task.yaml'
    p.write_text(yaml_str)
    task = Task.from_yaml(str(p))
    (r,) = list(task.resources)
    assert r.hooks and r.hooks[0]['run'] == 'echo from-resources-hooks'
    err = capsys.readouterr().err
    assert 'resources.hooks is deprecated' in err
    assert 'config.hooks' in err


def test_task_yaml_both_forms_prefers_config_hooks(tmp_path, capsys):
    """If both forms specified, config.hooks wins; resources.hooks
    ignored with a warning."""
    from sky.task import Task
    yaml_str = ('name: test\n'
                'config:\n'
                '  hooks:\n'
                '    - run: echo new\n'
                '      events: [autostop]\n'
                'resources:\n'
                '  cpus: 2\n'
                '  hooks:\n'
                '    - run: echo old\n'
                '      events: [autostop]\n')
    p = tmp_path / 'task.yaml'
    p.write_text(yaml_str)
    task = Task.from_yaml(str(p))
    (r,) = list(task.resources)
    assert r.hooks and r.hooks[0]['run'] == 'echo new'
    err = capsys.readouterr().err
    assert 'resources.hooks ignored' in err


def test_task_yaml_config_hooks_schema_rejects_unknown_event(tmp_path):
    """Schema validation runs against the new task.config.hooks
    location, so unknown event names are rejected."""
    from sky.task import Task
    yaml_str = ('name: test\n'
                'config:\n'
                '  hooks:\n'
                '    - run: echo bad\n'
                '      events: [reboot]\n'
                'resources:\n'
                '  cpus: 2\n')
    p = tmp_path / 'task.yaml'
    p.write_text(yaml_str)
    with pytest.raises(Exception):
        Task.from_yaml(str(p))


# ---------------------------------------------------------------------------
# Review feedback (kevinmingtarja) — behavioral fixes
# ---------------------------------------------------------------------------


def test_sky_down_claims_teardown_even_without_down_hooks():
    """`sky down` must claim the 'down' teardown slot unconditionally.

    Otherwise a cluster declaring only ``events: [preemption]`` hooks
    leaves the slot unclaimed during ``sky down`` → the kubelet SIGTERM
    that follows pod deletion then claims ``preemption`` → fires the
    preemption hook on what was actually an intentional teardown.

    Verifies the codegen sent to the head:
      1. Always calls `try_claim_teardown('down')`.
      2. The claim is **not** gated by any `if any(... 'down' in ...)`
         conditional that depends on the cluster's hook events.
    """
    from sky import core
    from sky.backends import cloud_vm_ray_backend

    captured = {}

    class _FakeHandle:
        pass

    class _FakeBackend(cloud_vm_ray_backend.CloudVmRayBackend):

        def __init__(self):  # pylint: disable=super-init-not-called
            pass

        def run_on_head(self, handle, cmd, **kw):  # type: ignore[override]
            captured['cmd'] = cmd
            return 0

    core._maybe_run_down_hooks(_FakeHandle(), _FakeBackend(), 'mycluster')

    cmd = captured.get('cmd', '')
    # The cmd is shell-quoted around the python -c '...' payload, so we
    # search for the unquoted token rather than the surrounding quotes.
    assert 'try_claim_teardown' in cmd, (
        f"_maybe_run_down_hooks codegen missing teardown claim. "
        f"Got: {cmd!r}")
    # The 'down' claim must not be gated by an `if any(... in events)`
    # filter — otherwise a cluster declaring only preemption hooks
    # leaves the slot unclaimed and SIGTERM later fires the preemption
    # hook on intentional teardown.
    assert 'if any(' not in cmd, (
        "The 'down' claim is currently nested inside `if any(... in "
        "events ...)`. It must be filed unconditionally so that "
        "subsequent kubelet SIGTERM (during the K8s pod delete) cannot "
        "claim 'preemption' and fire the preemption hook on a sky "
        f"down. Codegen was:\n{cmd}")


def test_relaunch_hooks_only_preserves_autostop(monkeypatch):
    """Re-launching with only hooks changed must NOT unset autostop.

    The buggy version of the ``elif hooks_payload is not None:`` branch
    in :func:`sky.execution._execute` passed
    ``idle_minutes_to_autostop=-1`` unconditionally, wiping any prior
    autostop on a re-launch that added/changed only ``config.hooks``.

    The fix extracts the kwarg computation into a helper that reads the
    cluster's prior ``autostop`` + ``to_down`` from the local DB. This
    test pins that helper.
    """
    from sky import execution
    from sky import global_user_state

    # Cluster previously had autostop=10 idle minutes + autodown.
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda *a, **kw: {
                            'autostop': 10,
                            'to_down': True,
                        })

    hooks = [{'run': 'echo bye', 'events': ['down']}]
    kwargs = execution._compute_set_autostop_args_for_hooks_only_relaunch(
        'mycluster', hooks)

    assert kwargs['idle_minutes_to_autostop'] == 10, (
        f"Expected prior autostop=10 to be preserved, got "
        f"{kwargs['idle_minutes_to_autostop']!r}. The buggy version "
        f"unset autostop with -1.")
    assert kwargs['down'] is True, (
        f"Expected prior to_down=True to be preserved, got "
        f"{kwargs['down']!r}.")
    assert kwargs['hooks'] == hooks


def test_relaunch_hooks_only_handles_missing_cluster_record(monkeypatch):
    """If the cluster record can't be read (e.g. first launch / race),
    fall back to ``idle_minutes_to_autostop=-1`` (no autostop) and
    ``down=False`` rather than crashing."""
    from sky import execution
    from sky import global_user_state

    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda *a, **kw: None)
    kwargs = execution._compute_set_autostop_args_for_hooks_only_relaunch(
        'mycluster', [])
    assert kwargs['idle_minutes_to_autostop'] == -1
    assert kwargs['down'] is False


def test_relaunch_warns_when_preemption_grace_increases():
    """Re-launching a K8s cluster with a *larger* preemption-hook
    timeout than the existing pod's terminationGracePeriodSeconds
    must warn the user.

    Pod templates are immutable after creation, so the larger timeout
    would otherwise be silently truncated by kubelet at SIGTERM —
    preemption hooks past the original grace get SIGKILLed mid-run.
    """
    from sky.backends.cloud_vm_ray_backend import (
        _maybe_warn_preemption_grace_change)

    prior = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    new = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 300}]

    warning = _maybe_warn_preemption_grace_change(k8s_cloud.Kubernetes(), prior,
                                                  new)
    assert warning is not None, (
        'Expected a warning when re-launching K8s cluster with a '
        'larger preemption-hook timeout than before.')
    assert '60' in warning and '300' in warning, (
        f'Warning should mention prior=60s and new=300s; got: {warning!r}')
    assert 'sky down' in warning.lower() or 'restart' in warning.lower(), (
        f'Warning should tell users to restart the cluster; got: {warning!r}')


def test_relaunch_no_warning_when_preemption_grace_unchanged():
    """Re-launch with same preemption-hook timeout = no warning."""
    from sky.backends.cloud_vm_ray_backend import (
        _maybe_warn_preemption_grace_change)
    prior = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    new = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    assert _maybe_warn_preemption_grace_change(k8s_cloud.Kubernetes(), prior,
                                               new) is None


def test_relaunch_no_warning_on_non_k8s_cloud():
    """terminationGracePeriodSeconds is K8s-specific; warn only there."""
    from sky.backends.cloud_vm_ray_backend import (
        _maybe_warn_preemption_grace_change)
    from sky.clouds import AWS
    prior = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    new = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 300}]
    assert _maybe_warn_preemption_grace_change(AWS(), prior, new) is None

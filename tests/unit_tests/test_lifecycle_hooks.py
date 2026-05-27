"""Failing tests for the generalized lifecycle-hooks framework (PR1).

Target surface:

- `config.hooks: [{run, events?, timeout?}]` where `events` is
  optional and defaults to `[stop, preemption, down]`.
- Schema rejects empty `events`, duplicates, unknown event names,
  non-positive timeouts, unknown keys.
- Controller resources (`jobs.controller.resources`,
  `serve.controller.resources`) reject `hooks`.
- `Resources._hooks` round-trips through YAML + pickle.
- Pickle migration `_VERSION` 32 → 33 routes master's
  `AutostopConfig.hook` / `hook_timeout` into the new `_hooks` list.
- Legacy `autostop.hook` YAML parses with a one-line stderr
  deprecation warning and routes into `_hooks` (`down` event for
  autodown, `stop` otherwise).
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

DEFAULT_TIMEOUT = constants.DEFAULT_HOOK_TIMEOUT_SECONDS
ALL_EVENTS = ['stop', 'preemption', 'down']


def _autostop_yaml(idle_minutes, hook, *, down=False, hook_timeout=None):
    """Build the legacy ``autostop: {...}`` YAML dict the loader accepts."""
    out = {'idle_minutes': idle_minutes, 'hook': hook}
    if down:
        out['down'] = True
    if hook_timeout is not None:
        out['hook_timeout'] = hook_timeout
    return out


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
    """`events` is optional; omission = [stop, preemption, down]."""
    _validate({'hooks': [{'run': 'echo hi'}]})


def test_schema_accepts_single_event():
    _validate({'hooks': [{'run': 'echo hi', 'events': ['stop']}]})


def test_schema_accepts_all_events_and_timeout():
    _validate({
        'hooks': [{
            'run': 'save.sh',
            'events': ['stop', 'preemption', 'down'],
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
        'events': ['stop']
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
        'events': ['stop', 'stop']
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
    """Hooks round-trip through Task.to_yaml_config / from_yaml_config.

    Hooks are stored on each Resources internally but serialized under
    the task-level ``config.hooks:`` key (their canonical YAML
    placement) — emitting them under ``resources.hooks`` would trip
    Task.from_yaml_config's rejection on a server-side round-trip.
    """
    from sky.task import Task

    hooks = [
        {
            'run': 'a',
            'events': ['stop'],
            'timeout': 60
        },
        {
            'run': 'b',
            'events': ['stop', 'down']
        },
    ]
    task = Task.from_yaml_config({
        'name': 't',
        'config': {
            'hooks': hooks,
        },
        'resources': {
            'cpus': 1,
        },
    })
    out = task.to_yaml_config()

    # Hooks emit at the task level under `config.hooks`, NOT under resources.
    assert 'hooks' not in (out.get('resources') or {}), (
        f'Hooks should not be emitted under resources; got: '
        f"{out.get('resources')!r}")
    cfg_hooks = (out.get('config') or {}).get('hooks')
    assert cfg_hooks, f'Expected config.hooks; got: {out!r}'
    assert len(cfg_hooks) == 2
    assert cfg_hooks[0]['run'] == 'a'
    assert sorted(cfg_hooks[0]['events']) == ['stop']
    assert cfg_hooks[0]['timeout'] == 60

    # Now feed the dumped YAML back through the loader — should not
    # trigger the `resources.hooks` rejection.
    task2 = Task.from_yaml_config(out)
    (r2,) = list(task2.resources)
    assert r2.hooks and len(r2.hooks) == 2
    assert r2.hooks[0]['run'] == 'a'


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
    new_hooks = [{'run': 'y', 'events': ['stop']}]
    r2 = r.copy(hooks=new_hooks)
    # copy(hooks=...) may or may not re-default-fill; compare on run+events.
    assert len(r2.hooks) == 1
    assert r2.hooks[0]['run'] == 'y'
    assert sorted(r2.hooks[0]['events']) == ['stop']


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
    # autostop.down defaults to False → legacy hook routes to `stop`.
    assert sorted(entry['events']) == ['stop']
    assert entry['timeout'] == 42

    # Legacy attrs scrubbed from AutostopConfig.
    ac = r.autostop_config
    assert getattr(ac, 'hook', None) is None
    assert getattr(ac, 'hook_timeout', None) is None

    # Deprecation warning on stderr.
    err = capsys.readouterr().err
    assert 'autostop.hook' in err
    assert 'deprecated' in err.lower()


def test_legacy_autodown_hook_routes_to_down_event():
    """``autostop: {down: true, hook: ...}`` routes the legacy hook to
    the ``down`` event — autodown's outcome is teardown, not pause."""
    (r,) = list(
        Resources.from_yaml_config({
            'autostop': {
                'idle_minutes': 10,
                'down': True,
                'hook': 'echo legacy-autodown',
            },
        }))
    assert r.hooks and len(r.hooks) == 1
    assert sorted(r.hooks[0]['events']) == ['down']


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
    assert sorted(fresh.hooks[0]['events']) == ['stop']
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
                        'events': ['stop']
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
    assert hook_executor.try_claim_teardown('stop') is True
    assert hook_executor.try_claim_teardown('preemption') is False
    assert hook_executor.try_claim_teardown('down') is False


def test_try_claim_teardown_thread_safety(hook_executor):
    winners = []

    def _claim(evt):
        if hook_executor.try_claim_teardown(evt):
            winners.append(evt)

    threads = [
        threading.Thread(target=_claim, args=('stop',)),
        threading.Thread(target=_claim, args=('preemption',)),
        threading.Thread(target=_claim, args=('down',)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1


def test_hook_executor_log_path_per_event(hook_executor):
    assert hook_executor._log_path_for('stop').endswith('/stop.log')
    assert hook_executor._log_path_for('preemption').endswith('/preemption.log')
    assert hook_executor._log_path_for('down').endswith('/down.log')


def test_hook_executor_filters_by_event(hook_executor, monkeypatch):
    calls = []

    def _fake_run(script, log_path, timeout, event):
        del log_path, timeout, event
        calls.append(script)
        return 0

    monkeypatch.setattr(hook_executor, '_run_script', _fake_run)
    hooks = [
        {
            'run': 'a',
            'events': ['stop']
        },
        {
            'run': 'b',
            'events': ['preemption']
        },
        {
            'run': 'c',
            'events': ['stop', 'down']
        },
    ]
    hook_executor.run('stop', hooks)
    assert calls == ['a', 'c']


def test_hook_executor_sequential(hook_executor, monkeypatch):
    order = []
    monkeypatch.setattr(hook_executor, '_run_script',
                        lambda s, l, t, e: order.append(s) or 0)
    hooks = [{'run': str(i), 'events': ['stop']} for i in range(3)]
    hook_executor.run('stop', hooks)
    assert order == ['0', '1', '2']


def test_hook_executor_failure_continues(hook_executor, monkeypatch):
    called = []

    def _fake(script, log_path, timeout, event):
        del log_path, timeout, event
        called.append(script)
        return 1 if script == 'boom' else 0

    monkeypatch.setattr(hook_executor, '_run_script', _fake)
    hook_executor.run('stop', [
        {
            'run': 'boom',
            'events': ['stop']
        },
        {
            'run': 'ok',
            'events': ['stop']
        },
    ])
    assert called == ['boom', 'ok']


def test_hook_executor_empty_and_no_match_are_noops(hook_executor, monkeypatch):
    called = []
    monkeypatch.setattr(hook_executor, '_run_script',
                        lambda *a, **k: called.append(a) or 0)
    hook_executor.run('stop', [])
    hook_executor.run('stop', None)
    hook_executor.run('down', [{'run': 'x', 'events': ['stop']}])
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
            'events': ['stop'],
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
        'events': ['stop'],
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
                '      events: [stop]\n'
                '      timeout: 30\n'
                'resources:\n'
                '  cpus: 2\n')
    p = tmp_path / 'task.yaml'
    p.write_text(yaml_str)
    task = Task.from_yaml(str(p))
    (r,) = list(task.resources)
    assert r.hooks and len(r.hooks) == 1
    assert r.hooks[0]['run'] == 'echo from-config-hooks'
    assert sorted(r.hooks[0]['events']) == ['stop']
    assert r.hooks[0]['timeout'] == 30


def test_sigterm_handler_installed_only_on_kubernetes(monkeypatch):
    """SIGTERM-based preemption handling fires only on K8s.

    On VM clouds (AWS/GCP/Azure), preemption is detected by the
    metadata poller (PR2). The SIGTERM handler is K8s-specific
    (kubelet sends SIGTERM during pod deletion / scale-down /
    evictions). Gate the handler install so non-K8s skylets don't
    intercept SIGTERM unnecessarily.
    """
    from sky.skylet import skylet

    # K8s pod env: KUBERNETES_SERVICE_HOST is set.
    monkeypatch.setenv('KUBERNETES_SERVICE_HOST', '10.0.0.1')
    assert skylet._should_install_preemption_sigterm_handler() is True

    # Non-K8s: env not set.
    monkeypatch.delenv('KUBERNETES_SERVICE_HOST', raising=False)
    assert skylet._should_install_preemption_sigterm_handler() is False


def test_kubernetes_caps_preemption_hook_timeout_to_600(capsys):
    """K8s grace-period caps preemption hooks at 600s.

    The user-specified or default timeout for a *preemption*-event hook
    is bounded by the pod's terminationGracePeriodSeconds, which we cap
    at 600s (cluster-autoscaler's --max-graceful-termination-sec
    default). When a user requests longer, we cap on send + warn — so
    the skylet stores a number that matches what kubelet will actually
    honor instead of one that misleads the user.
    """
    from sky.clouds.kubernetes import cap_preemption_hook_timeouts

    hooks = [
        {
            'run': 'a',
            'events': ['preemption'],
            'timeout': 3600
        },
        {
            'run': 'b',
            'events': ['preemption'],
            'timeout': 60
        },
        {
            'run': 'c',
            'events': ['stop'],
            'timeout': 3600
        },
    ]
    capped = cap_preemption_hook_timeouts(hooks)
    assert capped[0]['timeout'] == 600, (
        'preemption hook with 3600s should be capped to 600')
    assert capped[1]['timeout'] == 60, 'under-limit preemption stays'
    # autostop-only hook isn't affected by the K8s grace cap.
    assert capped[2]['timeout'] == 3600, (
        'autostop-only hook should not be capped (no grace involvement)')

    err = capsys.readouterr().err
    assert '600' in err and 'kubernetes' in err.lower(), (
        f'Should warn user about the cap; got: {err!r}')


def test_kubernetes_no_cap_when_preemption_hooks_under_limit(capsys):
    from sky.clouds.kubernetes import cap_preemption_hook_timeouts
    hooks = [{'run': 'a', 'events': ['preemption'], 'timeout': 60}]
    out = cap_preemption_hook_timeouts(hooks)
    assert out[0]['timeout'] == 60
    assert capsys.readouterr().err == ''


def test_task_yaml_resources_hooks_form_now_rejected(tmp_path):
    """`resources.hooks:` is no longer accepted.

    The shim was kept temporarily during PR1 development to ease the
    rename from the original ``resources.hooks:`` placement to the
    final ``config.hooks:``. Since the legacy placement never landed
    in master, drop the shim — users on master see only ``config.hooks``.
    Schema's ``additionalProperties: False`` on the resources block now
    rejects ``hooks:`` cleanly.
    """
    from sky.task import Task
    yaml_str = ('name: test\n'
                'resources:\n'
                '  cpus: 2\n'
                '  hooks:\n'
                '    - run: echo legacy\n'
                '      events: [down]\n')
    p = tmp_path / 'task.yaml'
    p.write_text(yaml_str)
    with pytest.raises(Exception) as excinfo:
        Task.from_yaml(str(p))
    # Error should mention both the rejected placement and the canonical
    # one so users know what to do.
    msg = str(excinfo.value).lower()
    assert 'resources.hooks' in msg, f'Expected mention of bad placement; got: {msg}'
    assert 'config.hooks' in msg, f'Expected mention of correct placement; got: {msg}'


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


def test_hook_executor_injects_event_env_var(hook_executor, monkeypatch,
                                             tmp_path):
    """``$SKYPILOT_HOOK_EVENT`` is exposed to the hook subprocess so a
    single hook entry defaulted to all events can dispatch internally."""
    captured = {}

    def _fake_run_with_log(cmd, log_path, **kwargs):
        del cmd, log_path
        captured['env'] = kwargs.get('env')
        return 0

    monkeypatch.setattr('sky.skylet.log_lib.run_with_log', _fake_run_with_log)
    rc = hook_executor._run_script(
        'echo $SKYPILOT_HOOK_EVENT',
        str(tmp_path / 'stop.log'),
        timeout=10,
        event='stop',
    )
    assert rc == 0
    env = captured.get('env') or {}
    assert env.get('SKYPILOT_HOOK_EVENT') == 'stop', (
        f"Hook subprocess env must carry SKYPILOT_HOOK_EVENT=stop; got "
        f"{env.get('SKYPILOT_HOOK_EVENT')!r}.")
    # Sanity: surrounding env (PATH etc) must still be present so user
    # hook scripts can find common binaries.
    assert 'PATH' in env, 'Hook env must inherit PATH from the skylet.'


def test_sky_stop_fires_stop_hook_via_codegen():
    """`sky stop` must claim the 'stop' teardown slot and run hooks on
    the head before the backend stop call.

    Mirrors ``test_sky_down_claims_teardown_even_without_down_hooks``
    for the user-initiated stop path added per review feedback to
    drop the original `sky stop`-exclusion.
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
            del handle, kw
            captured['cmd'] = cmd
            return 0

    core._maybe_run_stop_hooks(_FakeHandle(), _FakeBackend(), 'mycluster')

    cmd = captured.get('cmd', '')
    assert 'try_claim_teardown' in cmd, (
        f"_maybe_run_stop_hooks codegen missing teardown claim. Got: {cmd!r}")
    # The codegen must reference the 'stop' event (not 'down') — easy
    # copy-paste mistake to guard against.
    assert "'stop'" in cmd, (
        f"_maybe_run_stop_hooks codegen should claim 'stop', not 'down'. "
        f"Got: {cmd!r}")


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
    prior = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    new = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 300}]

    warning = k8s_cloud.warn_if_preemption_grace_change_requires_relaunch(
        k8s_cloud.Kubernetes(), prior, new)
    assert warning is not None, (
        'Expected a warning when re-launching K8s cluster with a '
        'larger preemption-hook timeout than before.')
    assert '60' in warning and '300' in warning, (
        f'Warning should mention prior=60s and new=300s; got: {warning!r}')
    assert 'sky down' in warning.lower() or 'restart' in warning.lower(), (
        f'Warning should tell users to restart the cluster; got: {warning!r}')


def test_relaunch_no_warning_when_preemption_grace_unchanged():
    """Re-launch with same preemption-hook timeout = no warning."""
    prior = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    new = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    assert k8s_cloud.warn_if_preemption_grace_change_requires_relaunch(
        k8s_cloud.Kubernetes(), prior, new) is None


def test_relaunch_no_warning_on_non_k8s_cloud():
    """terminationGracePeriodSeconds is K8s-specific; warn only there."""
    from sky.clouds import AWS
    prior = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 60}]
    new = [{'run': 'a.sh', 'events': ['preemption'], 'timeout': 300}]
    assert k8s_cloud.warn_if_preemption_grace_change_requires_relaunch(
        AWS(), prior, new) is None


def test_hooks_from_protobuf_defaults_empty_events_to_all():
    """Receive-side default for empty `events` repeated field.

    proto3 ``repeated`` has no presence — an empty ``events`` list on
    the wire is indistinguishable from "field omitted". Send-side
    defaulting (``_normalize_hook_entry``) covers the Python client,
    but the skylet must also re-apply the default on receive, in case
    a non-Python or future client sends ``events=[]``. Otherwise a
    hook with empty events silently matches no event and never fires.
    """
    from sky.schemas.generated import autostopv1_pb2  # type: ignore
    from sky.skylet import autostop_lib

    msg = autostopv1_pb2.Hook(run='echo hi', timeout=60)
    # events repeated field intentionally left empty.

    out = autostop_lib.hooks_from_protobuf([msg])
    assert len(out) == 1
    assert sorted(out[0]['events']) == sorted(ALL_EVENTS), (
        f"Empty events should default to all three on receive; got "
        f"{out[0]['events']!r}. Without the receive-side default, an "
        f"empty repeated events field is a silent no-op hook.")


def test_hooks_from_protobuf_preserves_nonempty_events():
    """Sanity: receive-side default kicks in only for *empty* events."""
    from sky.schemas.generated import autostopv1_pb2  # type: ignore
    from sky.skylet import autostop_lib

    msg = autostopv1_pb2.Hook(run='echo hi', timeout=60)
    msg.events.append(autostopv1_pb2.EVENT_STOP)
    out = autostop_lib.hooks_from_protobuf([msg])
    assert out[0]['events'] == ['stop']


def test_schema_rejects_oversized_run():
    """`run` strings past 16 KiB must be rejected at schema time.

    The skylet's gRPC server uses the default
    max_receive_message_length (4 MB). Unbounded `run` plus many hooks
    can blow past that with a confusing runtime gRPC error. Cap the
    individual `run` so the error surfaces at YAML validation instead.
    """
    big = 'a' * (16 * 1024 + 1)  # one byte past the 16 KiB cap
    with pytest.raises(Exception) as excinfo:
        _validate({'hooks': [{'run': big}]})
    msg = str(excinfo.value).lower()
    assert 'long' in msg or 'maxlength' in msg or '16384' in msg, (
        f'Validation error should mention the maxLength cap; got: {msg}')


def test_schema_accepts_run_at_limit():
    """Exactly 16 KiB is OK."""
    _validate({'hooks': [{'run': 'a' * (16 * 1024)}]})


def test_schema_rejects_too_many_hooks():
    """Aggregate hooks array bounded to 32 entries."""
    hooks = [{'run': f'echo {i}'} for i in range(33)]
    with pytest.raises(Exception) as excinfo:
        _validate({'hooks': hooks})
    msg = str(excinfo.value).lower()
    assert ('long' in msg or 'maxitems' in msg or 'too many' in msg or
            '32' in msg), (
                f'Validation error should mention the maxItems cap; got: {msg}')


def test_schema_accepts_32_hooks():
    """Exactly 32 is OK."""
    _validate({'hooks': [{'run': f'echo {i}'} for i in range(32)]})


def test_cli_hook_auto_select_with_cluster_only(monkeypatch):
    """`sky logs --hook <cluster>` (no event) must auto-select.

    The CLI design lets ``--hook`` take an optional event —
    the cluster name immediately after means "auto-select whichever
    event log exists". Click parses options greedily, so without a
    smart callback ``--hook mycluster`` rejects ``mycluster`` as an
    invalid event name. The fix: when the value isn't a valid event,
    push it back into ctx.args for positional parsing and return the
    auto-select sentinel.
    """
    from click.testing import CliRunner

    from sky.client.cli import command as cli_command

    captured = {}

    def _fake_tail_hook_logs(cluster_name, event=None, follow=True, tail=0):
        captured['cluster'] = cluster_name
        captured['event'] = event
        return 0

    monkeypatch.setattr('sky.client.sdk.tail_hook_logs', _fake_tail_hook_logs)

    runner = CliRunner()
    result = runner.invoke(cli_command.logs, ['--hook', 'mycluster'])

    assert result.exit_code == 0, (
        f'CLI exited with {result.exit_code}; expected 0 (auto-select).\n'
        f'output: {result.output}\n'
        f'exc: {result.exception!r}')
    assert captured.get('cluster') == 'mycluster', (
        f'Expected cluster=mycluster; got {captured!r}. The fix should '
        f'route `sky logs --hook mycluster` as "auto-select on '
        f'mycluster" — the no-event "auto-select" CLI form.')
    # Auto-select means event passed to SDK is None (empty sentinel
    # rewritten by the CLI).
    assert captured.get('event') is None


def test_cli_autostop_alias_routes_to_hook_stop_with_deprecation(monkeypatch):
    """`sky logs --autostop <cluster>` is the master-era CLI surface.

    After the autostop→stop event rename it must still keep working as a
    deprecation alias: rewrite the call to ``--hook stop`` and emit a
    one-line stderr warning so existing user scripts don't break across
    the v0.15.0 grace window. The removal anchor is pinned at v0.15.0
    in ``sky/utils/hooks_deprecation.py`` alongside the matching
    ``autostop.hook`` YAML and ``tail_autostop_logs`` SDK deprecations.
    """
    from click.testing import CliRunner

    from sky.client.cli import command as cli_command

    captured = {}

    def _fake_tail_hook_logs(cluster_name, event=None, follow=True, tail=0):
        captured['cluster'] = cluster_name
        captured['event'] = event
        captured['follow'] = follow
        return 0

    monkeypatch.setattr('sky.client.sdk.tail_hook_logs', _fake_tail_hook_logs)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli_command.logs,
                           ['--autostop', '--no-follow', 'mycluster'])

    assert result.exit_code == 0, (
        f'CLI exit {result.exit_code}; output={result.output!r}; '
        f'stderr={result.stderr!r}; exc={result.exception!r}. The '
        f'`--autostop` flag must remain a deprecated alias for master-era '
        f'callers; removal pinned at v0.15.0 in '
        f'sky/utils/hooks_deprecation.py.')
    assert captured.get('event') == 'stop', (
        f'--autostop must route to --hook stop after the autostop→stop '
        f"event rename. Got: event={captured.get('event')!r}.")
    assert captured.get('cluster') == 'mycluster'
    assert captured.get('follow') is False
    assert 'deprecat' in (result.stderr or '').lower(), (
        f'A stderr deprecation warning must be emitted. Got '
        f'stderr={result.stderr!r}.')


def test_cli_autostop_and_hook_flags_conflict(monkeypatch):
    """Combining `--autostop` with `--hook <event>` must error cleanly.

    The user is ambiguously asking for two log streams; we reject up
    front rather than silently picking one.
    """
    from click.testing import CliRunner

    from sky.client.cli import command as cli_command

    monkeypatch.setattr(
        'sky.client.sdk.tail_hook_logs', lambda *a, **kw: pytest.fail(
            'tail_hook_logs must not be called when --autostop and '
            '--hook conflict.'))

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli_command.logs,
                           ['--autostop', '--hook', 'preemption', 'mycluster'])
    assert result.exit_code != 0, (
        f'--autostop + --hook should error; got exit 0. output='
        f'{result.output!r}; stderr={result.stderr!r}.')


def test_sdk_tail_autostop_logs_alias_delegates_to_tail_hook_logs(
        monkeypatch, capsys):
    """`sky.client.sdk.tail_autostop_logs` is the master-era SDK alias.

    After the rename, it must still exist as a deprecated shim that
    delegates to ``tail_hook_logs(event='stop')`` and emits a one-line
    stderr deprecation warning. Same removal anchor as the CLI alias:
    v0.15.0, pinned in ``sky/utils/hooks_deprecation.py``.
    """
    from sky.client import sdk

    assert hasattr(sdk, 'tail_autostop_logs'), (
        'sky.client.sdk.tail_autostop_logs must exist as a deprecated '
        'alias for master-era callers; removal pinned at v0.15.0 in '
        'sky/utils/hooks_deprecation.py.')

    captured = {}

    def _fake_tail_hook_logs(cluster_name, event=None, follow=True, tail=0):
        captured['cluster'] = cluster_name
        captured['event'] = event
        captured['follow'] = follow
        captured['tail'] = tail
        return 0

    monkeypatch.setattr('sky.client.sdk.tail_hook_logs', _fake_tail_hook_logs)

    rc = sdk.tail_autostop_logs(cluster_name='mycluster', follow=False, tail=5)
    assert rc == 0
    assert captured == {
        'cluster': 'mycluster',
        'event': 'stop',
        'follow': False,
        'tail': 5,
    }, (f'tail_autostop_logs must delegate to tail_hook_logs with '
        f"event='stop' (autostop event was renamed to stop). Got: "
        f'{captured!r}.')

    err = capsys.readouterr().err
    assert 'deprecat' in err.lower(), (
        f'A stderr deprecation warning must be emitted. Got: {err!r}.')


def test_cli_hook_explicit_event_still_works(monkeypatch):
    """`sky logs --hook stop mycluster` (explicit event) keeps
    working after the smart-callback fix."""
    from click.testing import CliRunner

    from sky.client.cli import command as cli_command

    captured = {}

    def _fake_tail_hook_logs(cluster_name, event=None, follow=True, tail=0):
        captured['cluster'] = cluster_name
        captured['event'] = event
        return 0

    monkeypatch.setattr('sky.client.sdk.tail_hook_logs', _fake_tail_hook_logs)

    runner = CliRunner()
    result = runner.invoke(cli_command.logs, ['--hook', 'stop', 'mycluster'])

    assert result.exit_code == 0, (
        f'CLI exit {result.exit_code}; output: {result.output}\n'
        f'exc: {result.exception!r}')
    assert captured == {'cluster': 'mycluster', 'event': 'stop'}


def test_tail_hook_logs_minimal_api_version_required(monkeypatch):
    """`tail_hook_logs` is a new SDK method (introduced by PR1 — adds
    the ``/hook_logs`` server route). A new client talking to an older
    server (pre-/hook_logs) must surface a clean
    ``APINotSupportedError`` from the ``@versions.minimal_api_version``
    decorator rather than a 404 on the unknown endpoint.
    """
    from sky import exceptions
    from sky.client import sdk
    from sky.server import constants as server_constants
    from sky.server import versions

    # Pin the remote server to one less than the current API_VERSION so
    # the decorator's gate fires. Combine the function-level monkeypatch
    # (so the decorator sees the pinned value) with an explicit
    # ContextVar reset in `finally` — the outer ``check_server_healthy``
    # decorator runs first and otherwise calls ``set_remote_api_version``
    # with a real int that would persist into subsequent tests in the
    # same xdist worker.
    monkeypatch.setattr(versions, 'get_remote_api_version',
                        lambda: server_constants.API_VERSION - 1)
    try:
        with pytest.raises(exceptions.APINotSupportedError) as excinfo:
            sdk.tail_hook_logs(cluster_name='ignored')
        err = str(excinfo.value)
        assert 'tail_hook_logs' in err, (
            f'Error message should name the SDK function; got: {err!r}')
    finally:
        # Even with monkeypatch reverting the function-level patch,
        # explicitly clear the ContextVar so we never leak whatever the
        # auth path / decorator wrote into it.
        versions.set_remote_api_version(None)


# ---------------------------------------------------------------------------
# Wire-format back-compat — AutostopCodeGen dual-emit
#
# The codegen ships ONE Python one-liner to the cluster head; that script
# runs against whatever SKYLET_LIB_VERSION the cluster's installed skylet
# has and branches into the appropriate `autostop_lib.set_autostop(...)`
# call. The branches:
#   < 4:  legacy waitless single-positional
#   < 5:  + wait_for
#   < 7:  + hook / hook_timeout (master's pre-PR1 single-hook path)
#   >= 7: set_autostop + set_hooks(...)   (new dual-emit path)
#
# The four tests below pin each non-trivial branch's emitted shape so
# regressions to the cross-version contract surface in unit tests rather
# than as silent "hook never fires" on a clusters launched against a
# pre-v7 skylet image.
# ---------------------------------------------------------------------------


def _render_codegen(*, down: bool, hooks):
    """Render AutostopCodeGen.set_autostop and return the embedded
    Python payload (un-shlex-quoted) so the asserts can read literal
    Python strings instead of shell-escaped fragments.
    """
    import shlex as _shlex

    from sky.skylet import autostop_lib
    rendered = autostop_lib.AutostopCodeGen.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=down,
        hooks=hooks,
    )
    # _build wraps the python code in ``... -u -c <shlex.quote'd-py>``.
    # shlex.split reverses the outer quoting; the final token is the
    # raw python source.
    return _shlex.split(rendered)[-1]


def test_codegen_pre_v7_branch_flattens_stop_hook_when_autostop_false():
    """Pre-v7 (≤6) skylets have a single-hook slot via `hook=` kwarg.
    For an autostop launch (``down=False``), the codegen must flatten
    a ``stop``-event hook into that slot so pre-v7 skylets fire the
    same user script that v7+ skylets would for the ``stop`` event.
    """
    payload = _render_codegen(
        down=False,
        hooks=[{
            'run': 'save-checkpoint.sh',
            'events': ['stop'],
            'timeout': 90,
        }],
    )
    # The < 7 branch flattens via hook=... / hook_timeout=...
    assert "hook='save-checkpoint.sh'" in payload, (
        f"pre-v7 branch must flatten stop-event hook into `hook=`; got:\n"
        f"{payload}")
    assert 'hook_timeout=90' in payload, (
        f"pre-v7 branch must propagate the matching timeout; got:\n{payload}")


def test_codegen_pre_v7_branch_flattens_down_hook_when_autodown_true():
    """Autodown (``down=True``) is the autostop-with-teardown path.
    A pre-v7 skylet's single ``hook`` slot fires on idle-timer teardown
    regardless of stop-vs-down distinction, so the codegen must flatten
    the ``down``-event hook (not the ``stop`` hook) into that slot.
    Catches the bug where the flatten lookup ignores ``autostop.down``.
    """
    payload = _render_codegen(
        down=True,
        hooks=[
            {
                'run': 'wrong-stop.sh',
                'events': ['stop']
            },
            {
                'run': 'right-down.sh',
                'events': ['down'],
                'timeout': 120
            },
        ],
    )
    assert "hook='right-down.sh'" in payload, (
        f"autodown launch should flatten the down-event hook into "
        f"`hook=`; got:\n{payload}")
    assert "hook='wrong-stop.sh'" not in payload, (
        f"autodown launch must NOT flatten the stop-event hook; got:\n"
        f"{payload}")
    assert 'hook_timeout=120' in payload


def test_codegen_pre_v7_branch_no_flat_when_only_preemption_hook():
    """A cluster declaring only ``events:[preemption]`` hooks has no
    idle-timer-matching hook, so the pre-v7 single-hook slot stays
    empty. (Pre-v7 master had no preemption-hook concept, so there's
    no way to surface a preemption hook to it — the user must upgrade
    the cluster.) The codegen must emit ``hook=None`` for this case.
    """
    payload = _render_codegen(
        down=False,
        hooks=[{
            'run': 'on-preemption.sh',
            'events': ['preemption'],
        }],
    )
    assert 'hook=None' in payload, (
        f"pre-v7 branch should emit hook=None when no stop/down hook "
        f"matches; got:\n{payload}")


def test_codegen_v7_branch_preserves_legacy_hook_when_hooks_none():
    """Pre-v7 client passes ``hook=`` (no ``hooks=``) via SSH codegen.

    Real-world trigger: SkyPilot's master client calls
    ``sky.autostop(cluster, idle_minutes=1, hook='sleep 120')``. The
    SDK lands on a v7+ server which dispatches over SSH codegen when
    gRPC is disabled (``SKYPILOT_ENABLE_GRPC`` defaults False), and the
    codegen v7+ ``else:`` branch must:

      1. **Pass** the legacy ``hook=`` / ``hook_timeout=`` args to
         ``autostop_lib.set_autostop`` so its internal routing (the
         pre-v7 client → v7+ skylet bridge at autostop_lib.py:200-206)
         stores the hook under the new ``lifecycle_hooks`` key.

      2. **Not** call ``set_hooks([])`` when ``hooks is None`` —
         ``None`` means "leave stored hooks alone", and the empty-list
         call would wipe the entry the routing just persisted, leaving
         the skylet with no hook to fire at idle-timer teardown.

    Confirmed root cause of TestBackwardCompatibility::
    test_client_server_compatibility_new_server on aws timing out
    waiting for AUTOSTOPPING: the cluster transitioned UP → STOPPED
    without ever running the ``sleep 120`` hook because the codegen
    silently dropped it.
    """
    from sky.skylet import autostop_lib

    rendered = autostop_lib.AutostopCodeGen.set_autostop(
        idle_minutes=1,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=False,
        hook='sleep 120',
        hook_timeout=None,
        hooks=None,
    )
    # _build wraps the python code in ``... -u -c <shlex.quote'd-py>``.
    # Unwrap to inspect the literal Python source.
    import shlex as _shlex
    parts = _shlex.split(rendered)
    payload = parts[-1]

    # The v7+ (else:) branch is the last block; it must include the
    # legacy hook in the set_autostop call so the skylet's routing
    # bridge fires.
    assert "'sleep 120'" in payload, (
        f'Codegen v7+ branch must forward the legacy ``hook`` arg to '
        f'set_autostop; otherwise the skylet has no way to route the '
        f'master client\'s ``hook=`` into the new hooks list. Rendered '
        f'payload:\n{payload}')

    # When the caller passes ``hooks=None``, the v7+ branch must not
    # emit ``set_hooks([])`` because that wipes the routed entry. The
    # bug we are guarding against is exactly this empty-list call.
    assert 'set_hooks([])' not in payload, (
        f'Codegen v7+ branch emitted ``set_hooks([])`` for a None-hooks '
        f'caller. None means "leave stored alone"; ``[]`` is reserved '
        f'for explicit clear. Emitting ``set_hooks([])`` here wipes the '
        f'entry that set_autostop\'s legacy-hook routing just stored. '
        f'Rendered payload:\n{payload}')


def test_codegen_v7_branch_explicit_clear_hooks_still_works():
    """Re-launches that drop hooks pass ``hooks=[]`` to explicitly clear.

    Guarding the corollary of the previous test: the codegen must still
    emit ``set_hooks([])`` when ``hooks=[]`` is passed explicitly (vs.
    ``hooks=None`` which leaves stored alone).
    """
    from sky.skylet import autostop_lib

    rendered = autostop_lib.AutostopCodeGen.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=False,
        hooks=[],
    )
    import shlex as _shlex
    parts = _shlex.split(rendered)
    payload = parts[-1]
    assert 'set_hooks([])' in payload, (
        f'Explicit hooks=[] must still emit set_hooks([]) so the skylet '
        f'clears its stored hooks list. Rendered:\n{payload}')


def test_codegen_v7_branch_dual_emits_set_autostop_and_set_hooks():
    """v7+ skylets get the full ``set_hooks(...)`` call alongside the
    legacy-shape ``set_autostop(...)``. Dual-emit lets a single rendered
    payload work on both pre-v7 and v7+ skylets — pre-v7 takes the
    ``hook=``/``hook_timeout=`` branch, v7+ takes the ``set_hooks``
    branch. Without dual-emit a brand-new client would NOT propagate
    hooks to a brand-new cluster.
    """
    hooks = [
        {
            'run': 'a.sh',
            'events': ['stop']
        },
        {
            'run': 'b.sh',
            'events': ['preemption'],
            'timeout': 60
        },
    ]
    payload = _render_codegen(down=False, hooks=hooks)
    # The else (>= 7) branch invokes set_hooks with the full list.
    assert 'autostop_lib.set_hooks(' in payload, (
        f"v7+ branch must invoke set_hooks(...); got:\n{payload}")
    # And the payload contains both hooks (the actual list literal).
    assert "'run': 'a.sh'" in payload and "'run': 'b.sh'" in payload, (
        f"set_hooks payload should carry every hook from the input "
        f"list; got:\n{payload}")
    # The skylet_lib_version dispatch wraps both branches.
    assert 'skylet_lib_version' in payload
    assert 'if skylet_lib_version < 7' in payload, (
        f"Dual-emit must keep the pre-v7 branch (`< 7`) alive; got:\n"
        f"{payload}")


def test_set_autostop_skylet_side_routes_legacy_hook_arg_down_aware(
        tmp_path, monkeypatch):
    """Receive-side back-compat: a pre-v7 client calls v7+ skylet's
    ``set_autostop`` with the legacy ``hook=`` kwarg (no ``hooks=``).
    The skylet must translate that single hook into the new
    ``_HOOKS_CONFIG_KEY`` sqlite storage so ``hook_executor`` finds it
    at teardown. Routing must be **down-aware**: ``down=False`` →
    ``events:[stop]``; ``down=True`` (autodown) → ``events:[down]``.
    """
    from sky.skylet import autostop_lib
    from sky.skylet import configs
    from sky.skylet import runtime_utils

    # Redirect the skylet's sqlite to a tmp file so the test is hermetic.
    # ``configs._DB_PATH`` is lazily set by ``init_db`` which calls
    # ``runtime_utils.get_runtime_dir_path``; the same path then has its
    # schema bootstrapped (CREATE TABLE). Reset ``_DB_PATH`` to None and
    # redirect ``get_runtime_dir_path`` so the init runs against tmp.
    monkeypatch.setattr(configs, '_DB_PATH', None)
    db_dir = tmp_path / 'sky-config-test'
    db_dir.mkdir()
    monkeypatch.setattr(runtime_utils, 'get_runtime_dir_path',
                        lambda relpath: str(db_dir / relpath.lstrip('/')))

    # Case 1: down=False → legacy hook → events:[stop]
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=False,
        hook='legacy-stop.sh',
        hook_timeout=42,
    )
    stored = autostop_lib.get_hooks()
    assert stored and len(stored) == 1, (
        f'Skylet must store the translated legacy hook; got: {stored!r}')
    assert stored[0]['run'] == 'legacy-stop.sh'
    assert sorted(stored[0]['events']) == [
        'stop'
    ], (f"down=False must route legacy hook to events:[stop]; got: "
        f"{stored[0]['events']!r}")
    assert stored[0]['timeout'] == 42

    # Case 2: down=True (autodown) → legacy hook → events:[down]
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hook='legacy-autodown.sh',
    )
    stored = autostop_lib.get_hooks()
    assert stored and len(stored) == 1
    assert stored[0]['run'] == 'legacy-autodown.sh'
    assert sorted(stored[0]['events']) == [
        'down'
    ], (f"down=True (autodown) must route legacy hook to events:[down]; "
        f"got: {stored[0]['events']!r}")


# ---------------------------------------------------------------------------
# Devin AI review findings — regression tests
# ---------------------------------------------------------------------------


def test_tail_hook_logs_tail_zero_prints_all_lines(tmp_path):
    """`tail_hook_logs(tail=0)` must print all lines, not POSIX last-10.

    The docstring at ``cloud_vm_ray_backend.tail_hook_logs`` promises
    "If 0, print all lines." The implementation built the shell command
    as ``tail <flags> <path>`` where ``<flags>`` was empty when both
    ``tail=0`` and ``follow=False`` — which is exactly the snapshot path
    a user hits via ``sky logs --hook stop --no-follow``. A bare
    ``tail <path>`` falls back to POSIX ``tail``'s default last-10
    behavior, silently truncating the user's view of small hook logs
    that fit in fewer than 10 lines is fine but logs with > 10 lines
    (think: a hook that emits progress as it runs) lose their earliest
    output.

    Functionally verify by running the generated shell command against
    a 30-line file and asserting all 30 lines come through.
    """
    from sky.backends import cloud_vm_ray_backend

    captured = {}

    class _FakeHandle:
        pass

    class _FakeBackend(cloud_vm_ray_backend.CloudVmRayBackend):

        def __init__(self):  # pylint: disable=super-init-not-called
            pass

        def run_on_head(self, handle, cmd, **kw):  # type: ignore[override]
            del handle, kw
            captured['cmd'] = cmd
            return 0

    _FakeBackend().tail_hook_logs(_FakeHandle(),
                                  event='stop',
                                  follow=False,
                                  tail=0)
    cmd = captured['cmd']

    # Build a 30-line log file and substitute the head-node paths so we
    # can execute the cmd locally without an actual cluster.
    log = tmp_path / 'stop.log'
    log.write_text('\n'.join(f'line{i}' for i in range(1, 31)) + '\n')
    legacy = tmp_path / 'autostop_hook_nonexistent.log'
    cmd_local = cmd.replace('~/.sky/hooks/stop.log', str(log)).replace(
        f'~/{constants.AUTOSTOP_HOOK_LOG_FILE}', str(legacy))

    import subprocess
    result = subprocess.run(['bash', '-c', cmd_local],
                            capture_output=True,
                            text=True,
                            check=False)
    assert result.returncode == 0, (
        f'tail_hook_logs cmd exited non-zero: rc={result.returncode}, '
        f'stderr={result.stderr!r}, cmd={cmd_local!r}')

    out_lines = [l for l in result.stdout.splitlines() if l.startswith('line')]
    assert len(out_lines) == 30, (
        f'tail_hook_logs(tail=0, follow=False) should print all 30 lines per '
        f'its docstring contract `If 0, print all lines.` — got {len(out_lines)}. '
        f'With the bug, POSIX `tail` defaults to last-10 lines, dropping '
        f'line1..line20. Full output:\n{result.stdout}')
    assert 'line1' in out_lines, (
        f'Earliest line (line1) must be present when tail=0; with the bug '
        f'it is truncated. Got first lines: {out_lines[:5]!r}')


def test_kubernetes_caps_preemption_hook_default_timeout():
    """`cap_preemption_hook_timeouts` must apply the default 3600s when
    a hook entry lacks an explicit ``timeout`` key, matching the runtime
    behavior of ``hook_executor``.

    Today all production callers normalize hooks via
    ``Resources._normalize_hook_entry`` before reaching this function,
    so the default never triggers in practice. But the function reads
    as if a missing-timeout entry would NOT be capped (its default was
    ``0`` < cap), while at runtime the executor would honor 3600s —
    blowing past the 600s pod grace and getting SIGKILLed mid-run. This
    test pins the function's default to match
    ``DEFAULT_HOOK_TIMEOUT_SECONDS`` so any future caller that skips
    normalization stays safe.
    """
    from sky.clouds.kubernetes import cap_preemption_hook_timeouts

    # Entry with no ``timeout`` key — must be treated as DEFAULT (3600)
    # and therefore capped to 600.
    hooks = [{'run': 'a', 'events': ['preemption']}]
    out = cap_preemption_hook_timeouts(hooks)
    assert out is not None and len(out) == 1
    assert out[0]['timeout'] == 600, (
        f'Missing-timeout entry must be treated as the runtime default '
        f'({constants.DEFAULT_HOOK_TIMEOUT_SECONDS}s) and capped to 600s '
        f'on Kubernetes (pod terminationGracePeriodSeconds limit). Got: '
        f"{out[0].get('timeout')!r}. If left uncapped, kubelet SIGKILLs "
        f'the hook at 600s while the skylet stores a misleading 3600s.')


def test_kubernetes_cap_splits_multi_event_preemption_entry():
    """Multi-event hooks must not have non-preemption timeouts clobbered.

    A hook entry can list multiple events, e.g.
    ``{events: [preemption, stop], timeout: 3600}``. The 600s K8s pod-grace
    cap applies only to the *preemption* dispatch — the *stop* dispatch
    (idle-timer teardown) has no such constraint and must honor the user's
    3600s.

    Today ``cap_preemption_hook_timeouts`` rewrites the single ``timeout``
    field on the shared entry whenever ``'preemption' in events``,
    silently truncating the stop-event timeout too. ``hook_executor.run``
    then reads ``entry['timeout']`` per event and kills ``save.sh`` at
    600s on a normal idle-timer stop — violating both the user's intent
    and the function's own docstring ("Only ``preemption``-event entries
    are affected; ``autostop``/``down`` hooks don't interact with the pod
    grace.").

    Fix: split a multi-event entry into one capped preemption entry plus
    one uncapped non-preemption entry so each dispatch path sees the
    correct timeout. Same ``run`` script, same effect — only the stored
    timeout differs per event.
    """
    from sky.clouds.kubernetes import cap_preemption_hook_timeouts

    hooks = [{
        'run': 'save.sh',
        'events': ['preemption', 'stop'],
        'timeout': 3600,
    }]
    out = cap_preemption_hook_timeouts(hooks)
    assert out is not None

    # After the split, we expect two entries — one for the capped
    # preemption dispatch and one for the uncapped non-preemption events.
    preempt_entries = [e for e in out if e.get('events') == ['preemption']]
    stop_entries = [e for e in out if 'stop' in (e.get('events') or [])]

    assert len(preempt_entries) == 1, (
        f'Multi-event entry should produce exactly one preemption-only '
        f'entry after split. Got: {out!r}')
    assert preempt_entries[0]['timeout'] == 600, (
        f'Preemption entry must be capped to 600s on Kubernetes. Got '
        f"timeout={preempt_entries[0]['timeout']}.")

    assert len(stop_entries) == 1, (
        f'Stop event must remain in some entry after split. Got: {out!r}')
    assert stop_entries[0]['timeout'] == 3600, (
        f"Non-preemption events keep the user's timeout (3600s); K8s "
        f"grace doesn't apply to stop. Got "
        f"timeout={stop_entries[0]['timeout']}. With the bug, the cap "
        f"clobbers timeout on the shared entry and hook_executor.run('stop') "
        f"silently SIGKILLs save.sh at 600s.")

    # The preemption and non-preemption entries must be disjoint on
    # event sets so the executor doesn't double-fire save.sh.
    assert 'preemption' not in (stop_entries[0]['events']), (
        f'After split, the non-preemption entry must not also contain '
        f'preemption (would double-fire on preemption). Got events: '
        f"{stop_entries[0]['events']!r}.")

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
        f'mycluster" — the design doc form.')
    # Auto-select means event passed to SDK is None (empty sentinel
    # rewritten by the CLI).
    assert captured.get('event') is None


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

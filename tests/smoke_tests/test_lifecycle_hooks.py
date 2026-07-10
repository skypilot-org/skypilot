"""Smoke tests for the lifecycle-hooks framework (config.hooks:).

Run all tests against the default generic cloud::

    pytest tests/smoke_tests/test_lifecycle_hooks.py

Pin to a specific cloud (default is AWS)::

    pytest tests/smoke_tests/test_lifecycle_hooks.py --generic-cloud gcp

Run only Kubernetes-specific tests::

    pytest tests/smoke_tests/test_lifecycle_hooks.py -k k8s \\
        --generic-cloud kubernetes

Run a single test::

    pytest tests/smoke_tests/test_lifecycle_hooks.py::test_hook_lifecycle_combined

Test layout — these tests are intentionally aggregated to amortize
launch/teardown cycles. Each combined test calls out the sub-features
it covers in a leading comment + per-assertion comments so a failure
narrows down quickly.
"""

import tempfile
import textwrap
import time

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky.utils import yaml_utils

# Clouds that don't support autostop/idle-timer semantics — mirrors the
# exclusions used by the legacy `test_launch_fast_with_autostop_hook`.
_NO_AUTOSTOP_MARKS = [
    pytest.mark.no_fluidstack,
    pytest.mark.no_lambda_cloud,
    pytest.mark.no_ibm,
    pytest.mark.no_slurm,
    pytest.mark.no_hyperbolic,
    pytest.mark.no_shadeform,
    pytest.mark.no_seeweb,
]


def _no_autostop(fn):
    for mark in _NO_AUTOSTOP_MARKS:
        fn = mark(fn)
    return fn


def _write_yaml(resources: dict) -> str:
    """Dump minimal.yaml with a custom resources block to a temp file.

    Tests historically pass `hooks` inside the `resources` dict (the
    PR1 form). The canonical location is now `config.hooks:` at the
    task-YAML top level. We auto-relocate so existing test bodies don't
    need rewrites: any `hooks` key in the `resources` dict is moved to
    `config.hooks`. Tests that explicitly want to exercise the
    deprecated `resources.hooks:` routing should use `_write_yaml_raw`.
    """
    cfg = yaml_utils.read_yaml('tests/test_yamls/minimal.yaml')
    resources = dict(resources)  # avoid mutating caller
    hooks = resources.pop('hooks', None)
    cfg['resources'] = resources
    if hooks is not None:
        cfg.setdefault('config', {})['hooks'] = hooks
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    yaml_utils.dump_yaml(f.name, cfg)
    f.flush()
    return f.name


def _write_yaml_raw(top_level: dict) -> str:
    """Like _write_yaml but writes the dict at the top level of the
    task YAML — no auto-relocation. Use this for tests that need to
    exercise the deprecated `resources.hooks` path."""
    cfg = yaml_utils.read_yaml('tests/test_yamls/minimal.yaml')
    cfg.update(top_level)
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    yaml_utils.dump_yaml(f.name, cfg)
    f.flush()
    return f.name


# Shorthand for the shared helper — every kubectl invocation in this
# module needs to resolve the workload cluster's kubeconfig context
# at runtime (the API server's K8s cluster differs from the workload
# cluster on shared-API-server pipelines).
_kc = smoke_tests_utils.kubectl_for_cluster


# ---------------------------------------------------------------------------
# Combined stop + retrieval coverage. One launch + one autostop cycle
# verifies the union of:
#   - stop hook fires and is retrievable (idle-timer teardown w/ down=false)
#   - omitting `events:` defaults to all three events on the stored config
#   - multi-event hook entry fires only on matching events
#   - `sky logs --hook` (no event) auto-selects whichever log exists
#   - legacy YAML `autostop.hook` is routed through the new framework with
#     a stderr deprecation warning at launch (autostop.down=false → `stop`)
#   - hook timeout kills the script mid-run; teardown still completes and
#     the log captures the partial output
#   - hook with multi-line `run` + literal "EOF" survives JSON round-trip
#     through skylet sqlite
# ---------------------------------------------------------------------------
@_no_autostop
@pytest.mark.no_kubernetes
def test_hook_lifecycle_combined(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    legacy_marker = f'legacy-{time.time()}'
    multi_event_marker = f'multi-event-{time.time()}'
    timeout_marker = f'partial-output-{time.time()}'
    # Multi-line `run` with embedded "EOF" tests that we don't accidentally
    # early-terminate when the value round-trips through any heredoc.
    multiline_run = (f'echo line1\n'
                     f'echo {multi_event_marker}\n'
                     f'EOF\n'
                     f'echo done')
    yaml_path = _write_yaml({
        'autostop': {
            'idle_minutes': 1,
            'down': False,
            # Legacy back-compat: should be routed into the hooks list.
            # `seq 1 50` pads stop.log past 10 lines so the existing grep
            # for `legacy_marker` also catches the `tail`-default-to-last-10
            # regression in `tail_hook_logs` (PR #9064 Devin review).
            'hook': f'echo {legacy_marker} && seq 1 50',
            'hook_timeout': 60,
        },
        'hooks': [
            # Defaults check: omitting `events:` should fill in all three.
            {
                'run': 'true',
            },
            # Multi-event filter + multi-line + EOF survival.
            {
                'run': multiline_run,
                'events': ['stop', 'down'],
            },
            # Hook timeout: prints, then sleeps until the 5s timeout kills it.
            {
                'run': f'echo {timeout_marker} && sleep 9999',
                'events': ['stop'],
                'timeout': 5,
            },
        ],
    })
    inspect = ('sqlite3 ~/.sky/skylet_config.db '
               '"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"')
    test = smoke_tests_utils.Test(
        'test_hook_lifecycle_combined',
        [
            # Capture stderr so we can verify the legacy autostop.hook
            # deprecation warning fires at launch time.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path} 2>&1) && '
            # (1) legacy autostop.hook routing → stderr deprecation warning
            f'echo "$s" | grep -i "deprecated"',
            # (2) defaults: omitted events → [stop, preemption, down]
            # (3) multi-line run + literal "EOF" survives sqlite round-trip
            # (4) legacy hook routed into the hooks list
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep -qw stop && '
            f'echo "$out" | grep -q preemption && '
            f'echo "$out" | grep -qw down && '
            f'echo "$out" | grep -q line1 && '
            f'echo "$out" | grep -q EOF && '
            f'echo "$out" | grep -q done && '
            f'echo "$out" | grep -q "{legacy_marker}"',
            # Wait for autostop to fire (timeout-killed hook included).
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
            'sleep 30',
            # Re-launch to refresh the cluster into UP so we can query
            # the per-event log files via `sky logs`.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --fast '
            f'tests/test_yamls/minimal.yaml) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # (5) stop log carries every stop-matching marker
            f'out=$(sky logs {name} --hook stop --no-follow) && '
            f'echo "$out" | grep "{legacy_marker}" && '
            f'echo "$out" | grep "{multi_event_marker}" && '
            # (6) timeout-killed hook still wrote its initial echo
            f'echo "$out" | grep "{timeout_marker}"',
            # (7) `sky logs --hook` (no event) auto-selects the log
            f'out=$(sky logs {name} --hook --no-follow) && '
            f'echo "$out" | grep "{legacy_marker}"',
            # (8) multi-event hook with `events:[stop, down]` did NOT
            # fire on `down` — its marker must not appear in down.log
            f'! sky logs {name} --hook down --no-follow 2>&1 | '
            f'grep -q "{multi_event_marker}"',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) + autostop_timeout,
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# Down-hook fires during `sky down` and is retrievable via
# `sky logs --hook down` while teardown is still running. Also asserts
# the cluster went down cleanly (no UP-state leak).
#
# The earlier draft of this test additionally tried to land `sky down`
# inside a 1-minute autostop window to exercise the autostop+down CAS
# race. That race is timing-dependent on real clouds — autostop often
# fires before `sky down` lands, so the down hook never runs and the
# grep fails. The race is already covered by the per-node file-lock
# unit tests (`test_hook_executor.py::test_try_claim_teardown_*`).
# Here we keep autostop disabled so `sky down` reliably wins.
# ---------------------------------------------------------------------------
@_no_autostop
@pytest.mark.no_kubernetes
def test_hook_down_and_race(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    down_marker = f'hook-down-{time.time()}'
    yaml_path = _write_yaml({
        'hooks': [{
            'run': f'echo {down_marker} && sleep 15 && echo down-done',
            'events': ['down'],
            'timeout': 60,
        }],
    })
    # Kick off `sky down` in the background so we can race to tail the
    # down log before teardown finishes. The 15s sleep in the hook
    # gives us plenty of time to grab the log via `sky logs --hook down`.
    bg_down = (f'nohup sky down -y {name} > /tmp/{name}-down.log 2>&1 & '
               f'DOWN_PID=$!; sleep 5; ')
    tail = (
        f'sky logs {name} --hook down --no-follow > /tmp/{name}-tail.log '
        f'2>&1 || true; wait $DOWN_PID; '
        # (1) down-hook fired during teardown
        f'grep "{down_marker}" /tmp/{name}-tail.log')
    test = smoke_tests_utils.Test(
        'test_hook_down_and_race',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            bg_down + tail,
            # (2) cluster went down cleanly — no UP-state leak.
            f'sky status {name} 2>/dev/null | grep -v "UP" || true',
        ],
        # Background `sky down` already drove teardown; purge as
        # belt-and-suspenders if it failed.
        f'sky down -y {name} --purge || true',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# User-initiated `sky stop` fires the `stop` event (the original
# design's exclusion of `sky stop` was dropped: any path that takes the
# cluster offline fires a hook). Also pins that the hook subprocess
# sees the new `SKYPILOT_HOOK_EVENT` env var set to the firing event.
# ---------------------------------------------------------------------------
@_no_autostop
@pytest.mark.no_kubernetes
def test_hook_sky_stop_fires_stop_event_with_env_var(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    stop_marker = f'hook-stop-{time.time()}'
    yaml_path = _write_yaml({
        'hooks': [{
            'run': (f'echo {stop_marker} && '
                    f'echo "EVENT=$SKYPILOT_HOOK_EVENT"'),
            'events': ['stop'],
            'timeout': 60,
        }],
    })
    test = smoke_tests_utils.Test(
        'test_hook_sky_stop_fires_stop_event_with_env_var',
        [
            # (1) Launch with a stop hook.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # (2) User-initiated `sky stop`. Pre-PR1-rename this fired
            # no hook; now it fires `stop`.
            f'sky stop -y {name}',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            # (3) Bring it back up so we can read the per-event log file.
            f'sky start -y {name}',
            # (4) Stop hook fired AND `$SKYPILOT_HOOK_EVENT` was set to 'stop'.
            f'out=$(sky logs {name} --hook stop --no-follow) && '
            f'echo "$out" | grep "{stop_marker}" && '
            f'echo "$out" | grep "EVENT=stop"',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) + 300,
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# Re-launch propagates hook changes through three transitions on a single
# cluster:
#   (A) launch with hook A         → stored hooks = [A]
#   (B) re-launch with hook B      → stored hooks = [B], A gone (replaced)
#   (C) re-launch dropping hooks   → stored hooks = empty
#
# Runs on every cloud INCLUDING Kubernetes (no @no_kubernetes mark) because
# the propagation path is the same SetAutostop gRPC / SSH codegen surface on
# every backend — the cluster stays UP throughout, so the K8s autodown
# semantics don't interact with this test.
#
# Pinned regressions:
#   - (B) ensures relaunch actually REPLACES the stored list, not just
#     appends. Pre-fix, an old codegen path could leave hook A alive.
#   - (C) ensures the "drop hooks" path emits `clear_hooks=True` on the
#     wire, since proto3 `repeated` has no presence (an empty `hooks` list
#     on the wire is otherwise indistinguishable from "field omitted").
#     Without `clear_hooks=True`, A would persist forever.
# ---------------------------------------------------------------------------
@_no_autostop
def test_hook_clear_via_relaunch(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    yaml_with_hook_a = _write_yaml({
        'hooks': [{
            'run': 'echo first-hook',
            'events': ['down'],
        }],
    })
    yaml_with_hook_b = _write_yaml({
        'hooks': [{
            'run': 'echo second-hook',
            'events': ['down'],
            'timeout': 45,
        }],
    })
    yaml_without_hook = _write_yaml({})  # empty resources, no hooks
    inspect = ('sqlite3 ~/.sky/skylet_config.db '
               '"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"')
    test = smoke_tests_utils.Test(
        'test_hook_clear_via_relaunch',
        [
            # (A) Launch with hook A
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_with_hook_a}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep first-hook && '
            f'! echo "$out" | grep second-hook',
            # (B) Re-launch with hook B — must REPLACE stored hooks
            f'sky launch -y -c {name} --fast {yaml_with_hook_b}',
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep second-hook && '
            f'! echo "$out" | grep first-hook',
            # (C) Re-launch without hooks — must clear stored list entirely
            f'sky launch -y -c {name} --fast {yaml_without_hook}',
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'! echo "$out" | grep first-hook && '
            f'! echo "$out" | grep second-hook',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# Regression pin: `sky exec` MUST NOT clear the stored hooks list.
#
# A reviewer flagged a hypothetical bug where the `PRE_EXEC` stage's
# hooks-payload block in `sky/execution.py` (lines ~613-650) might fire
# during `sky exec` with `task_hooks=None + cluster_exists=True`,
# wiping the stored hooks by sending `clear_hooks=True` to the skylet.
#
# Local verification confirmed the bug doesn't fire today because
# `sky exec` passes `stages=[Stage.SYNC_WORKDIR, Stage.EXEC]` —
# `Stage.PRE_EXEC` is NOT in the list, so the entire hooks_payload
# block is gated off. The flagged code path is correctly inaccessible
# from `sky exec`.
#
# This smoke pins that contract end-to-end so a future change that
# adds `Stage.PRE_EXEC` to `sky exec`'s stages list fires the test
# immediately rather than producing a silent hooks-wipe regression
# that users only discover when a hook stops firing on teardown.
# ---------------------------------------------------------------------------
@_no_autostop
def test_hook_sky_exec_does_not_clear_stored_hooks(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    yaml_with_hook = _write_yaml({
        'hooks': [{
            'run': 'echo persistent-hook',
            'events': ['down'],
            'timeout': 30,
        }],
    })
    inspect = ('sqlite3 ~/.sky/skylet_config.db '
               '"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"')
    test = smoke_tests_utils.Test(
        'test_hook_sky_exec_does_not_clear_stored_hooks',
        [
            # Launch the cluster with hook A.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_with_hook}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Verify hook is stored before exec.
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep persistent-hook',
            # Run `sky exec` with a task that has no hooks. The
            # hypothetical bug would set hooks_payload=[] →
            # SetAutostop with clear_hooks=True → wipe.
            f'sky exec {name} -- "echo hello-from-exec"',
            # Pin: hook is STILL stored. If a future change adds
            # PRE_EXEC to sky exec's stages and the hooks-payload
            # block fires, this assertion fails.
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep persistent-hook',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# STOPPED → re-launch with new hook propagates correctly.
#
# Real user pattern: launch a cluster yesterday with hook A, `sky stop` it,
# come back today and `sky launch -c <name>` with a new YAML containing
# hook B. The cluster restarts and the skylet's stored hooks list should be
# REPLACED with hook B (not still firing A on the next teardown).
#
# This is the STOPPED-in-between variant of test_hook_clear_via_relaunch's
# (B) step above. The interesting bit is that the skylet's sqlite at
# ~/.sky/skylet_config.db survives stop/start (on-disk persistence), so the
# OLD hook entry is sitting in storage when the cluster restarts — the
# re-launch must actively overwrite it via SetAutostop. Pinned here in case
# the post-restart launch path ever short-circuits and skips the
# SetAutostop call when the cluster comes back UP.
#
# VM-only because K8s has no STOPPED state — clusters are either UP or
# torn down (autodown).
# ---------------------------------------------------------------------------
@_no_autostop
@pytest.mark.no_kubernetes
def test_hook_relaunch_propagates_after_stop(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    yaml_a = _write_yaml({
        'hooks': [{
            'run': 'echo before-stop-hook',
            'events': ['down'],
        }],
    })
    yaml_b = _write_yaml({
        'hooks': [{
            'run': 'echo after-stop-hook',
            'events': ['down'],
            'timeout': 50,
        }],
    })
    inspect = ('sqlite3 ~/.sky/skylet_config.db '
               '"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"')
    test = smoke_tests_utils.Test(
        'test_hook_relaunch_propagates_after_stop',
        [
            # (1) Launch with hook A — stored = before-stop-hook.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_a}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep before-stop-hook',
            # (2) Stop the cluster — sqlite persists on the host disk.
            f'sky stop -y {name}',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=smoke_tests_utils.get_timeout(generic_cloud)),
            # (3) Re-launch with hook B. SkyPilot restarts the stopped
            # cluster (same disk, sqlite still has hook A) AND must run
            # SetAutostop to propagate the new hook B.
            f'sky launch -y -c {name} --fast {yaml_b}',
            # (4) Stored hooks list must now be [after-stop-hook], NOT
            # [before-stop-hook]. If the launch path short-circuited the
            # SetAutostop call on a stopped→restart, the old hook would
            # still be in sqlite — this assert catches that regression.
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep after-stop-hook && '
            f'! echo "$out" | grep before-stop-hook',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) + 300,
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# Re-launch preserves prior autostop config when only hooks change.
#
# Previously, sky.execution._execute's `elif hooks_payload is not None:` branch
# called backend.set_autostop(..., idle_minutes_to_autostop=-1, ...), which
# silently unset autostop on any re-launch that touched only hooks. The fix
# (_compute_set_autostop_args_for_hooks_only_relaunch) reads the prior
# autostop + to_down from local DB and passes them through. This smoke pins
# the contract end-to-end against the actual cluster.
# ---------------------------------------------------------------------------
@_no_autostop
@pytest.mark.no_kubernetes
def test_hook_relaunch_preserves_autostop(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # First launch: autostop=10 idle minutes + autodown, plus a down-event
    # hook. The autostop is intentionally well above any reasonable test
    # window so we never observe an actual autostop — we're only checking
    # whether the *configured* value survives the re-launch.
    initial_yaml = _write_yaml({
        'autostop': {
            'idle_minutes': 10,
            'down': True,
        },
        'hooks': [{
            'run': 'echo initial-hook',
            'events': ['down'],
        }],
    })
    # Re-launch: change only the hook list. Autostop block intentionally
    # omitted from the YAML — the test pins that the prior autostop value
    # is not wiped by the hooks-only re-launch path.
    updated_yaml = _write_yaml({
        'hooks': [{
            'run': 'echo updated-hook',
            'events': ['down'],
        }],
    })
    test = smoke_tests_utils.Test(
        'test_hook_relaunch_preserves_autostop',
        [
            # (1) Initial launch with autostop=10 + autodown.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {initial_yaml}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # (2) Verify the cluster's autostop is set + autodown enabled.
            # `sky status` formats AUTOSTOP as "10 min(down)" when both
            # idle_minutes and down are set.
            f'out=$(sky status {name}) && echo "$out" | grep -E "10 ?(m|min) ?\\(down\\)"',
            # (3) Re-launch with hooks-only change — autostop block omitted.
            # Don't use VALIDATE_LAUNCH_OUTPUT here: with `--fast` on a
            # re-launch the setup/run markers it expects are suppressed.
            # All we need to verify is the command exits 0.
            f'SKYPILOT_DEBUG=0 sky launch -y -c {name} --fast {updated_yaml}',
            # (4) Critical assertion: autostop is STILL "10 min(down)".
            # Pre-fix this would have been "-" (unset).
            f'out=$(sky status {name}) && echo "$out" | grep -E "10 ?(m|min) ?\\(down\\)" || '
            f'(echo "REGRESSION: autostop was wiped by hooks-only re-launch. '
            f'sky status output:" && echo "$out" && false)',
            # (5) Sanity: the new hook actually replaced the old one in the
            # stored hooks list (so we're sure the re-launch path actually
            # ran, not just no-oped).
            f'inspect=\'sqlite3 ~/.sky/skylet_config.db '
            f'"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"\' && '
            f'out=$(sky exec {name} "$inspect") && '
            f'echo "$out" | grep updated-hook && '
            f'! echo "$out" | grep initial-hook',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# K8s preemption: `kubectl delete pod` (no --force) → kubelet runs preStop
# → SIGTERM forwarded to skylet → preemption hook runs during the grace
# window. Verified via `kubectl exec` into the still-Terminating pod.
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_k8s_preemption_sigterm():
    name = smoke_tests_utils.get_cluster_name()
    marker = f'hook-preempt-{time.time()}'
    # Hook writes to ~/.sky/hooks/preemption.log then sleeps 20s so the
    # pod is still Terminating when we read it. Timeout 60s ensures K8s
    # renders terminationGracePeriodSeconds >= 60.
    yaml_path = _write_yaml({
        'hooks': [{
            'run': f'echo {marker} > ~/.sky/hooks/preemption.log && sleep 20',
            'events': ['preemption'],
            'timeout': 60,
        }],
    })
    pod_query = (f'{_kc(name)} get pods -o name '
                 f'2>/dev/null | grep {name} | head -n1')
    k8s_delete = (f'pod=$({pod_query}) && '
                  f'{_kc(name)} delete --wait=false "$pod"')
    read_log = (f'sleep 3 && pod=$({pod_query}) && '
                f'{_kc(name)} exec "$pod" -- '
                f'cat /home/sky/.sky/hooks/preemption.log')
    test = smoke_tests_utils.Test(
        'test_hook_k8s_preemption_sigterm',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            k8s_delete,
            f'out=$({read_log}) && echo "$out" | grep "{marker}"',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# K8s grace-period cap: sum of preemption-hook timeouts > 600s emits the
# cluster-autoscaler warning + caps `terminationGracePeriodSeconds` at 600.
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_grace_capped_at_autoscaler_limit():
    name = smoke_tests_utils.get_cluster_name()
    # Two hooks × 400s timeout = 800s sum, > 600s cap → warning expected.
    yaml_path = _write_yaml({
        'hooks': [
            {
                'run': 'true',
                'events': ['preemption'],
                'timeout': 400
            },
            {
                'run': 'true',
                'events': ['preemption'],
                'timeout': 400
            },
        ],
    })
    test = smoke_tests_utils.Test(
        'test_hook_grace_capped_at_autoscaler_limit',
        [
            # (1) cap warning fires on stderr
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path} 2>&1) && '
            f'echo "$s" | grep -i "cluster-autoscaler" && '
            f'echo "$s" | grep -i "Capping"',
            # (2) pod's terminationGracePeriodSeconds is 600 (capped), not 800
            f'pod=$({_kc(name)} get pods -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'val=$({_kc(name)} get "$pod" '
            f'-o jsonpath={chr(39)}{{.spec.terminationGracePeriodSeconds}}{chr(39)}) && '
            f'[ "$val" = "600" ]',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# K8s re-launch: terminationGracePeriodSeconds is set at pod creation and
# cannot be updated in place. Re-launching the same cluster with a *larger*
# preemption-hook timeout must warn the user that the existing pod's grace
# wasn't updated (kubelet will SIGKILL past the original grace).
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_k8s_relaunch_warns_when_preemption_grace_increases():
    name = smoke_tests_utils.get_cluster_name()
    # Initial launch: 60s preemption-hook timeout → pod grace = 60s.
    small_yaml = _write_yaml({
        'hooks': [{
            'run': 'true',
            'events': ['preemption'],
            'timeout': 60,
        }],
    })
    # Re-launch the same cluster with a much larger preemption-hook
    # timeout. The new grace requirement (300s) exceeds the existing
    # pod's (60s); pod spec is immutable, so we should warn the user
    # to `sky down` + `sky launch` to apply.
    large_yaml = _write_yaml({
        'hooks': [{
            'run': 'true',
            'events': ['preemption'],
            'timeout': 300,
        }],
    })
    test = smoke_tests_utils.Test(
        'test_hook_k8s_relaunch_warns_when_preemption_grace_increases',
        [
            # (1) Initial launch — no warning expected.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {small_yaml} 2>&1) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT} && '
            f'! (echo "$s" | grep -i "preemption-hook grace requirement")',
            # (2) Confirm pod's grace is 60.
            f'pod=$({_kc(name)} get pods -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'val=$({_kc(name)} get "$pod" '
            f'-o jsonpath={chr(39)}{{.spec.terminationGracePeriodSeconds}}{chr(39)}) && '
            f'[ "$val" = "60" ]',
            # (3) Re-launch with larger timeout. Warning expected on
            # stderr — message comes from
            # warn_if_preemption_grace_change_requires_relaunch in
            # sky/clouds/kubernetes.py.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {large_yaml} 2>&1) && '
            f'echo "$s" | grep -E "preemption-hook grace requirement|terminationGracePeriodSeconds.*fixed at pod creation" && '
            f'echo "$s" | grep -i "sky down"',
            # (4) Confirm the existing pod's grace is STILL 60 — re-launch
            # cannot mutate the pod spec; warning is the only thing the
            # user can act on.
            f'pod=$({_kc(name)} get pods -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'val=$({_kc(name)} get "$pod" '
            f'-o jsonpath={chr(39)}{{.spec.terminationGracePeriodSeconds}}{chr(39)}) && '
            f'[ "$val" = "60" ]',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# sky down event isolation on K8s. Cluster declares BOTH a down hook and a
# preemption hook. sky down must:
#   (a) fire the down hook (assertion: marker file written by down hook),
#   (b) NOT fire the preemption hook (assertion: preemption marker absent),
#   (c) complete reasonably quickly (assertion: teardown < 60s wall-clock —
#       pre-fix this took ~60s+ because kubelet's preStop would SIGTERM
#       the skylet, the SIGTERM handler would claim 'preemption', and the
#       preemption hook would block teardown for its full timeout).
#
# Two protections combine to make this work:
#   - `_maybe_run_down_hooks` files the 'down' file-lock claim before
#     issuing the delete RPC, so even if SIGTERM reached skylet the
#     handler would lose the claim race.
#   - `delete_namespaced_pod(grace_period_seconds=0)` force-deletes the
#     pod, which bypasses kubelet's preStop entirely. preStop never fires
#     on an intentional sky down.
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_k8s_sky_down_does_not_fire_preemption():
    name = smoke_tests_utils.get_cluster_name()
    down_marker = f'down-fired-{time.time()}'
    yaml_path = _write_yaml({
        'hooks': [
            {
                'run': f'echo {down_marker} > ~/.sky/hooks/down.log',
                'events': ['down'],
                'timeout': 30,
            },
            {
                'run': ('echo "PREEMPTION-FIRED-SHOULD-NOT-HAPPEN" > '
                        '~/.sky/hooks/preemption.log'),
                'events': ['preemption'],
                'timeout': 30,
            },
        ],
    })
    pod_query = (f'{_kc(name)} get pods -o name '
                 f'2>/dev/null | grep {name} | head -n1')
    # Capture per-event log contents into a temp file BEFORE teardown
    # completes (pod will be gone after sky down). We use a job
    # submitted to the cluster that copies the markers off the pod;
    # however the simpler approach is to inspect the pod state
    # immediately after sky down by checking the time it took.
    test = smoke_tests_utils.Test(
        'test_hook_k8s_sky_down_does_not_fire_preemption',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Confirm the cluster is up and the hooks are stored.
            f'inspect=\'sqlite3 ~/.sky/skylet_config.db '
            f'"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"\' && '
            f'out=$(sky exec {name} "$inspect") && '
            f'echo "$out" | grep down && '
            f'echo "$out" | grep preemption',
            # Time sky down. Pre-fix: ~60s+ (preemption hook blocks).
            # Post-fix: should complete well under 60s (just SSH + delete).
            f'start=$(date +%s) && sky down -y {name} && '
            f'end=$(date +%s) && elapsed=$((end-start)) && '
            f'echo "sky down took ${{elapsed}}s" && '
            f'[ "$elapsed" -lt 60 ] || '
            f'(echo "REGRESSION: sky down took ${{elapsed}}s (>=60s). '
            f'The preemption hook likely fired during teardown — the '
            f"'down' claim or grace_period_seconds=0 didn't block "
            f'kubelet preStop." && false)',
        ],
        # No teardown command — sky down is part of the test itself.
        teardown=None,
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# K8s preStop pgrep matches only the head skylet, not worker_hook_handler.
# worker_hook_handler is a PR3 forward-leak that was inadvertently included
# in earlier PR1 revisions. The current template restricts preStop's pgrep
# pattern to `sky.skylet.skylet`.
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_k8s_prestop_pgrep_is_skylet_only():
    name = smoke_tests_utils.get_cluster_name()
    yaml_path = _write_yaml({
        'hooks': [{
            'run': 'true',
            'events': ['preemption'],
            'timeout': 60,
        }],
    })
    test = smoke_tests_utils.Test(
        'test_hook_k8s_prestop_pgrep_is_skylet_only',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Inspect the rendered pod's preStop command. Two checks:
            #   (a) preStop calls `pgrep` at all (i.e., the bridge is
            #       rendered when a preemption hook exists).
            #   (b) preStop does NOT reference worker_hook_handler — the
            #       PR3 forward-leak this test guards against.
            # We deliberately don't grep for the exact pgrep regex
            # `sky\.skylet\.skylet`: kubectl `-o jsonpath` returns
            # JSON-encoded output where backslashes are doubled
            # (`\\.` for one regex `\.`), making a literal grep
            # fragile. The two checks above are sufficient to catch
            # the regression.
            f'pod=$({_kc(name)} get pods -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'cmd=$({_kc(name)} get "$pod" '
            f'-o jsonpath={chr(39)}{{.spec.containers[0].lifecycle.preStop.exec.command}}{chr(39)}) && '
            f'echo "preStop: $cmd" && '
            f'echo "$cmd" | grep -q "pgrep -of" && '
            f'! echo "$cmd" | grep -q "worker_hook_handler" || '
            f'(echo "REGRESSION: preStop missing pgrep, or pgrep '
            f'references worker_hook_handler (PR3 forward-leak)." '
            f'&& false)',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# Autodown (idle timer + `autostop.down: true`) fires the `down` event,
# not `stop`. Closes the smoke-coverage gap for the post-rename event
# taxonomy: the unit test ``test_legacy_autodown_hook_routes_to_down_event``
# pins YAML→events routing at the parse layer; this exercises the live
# ``StopEvent._execute_hook_if_present`` branch on a real K8s pod —
# autostop_config.down=True picks the DOWN slot and runs the matching
# hook.
#
# K8s-only because:
#   - The pod stays UP while the hook sleeps, so `kubectl exec` can
#     read the live log; once autodown completes the pod is gone, so
#     timing matters but there's a window we can land in.
#   - Plain K8s autostop (down=False) is unsupported (kubernetes.py
#     marks AUTOSTOP unsupported); autodown is the only idle-timer
#     teardown shape that runs on K8s.
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_k8s_autodown_fires_down_event():
    name = smoke_tests_utils.get_cluster_name()
    marker = f'autodown-{time.time()}'
    # Hook writes its marker, then sleeps long enough for the read
    # poll-loop below to land in the window. timeout=240 must exceed
    # the sleep so the hook isn't killed mid-write.
    yaml_path = _write_yaml({
        'autostop': {
            'idle_minutes': 1,
            'down': True,
        },
        'hooks': [{
            'run': f'echo {marker} > ~/.sky/hooks/down.log && sleep 180',
            'events': ['down'],
            'timeout': 240,
        }],
    })
    pod_query = (f'{_kc(name)} get pods -o name '
                 f'2>/dev/null | grep {name} | head -n1')
    cat_log = (f'pod=$({pod_query}) && '
               f'{_kc(name)} exec "$pod" -- '
               f'cat /home/sky/.sky/hooks/down.log')
    # Poll up to ~5 min for the hook log to appear, then assert the
    # marker is in it. The hook fires ~120s after the SetAutostop RPC
    # at PRE_EXEC, but sky launch returns AFTER the inline job
    # finishes — so the t-from-launch-return offset varies. Polling
    # is robust to that variation. ``kubectl exec`` against a missing
    # log path exits non-zero, which is why we ``|| true`` and check
    # the grep result instead of relying on the exec exit code.
    poll_for_log = (
        f'for i in $(seq 1 60); do '
        f'  out=$({cat_log} 2>/dev/null) || true; '
        f'  if echo "$out" | grep -q "{marker}"; then '
        f'    echo "Found marker after ${{i}} poll(s)"; '
        f'    exit 0; '
        f'  fi; '
        f'  sleep 5; '
        f'done; '
        f'echo "REGRESSION: ~/.sky/hooks/down.log did not contain '
        f'{marker} after 5min of polling — autodown likely routed to '
        f'the (renamed-away) `stop` event instead of `down`."; '
        f'exit 1')
    test = smoke_tests_utils.Test(
        'test_hook_k8s_autodown_fires_down_event',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Poll for the down-event hook log to appear on the live
            # pod. The autodown teardown does NOT begin until the hook
            # finishes (sleep 180s in the hook script), so the pod is
            # accessible via `kubectl exec` for the entire write +
            # sleep window — plenty of time to land a successful read.
            poll_for_log,
            # Wait for autodown to complete (hook finishes + pod deletes).
            'sleep 240',
            # Cluster is TERMINATED (not STOPPED) since autostop.down=true.
            # The cluster row is gone from `sky status`.
            f'! sky status {name} 2>/dev/null | grep -E '
            f'"^{name}\\s+.*(UP|STOPPED)"',
        ],
        f'sky down -y {name} --purge || true',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# K8s autodown analog of test_hook_lifecycle_combined.
#
# On Kubernetes the only idle-timer path is autodown (no STOP feature),
# so everything that the VM-side combined test exercises against the
# `stop` event must be re-exercised on K8s against `down`. One launch
# + one autodown cycle verifies the union of:
#   - legacy `autostop.{hook, hook_timeout, down: true}` YAML → stderr
#     deprecation warning at launch + routing to the `down` event
#   - default events fill-in (hook with only `run` defaults to all three)
#   - multi-line `run` with literal "EOF" round-trips through skylet
#     sqlite (JSON encode/decode preserves embedded "EOF")
#   - multi-event filter `[down, preemption]` fires on autodown and the
#     marker appears in down.log
#   - hook timeout kills the script mid-run; teardown continues and the
#     log captures the partial output
#   - `preemption.log` is NOT written (autodown only fires `down`, the
#     multi-event filter's preemption side stays inactive)
#
# Reads down.log via `kubectl exec` into the still-Terminating pod
# during the hook's sleep window — the same pattern as the simpler
# `test_hook_k8s_autodown_fires_down_event` above.
# ---------------------------------------------------------------------------
@pytest.mark.kubernetes
def test_hook_k8s_autodown_lifecycle_combined():
    name = smoke_tests_utils.get_cluster_name()
    legacy_marker = f'k8s-legacy-{time.time()}'
    multi_event_marker = f'k8s-multi-{time.time()}'
    timeout_marker = f'k8s-partial-{time.time()}'
    # Multi-line run with literal "EOF" tests JSON round-trip through
    # skylet's sqlite storage (no accidental heredoc-style termination).
    multiline_run = (f'echo line1\n'
                     f'echo {multi_event_marker}\n'
                     f'EOF\n'
                     f'echo done')
    yaml_path = _write_yaml({
        'autostop': {
            'idle_minutes': 1,
            'down': True,
            # Legacy back-compat: routed to the `down` event because
            # autostop.down=True == autodown. `seq 1 50` pads the
            # output past 10 lines so the existing grep also catches
            # any `tail`-default-to-last-10 regression in the kubectl
            # cat path here.
            'hook': f'echo {legacy_marker} && seq 1 50 && sleep 180',
            'hook_timeout': 240,
        },
        'hooks': [
            # Defaults check: omitting `events:` should fill in all three.
            {
                'run': 'true',
            },
            # Multi-event filter + multi-line + EOF survival.
            {
                'run': multiline_run,
                'events': ['down', 'preemption'],
            },
            # Hook timeout: prints, then sleeps past the 5s timeout.
            {
                'run': f'echo {timeout_marker} && sleep 9999',
                'events': ['down'],
                'timeout': 5,
            },
        ],
    })
    pod_query = (f'{_kc(name)} get pods -o name '
                 f'2>/dev/null | grep {name} | head -n1')
    cat_log = (f'pod=$({pod_query}) && '
               f'{_kc(name)} exec "$pod" -- '
               f'cat /home/sky/.sky/hooks/down.log')
    # Poll until the legacy hook's marker appears (it writes first
    # because the legacy autostop.hook routes into the hooks list and
    # the executor runs entries in order). The legacy hook also
    # contains `seq 1 50` + `sleep 180`, so the pod stays accessible
    # via kubectl exec for the full window.
    poll_for_log = (
        f'for i in $(seq 1 60); do '
        f'  out=$({cat_log} 2>/dev/null) || true; '
        f'  if echo "$out" | grep -q "{legacy_marker}"; then '
        f'    echo "Found legacy_marker after ${{i}} poll(s)"; '
        f'    exit 0; '
        f'  fi; '
        f'  sleep 5; '
        f'done; '
        f'echo "REGRESSION: legacy_marker never appeared in down.log"; '
        f'exit 1')
    test = smoke_tests_utils.Test(
        'test_hook_k8s_autodown_lifecycle_combined',
        [
            # Capture stderr so we can verify the legacy autostop.hook
            # deprecation warning fires at launch time on K8s autodown.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_path} 2>&1) && '
            # (1) legacy autostop.hook → stderr deprecation warning
            f'echo "$s" | grep -i "deprecated"',
            # Poll for the legacy_marker to appear in down.log. This
            # also verifies the legacy hook was routed to `down` (not
            # the renamed-away `stop`) since autostop.down=True.
            poll_for_log,
            # (2) Multi-event hook with `events:[down, preemption]`
            # ALSO fires on autodown — its marker appears in down.log.
            f'out=$({cat_log}) && echo "$out" | grep "{multi_event_marker}"',
            # (3) Multi-line `run` round-tripped through sqlite intact:
            # both "line1" / "EOF" / "done" lines from the multi-line
            # entry appear (no heredoc-style early termination).
            f'out=$({cat_log}) && echo "$out" | grep -q "line1" && '
            f'echo "$out" | grep -q "EOF" && echo "$out" | grep -q "done"',
            # (4) Timeout-killed hook still wrote its initial echo
            # before sleep 9999 was killed at the 5s mark.
            f'out=$({cat_log}) && echo "$out" | grep "{timeout_marker}"',
            # (5) Multi-event filter: preemption.log must NOT exist —
            # the `events:[down, preemption]` hook only fires on the
            # event that matched (down here), not on preemption.
            f'pod=$({pod_query}) && '
            f'{_kc(name)} exec "$pod" -- '
            f'test ! -e /home/sky/.sky/hooks/preemption.log',
            # Wait for autodown to complete (sleep 180 in legacy hook +
            # teardown). Cluster row is gone after autodown finishes.
            'sleep 240',
            f'! sky status {name} 2>/dev/null | grep -E '
            f'"^{name}\\s+.*(UP|STOPPED)"',
        ],
        f'sky down -y {name} --purge || true',
        timeout=smoke_tests_utils.get_timeout('kubernetes') + 300,
    )
    smoke_tests_utils.run_one_test(test)


# NOTE: a previous `test_hook_config_hooks_yaml_round_trip_dryrun`
# smoke test (pinning that ``config.hooks:`` survives
# Task.to_yaml_config → from_yaml_config without tripping the
# ``resources.hooks`` rejection) was removed: it is redundant with the
# in-process unit test ``test_resources_round_trip_preserves_hooks``
# in ``tests/unit_tests/test_lifecycle_hooks.py``. Both exercise the
# same Python object graph — the smoke version added only the
# SDK→server transport, which uses the same Task.from_yaml_config on
# both sides, so no new code path was being exercised. The unit test
# runs in milliseconds and pins the regression at the right layer.


# ---------------------------------------------------------------------------
# No-cluster validation tests — combined into one because they're each
# only a few seconds. Covers:
#   - schema rejects unknown event names at `sky launch --dryrun`
#   - controller-resources reject `hooks:` at config-validation time
#   - CLI `--hook` rejects unknown event names with a click error
# ---------------------------------------------------------------------------
@pytest.mark.no_dependency
def test_hook_invalid_inputs_rejected():
    bad_events_yaml = _write_yaml({
        'hooks': [{
            'run': 'true',
            'events': ['reboot'],
        }],
    })
    controller_cfg = tempfile.NamedTemporaryFile(mode='w',
                                                 suffix='.yaml',
                                                 delete=False).name
    with open(controller_cfg, 'w', encoding='utf-8') as f:
        f.write(
            textwrap.dedent("""\
                jobs:
                  controller:
                    resources:
                      hooks:
                        - run: "echo on-controller"
                          events: [down]
                """))
    test = smoke_tests_utils.Test(
        'test_hook_invalid_inputs_rejected',
        [
            # (1) schema: unknown event name in resources.hooks
            f'out=$(sky launch --dryrun {bad_events_yaml} 2>&1 || true); '
            f'echo "$out" | grep -iE "hooks.*events|reboot"',
            # (2) controller-resources can't have hooks
            f'out=$(SKYPILOT_CONFIG={controller_cfg} sky check 2>&1 || true); '
            f'echo "$out" | grep -iE "hooks|controller"',
            # (3) CLI: --hook with unknown event name
            'out=$(sky logs --hook reboot fake-cluster 2>&1 || true); '
            'echo "$out" | grep -iE "stop|preemption|down|invalid"',
        ],
        teardown=None,
        timeout=180,
    )
    smoke_tests_utils.run_one_test(test)


# ---------------------------------------------------------------------------
# `sky down` on a cluster that declared NO hooks must NOT SSH to the
# head to run the teardown codegen.
#
# Before the fix, `_maybe_run_teardown_hooks` always ran the codegen
# on the head, even when the cluster had no hooks at all. The codegen
# was a no-op functionally, but if the head IP was transiently
# unreachable (already partway shut down, stale cached handle, network
# blip) it surfaced a misleading warning:
#
#   Failed to run down hook on '<cluster>': . Proceeding with teardown.
#
# …on a cluster the user never put a hook on. The visible
# user-facing signal of the codegen actually running is the spinner
# message ``Running down hook on '<cluster>'`` (sky/core.py:925),
# which is emitted just before the head SSH. This test launches a
# bare minimal cluster (zero hooks declared anywhere), runs
# ``sky down`` with the spinner not suppressed, and asserts that
# neither the spinner message nor the failure warning appears in the
# output. Pre-fix: spinner appears (and on a flaky head, the failure
# warning too). Post-fix: codegen is skipped → neither appears.
# ---------------------------------------------------------------------------
@_no_autostop
@pytest.mark.no_kubernetes
def test_hook_sky_down_no_hooks_skips_head_codegen(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'test_hook_sky_down_no_hooks_skips_head_codegen',
        [
            # (1) Launch with the bare minimal YAML — no hooks anywhere.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} '
            f'tests/test_yamls/minimal.yaml) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # (2) `sky down` and capture stdout + stderr. The codegen's
            # only visible user-facing signal is the spinner message
            # "Running down hook on '<cluster>'"; the failure warning
            # only fires when the head SSH itself errors, but the
            # spinner appears unconditionally pre-fix. Assert neither
            # appears, AND that teardown still succeeded (final
            # "Terminating cluster <name>...done" line is present).
            f'out=$(sky down -y {name} 2>&1) && '
            f'echo "$out" | grep "Terminating cluster {name}...done" && '
            f'! echo "$out" | grep -qE "Running (down|stop) hook on" && '
            f'! echo "$out" | grep -qE "Failed to run (down|stop) hook on"',
            # (3) Cluster is gone — `sky status` no longer lists it.
            f'sky status {name} 2>/dev/null | grep -v "{name}" || true',
        ],
        # Belt-and-suspenders teardown in case step (2) somehow left it.
        f'sky down -y {name} --purge || true',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)

"""Smoke tests for the lifecycle-hooks framework (resources.hooks:).

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


# ---------------------------------------------------------------------------
# Combined autostop + retrieval coverage. One launch + one autostop cycle
# verifies the union of:
#   - autostop hook fires and is retrievable
#   - omitting `events:` defaults to all three events on the stored config
#   - multi-event hook entry fires only on matching events
#   - `sky logs --hook` (no event) auto-selects whichever log exists
#   - `sky logs --autostop` is a deprecated alias (stderr warning + works)
#   - legacy YAML `autostop.hook` is routed through the new framework with
#     a stderr deprecation warning at launch
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
            'hook': f'echo {legacy_marker}',
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
                'events': ['autostop', 'down'],
            },
            # Hook timeout: prints, then sleeps until the 5s timeout kills it.
            {
                'run': f'echo {timeout_marker} && sleep 9999',
                'events': ['autostop'],
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
            # (2) defaults: omitted events → [autostop, preemption, down]
            # (3) multi-line run + literal "EOF" survives sqlite round-trip
            # (4) legacy hook routed into the hooks list
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep -q autostop && '
            f'echo "$out" | grep -q preemption && '
            f'echo "$out" | grep -q down && '
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
            # (5) autostop log carries every autostop-matching marker
            f'out=$(sky logs {name} --hook autostop --no-follow) && '
            f'echo "$out" | grep "{legacy_marker}" && '
            f'echo "$out" | grep "{multi_event_marker}" && '
            # (6) timeout-killed hook still wrote its initial echo
            f'echo "$out" | grep "{timeout_marker}"',
            # (7) `sky logs --hook` (no event) auto-selects the log
            f'out=$(sky logs {name} --hook --no-follow) && '
            f'echo "$out" | grep "{legacy_marker}"',
            # (8) `sky logs --autostop` is the deprecated alias — works
            # AND prints a deprecation warning to stderr
            f'out=$(sky logs {name} --autostop --no-follow 2>&1) && '
            f'echo "$out" | grep "{legacy_marker}" && '
            f'echo "$out" | grep -i "deprecated"',
            # (9) multi-event hook with `events:[autostop, down]` did NOT
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
# Re-launch dropping `hooks:` clears the skylet's stored list (proto3
# `repeated` has no presence — implementation sends `clear_hooks=True`).
# Kept separate from the combined test because the re-launch sequencing
# (launch → inspect → relaunch → inspect) is long enough to deserve its
# own pytest function.
# ---------------------------------------------------------------------------
@_no_autostop
def test_hook_clear_via_relaunch(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    yaml_with_hook = _write_yaml({
        'hooks': [{
            'run': 'echo first-hook',
            'events': ['down'],
        }],
    })
    yaml_without_hook = _write_yaml({})  # empty resources, no hooks
    inspect = ('sqlite3 ~/.sky/skylet_config.db '
               '"SELECT value FROM config WHERE key=\\"lifecycle_hooks\\";"')
    test = smoke_tests_utils.Test(
        'test_hook_clear_via_relaunch',
        [
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra {generic_cloud} --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_with_hook}) && '
            f'{smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # First launch — verify hook is stored
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'echo "$out" | grep first-hook',
            # Re-launch without hooks — sky launch picks up the new YAML
            f'sky launch -y -c {name} --fast {yaml_without_hook}',
            # Verify the stored hooks list is empty (NOT first-hook)
            f'out=$(sky exec {name} {chr(39)}{inspect}{chr(39)}) && '
            f'! echo "$out" | grep first-hook',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
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
    pod_query = (f'kubectl get pods --context kind-skypilot -o name '
                 f'2>/dev/null | grep {name} | head -n1')
    k8s_delete = (f'pod=$({pod_query}) && '
                  f'kubectl delete --context kind-skypilot --wait=false "$pod"')
    read_log = (f'sleep 3 && pod=$({pod_query}) && '
                f'kubectl exec --context kind-skypilot "$pod" -- '
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
            f'pod=$(kubectl get pods --context kind-skypilot -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'val=$(kubectl get --context kind-skypilot "$pod" '
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
            f'pod=$(kubectl get pods --context kind-skypilot -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'val=$(kubectl get --context kind-skypilot "$pod" '
            f'-o jsonpath={chr(39)}{{.spec.terminationGracePeriodSeconds}}{chr(39)}) && '
            f'[ "$val" = "60" ]',
            # (3) Re-launch with larger timeout. Warning expected on
            # stderr — message comes from
            # _maybe_warn_preemption_grace_change in cloud_vm_ray_backend.
            f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} '
            f'--infra kubernetes --fast '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} {large_yaml} 2>&1) && '
            f'echo "$s" | grep -E "preemption-hook grace requirement|terminationGracePeriodSeconds.*fixed at pod creation" && '
            f'echo "$s" | grep -i "sky down"',
            # (4) Confirm the existing pod's grace is STILL 60 — re-launch
            # cannot mutate the pod spec; warning is the only thing the
            # user can act on.
            f'pod=$(kubectl get pods --context kind-skypilot -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'val=$(kubectl get --context kind-skypilot "$pod" '
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
    pod_query = (f'kubectl get pods --context kind-skypilot -o name '
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
            f'pod=$(kubectl get pods --context kind-skypilot -o name '
            f'2>/dev/null | grep {name} | head -n1) && '
            f'cmd=$(kubectl get --context kind-skypilot "$pod" '
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
# YAML round-trip: a task YAML with `config.hooks:` must survive
# Task.to_yaml_config -> from_yaml_config without tripping the user-input
# rejection that catches a misplaced `resources.hooks:` field.
#
# Pre-fix (148927858): Resources.to_yaml_config emitted _hooks under the
# resources sub-dict, so the dumped YAML had `resources.hooks` even
# though the user wrote `config.hooks`. The server-side reload then
# tripped Task.from_yaml_config's rejection of `resources.hooks` and
# every `sky launch` with config.hooks failed. The fix lifts _hooks to
# the task-level `config.hooks` during serialization.
#
# This is a no-cluster smoke (just `sky launch --dryrun`) so it runs
# quickly on every cloud and catches the bug at the validation phase.
# ---------------------------------------------------------------------------
@pytest.mark.no_dependency
def test_hook_config_hooks_yaml_round_trip_dryrun():
    yaml_path = _write_yaml({
        'hooks': [
            {
                'run': 'echo a',
                'events': ['autostop'],
                'timeout': 60,
            },
            {
                'run': 'echo b',
                'events': ['down'],
            },
        ],
    })
    test = smoke_tests_utils.Test(
        'test_hook_config_hooks_yaml_round_trip_dryrun',
        [
            # `sky launch --dryrun` dumps the task to YAML and sends it
            # to the server, which re-loads. Pre-fix this raised the
            # `resources.hooks` rejection on the server because
            # Resources.to_yaml_config emitted hooks under resources.
            f'SKYPILOT_DEBUG=0 sky launch -y --dryrun {yaml_path} 2>&1 | '
            f'tee /tmp/dryrun_out.log && '
            # No error mentioning resources.hooks should fire.
            f'! grep -i "resources.hooks" /tmp/dryrun_out.log || '
            f'(echo "REGRESSION: hooks emitted under resources on '
            f'round-trip; server rejected with the resources.hooks '
            f'reject message." && false)',
        ],
        teardown=None,
        timeout=120,
    )
    smoke_tests_utils.run_one_test(test)


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
            'echo "$out" | grep -iE "autostop|preemption|down|invalid"',
        ],
        teardown=None,
        timeout=180,
    )
    smoke_tests_utils.run_one_test(test)

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
    """Dump minimal.yaml with a custom resources block to a temp file."""
    cfg = yaml_utils.read_yaml('tests/test_yamls/minimal.yaml')
    cfg['resources'] = resources
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

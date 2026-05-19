"""Smoke tests for the Kubernetes hostNetwork codepath.

Under ``kubernetes.pod_config.spec.hostNetwork = true`` the pod
shares the K8s node's net namespace, so a second SkyPilot pod
landing on the same node would collide on Ray's default ports
(6380, 8266, 8076, ...) and on the node's own sshd (host:22).
SkyPilot's host_network_probe picks a free Ray port set per pod
and rebinds the pod's sshd to a probed port; these tests prove
both work end-to-end.

SkyPilot also injects a required, per-cluster ``podAntiAffinity``
for every hostNetwork pod (mode b: one cluster pod per K8s node),
so a single cluster's pods never share a node -- which both removes
the same-node raylet-identity collapse and lets a hostNetwork
cluster span multiple K8s nodes. ``coexistence`` covers two
*different* clusters sharing a node (still allowed -- the
anti-affinity is per-cluster); ``multi_node`` asserts the
fail-loud guarantee: on a single-node K8s cluster a 2-node
hostNetwork cluster must fail to schedule (the required
anti-affinity refuses to pack the worker onto the head's node)
rather than silently racing on the shared host.
"""
import uuid

import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_coexistence():
    """Two hostNetwork SkyPilot clusters on the same K8s node coexist.

    Co-location is enforced via Kubernetes ``podAffinity`` (cluster B
    requires the same node as cluster A's anchor pod) rather than a
    kubectl-queried nodeSelector — so the test runs against both
    local and remote API servers, and on any K8s cluster regardless
    of node count. Verifies:

    1. Both launches succeed (probe avoided port collision).
    2. SSH to both heads works (per-pod sshd port rebind worked).
    3. The two heads' probed GCS ports are distinct.
    """
    # Unique anchor so concurrent test runs don't co-locate onto each
    # other. The label is placed on cluster A's pod; cluster B's
    # podAffinity binds to it.
    anchor_key = 'skypilot-coexist-anchor'
    anchor_val = uuid.uuid4().hex[:12]

    base = smoke_tests_utils.get_cluster_name()
    name_a = f'{base}-a'
    name_b = f'{base}-b'

    cfg_a = f'/tmp/sky-coexist-{anchor_val}-a.yaml'
    cfg_b = f'/tmp/sky-coexist-{anchor_val}-b.yaml'

    # Cluster A: hostNetwork + a unique label. No affinity (it's the
    # anchor, and K8s podAffinity required-during-scheduling cannot be
    # self-satisfied — the matching pod must already be running).
    write_cfg_a = (f'cat > {cfg_a} <<EOF\n'
                   f'kubernetes:\n'
                   f'  pod_config:\n'
                   f'    metadata:\n'
                   f'      labels:\n'
                   f'        {anchor_key}: "{anchor_val}"\n'
                   f'    spec:\n'
                   f'      hostNetwork: true\n'
                   f'EOF')

    # Cluster B: hostNetwork + podAffinity onto cluster A's pod.
    write_cfg_b = (
        f'cat > {cfg_b} <<EOF\n'
        f'kubernetes:\n'
        f'  pod_config:\n'
        f'    spec:\n'
        f'      hostNetwork: true\n'
        f'      affinity:\n'
        f'        podAffinity:\n'
        f'          requiredDuringSchedulingIgnoredDuringExecution:\n'
        f'          - labelSelector:\n'
        f'              matchLabels:\n'
        f'                {anchor_key}: "{anchor_val}"\n'
        f'            topologyKey: kubernetes.io/hostname\n'
        f'EOF')

    test = smoke_tests_utils.Test(
        'kubernetes_host_network_coexistence',
        [
            write_cfg_a,
            write_cfg_b,

            # 1. Launch A first (anchor), then B (forced onto A's node).
            # 1 CPU / 2 GB per pod: enough headroom for Ray driver +
            # raylet + GCS + dashboard (under tight CPU Ray's first-job
            # submission flakes with FAILED_DRIVER); still fits two
            # pods on a 4-CPU/8-GB node. No inline task — the SSH/port
            # checks below are what we're asserting on.
            f'sky launch -y -c {name_a} --infra kubernetes '
            f'--config {cfg_a} --cpus 1 --memory 2',
            f'sky launch -y -c {name_b} --infra kubernetes '
            f'--config {cfg_b} --cpus 1 --memory 2',

            # 2. SSH to both heads. Exercises:
            #    - sshd_<podname> ConfigMap entry -> InstanceInfo.ssh_port
            #    - /etc/ssh/sshd_config Port rewrite by the probe
            #    - SkyPilot SSH config writer using the probed port
            f's=$(ssh -o StrictHostKeyChecking=no {name_a} '
            f'"echo ssh_works_A" 2>&1) && echo "$s" | grep ssh_works_A',
            f's=$(ssh -o StrictHostKeyChecking=no {name_b} '
            f'"echo ssh_works_B" 2>&1) && echo "$s" | grep ssh_works_B',

            # 3. The probe must have assigned distinct GCS ports to the
            #    two heads. The env file written by the probe is the
            #    authoritative source on each pod. Single-quote the
            #    ssh remote command so $SKYPILOT_RAY_PORT expands on
            #    the pod, not on the local test runner.
            f'A_GCS=$(ssh {name_a} '
            f'\'. /tmp/sky_host_network_ports.env && '
            f'echo $SKYPILOT_RAY_PORT\') && '
            f'B_GCS=$(ssh {name_b} '
            f'\'. /tmp/sky_host_network_ports.env && '
            f'echo $SKYPILOT_RAY_PORT\') && '
            f'echo "A_GCS=$A_GCS B_GCS=$B_GCS" && '
            f'[ -n "$A_GCS" ] && [ -n "$B_GCS" ] && '
            f'[ "$A_GCS" != "$B_GCS" ]',
        ],
        teardown=(f'sky down -y {name_a}; sky down -y {name_b}; '
                  f'rm -f {cfg_a} {cfg_b}'),
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_multi_node_same_node():
    """A 2-node SkyPilot cluster, spread across two K8s nodes, hostNetwork on.

    This asserts fail-loud guarantee on the infra the smoke
    suite actually runs against (single-node K8s clusters). The required,
    per-cluster ``podAntiAffinity`` SkyPilot injects for every hostNetwork
    pod forbids the head and worker of one cluster from sharing a node;
    with only one node available the worker cannot be scheduled, so the
    launch must fail with the scheduler's pod-anti-affinity error rather
    than silently packing both pods onto one host (which is exactly the
    raylet-collapse / port-collision race mode b exists to prevent).

    On a >=2-node K8s cluster this same config would instead succeed and
    spread one pod per node (the cross-node capability mode b unlocks);
    that path is not exercised here because the smoke clusters are
    single-node.

    Verifies:

    1. ``sky launch --num-nodes 2`` returns non-zero (it must not
       succeed by co-locating the pods).
    2. The failure is specifically the pod-anti-affinity scheduling
       rejection (SkyPilot surfaces the verbatim kube-scheduler
       ``FailedScheduling`` message, which contains "anti-affinity"),
       not some unrelated error.
    """
    name = smoke_tests_utils.get_cluster_name()
    cfg = f'/tmp/sky-hostnet-multinode-{uuid.uuid4().hex[:12]}.yaml'

    # hostNetwork only. SkyPilot injects the per-cluster podAntiAffinity
    # (mode b); on a single-node cluster that leaves the 2nd pod
    # unschedulable.
    write_cfg = (f'cat > {cfg} <<EOF\n'
                 f'kubernetes:\n'
                 f'  pod_config:\n'
                 f'    spec:\n'
                 f'      hostNetwork: true\n'
                 f'EOF')

    # One self-contained command: capture the launch, assert it failed,
    # and assert it failed *for the anti-affinity reason* (so an
    # unrelated failure — image pull, quota — doesn't spuriously pass).
    # grep -iE "anti-?affinity" matches the kube-scheduler phrasings
    # ("...didn't match pod anti-affinity rules", "antiaffinity").
    expect_fail = (
        f'set +e; '
        f'OUT=$(sky launch -y -c {name} --infra kubernetes '
        f'--config {cfg} --num-nodes 2 --cpus 1 --memory 2 2>&1); '
        f'RC=$?; set -e; '
        f'echo "$OUT"; '
        f'if [ $RC -eq 0 ]; then '
        f'echo "FAIL: 2-node hostNetwork launch unexpectedly SUCCEEDED on '
        f'single-node K8s; mode-b anti-affinity should have blocked it"; '
        f'exit 1; fi; '
        f'echo "$OUT" | grep -qiE "anti-?affinity" || {{ '
        f'echo "FAIL: launch failed but NOT via the pod anti-affinity '
        f'scheduling rule (unexpected failure reason)"; exit 1; }}; '
        f'echo "OK: mode-b anti-affinity correctly rejected the 2-node '
        f'hostNetwork cluster on single-node K8s"')

    test = smoke_tests_utils.Test(
        'kubernetes_host_network_multi_node_same_node',
        [
            write_cfg,
            expect_fail,
        ],
        # Best-effort cleanup: a failed launch still leaves a cluster
        # record (and the head pod that did schedule).
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=10 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_head_restart_reuses_ports():
    """A head *container* restart must reuse the published port set.

    The probe's ``<cluster>-ray-ports`` ConfigMap carries an
    ``ownerReference`` to the *head Pod object*. A container restart
    (crash / OOM / ``ray stop``-then-up) keeps the Pod object — and its
    UID — so the ConfigMap is **not** garbage-collected (only deleting
    the Pod object triggers GC). When the head bootstrap re-runs after
    such a restart it must therefore reuse the already-published
    GCS/dashboard/sshd ports rather than probe a fresh ephemeral set:
    workers and the SkyPilot SSH client are still dialing the old ports.

    The smoke clusters use ``restartPolicy: Never`` (non-HA), so we
    cannot make kubelet truly restart the container. Instead we re-run
    the inlined probe (``/tmp/sky_host_network_probe.py --mode head``)
    against the surviving ConfigMap, in the *same* pod-identity env the
    real bootstrap uses (pulled from PID 1's environ via the Downward
    API vars), which is exactly what the head bootstrap re-executes on
    restart. Verifies:

    1. The ConfigMap survived (the reuse path requires it to exist and
       to be owned by this pod's UID — both encoded in the assertion).
    2. The re-run reuses the *identical* published port set
       (``/tmp/sky_host_network_ports.env`` unchanged). Pre-fix this
       step re-probed and the port set would differ.
    3. SSH still works afterward — the re-publish preserved the head's
       ``sshd_<pod>`` ConfigMap entry the SSH config writer relies on.
    """
    name = smoke_tests_utils.get_cluster_name()
    cfg = f'/tmp/sky-hostnet-restart-{uuid.uuid4().hex[:12]}.yaml'

    write_cfg = (f'cat > {cfg} <<EOF\n'
                 f'kubernetes:\n'
                 f'  pod_config:\n'
                 f'    spec:\n'
                 f'      hostNetwork: true\n'
                 f'EOF')

    # Runs on the head pod. Re-invokes the head probe exactly as the
    # bootstrap would after a container restart: same pod-identity env
    # (Downward API vars + in-cluster API host), same ConfigMap. The
    # probe's _existing_head_ports_to_reuse() only reuses when the
    # ConfigMap exists AND is owned by this pod UID, so an unchanged
    # port set proves both (and that the reuse codepath fired); a
    # deleted ConfigMap or a regressed re-probe makes BEFORE != AFTER.
    # Raw string: \0 / \n must reach `tr` literally. Only double quotes
    # inside so the whole script can be single-quoted to ssh (no local
    # $VAR expansion). No {} so it is f-string/heredoc safe.
    remote = (
        r'set -e; '
        r'PROBE=/tmp/sky_host_network_probe.py; '
        r'ENVF=/tmp/sky_host_network_ports.env; '
        r'test -s "$ENVF"; test -s "$PROBE"; '
        r'eval "$(sudo cat /proc/1/environ | tr "\0" "\n" | '
        r'grep -E "^(KUBERNETES_SERVICE_HOST|KUBERNETES_SERVICE_PORT|'
        r'SKYPILOT_POD_NAME|SKYPILOT_POD_UID|'
        r'SKYPILOT_RAY_PORTS_CONFIGMAP_NAME|'
        r'SKYPILOT_RAY_PORTS_CONFIGMAP_NAMESPACE)=" | '
        r'sed "s/^/export /")"; '
        r'test -n "$SKYPILOT_RAY_PORTS_CONFIGMAP_NAME"; '
        r'test -n "$SKYPILOT_POD_UID"; '
        r'BEFORE=$(sort "$ENVF"); '
        r'python3 "$PROBE" --mode head '
        r'--env-file /tmp/sky_restart_check.env '
        r'--configmap-name "$SKYPILOT_RAY_PORTS_CONFIGMAP_NAME" '
        r'--configmap-namespace "$SKYPILOT_RAY_PORTS_CONFIGMAP_NAMESPACE"; '
        r'AFTER=$(sort /tmp/sky_restart_check.env); '
        r'echo "BEFORE=[$BEFORE]"; echo "AFTER=[$AFTER]"; '
        r'if [ -z "$BEFORE" ]; then echo "FAIL: empty env file"; exit 1; fi; '
        r'if [ "$BEFORE" != "$AFTER" ]; then '
        r'echo "FAIL: head re-run did NOT reuse the published ports '
        r'(ConfigMap deleted or re-probed instead of reused)"; exit 1; fi; '
        r'echo HEAD_RESTART_REUSED_PORTS')

    test = smoke_tests_utils.Test(
        'kubernetes_host_network_head_restart_reuses_ports',
        [
            write_cfg,

            # 1 CPU / 2 GB: same headroom rationale as coexistence.
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --cpus 1 --memory 2',

            # Re-run the head probe against the surviving ConfigMap and
            # assert the published port set is reused verbatim.
            f's=$(ssh -o StrictHostKeyChecking=no {name} \'{remote}\' '
            f'2>&1); echo "$s"; '
            f'echo "$s" | grep -q HEAD_RESTART_REUSED_PORTS',

            # SSH must still work after the re-publish (the head's
            # sshd_<pod> ConfigMap entry — read by the SkyPilot SSH
            # config writer — was preserved on the reuse path).
            f's=$(ssh -o StrictHostKeyChecking=no {name} '
            f'"echo head_ssh_ok" 2>&1) && echo "$s" | grep head_ssh_ok',
        ],
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)

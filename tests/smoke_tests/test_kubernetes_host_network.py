"""Smoke tests for the Kubernetes hostNetwork codepath.

Under ``kubernetes.pod_config.spec.hostNetwork = true`` the pod
shares the K8s node's net namespace, so a second SkyPilot pod
landing on the same node would collide on Ray's default ports
(6380, 8266, 8076, ...) and on the node's own sshd (host:22).
The API server assigns each pod a contiguous block and declares it
as ``hostPort`` in the pod spec, so the scheduler refuses to
co-schedule two pods wanting the same port; the pod then binds each
one to prove it is free before Ray takes it, and rebinds its sshd to
the assigned port. These tests prove that works end-to-end.

The assignment moved server-side so that a workload pod needs no
Kubernetes API access: it used to pick ports itself and publish them
to a ConfigMap, which required ``configmaps: create/update`` on the
pod's service account.

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

import sky
from sky.client import sdk as sky_sdk

_kc = smoke_tests_utils.kubectl_for_cluster


def _schedulable_nodes() -> int:
    """Ready, uncordoned K8s nodes; 1 when it cannot be read.

    A hostNetwork cluster places each of its pods on a different node, so
    this decides whether a multi-node case can run at all. 1 on error fails
    closed: callers then take the single-node branch or skip.
    """
    try:
        nodes_info = sky.get(sky_sdk.kubernetes_node_info())
        return sum(1 for n in nodes_info.node_info_dict.values()
                   if n.is_ready and not n.is_cordoned)
    except Exception:  # pylint: disable=broad-except
        return 1


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
            #    - the pod spec's named `ssh` port -> InstanceInfo.ssh_port
            #    - /etc/ssh/sshd_config Port rewrite by the probe
            #    - SkyPilot SSH config writer using the assigned port
            f's=$(ssh -o StrictHostKeyChecking=no {name_a} '
            f'"echo ssh_works_A" 2>&1) && echo "$s" | grep ssh_works_A',
            f's=$(ssh -o StrictHostKeyChecking=no {name_b} '
            f'"echo ssh_works_B" 2>&1) && echo "$s" | grep ssh_works_B',

            # 3. The two heads must hold distinct GCS ports. Read from the
            #    pod spec, where the server wrote them and what the pod is
            #    started with. Not over ssh: a container's env is not
            #    passed into the login session sshd starts, so `echo $VAR`
            #    there is empty.
            _RESOLVE_PODC.format(name=name_a) +
            f' && A_GCS=$({_head_env("SKYPILOT_RAY_PORT")}) && ' +
            _RESOLVE_PODC.format(name=name_b) +
            f' && B_GCS=$({_head_env("SKYPILOT_RAY_PORT")}) && '
            'echo "A_GCS=$A_GCS B_GCS=$B_GCS" && '
            '[ -n "$A_GCS" ] && [ -n "$B_GCS" ] && '
            '[ "$A_GCS" != "$B_GCS" ]',
        ],
        teardown=(f'sky down -y {name_a}; sky down -y {name_b}; '
                  f'rm -f {cfg_a} {cfg_b}'),
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_multi_node_same_node():
    """A 2-node hostNetwork SkyPilot cluster exercises mode-b's
    per-cluster ``podAntiAffinity`` in *both* documented regimes —
    the branch is picked at runtime from the API server's view of
    the target K8s cluster.

    Single-node K8s
        Anti-affinity makes the worker unschedulable, so
        ``sky launch --num-nodes 2`` must fail with the scheduler's
        actual rejection (verbatim ``didn't match pod anti-affinity
        rules`` from kube-scheduler). This is the fail-loud guarantee
        that protects against same-node port collisions / raylet
        identity collapse on Kind-style smoke pipelines.

    Multi-node K8s
        Anti-affinity is satisfied by spreading pods across nodes, so
        the same launch must succeed *and* the head + worker pods must
        land on distinct K8s nodes (mode-b's cross-node happy path).
        This exercises the capability mode b actually unlocks — a
        regime the test previously did not cover.

    The single-node grep is strict (``didn't match.*anti-affinity``)
    rather than the bare substring ``anti-affinity``: the looser form
    also matched SkyPilot's debug dump of the generated pod spec
    (``"podAntiAffinity":``), so a transient connection error
    incidentally containing that field could pass the test for the
    wrong reason — observed historically on the shared-API-server
    pipeline.
    """
    # Resolve which regime to exercise. Failing closed (treat as
    # single-node) is the right default: that assertion is strictly
    # stricter, so a misclassified multi-node pipeline still fails
    # loudly rather than silently giving up coverage.
    multi_node = _schedulable_nodes() > 1

    name = smoke_tests_utils.get_cluster_name()
    cfg = f'/tmp/sky-hostnet-multinode-{uuid.uuid4().hex[:12]}.yaml'

    # hostNetwork only. SkyPilot injects the per-cluster podAntiAffinity
    # (mode b).
    write_cfg = (f'cat > {cfg} <<EOF\n'
                 f'kubernetes:\n'
                 f'  pod_config:\n'
                 f'    spec:\n'
                 f'      hostNetwork: true\n'
                 f'EOF')

    launch = (f'sky launch -y -c {name} --infra kubernetes '
              f'--config {cfg} --num-nodes 2 --cpus 1 --memory 2')

    if not multi_node:
        assertion = (
            f'set +e; OUT=$({launch} 2>&1); RC=$?; set -e; '
            f'echo "$OUT"; '
            f'if [ $RC -eq 0 ]; then '
            f'echo "FAIL: 2-node hostNetwork launch unexpectedly SUCCEEDED '
            f'on single-node K8s; mode-b anti-affinity should have blocked '
            f'it"; exit 1; fi; '
            f'echo "$OUT" | grep -qiE "didn.t match.*anti-?affinity" || {{ '
            f'echo "FAIL: launch failed but NOT via the pod anti-affinity '
            f'scheduling rule (unexpected failure reason)"; exit 1; }}; '
            f'echo "OK: mode-b anti-affinity correctly rejected the 2-node '
            f'hostNetwork cluster on single-node K8s"')
    else:
        # Read {.spec.nodeName} for every pod whose name contains the
        # cluster name; sort -u | wc -l gives the unique-node count.
        node_count = (f'{_kc(name)} get pods -o name 2>/dev/null '
                      f'| grep {name} | while read p; do '
                      f'{_kc(name)} get $p '
                      f'-o jsonpath=\'{{.spec.nodeName}}{{"\\n"}}\'; '
                      f'done | sort -u | grep -c .')
        assertion = (
            f'{launch} || {{ '
            f'echo "FAIL: 2-node hostNetwork launch FAILED on multi-node '
            f'K8s; mode-b should let pods spread across nodes"; exit 1; }}; '
            f'NODES=$({node_count}); '
            f'if [ "$NODES" -lt 2 ]; then '
            f'echo "FAIL: head+worker landed on the same K8s node '
            f'($NODES distinct nodes); mode-b anti-affinity should have '
            f'spread them"; exit 1; fi; '
            f'echo "OK: 2-node hostNetwork cluster spread across $NODES '
            f'distinct K8s nodes (mode-b cross-node happy path)"')

    test = smoke_tests_utils.Test(
        'kubernetes_host_network_multi_node_same_node',
        [
            write_cfg,
            assertion,
        ],
        # Best-effort cleanup: a failed launch still leaves a cluster
        # record (and the head pod that did schedule); a successful
        # multi-node launch leaves both pods.
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=10 * 60,
    )
    smoke_tests_utils.run_one_test(test)


def _hostnet_cfg(tag: str) -> tuple:
    """A hostNetwork config file and the command that writes it."""
    cfg = f'/tmp/sky-hostnet-{tag}-{uuid.uuid4().hex[:12]}.yaml'
    return cfg, (f'cat > {cfg} <<EOF\n'
                 f'kubernetes:\n'
                 f'  pod_config:\n'
                 f'    spec:\n'
                 f'      hostNetwork: true\n'
                 f'EOF')


# The on-cloud cluster name, which is what the pod label carries.
_RESOLVE_PODC = ('PODC=$(kubectl get pods -o jsonpath=\'{{range .items[*]}}'
                 '{{.metadata.labels.skypilot-cluster-name}}{{"\\n"}}{{end}}\' '
                 '| grep -m1 "^{name}")')

_HEAD_PORTS = ('kubectl get pod -l skypilot-cluster-name=$PODC '
               '-o jsonpath=\'{.items[0].spec.containers[?(@.name=="ray-node")]'
               '.ports[*].hostPort}\' | tr " " "\\n" | sort -n | tr "\\n" " "')


def _head_env(var: str) -> str:
    """A command printing the ray-node container's env var from the spec."""
    return ('kubectl get pod -l skypilot-cluster-name=$PODC -o jsonpath=\''
            '{.items[0].spec.containers[?(@.name=="ray-node")]'
            f'.env[?(@.name=="{var}")].value}}\'')


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_block_shape():
    """The assigned block reaches the pod spec in the shape everything
    downstream assumes.

    Contiguous, and ``hostPort == containerPort`` on every entry: the
    first is how a pod's ports are reconstructed (block start plus each
    name's index), the second is what makes the scheduler account for
    them at all. A block that is neither still runs -- until a second
    cluster lands on the node, or the SSH client reconstructs a port
    nothing is listening on.
    """
    name = smoke_tests_utils.get_cluster_name()
    cfg, write_cfg = _hostnet_cfg('shape')
    test = smoke_tests_utils.Test(
        'kubernetes_host_network_block_shape',
        [
            write_cfg,
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --cpus 1 --memory 2',
            _RESOLVE_PODC.format(name=name) + ' && ' +
            f'PORTS=$({_HEAD_PORTS}) && echo "ports=$PORTS" && '
            'python3 -c "'
            'import sys; p=[int(x) for x in sys.argv[1].split()]; '
            'assert p, \'no hostPorts declared\'; '
            'assert p==list(range(p[0],p[0]+len(p))), (\'not contiguous\',p); '
            'print(\'contiguous\', p[0], p[-1])" "$PORTS"',
            # hostPort must equal containerPort on every entry. Under
            # hostNetwork the API server defaults one to the other, so a
            # spec that relied on the default would pass a weaker check.
            _RESOLVE_PODC.format(name=name) + ' && ' +
            'kubectl get pod -l skypilot-cluster-name=$PODC -o jsonpath='
            '\'{range .items[0].spec.containers[?(@.name=="ray-node")]'
            '.ports[*]}{.hostPort}:{.containerPort}{" "}{end}\' '
            '| tr " " "\\n" | grep -v "^$" | '
            'awk -F: \'$1!=$2 {print "MISMATCH",$0; bad=1} '
            'END {exit bad+0}\'',
            # The head's GCS env is the first port of its declared block.
            # This pair is what a restarted container reads its ports from, so
            # it is what keeps them stable across a container restart.
            _RESOLVE_PODC.format(name=name) + ' && ' +
            f'GCS=$({_head_env("SKYPILOT_RAY_PORT")}) && '
            f'FIRST=$({_HEAD_PORTS} | cut -d" " -f1) && '
            'echo "gcs_env=$GCS first_port=$FIRST" && '
            '[ -n "$GCS" ] && [ "$GCS" = "$FIRST" ]',
        ],
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_worker_recovery_keeps_head_ports():
    """A worker lost to node failure is recreated without moving the head.

    The head's ports are read back off the live head pod, not re-drawn:
    re-drawing would hand the new worker a GCS port the head is not
    listening on, and it would never join. This is the path that found a
    real defect -- the in-pod probe ran outside the guard that skips Ray
    start when Ray is already up, so a retry asserted against its own
    raylet and the launch reported failure on a cluster that was fine.
    """
    if _schedulable_nodes() < 2:
        pytest.skip('needs two schedulable nodes: a hostNetwork cluster puts '
                    'each pod on its own node')
    name = smoke_tests_utils.get_cluster_name()
    cfg, write_cfg = _hostnet_cfg('recover')
    test = smoke_tests_utils.Test(
        'kubernetes_host_network_worker_recovery_keeps_head_ports',
        [
            write_cfg,
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --num-nodes 2 --cpus 1 --memory 2',
            _RESOLVE_PODC.format(name=name) + ' && ' +
            f'BEFORE=$({_HEAD_PORTS}) && echo "head_before=$BEFORE" && '
            # Each step is its own shell; keep BEFORE for the later step.
            f'[ -n "$BEFORE" ] && echo "$BEFORE" > {cfg}.before && '
            # Lose the worker the way a node failure would: delete the pod,
            # not the cluster. `sky down` would take the record with it and
            # make this a fresh launch instead of a recovery.
            'W=$(kubectl get pod -l skypilot-cluster-name=$PODC '
            '-o name | grep -- "-worker" | head -1) && '
            'kubectl delete $W --wait=true',
            # Same node count: this is recovery, not scaling. Scaling an
            # existing cluster is refused before provisioning.
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --num-nodes 2 --cpus 1 --memory 2',
            _RESOLVE_PODC.format(name=name) + ' && ' +
            f'AFTER=$({_HEAD_PORTS}) && echo "head_after=$AFTER" && '
            f'[ -n "$AFTER" ] && [ "$(cat {cfg}.before)" = "$AFTER" ]',
            # The rebuilt worker actually joined.
            f'sky exec {name} --num-nodes 2 "echo recovered_ok" && '
            f'sky logs {name} --status',
        ],
        teardown=f'sky down -y {name}; rm -f {cfg} {cfg}.before',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_relaunch_after_all_pods_gone():
    """With no pod left to read, the cluster gets a fresh block.

    The failure this guards against is a wedge: a block resolved from
    something stale rather than from a live pod would be handed out again
    and again, and a cluster whose ports are taken could never come back.
    """
    name = smoke_tests_utils.get_cluster_name()
    cfg, write_cfg = _hostnet_cfg('wedge')
    test = smoke_tests_utils.Test(
        'kubernetes_host_network_relaunch_after_all_pods_gone',
        [
            write_cfg,
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --cpus 1 --memory 2',
            _RESOLVE_PODC.format(name=name) + ' && ' +
            f'BEFORE=$({_HEAD_PORTS}) && echo "before=$BEFORE" && '
            'kubectl delete pod -l skypilot-cluster-name=$PODC '
            '--wait=true',
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --cpus 1 --memory 2',
            _RESOLVE_PODC.format(name=name) + ' && ' +
            f'AFTER=$({_HEAD_PORTS}) && echo "after=$AFTER" && '
            '[ -n "$AFTER" ]',
            f'sky exec {name} "echo relaunch_ok" && sky logs {name} --status',
        ],
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_ssh_port_needs_no_configmap():
    """The sshd port is discoverable from the pod alone.

    This is the reason the assignment moved server-side, and nothing was
    guarding it: a client that can read pods and nothing else must still
    find the port. Both halves are asserted, because either alone passes
    for the wrong reason -- reading the pod proves nothing if no ConfigMap
    would have been consulted anyway, and "no ConfigMap exists" proves
    nothing if the port were coming from somewhere else again.
    """
    name = smoke_tests_utils.get_cluster_name()
    cfg, write_cfg = _hostnet_cfg('nocm')
    test = smoke_tests_utils.Test(
        'kubernetes_host_network_ssh_port_needs_no_configmap',
        [
            write_cfg,
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --cpus 1 --memory 2',
            # The port the SSH proxy command selects, by name, from the pod.
            _RESOLVE_PODC.format(name=name) + ' && ' +
            'P=$(kubectl get pod -l skypilot-cluster-name=$PODC -o jsonpath='
            '\'{.items[0].spec.containers[?(@.name=="ray-node")]'
            '.ports[?(@.name=="ssh")].containerPort}\') && '
            'echo "ssh_port=$P" && [ -n "$P" ] && [ "$P" != 22 ]',
            # And no ray-ports ConfigMap is left to read.
            _RESOLVE_PODC.format(name=name) + ' && ' +
            'N=$(kubectl get configmap -o name | grep -c -- '
            '"$PODC-ray-ports" || true) && echo "configmaps=$N" && '
            '[ "$N" = 0 ]',
            f's=$(ssh -o StrictHostKeyChecking=no {name} '
            f'"echo ssh_no_cm_ok" 2>&1) && echo "$s" | grep ssh_no_cm_ok',
        ],
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
    )
    smoke_tests_utils.run_one_test(test)

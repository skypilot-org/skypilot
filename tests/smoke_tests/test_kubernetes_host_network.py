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
anti-affinity is per-cluster); ``multi_node`` covers one cluster
spread across nodes.
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

    With mode b (the required, per-cluster ``podAntiAffinity`` SkyPilot
    injects for every hostNetwork pod) the head and worker of one
    cluster *cannot* land on the same K8s node, so this exercises
    cross-K8s-node hostNetwork end to end with no anchor / podAffinity
    needed — the injected rule does the spreading.

    Requires the test K8s cluster to have >=2 schedulable nodes (that
    is the scenario under test; on a single-node cluster the required
    anti-affinity correctly leaves the worker Pending and the launch
    fails loudly rather than silently racing on the shared host).

    Verifies:

    1. The 2-node launch succeeds — under the required per-cluster
       anti-affinity, success means the two pods are on *different* K8s
       nodes (a same-node placement would leave the worker Pending and
       fail this step).
    2. Ray works across nodes: ``sky exec`` runs on the cluster, which
       implies the worker (on a different K8s node) joined the head's
       Ray cluster over the head's routable host IP + probed GCS port.
    3. SSH to the head works (per-pod sshd port rebind + SkyPilot SSH
       config writer using the probed port).
    4. SSH to the worker works — its separately probed sshd port, on a
       different K8s node from the head.
    """
    name = smoke_tests_utils.get_cluster_name()
    cfg = f'/tmp/sky-hostnet-multinode-{uuid.uuid4().hex[:12]}.yaml'

    # hostNetwork only — no podAffinity / anchor. SkyPilot's injected
    # per-cluster podAntiAffinity (mode b) spreads the head and worker
    # onto separate K8s nodes by itself.
    write_cfg = (f'cat > {cfg} <<EOF\n'
                 f'kubernetes:\n'
                 f'  pod_config:\n'
                 f'    spec:\n'
                 f'      hostNetwork: true\n'
                 f'EOF')

    test = smoke_tests_utils.Test(
        'kubernetes_host_network_multi_node_same_node',
        [
            write_cfg,

            # 1. 2-node launch. Under the required per-cluster
            # anti-affinity a successful launch *is* the proof the two
            # pods are on different K8s nodes (a same-node placement
            # would leave the worker Pending and fail this step). 1 CPU
            # / 2 GB per pod keeps Ray's first job submission off the
            # FAILED_DRIVER edge.
            f'sky launch -y -c {name} --infra kubernetes '
            f'--config {cfg} --num-nodes 2 --cpus 1 --memory 2',

            # 2. Ray must work across the two nodes. `sky exec` runs the
            # task through Ray on the head, which means the worker (on a
            # different K8s node) joined the head over its routable host
            # IP + probed GCS port.
            f'sky exec {name} -- echo "job_ok"',
            f'sky logs {name} 1 --status',

            # 3. SSH to head — per-pod sshd port rebind + SkyPilot SSH
            # config using the probed port.
            f's=$(ssh -o StrictHostKeyChecking=no {name} '
            f'"echo ssh_head_ok" 2>&1) && echo "$s" | grep ssh_head_ok',

            # 4. SSH to the worker — its separately probed sshd port, on
            # a different K8s node from the head.
            f's=$(ssh -o StrictHostKeyChecking=no {name}-worker1 '
            f'"echo ssh_worker_ok" 2>&1) && echo "$s" | grep ssh_worker_ok',
        ],
        teardown=f'sky down -y {name}; rm -f {cfg}',
        timeout=5 * 60,
    )
    smoke_tests_utils.run_one_test(test)

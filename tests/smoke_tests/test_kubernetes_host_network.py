"""Smoke tests for the Kubernetes hostNetwork codepath.

Under ``kubernetes.pod_config.spec.hostNetwork = true`` the pod
shares the K8s node's net namespace, so a second SkyPilot pod
landing on the same node would collide on Ray's default ports
(6380, 8266, 8076, ...) and on the node's own sshd (host:22).
SkyPilot's host_network_probe picks a free Ray port set per pod
and rebinds the pod's sshd to a probed port; these tests prove
both work end-to-end.
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
def test_kubernetes_host_network_multi_node():
    """A 2-node SkyPilot cluster, both pods on one K8s node, hostNetwork on.

    Head and worker pods are created in parallel by SkyPilot's K8s
    provisioner, so a self-referential podAffinity would deadlock
    (neither pod can satisfy the other's required-during-scheduling
    constraint until one is running). To force co-location anyway, an
    anchor SkyPilot cluster is launched first with a unique label;
    the 2-node target cluster's pod_config then sets podAffinity onto
    that label, which both pods can independently satisfy.

    Verifies:

    1. Both target pods land on the anchor's K8s node (otherwise the
       second pod would Pending forever, failing the launch step).
    2. Ray cluster works: ``sky exec`` runs on the cluster (which
       implies head + worker have joined the Ray cluster — the worker
       reading the head's probed GCS port from the ConfigMap).
    3. SSH to the head works.
    4. SSH to the worker works — exercises the per-pod sshd port
       rebind on the worker pod and the SkyPilot SSH config writer's
       use of ``pod_sshd_ports[worker_pod_name]``.
    """
    anchor_key = 'skypilot-multinode-anchor'
    anchor_val = uuid.uuid4().hex[:12]

    base = smoke_tests_utils.get_cluster_name()
    name_anchor = f'{base}-anchor'
    name_multi = f'{base}-multi'

    cfg_anchor = f'/tmp/sky-multinode-{anchor_val}-anchor.yaml'
    cfg_multi = f'/tmp/sky-multinode-{anchor_val}-multi.yaml'

    # Anchor: hostNetwork + unique label, no affinity.
    write_cfg_anchor = (f'cat > {cfg_anchor} <<EOF\n'
                        f'kubernetes:\n'
                        f'  pod_config:\n'
                        f'    metadata:\n'
                        f'      labels:\n'
                        f'        {anchor_key}: "{anchor_val}"\n'
                        f'    spec:\n'
                        f'      hostNetwork: true\n'
                        f'EOF')

    # 2-node target: hostNetwork + podAffinity onto anchor's node.
    # Both head and worker pods inherit this pod_config and so each
    # can independently match against the anchor pod.
    write_cfg_multi = (
        f'cat > {cfg_multi} <<EOF\n'
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
        'kubernetes_host_network_multi_node',
        [
            write_cfg_anchor,
            write_cfg_multi,

            # 1. Launch anchor (1 pod), then the 2-node target. If the
            # target launch returns success, both pods scheduled — i.e.
            # both landed on the anchor's node. 1 CPU / 2 GB per pod
            # leaves Ray driver enough headroom (under tighter CPU
            # the first job submission flakes with FAILED_DRIVER) and
            # still fits 3 pods on a 4-CPU/8-GB node.
            f'sky launch -y -c {name_anchor} --infra kubernetes '
            f'--config {cfg_anchor} --cpus 1 --memory 2',
            f'sky launch -y -c {name_multi} --infra kubernetes '
            f'--config {cfg_multi} --num-nodes 2 --cpus 1 --memory 2',

            # 2. Ray cluster must work end-to-end. `sky exec` runs the
            # task through Ray on the head, which means the head <->
            # worker join happened (worker read head's probed GCS port
            # from the ConfigMap).
            f'sky exec {name_multi} -- echo "job_ok"',
            f'sky logs {name_multi} 1 --status',

            # 3. SSH to head — exercises per-pod sshd port rebind on
            # the head + SkyPilot SSH config using the probed port.
            f's=$(ssh -o StrictHostKeyChecking=no {name_multi} '
            f'"echo ssh_head_ok" 2>&1) && echo "$s" | grep ssh_head_ok',

            # 4. SSH to worker — same path as the head but uses the
            # worker pod's separately probed sshd port (read from the
            # ConfigMap's sshd_<podname> entry into InstanceInfo.ssh_port).
            f's=$(ssh -o StrictHostKeyChecking=no {name_multi}-worker1 '
            f'"echo ssh_worker_ok" 2>&1) && echo "$s" | grep ssh_worker_ok',
        ],
        teardown=(f'sky down -y {name_anchor}; sky down -y {name_multi}; '
                  f'rm -f {cfg_anchor} {cfg_multi}'),
        timeout=5 * 60,
    )
    smoke_tests_utils.run_one_test(test)

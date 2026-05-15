"""Smoke tests for the Kubernetes hostNetwork codepath.

Under ``kubernetes.pod_config.spec.hostNetwork = true`` the pod shares the K8s
node's net namespace, so a second SkyPilot pod landing on the same node would
collide on Ray's default ports (6380, 8266, 8076, ...) and on the node's own
sshd (host:22). SkyPilot derives a deterministic, collision-resistant port set
and per-pod loopback IP from the cluster name + rank (no startup probe, no
ConfigMap) and rebinds each pod's sshd to a deterministic port. These tests
prove both work end-to-end.

Co-location is forced via Kubernetes ``podAffinity`` onto a pre-launched
anchor pod (rather than a kubectl-queried nodeSelector) so the tests run
against both local and remote API servers and on any K8s cluster regardless
of node count. A self-referential podAffinity would deadlock SkyPilot's
parallel head+worker creation, so an anchor cluster carries the unique label.
"""
import uuid

import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.kubernetes
@pytest.mark.no_dependency
def test_kubernetes_host_network_coexistence():
    """Two hostNetwork SkyPilot clusters on the same K8s node coexist.

    Both launches succeeding is itself the proof that the deterministic
    per-cluster port windows didn't collide: a collision would leave the
    second cluster's Ray daemons unable to bind, failing its launch step.
    SSH to both heads additionally exercises the per-pod sshd rebind + the
    port-forward proxy ``-p`` routing.
    """
    anchor_key = 'skypilot-coexist-anchor'
    anchor_val = uuid.uuid4().hex[:12]

    base = smoke_tests_utils.get_cluster_name()
    name_a = f'{base}-a'
    name_b = f'{base}-b'

    cfg_a = f'/tmp/sky-coexist-{anchor_val}-a.yaml'
    cfg_b = f'/tmp/sky-coexist-{anchor_val}-b.yaml'

    # Cluster A is the anchor (a podAffinity required-during-scheduling rule
    # cannot be self-satisfied — the matching pod must already be running).
    write_cfg_a = (f'cat > {cfg_a} <<EOF\n'
                   f'kubernetes:\n'
                   f'  pod_config:\n'
                   f'    metadata:\n'
                   f'      labels:\n'
                   f'        {anchor_key}: "{anchor_val}"\n'
                   f'    spec:\n'
                   f'      hostNetwork: true\n'
                   f'EOF')

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

            # 1. Launch A (anchor), then B (forced onto A's node). 1 CPU /
            # 2 GB per pod gives Ray's driver headroom (under tighter CPU the
            # first job submission flakes with FAILED_DRIVER) and still fits
            # two pods on a 4-CPU / 8-GB node.
            f'sky launch -y -c {name_a} --infra kubernetes '
            f'--config {cfg_a} --cpus 1 --memory 2',
            f'sky launch -y -c {name_b} --infra kubernetes '
            f'--config {cfg_b} --cpus 1 --memory 2',

            # 2. SSH to both heads. Exercises the deterministic sshd port
            # rebind + the port-forward proxy `-p` routing.
            f's=$(ssh -o StrictHostKeyChecking=no {name_a} '
            f'"echo ssh_works_A" 2>&1) && echo "$s" | grep ssh_works_A',
            f's=$(ssh -o StrictHostKeyChecking=no {name_b} '
            f'"echo ssh_works_B" 2>&1) && echo "$s" | grep ssh_works_B',

            # 3. Ray on both heads is healthy (ports didn't collide).
            f'sky exec {name_a} -- echo ray_ok_a',
            f'sky logs {name_a} 1 --status',
            f'sky exec {name_b} -- echo ray_ok_b',
            f'sky logs {name_b} 1 --status',
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

    Verifies:
    1. Both target pods land on the anchor's K8s node (otherwise the second
       pod Pending forever, failing the launch step).
    2. Ray works: ``sky exec`` runs on the cluster — implying head + worker
       joined Ray, with the worker dialing the head's *deterministic* GCS
       port + loopback IP it computed locally (no ConfigMap handoff).
    3. SSH to the head works.
    4. SSH to the worker works — exercises the worker pod's own
       deterministic sshd port (rank-derived) end-to-end.
    """
    anchor_key = 'skypilot-multinode-anchor'
    anchor_val = uuid.uuid4().hex[:12]

    base = smoke_tests_utils.get_cluster_name()
    name_anchor = f'{base}-anchor'
    name_multi = f'{base}-multi'

    cfg_anchor = f'/tmp/sky-multinode-{anchor_val}-anchor.yaml'
    cfg_multi = f'/tmp/sky-multinode-{anchor_val}-multi.yaml'

    write_cfg_anchor = (f'cat > {cfg_anchor} <<EOF\n'
                        f'kubernetes:\n'
                        f'  pod_config:\n'
                        f'    metadata:\n'
                        f'      labels:\n'
                        f'        {anchor_key}: "{anchor_val}"\n'
                        f'    spec:\n'
                        f'      hostNetwork: true\n'
                        f'EOF')

    # Both head and worker pods inherit this pod_config, so each can
    # independently satisfy the affinity against the already-running anchor.
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
            f'sky launch -y -c {name_anchor} --infra kubernetes '
            f'--config {cfg_anchor} --cpus 1 --memory 2',
            f'sky launch -y -c {name_multi} --infra kubernetes '
            f'--config {cfg_multi} --num-nodes 2 --cpus 1 --memory 2',
            f'sky exec {name_multi} -- echo "job_ok"',
            f'sky logs {name_multi} 1 --status',
            f's=$(ssh -o StrictHostKeyChecking=no {name_multi} '
            f'"echo ssh_head_ok" 2>&1) && echo "$s" | grep ssh_head_ok',
            f's=$(ssh -o StrictHostKeyChecking=no {name_multi}-worker1 '
            f'"echo ssh_worker_ok" 2>&1) && echo "$s" | grep ssh_worker_ok',
        ],
        teardown=(f'sky down -y {name_anchor}; sky down -y {name_multi}; '
                  f'rm -f {cfg_anchor} {cfg_multi}'),
        timeout=5 * 60,
    )
    smoke_tests_utils.run_one_test(test)

"""Smoke tests for the Kubernetes hostNetwork codepath.

Under ``kubernetes.pod_config.spec.hostNetwork = true`` the pod
shares the K8s node's net namespace, so a second SkyPilot pod
landing on the same node would collide on Ray's default ports
(6380, 8266, 8076, ...) and on the node's own sshd (host:22).
SkyPilot's host_network_probe picks a free Ray port set per pod
and rebinds the pod's sshd to a probed port; these tests prove
both work end-to-end.
"""
import subprocess

import pytest
from smoke_tests import smoke_tests_utils


def _pick_worker_node() -> str:
    """Return the name of one worker node (non control-plane).

    Smoke tests run against the user's current kubeconfig; we want a
    node that the scheduler will actually place workloads on.
    """
    result = subprocess.run([
        'kubectl', 'get', 'nodes', '-o',
        'jsonpath={range .items[?(@.metadata.labels.node-role\\.'
        'kubernetes\\.io/control-plane!="")]}{.metadata.name}'
        '{"\\n"}{end}'
        '{range .items[?(@.metadata.labels.node-role\\.'
        'kubernetes\\.io/control-plane=="")]}{.metadata.name}'
        '{"\\n"}{end}'
    ],
                            capture_output=True,
                            text=True,
                            check=False)
    if result.returncode != 0:
        pytest.skip(f'kubectl get nodes failed: {result.stderr}')
    # Worker nodes appear after the control-plane block. Fall back to
    # any node if the cluster has no nodes labeled control-plane (kind,
    # minikube, etc.).
    names = [n for n in result.stdout.splitlines() if n]
    if not names:
        pytest.skip('No nodes returned by kubectl')
    # Prefer the last node — on managed clusters control-plane nodes
    # are listed first; on single-node clusters this still works.
    return names[-1]


@pytest.mark.kubernetes
@pytest.mark.resource_heavy
@pytest.mark.no_dependency
def test_kubernetes_host_network_coexistence():
    """Two hostNetwork SkyPilot clusters on the same K8s node coexist.

    Forces both clusters' pods onto the same K8s node via
    ``nodeSelector: kubernetes.io/hostname=<node>`` and turns on
    ``hostNetwork: true``. Verifies:

    1. Both launches succeed (head_network_probe picked free ports).
    2. Both pods landed on the target node (the actual repro setup).
    3. ``ssh <cluster>`` works against both heads (sshd_<podname>
       propagation to InstanceInfo.ssh_port and the per-pod sshd Port
       rewrite both worked).
    4. The two clusters' head GCS ports are distinct (probe didn't
       hand out the same number twice).
    """
    if smoke_tests_utils.is_non_docker_remote_api_server():
        pytest.skip('Skipping test because the Kubernetes configs and '
                    'credentials are located on the remote API server '
                    'and not the machine where the test is running')

    target_node = _pick_worker_node()

    base = smoke_tests_utils.get_cluster_name()
    name_a = f'{base}-a'
    name_b = f'{base}-b'

    # Extract a pod's node name by the skypilot-cluster-name annotation
    # (the label is hash-suffixed; the annotation is the exact name).
    # Not an f-string, so braces here are literal (no doubling needed).
    get_pod_node = ('kubectl get pods --all-namespaces -o jsonpath=\''
                    '{range .items[*]}'
                    '{.metadata.annotations.skypilot-cluster-name} '
                    '{.spec.nodeName}{"\\n"}{end}\'')

    test = smoke_tests_utils.Test(
        'kubernetes_host_network_coexistence',
        [
            # 1. Launch both clusters onto the same node with hostNetwork.
            f'sky launch -y -c {name_a} --infra kubernetes '
            f'--cpus 0.5 --memory 1 -- echo "hello from A"',
            f'sky logs {name_a} 1 --status',
            f'sky launch -y -c {name_b} --infra kubernetes '
            f'--cpus 0.5 --memory 1 -- echo "hello from B"',
            f'sky logs {name_b} 1 --status',

            # 2. Both pods must be on the target node.
            f'A_NODE=$({get_pod_node} | grep "^{name_a} " '
            f'| awk \'{{print $2}}\') && '
            f'B_NODE=$({get_pod_node} | grep "^{name_b} " '
            f'| awk \'{{print $2}}\') && '
            f'echo "A_NODE=$A_NODE B_NODE=$B_NODE" && '
            f'[ "$A_NODE" = "{target_node}" ] && '
            f'[ "$B_NODE" = "{target_node}" ]',

            # 3. SSH must work against both heads. This exercises:
            #    - sshd_<podname> ConfigMap entry -> InstanceInfo.ssh_port
            #    - /etc/ssh/sshd_config Port rewrite by the probe
            #    - SkyPilot SSH config writer using the probed port
            f's=$(ssh -o StrictHostKeyChecking=no {name_a} '
            f'"echo ssh_works_A" 2>&1) && echo "$s" | grep ssh_works_A',
            f's=$(ssh -o StrictHostKeyChecking=no {name_b} '
            f'"echo ssh_works_B" 2>&1) && echo "$s" | grep ssh_works_B',

            # 4. The probe must have assigned distinct GCS ports to the
            #    two heads. The env file written by the probe is the
            #    authoritative source on each pod.
            f'A_GCS=$(ssh {name_a} '
            f'". /tmp/sky_host_network_ports.env && '
            f'echo $SKYPILOT_RAY_PORT") && '
            f'B_GCS=$(ssh {name_b} '
            f'". /tmp/sky_host_network_ports.env && '
            f'echo $SKYPILOT_RAY_PORT") && '
            f'echo "A_GCS=$A_GCS B_GCS=$B_GCS" && '
            f'[ -n "$A_GCS" ] && [ -n "$B_GCS" ] && '
            f'[ "$A_GCS" != "$B_GCS" ]',
        ],
        teardown=f'sky down -y {name_a}; sky down -y {name_b}',
        timeout=smoke_tests_utils.get_timeout('kubernetes'),
        config_dict={
            'kubernetes': {
                'pod_config': {
                    'spec': {
                        'hostNetwork': True,
                        'nodeSelector': {
                            'kubernetes.io/hostname': target_node,
                        },
                    },
                },
            },
        },
    )
    smoke_tests_utils.run_one_test(test)

"""Unit tests for deterministic hostNetwork port/IP derivation.

These cover the scheme that replaces PR #9604's runtime probe + ConfigMap
discovery: every pod independently derives an identical port set and per-pod
loopback IP from sha256(cluster_name) + rank, so there is no head->worker
handoff to test end-to-end here — only the math and the rendered commands.
"""
import hashlib
import subprocess

import pytest

from sky.provision import instance_setup
from sky.provision.kubernetes import host_network


def test_port_base_range_and_alignment():
    for name in ['a', 'sky-cluster', 'x' * 200, 'cluster-with-üñïçødé']:
        pb = host_network.port_base(name)
        assert 20000 <= pb <= 31900, (name, pb)
        assert pb % 100 == 0, (name, pb)


def test_port_base_matches_documented_formula():
    name = 'my-cluster-on-cloud'
    h = hashlib.sha256(name.encode()).digest()
    expected = 20000 + (int.from_bytes(h[:2], 'big') % 120) * 100
    assert host_network.port_base(name) == expected


def test_derivation_is_deterministic():
    name = 'deterministic-check'
    assert host_network.derive_ports(name,
                                     0) == host_network.derive_ports(name, 0)
    assert host_network.derive_ports(name,
                                     5) == host_network.derive_ports(name, 5)
    assert host_network.derive_node_ip(name, 2) == host_network.derive_node_ip(
        name, 2)


def test_port_offsets_within_rank_slot():
    name = 'offset-cluster'
    pb = host_network.port_base(name)
    p = host_network.derive_ports(name, 0)
    assert p.gcs == pb
    assert p.dashboard == pb + 1
    assert p.node_manager == pb + 2
    assert p.object_manager == pb + 3
    assert p.runtime_env_agent == pb + 4
    assert p.dashboard_agent_listen == pb + 5
    assert p.metrics_export == pb + 6
    assert p.ray_client_server == pb + 7
    assert p.sshd == pb + 9  # 8 is reserved


def test_head_and_worker_ports_are_disjoint():
    name = 'disjoint-cluster'
    head = set(vars(host_network.derive_ports(name, 0)).values())
    for rank in range(1, host_network.MAX_RANK + 1):
        worker = set(vars(host_network.derive_ports(name, rank)).values())
        assert head.isdisjoint(worker), rank
    # And every pod's slot stays inside the cluster's 100-port window.
    pb = host_network.port_base(name)
    top = max(
        vars(host_network.derive_ports(name, host_network.MAX_RANK)).values())
    assert top < pb + 100


def test_unrelated_clusters_usually_get_different_windows():
    # Not a guarantee (birthday-paradox bucket collisions are accepted), but
    # two arbitrary names should not systematically collide.
    a = host_network.port_base('cluster-alpha-xyz')
    b = host_network.port_base('cluster-beta-xyz')
    assert a != b


def test_node_ip_scheme():
    name = 'ip-cluster'
    h = hashlib.sha256(name.encode()).digest()
    for rank in (0, 1, 9):
        ip = host_network.derive_node_ip(name, rank)
        octets = ip.split('.')
        assert octets[0] == '127'
        assert int(octets[1]) == (h[0] | 0x80)
        assert int(octets[1]) >= 128  # never lands in 127.0.0.x
        assert int(octets[2]) == h[1]
        assert int(octets[3]) == rank
    assert host_network.derive_node_ip(name, 0) != host_network.derive_node_ip(
        name, 1)


def test_node_ip_prefix_consistent_with_derive_node_ip():
    name = 'prefix-cluster'
    prefix = host_network.node_ip_prefix(name)
    assert host_network.derive_node_ip(name, 4) == f'{prefix}4'


@pytest.mark.parametrize('pod_name,expected', [
    ('mycluster-head', ('mycluster', 0)),
    ('mycluster-worker1', ('mycluster', 1)),
    ('mycluster-worker42', ('mycluster', 42)),
    ('a-b-c-worker3', ('a-b-c', 3)),
    ('weird-worker-in-name-head', ('weird-worker-in-name', 0)),
])
def test_cluster_and_rank_from_pod_name(pod_name, expected):
    assert host_network.cluster_and_rank_from_pod_name(pod_name) == expected
    assert host_network.rank_from_pod_name(pod_name) == expected[1]


def test_negative_rank_rejected():
    with pytest.raises(ValueError):
        host_network.derive_ports('c', -1)
    with pytest.raises(ValueError):
        host_network.derive_node_ip('c', -1)


@pytest.mark.parametrize('pod_config,expected', [
    (None, False),
    ({}, False),
    ({
        'spec': {}
    }, False),
    ({
        'spec': {
            'hostNetwork': False
        }
    }, False),
    ({
        'spec': {
            'hostNetwork': True
        }
    }, True),
])
def test_is_host_network(pod_config, expected):
    assert host_network.is_host_network(pod_config) is expected


def test_worker_rank_shell_keeps_head_at_zero():
    # Smoke check the bash: head pod name -> rank 0, worker -> its index.
    shell = host_network.worker_rank_shell()
    for pod, want in [('cl-head', '0'), ('cl-worker0', '0'),
                      ('cl-worker7', '7'), ('cl-worker13', '13')]:
        out = subprocess.run([
            'bash', '-c', f'SKYPILOT_POD_NAME={pod}; {shell}; '
            f'echo ${host_network.WORKER_RANK_ENV}'
        ],
                             capture_output=True,
                             text=True,
                             check=True)
        assert out.stdout.strip() == want, (pod, out.stdout)


# --- Regression: the non-hostNetwork command must not change at all. -------


def test_ray_commands_byte_identical_without_host_network():
    """ray_{head,worker}_start_command(...) with host_network_params=None must
    be byte-identical to the legacy call (no regression for VM / non-host
    -network K8s clusters, which is the overwhelmingly common path)."""
    cr = '{"A100":1}'
    cro = {'object-store-memory': 500000000, 'num-cpus': '4'}
    # Default (None) param must equal the call without the param at all.
    assert (instance_setup.ray_head_start_command(
        cr, dict(cro)) == instance_setup.ray_head_start_command(
            cr, dict(cro), host_network_params=None))
    for nr in (True, False):
        assert (instance_setup.ray_worker_start_command(
            cr, dict(cro),
            no_restart=nr) == instance_setup.ray_worker_start_command(
                cr, dict(cro), no_restart=nr, host_network_params=None))
    # And the legacy default ports are still present literally.
    h = instance_setup.ray_head_start_command(None, None)
    assert '--port=6380 ' in h
    assert '--dashboard-port=8266 ' in h
    assert '--object-manager-port=8076 ' in h
    assert '--node-ip-address' not in h


def test_host_network_head_command_bakes_literal_ports():
    params = host_network.HostNetworkRayParams.build('clusterX')
    p = params.head_ports
    cmd = instance_setup.ray_head_start_command(None,
                                                None,
                                                host_network_params=params)
    assert f'--port={p.gcs} ' in cmd
    assert f'--dashboard-port={p.dashboard} ' in cmd
    assert f'--object-manager-port={p.object_manager} ' in cmd
    assert f'--node-manager-port={p.node_manager} ' in cmd
    assert f'--ray-client-server-port={p.ray_client_server} ' in cmd
    assert f'--node-ip-address={params.head_node_ip} ' in cmd
    # Port file + ray-status wait must use the derived port, not 6380.
    assert f'"ray_port":{p.gcs}' in cmd
    assert f'RAY_ADDRESS={params.head_node_ip}:{p.gcs} ' in cmd
    assert '6380' not in cmd


def test_host_network_worker_command_uses_runtime_rank():
    params = host_network.HostNetworkRayParams.build('clusterX')
    cmd = instance_setup.ray_worker_start_command(None,
                                                  None,
                                                  no_restart=False,
                                                  host_network_params=params)
    # rank_shell defines the rank var before anything references it.
    assert cmd.startswith(params.rank_shell + '; ')
    assert host_network.WORKER_RANK_ENV in cmd
    # Worker ports are per-rank bash arithmetic, not literals.
    assert params.worker_port_exprs['node_manager'] in cmd
    assert params.worker_node_ip_expr in cmd
    # Worker still dials the head via the env exported by the template.
    assert '--address=${SKYPILOT_RAY_HEAD_IP}:${SKYPILOT_RAY_PORT}' in cmd

"""Unit tests for sky.provision.pbs.utils."""
# pylint: disable=protected-access
import os
from unittest import mock

import pytest

from sky import exceptions
from sky.adaptors import pbs as pbs_adaptor
from sky.provision.pbs import utils
from sky.utils import command_runner


def _node(name,
          cpus=8,
          mem=32.0,
          gpus=0,
          gpu_type=None,
          queue=None,
          state='free',
          assigned_cpus=0,
          assigned_gpus=0,
          qlist=None):
    return pbs_adaptor.NodeInfo(
        node=name,
        state=state,
        cpus=cpus,
        memory_gb=mem,
        gpus=gpus,
        gpu_type=gpu_type,
        gpu_type_resource=('gpu_type' if gpu_type else None),
        queue=queue,
        assigned_cpus=assigned_cpus,
        assigned_gpus=assigned_gpus,
        jobs=[],
        qlist=qlist)


class TestPbsInstanceType:

    @pytest.mark.parametrize('name,cpus,mem,count,acc', [
        ('4CPU--16GB', 4.0, 16.0, None, None),
        ('0.5CPU--1.5GB', 0.5, 1.5, None, None),
        ('4CPU--16GB--V100:1', 4.0, 16.0, 1, 'V100'),
        ('8CPU--32GB--A100-80GB:4', 8.0, 32.0, 4, 'A100-80GB'),
    ])
    def test_roundtrip(self, name, cpus, mem, count, acc):
        inst = utils.PbsInstanceType.from_instance_type(name)
        assert (inst.cpus, inst.memory, inst.accelerator_count,
                inst.accelerator_type) == (cpus, mem, count, acc)
        assert inst.name == name
        assert utils.PbsInstanceType.is_valid_instance_type(name)

    @pytest.mark.parametrize('name', ['4CPU', 'm5.large', '4CPU--16GB--A100'])
    def test_invalid(self, name):
        assert not utils.PbsInstanceType.is_valid_instance_type(name)
        with pytest.raises(ValueError):
            utils.PbsInstanceType.from_instance_type(name)

    def test_from_resources_rounds_up_gpus(self):
        inst = utils.PbsInstanceType.from_resources(2, 8, 0.5, 'T4')
        assert inst.name == '2CPU--8GB--T4:1'
        assert utils.PbsInstanceType.from_resources(2, 8).name == '2CPU--8GB'


class TestConfigLookups:

    def test_gpu_resource_defaults(self):
        with mock.patch.object(utils.skypilot_config,
                               'get_effective_region_config',
                               side_effect=lambda **kw: kw['default_value']):
            assert utils.get_gpu_resource('c') == 'ngpus'
            assert utils.get_gpu_type_resources(
                'c') == utils.DEFAULT_GPU_TYPE_RESOURCES

    def test_gpu_type_resource_configured(self):

        def _config(**kw):
            if kw['keys'] == ('gpu_type_resource',):
                return 'gpumodel'
            return kw['default_value']

        with mock.patch.object(utils.skypilot_config,
                               'get_effective_region_config',
                               side_effect=_config):
            assert utils.get_gpu_type_resources('c') == ('gpumodel',)

    def test_lookup_gpu_queue_map(self):
        with mock.patch.object(utils.skypilot_config,
                               'get_effective_region_config',
                               return_value={
                                   'h100': 'gpuq',
                                   'A100': ['a', 'b']
                               }):
            assert utils.lookup_gpu_queue_map('c', 'H100') == ['gpuq']
            assert utils.lookup_gpu_queue_map('c', 'a100') == ['a', 'b']
            assert utils.lookup_gpu_queue_map('c', 'T4') is None

    def test_expand_path_vars(self):
        env = {'USER': 'alice', 'SCRATCH': '/scratch'}
        assert utils.expand_path_vars('$SCRATCH/${USER}/x',
                                      env) == ('/scratch/alice/x')
        assert utils.expand_path_vars('$UNKNOWN/x', env) == '$UNKNOWN/x'


class TestQueues:

    def test_get_queues_orders_default_first_and_skips_stopped(self):
        queues = {
            'b': pbs_adaptor.PbsQueue('b', False, 'Execution', True, True, None,
                                      None),
            'workq': pbs_adaptor.PbsQueue('workq', True, 'Route', True, True,
                                          None, None),
            'a': pbs_adaptor.PbsQueue('a', False, 'Execution', True, True, None,
                                      None),
            'off': pbs_adaptor.PbsQueue('off', False, 'Execution', True, False,
                                        None, None),
            # Only reachable through a routing queue; qsub -q rejects it.
            'routed': pbs_adaptor.PbsQueue('routed',
                                           False,
                                           'Execution',
                                           True,
                                           True,
                                           None,
                                           None,
                                           from_route_only=True),
        }
        with mock.patch.object(utils, 'get_queue_infos', return_value=queues):
            assert utils.get_queues('c') == ['workq', 'a', 'b']
            assert utils.get_cluster_default_queue('c') == 'workq'

    def test_nodes_for_queue(self):
        nodes = [
            _node('any'),
            _node('g', queue='gpuq'),
            _node('c', queue='cpu')
        ]
        assert [n.node for n in utils.nodes_for_queue(nodes, 'gpuq')
               ] == ['any', 'g']
        assert len(utils.nodes_for_queue(nodes, None)) == 3
        # A routing queue can land on any node.
        assert len(utils.nodes_for_queue(nodes, 'route',
                                         is_routing_queue=True)) == 3

    def test_nodes_for_queue_with_qlist(self):
        nodes = [
            _node('gpu', qlist='gpuq'),
            _node('jupyter', qlist='jupytergpu'),
            _node('multi', qlist='smallq,gpuq'),
            _node('untagged'),
        ]
        # A queue stamping Qlist on its chunks only lands on tagged nodes.
        assert [
            n.node
            for n in utils.nodes_for_queue(nodes, 'gpuq', qlists={'gpuq'})
        ] == ['gpu', 'multi']
        # A routing queue takes the union of its destinations' tags.
        assert [
            n.node for n in utils.nodes_for_queue(nodes,
                                                  'cx',
                                                  is_routing_queue=True,
                                                  qlists={'gpuq', 'jupytergpu'})
        ] == ['gpu', 'jupyter', 'multi']
        # Without Qlist, every node counts.
        assert len(utils.nodes_for_queue(nodes, 'gpuq')) == 4

    def test_queue_qlists(self):

        def _queue(name, queue_type='Execution', qlist=None, dests=()):
            return pbs_adaptor.PbsQueue(name,
                                        False,
                                        queue_type,
                                        True,
                                        True,
                                        None,
                                        None,
                                        qlist=qlist,
                                        route_destinations=dests)

        queues = {
            'gpu': _queue('gpu', qlist='gpu'),
            'cpu': _queue('cpu', qlist='cpu'),
            'plain': _queue('plain'),
            'cx': _queue('cx', 'Route', dests=('gpu', 'cpu')),
            'any': _queue('any', 'Route', dests=('gpu', 'plain')),
        }
        with mock.patch.object(utils, 'get_queue_infos', return_value=queues):
            assert utils._queue_qlists('c', 'gpu') == {'gpu'}
            assert utils._queue_qlists('c', 'plain') is None
            assert utils._queue_qlists('c', 'cx') == {'gpu', 'cpu'}
            # One unrestricted destination makes the routing queue
            # unrestricted.
            assert utils._queue_qlists('c', 'any') is None
            assert utils._queue_qlists('c', 'missing') is None
            assert utils._queue_qlists('c', None) is None


class TestFitAndResolve:

    def test_resolve_gpu_type_fuzzy(self):
        nodes = [
            _node('n1', gpus=4, gpu_type='nvidia_h100_80gb_hbm3'),
            _node('n2', gpus=8, gpu_type='NVIDIA_A100_SXM4_80GB'),
            _node('n3', gpus=8, gpu_type='NVIDIA_A100_SXM4_80GB'),
        ]
        with mock.patch.object(utils, 'get_pbs_nodes_info',
                               return_value=nodes), \
             mock.patch.object(utils, '_is_routing_queue',
                               return_value=False):
            assert utils.resolve_gpu_type('c', 'H100',
                                          1) == ('nvidia_h100_80gb_hbm3')
            assert utils.resolve_gpu_type('c', 'A100-80GB',
                                          8) == 'NVIDIA_A100_SXM4_80GB'
            with pytest.raises(exceptions.ResourcesUnavailableError,
                               match='H100'):
                utils.resolve_gpu_type('c', 'H100', 8)
            with pytest.raises(exceptions.ResourcesUnavailableError,
                               match='Discovered GPU types'):
                utils.resolve_gpu_type('c', 'L4', 1)

    def test_resolve_gpu_type_untyped_nodes(self):
        nodes = [_node('n1', gpus=4)]
        with mock.patch.object(utils, 'get_pbs_nodes_info',
                               return_value=nodes), \
             mock.patch.object(utils, '_is_routing_queue',
                               return_value=False):
            assert utils.resolve_gpu_type('c', 'H100', 1) is None

    def test_check_instance_fits(self):
        nodes = [
            _node('cpu', cpus=16, mem=64),
            _node('gpu',
                  cpus=32,
                  mem=256,
                  gpus=4,
                  gpu_type='A100',
                  queue='gpuq'),
        ]
        with mock.patch.object(utils, 'get_pbs_nodes_info',
                               return_value=nodes), \
             mock.patch.object(utils, '_is_routing_queue',
                               return_value=False), \
             mock.patch.object(utils, 'lookup_gpu_queue_map',
                               return_value=None):
            assert utils.check_instance_fits('c', '8CPU--32GB')[0]
            fits, reason = utils.check_instance_fits('c', '64CPU--32GB')
            assert not fits and 'Max found: 32 CPUs' in reason
            assert utils.check_instance_fits('c', '8CPU--32GB--A100:2',
                                             'gpuq')[0]
            fits, reason = utils.check_instance_fits('c', '8CPU--32GB--A100:2',
                                                     'cpu')
            assert not fits and 'No nodes with 2x A100 in queue cpu' in reason
            fits, reason = utils.check_instance_fits('c', '8CPU--32GB--H100:2',
                                                     'gpuq')
            assert not fits and 'No GPU nodes matching' in reason
            fits, reason = utils.check_instance_fits('c', '8CPU--32GB--A100:8')
            assert not fits

    def test_check_instance_fits_with_queue_map_ignores_type(self):
        nodes = [_node('gpu', cpus=32, mem=256, gpus=4, queue='gpuq')]
        with mock.patch.object(utils, 'get_pbs_nodes_info',
                               return_value=nodes), \
             mock.patch.object(utils, '_is_routing_queue',
                               return_value=False), \
             mock.patch.object(utils, 'lookup_gpu_queue_map',
                               return_value=['gpuq']):
            assert utils.check_instance_fits('c', '8CPU--32GB--H100:2',
                                             'gpuq')[0]

    def test_check_instance_fits_unreachable(self):
        with mock.patch.object(utils,
                               'get_pbs_nodes_info',
                               side_effect=FileNotFoundError()):
            fits, reason = utils.check_instance_fits('c', '8CPU--32GB')
            assert not fits and 'does not exist' in reason


class TestNodeInfoList:

    def test_build_node_info_list(self):
        nodes = [
            _node('g',
                  cpus=32,
                  mem=256.123,
                  gpus=8,
                  gpu_type='nvidia_h100',
                  assigned_cpus=8,
                  assigned_gpus=2,
                  queue='gpuq'),
            _node('d', cpus=8, gpus=2, state='offline,down'),
            _node('u', cpus=8, gpus=2),
        ]
        infos = utils._build_pbs_node_info_list('c', nodes)
        assert infos[0] == {
            'node_name': 'g',
            'pbs_cluster_name': 'c',
            'queue': 'gpuq',
            'node_state': 'free',
            'gpu_type': 'H100',
            'total_gpus': 8,
            'free_gpus': 6,
            'vcpu_count': 32,
            'memory_gb': 256.12,
            'free_vcpus': 24,
        }
        assert infos[1]['free_gpus'] == 0
        assert infos[2]['gpu_type'] == 'GPU'
        assert utils._build_pbs_node_info_list('c', []) == []


class TestNodePortFiles:

    def test_parse_node_port_files(self):
        output = (
            'rank0.port node1 23456 /tmp/pbs.1/sky-x\n'
            'rank1.port node2 23457 /tmp/pbs.1/sky-x\n'
            'rank2.port node3\n'  # partially written
            'garbage line here\n')
        assert utils.parse_node_port_files(output) == {
            0: utils.NodeEndpoint('node1', 23456, '/tmp/pbs.1/sky-x'),
            1: utils.NodeEndpoint('node2', 23457, '/tmp/pbs.1/sky-x'),
        }

    def test_paths(self):
        home = utils.sky_cluster_home_dir('/base', 'sky-x')
        assert home == '/base/.sky_clusters/sky-x'
        assert utils.sshd_dir(home) == '/base/.sky_clusters/sky-x/.sky_sshd'
        assert utils.node_port_file(home, 2).endswith('/.sky_sshd/rank2.port')
        cmd = utils.list_node_port_files_command(home)
        assert '/base/.sky_clusters/sky-x/.sky_sshd/rank*.port' in cmd


class TestValidateWalltime:

    @pytest.mark.parametrize('value', ['30', '10:00', '04:00:00', '240:00:00'])
    def test_valid(self, value):
        utils.validate_walltime(value)

    @pytest.mark.parametrize('value', ['1d', '1-00:00:00', '4:00:00:00', ''])
    def test_invalid(self, value):
        with pytest.raises(ValueError):
            utils.validate_walltime(value)


class TestLoginNodeProxyCommand:

    def test_basic(self, tmp_path):
        key = tmp_path / 'id_ed25519'
        key.write_text('')
        cmd = utils.build_login_node_proxy_command({
            'hostname': 'login.example.com',
            'port': 2222,
            'user': 'alice',
            'private_key': str(key),
        })
        assert cmd.startswith(f'ssh -T -i {key} ')
        assert '-o Port=2222' in cmd
        assert cmd.endswith("-W '[%h]:%p' alice@login.example.com")
        # Shares the control master of the login-node runner.
        login_cmd = ' '.join(
            utils.make_login_node_runner({
                'hostname': 'login.example.com',
                'port': 2222,
                'user': 'alice',
                'private_key': str(key),
            }).ssh_base_command(ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
                                port_forward=None,
                                connect_timeout=None))
        assert cmd[:-len("-W '[%h]:%p' alice@login.example.com")].strip() == (
            login_cmd[:-len('alice@login.example.com')].strip())
        assert 'ControlMaster=auto' in cmd
        # No IdentitiesOnly unless the config asked for it.
        assert 'IdentitiesOnly' not in cmd

    def test_nested_proxy(self):
        cmd = utils.build_login_node_proxy_command({
            'hostname': 'login',
            'port': 22,
            'user': 'alice',
            'proxyjump': 'bastion',
            'identities_only': True,
        })
        assert 'ProxyCommand' in cmd
        assert 'bastion' in cmd
        assert 'IdentitiesOnly=yes' in cmd
        # No control master over a proxied hop.
        assert 'ControlMaster' not in cmd

    def test_user_control_path(self):
        cmd = utils.build_login_node_proxy_command({
            'hostname': 'login',
            'port': 22,
            'user': 'alice',
            'control_path': '/home/me/.ssh/login-abc',
        })
        assert '-o ControlPath=/home/me/.ssh/login-abc' in cmd
        assert '-o ControlMaster=auto' in cmd

    def test_get_ssh_control_path(self):
        assert utils.get_ssh_control_path({}) is None
        assert utils.get_ssh_control_path({'controlpath': '~/.ssh/x'
                                          }) == os.path.expanduser('~/.ssh/x')

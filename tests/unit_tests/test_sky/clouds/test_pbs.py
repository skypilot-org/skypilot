"""Tests for the PBS cloud implementation."""
# pylint: disable=protected-access
import os
from unittest import mock

import jsonschema
import pytest

from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky import skypilot_config
from sky.adaptors import pbs as pbs_adaptor
from sky.clouds import pbs as pbs_cloud
from sky.provision.pbs import utils as pbs_utils
from sky.skylet import constants
from sky.utils import infra_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import yaml_utils

_SSH_CONFIG = {
    'hostname': 'login.example.com',
    'port': '22',
    'user': 'alice',
    'identityfile': ['/home/user/.ssh/id_rsa'],
    'controlpath': '/home/user/.ssh/login-abc',
}


def _node(name, cpus=32, mem=256.0, gpus=4, gpu_type='A100', queue=None):
    return pbs_adaptor.NodeInfo(
        node=name,
        state='free',
        cpus=cpus,
        memory_gb=mem,
        gpus=gpus,
        gpu_type=gpu_type,
        gpu_type_resource='gpu_type' if gpu_type else None,
        queue=queue,
        assigned_cpus=0,
        assigned_gpus=0,
        jobs=[])


class TestRegistration:

    def test_registered_with_aliases(self):
        assert isinstance(clouds.PBS(), clouds.Cloud)
        assert clouds.PBS.canonical_name() == 'pbs'
        assert isinstance(registry.CLOUD_REGISTRY.from_str('pbspro'),
                          clouds.PBS)
        assert isinstance(registry.CLOUD_REGISTRY.from_str('openpbs'),
                          clouds.PBS)
        assert 'pbs' in constants.ALL_CLOUDS
        assert not clouds.PBS.uses_ray()

    def test_config_keys_registered(self):
        assert ('pbs',
                'qsub_options') in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK
        assert ('pbs', 'quota') in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK
        assert ('pbs',
                'cluster_configs') in constants.SKIPPED_CLIENT_OVERRIDE_KEYS


class TestExistingAllowedClusters:

    @pytest.mark.parametrize('allowed,expected', [
        (None, ['a', 'b']),
        ('all', ['a', 'b']),
        (['b'], ['b']),
        (['b', 'missing'], ['b']),
    ])
    def test_allowed_clusters(self, allowed, expected):
        with mock.patch.object(pbs_utils, 'get_all_pbs_cluster_names',
                               return_value=['a', 'b']), \
             mock.patch.object(skypilot_config, 'get_workspace_cloud',
                               return_value=skypilot_config.config_utils.Config()), \
             mock.patch.object(skypilot_config, 'get_effective_region_config',
                               return_value=allowed):
            assert clouds.PBS.existing_allowed_clusters(silent=True) == expected

    def test_no_config_file(self):
        with mock.patch.object(pbs_utils,
                               'get_all_pbs_cluster_names',
                               return_value=[]):
            assert clouds.PBS.existing_allowed_clusters() == []
            assert clouds.PBS.expand_infras() == []


class TestRegionsWithOffering:

    def _patch(self, nodes, queues, queue_map=None, cpu_queue=None):
        return [
            mock.patch.object(clouds.PBS,
                              'existing_allowed_clusters',
                              return_value=['c1']),
            mock.patch.object(pbs_utils, 'get_queues', return_value=queues),
            mock.patch.object(pbs_utils,
                              'get_pbs_nodes_info',
                              return_value=nodes),
            mock.patch.object(pbs_utils,
                              '_is_routing_queue',
                              return_value=False),
            mock.patch.object(pbs_utils,
                              'lookup_gpu_queue_map',
                              return_value=queue_map),
            mock.patch.object(pbs_utils,
                              'lookup_cpu_queue',
                              return_value=cpu_queue),
        ]

    def test_queues_are_zones(self):
        patches = self._patch(
            [_node('n1'), _node('n2', queue='gpuq')], ['workq', 'gpuq'])
        for p in patches:
            p.start()
        try:
            regions = clouds.PBS.regions_with_offering(None, None, False, None,
                                                       None)
            assert [r.name for r in regions] == ['c1']
            assert [z.name for z in regions[0].zones] == ['workq', 'gpuq']
            regions = clouds.PBS.regions_with_offering('8CPU--32GB--A100:2',
                                                       None, False, None, None)
            assert [z.name for z in regions[0].zones] == ['workq', 'gpuq']
            regions = clouds.PBS.regions_with_offering('8CPU--32GB--H100:2',
                                                       None, False, None, None)
            assert regions == []
            regions = clouds.PBS.regions_with_offering('8CPU--32GB', None,
                                                       False, 'other', None)
            assert regions == []
        finally:
            for p in patches:
                p.stop()

    def test_gpu_queue_map_narrows_queues(self):
        patches = self._patch([_node('n1', gpu_type=None)], ['workq', 'gpuq'],
                              queue_map=['gpuq'])
        for p in patches:
            p.start()
        try:
            regions = clouds.PBS.regions_with_offering('8CPU--32GB--H100:2',
                                                       None, False, None, None)
            assert [z.name for z in regions[0].zones] == ['gpuq']
        finally:
            for p in patches:
                p.stop()

    def test_cpu_queue(self):
        patches = self._patch([_node('n1')], ['workq', 'cpu'], cpu_queue='cpu')
        for p in patches:
            p.start()
        try:
            regions = clouds.PBS.regions_with_offering('8CPU--32GB', None,
                                                       False, None, None)
            assert [z.name for z in regions[0].zones] == ['cpu']
        finally:
            for p in patches:
                p.stop()

    def test_zones_provision_loop_one_queue_at_a_time(self):
        patches = self._patch([_node('n1')], ['workq', 'gpuq'])
        for p in patches:
            p.start()
        try:
            zones = list(
                clouds.PBS.zones_provision_loop(region='c1',
                                                num_nodes=1,
                                                instance_type='8CPU--32GB'))
            assert [[z.name for z in zs] for zs in zones] == [['workq'],
                                                              ['gpuq']]
        finally:
            for p in patches:
                p.stop()


class TestFeasibleResources:

    def test_gpu_request_builds_instance_type(self):
        with mock.patch.object(clouds.PBS, 'regions_with_offering',
                               return_value=[clouds.Region('c1')]) as rwo, \
             mock.patch.object(pbs_utils, 'get_all_pbs_cluster_names',
                               return_value=['c1']):
            resources = resources_lib.Resources(cloud=clouds.PBS(),
                                                accelerators={'A100': 2})
            feasible = clouds.PBS()._get_feasible_launchable_resources(
                resources)
        assert len(feasible.resources_list) == 1
        assert feasible.resources_list[0].instance_type == '8CPU--32GB--A100:2'
        assert rwo.call_args.args[0] == '8CPU--32GB--A100:2'

    def test_cpu_request_uses_defaults(self):
        with mock.patch.object(clouds.PBS, 'regions_with_offering',
                               return_value=[clouds.Region('c1')]), \
             mock.patch.object(pbs_utils, 'get_all_pbs_cluster_names',
                               return_value=['c1']):
            resources = resources_lib.Resources(cloud=clouds.PBS(),
                                                cpus='4',
                                                memory='32')
            feasible = clouds.PBS()._get_feasible_launchable_resources(
                resources)
        assert feasible.resources_list[0].instance_type == '4CPU--32GB'

    def test_nothing_fits(self):
        with mock.patch.object(clouds.PBS,
                               'regions_with_offering',
                               return_value=[]):
            resources = resources_lib.Resources(cloud=clouds.PBS(), cpus='4')
            feasible = clouds.PBS()._get_feasible_launchable_resources(
                resources)
        assert feasible.resources_list == []


class TestMakeDeployResourcesVariables:

    def _config(self, tmp_path, config_dict):
        config_path = tmp_path / 'config.yaml'
        config_path.write_text(yaml_utils.dump_yaml_str(config_dict))
        return config_path

    def _deploy_vars(self,
                     tmp_path,
                     config_dict,
                     instance_type='8CPU--32GB--A100:2',
                     zone='gpuq',
                     overrides=None):
        config_path = self._config(tmp_path, config_dict)
        ssh_config = mock.MagicMock()
        ssh_config.lookup.return_value = _SSH_CONFIG
        client = mock.MagicMock()
        client.get_env.return_value = {'HOME': '/home/alice', 'USER': 'alice'}
        client.get_remote_home_dir.return_value = '/home/alice'
        saved = (skypilot_config._GLOBAL_CONFIG_PATH,
                 skypilot_config._PROJECT_CONFIG_PATH,
                 skypilot_config._global_config_context)
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop(skypilot_config.ENV_VAR_GLOBAL_CONFIG, None)
        try:
            skypilot_config._GLOBAL_CONFIG_PATH = str(config_path)
            skypilot_config._PROJECT_CONFIG_PATH = str(tmp_path / 'none.yaml')
            skypilot_config._global_config_context = (
                skypilot_config.ConfigContext())
            skypilot_config.reload_config()
            with mock.patch.object(pbs_utils, 'get_pbs_ssh_config',
                                   return_value=ssh_config), \
                 mock.patch.object(pbs_utils, 'make_client_from_ssh_config',
                                   return_value=client), \
                 mock.patch.object(pbs_utils, 'get_pbs_nodes_info',
                                   return_value=[_node('n1', queue='gpuq')]), \
                 mock.patch.object(pbs_utils, '_is_routing_queue',
                                   return_value=False), \
                 mock.patch.object(pbs_utils, 'get_queues',
                                   return_value=['workq', 'gpuq']):
                resources = resources_lib.Resources(
                    cloud=clouds.PBS(),
                    instance_type=instance_type,
                    region='c1',
                    zone=zone,
                    _cluster_config_overrides=overrides)
                return clouds.PBS().make_deploy_resources_variables(
                    resources, mock.MagicMock(), clouds.Region('c1'),
                    [clouds.Zone(zone)] if zone else None, 1)
        finally:
            (skypilot_config._GLOBAL_CONFIG_PATH,
             skypilot_config._PROJECT_CONFIG_PATH,
             skypilot_config._global_config_context) = saved
            env.stop()
            skypilot_config.reload_config()

    def test_basic_variables(self, tmp_path):
        deploy_vars = self._deploy_vars(tmp_path, {})
        assert deploy_vars['pbs_cluster'] == 'c1'
        assert deploy_vars['pbs_queue'] == 'gpuq'
        assert deploy_vars['cpus'] == '8.0'
        assert deploy_vars['memory'] == '32.0'
        assert deploy_vars['accelerator_count'] == '2'
        assert deploy_vars['accelerator_type'] == 'A100'
        assert deploy_vars['gpu_type_resource'] == 'gpu_type'
        assert deploy_vars['gpu_resource'] == 'ngpus'
        assert deploy_vars['ssh_hostname'] == 'login.example.com'
        assert deploy_vars['ssh_user'] == 'alice'
        assert deploy_vars['pbs_private_key'] == '/home/user/.ssh/id_rsa'
        assert deploy_vars['pbs_control_path'] == '/home/user/.ssh/login-abc'
        assert deploy_vars['sky_base_dir'] == '/home/alice'
        # A pinned queue waits as long as it takes.
        assert deploy_vars['provision_timeout'] == 24 * 60 * 60
        assert deploy_vars['qsub_options'] == {}
        assert deploy_vars['place'] is None
        assert deploy_vars['pbs_tmpdir'] is None

    def test_workdir_tmpdir_and_options_precedence(self, tmp_path):
        config = {
            'pbs': {
                'qsub_options': {
                    'A': 'global',
                    'm': 'n'
                },
                'select_resources': {
                    'tmp_size': '100gb'
                },
                'place': 'pack',
                'cluster_configs': {
                    'c1': {
                        'workdir': '/scratch/$USER',
                        'tmpdir': '/local/$USER',
                        'qsub_options': {
                            'A': 'cluster'
                        },
                        'queue_configs': {
                            'gpuq': {
                                'qsub_options': {
                                    'A': 'queue',
                                    'walltime': '2:00:00'
                                },
                                'select_resources': {
                                    'tmp_size': '400gb'
                                },
                                'place': 'scatter:excl',
                            }
                        }
                    }
                }
            }
        }
        jsonschema.validate(config, schemas.get_config_schema())
        deploy_vars = self._deploy_vars(
            tmp_path,
            config,
            overrides={'pbs': {
                'qsub_options': {
                    'walltime': '1:00:00'
                }
            }})
        assert deploy_vars['sky_base_dir'] == '/scratch/alice'
        assert deploy_vars['pbs_tmpdir'] == '/local/$USER'
        assert deploy_vars['qsub_options'] == {
            'A': 'queue',
            'm': 'n',
            'walltime': '1:00:00',
        }
        assert deploy_vars['select_resources'] == {'tmp_size': '400gb'}
        assert deploy_vars['place'] == 'scatter:excl'

    def test_quota_wins_over_qsub_options(self, tmp_path):
        config = {
            'pbs': {
                'qsub_options': {
                    'A': 'from-options'
                },
                'quota': {
                    'account': 'acct',
                    'project': 'proj'
                },
            }
        }
        deploy_vars = self._deploy_vars(tmp_path, config)
        assert deploy_vars['qsub_options'] == {'A': 'acct', 'P': 'proj'}

    def test_gpu_queue_map_requests_untyped_gpus(self, tmp_path):
        config = {'pbs': {'gpu_queue_map': {'A100': 'gpuq'}}}
        deploy_vars = self._deploy_vars(tmp_path, config)
        assert deploy_vars['accelerator_type'] is None
        assert deploy_vars['gpu_type_resource'] is None
        assert deploy_vars['accelerator_count'] == '2'

    def test_unpinned_queue_short_timeout(self, tmp_path):
        deploy_vars = self._deploy_vars(tmp_path, {}, zone=None)
        assert deploy_vars['provision_timeout'] == 120
        # Default queue first.
        assert deploy_vars['pbs_queue'] == 'workq'


class TestCredentials:

    def test_missing_config_file(self):
        with mock.patch.object(pbs_utils,
                               'get_pbs_ssh_config',
                               side_effect=FileNotFoundError()):
            ok, reason = clouds.PBS._check_compute_credentials()
        assert not ok
        assert '~/.pbs/config' in reason

    def test_enabled_with_warnings(self):
        ssh_config = mock.MagicMock()
        ssh_config.lookup.return_value = _SSH_CONFIG
        client = mock.MagicMock()
        client.info.return_value = 'Server\n'
        client.get_env.return_value = {'HOME': '/home/alice'}
        client.check_dir_shared_fs.return_value = 'ext2/ext3'
        client.check_sshd_available.return_value = True
        with mock.patch.object(pbs_utils, 'get_pbs_ssh_config',
                               return_value=ssh_config), \
             mock.patch.object(clouds.PBS, 'existing_allowed_clusters',
                               return_value=['c1']), \
             mock.patch.object(pbs_utils, 'make_client_from_ssh_config',
                               return_value=client), \
             mock.patch.object(skypilot_config, 'get_effective_region_config',
                               return_value=None):
            ok, ctx2text = clouds.PBS._check_compute_credentials()
        assert ok
        assert 'enabled' in ctx2text['c1']
        assert 'not a shared filesystem' in ctx2text['c1']
        client.check_dir_shared_fs.assert_called_once_with('/home/alice')

    def test_unreachable_cluster(self):
        ssh_config = mock.MagicMock()
        ssh_config.lookup.return_value = _SSH_CONFIG
        client = mock.MagicMock()
        client.info.side_effect = exceptions.CommandError(
            255, 'qstat', 'no', 'route')
        with mock.patch.object(pbs_utils, 'get_pbs_ssh_config',
                               return_value=ssh_config), \
             mock.patch.object(clouds.PBS, 'existing_allowed_clusters',
                               return_value=['c1']), \
             mock.patch.object(pbs_utils, 'make_client_from_ssh_config',
                               return_value=client):
            ok, ctx2text = clouds.PBS._check_compute_credentials()
        assert not ok
        assert ctx2text['c1'].startswith('disabled')

    def test_missing_user(self):
        ssh_config = mock.MagicMock()
        ssh_config.lookup.return_value = {'hostname': 'login'}
        with mock.patch.object(pbs_utils, 'get_pbs_ssh_config',
                               return_value=ssh_config), \
             mock.patch.object(clouds.PBS, 'existing_allowed_clusters',
                               return_value=['c1']):
            ok, ctx2text = clouds.PBS._check_compute_credentials()
        assert not ok
        assert 'User is missing' in ctx2text['c1']


class TestValidateRegionZone:

    def test_validation(self):
        with mock.patch.object(pbs_utils, 'get_all_pbs_cluster_names',
                               return_value=['c1']), \
             mock.patch.object(clouds.PBS, 'existing_allowed_clusters',
                               return_value=['c1']), \
             mock.patch.object(pbs_utils, 'get_queues',
                               return_value=['workq']):
            assert clouds.PBS().validate_region_zone('c1',
                                                     'workq') == ('c1', 'workq')
            with pytest.raises(ValueError, match='not found in PBS config'):
                clouds.PBS().validate_region_zone('c2', None)
            with pytest.raises(ValueError, match='Queue'):
                clouds.PBS().validate_region_zone('c1', 'nope')
            with pytest.raises(ValueError, match='without specifying'):
                clouds.PBS().validate_region_zone(None, 'workq')


class TestSchema:

    def test_workspace_pbs_config(self):
        config = {
            'workspaces': {
                'ws': {
                    'pbs': {
                        'allowed_clusters': ['c1'],
                        'quota': {
                            'project': 'p'
                        },
                        'cluster_configs': {
                            'c1': {
                                'queue_configs': {
                                    'q': {
                                        'qsub_options': {
                                            'walltime': '1:00:00'
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        jsonschema.validate(config, schemas.get_config_schema())

    @pytest.mark.parametrize('bad', [
        {
            'pbs': {
                'qsub_options': {
                    'A': 'x\nqdel'
                }
            }
        },
        {
            'pbs': {
                'quota': {
                    'account': ''
                }
            }
        },
        {
            'pbs': {
                'select_resources': {
                    'a': 'b:c'
                }
            }
        },
        {
            'pbs': {
                'unknown': 1
            }
        },
        {
            'workspaces': {
                'ws': {
                    'pbs': {
                        'workdir': '/x'
                    }
                }
            }
        },
    ])
    def test_rejected(self, bad):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schemas.get_config_schema())

    def test_effective_quota_value(self, tmp_path):
        config_path = tmp_path / 'config.yaml'
        config_path.write_text(
            yaml_utils.dump_yaml_str({
                'pbs': {
                    'quota': {
                        'account': 'global'
                    },
                    'cluster_configs': {
                        'c1': {
                            'quota': {
                                'account': 'cluster'
                            },
                            'queue_configs': {
                                'q': {
                                    'quota': {
                                        'account': 'queue'
                                    }
                                }
                            }
                        }
                    }
                }
            }))
        saved = (skypilot_config._GLOBAL_CONFIG_PATH,
                 skypilot_config._PROJECT_CONFIG_PATH,
                 skypilot_config._global_config_context)
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop(skypilot_config.ENV_VAR_GLOBAL_CONFIG, None)
        try:
            skypilot_config._GLOBAL_CONFIG_PATH = str(config_path)
            skypilot_config._PROJECT_CONFIG_PATH = str(tmp_path / 'none.yaml')
            skypilot_config._global_config_context = (
                skypilot_config.ConfigContext())
            skypilot_config.reload_config()
            get = skypilot_config.get_effective_pbs_quota_value
            assert get('account') == 'global'
            assert get('account', cluster='c1') == 'cluster'
            assert get('account', cluster='c1', queue='q') == 'queue'
            assert get('account', cluster='c1', queue='other') == 'cluster'
            assert get('project', cluster='c1', queue='q') is None
            # A task override applies at the scope it is written at: the
            # queue-level server value still outranks a cloud-level one.
            assert get('account',
                       cluster='c1',
                       queue='q',
                       override_configs={'pbs': {
                           'quota': {
                               'account': 'task'
                           }
                       }}) == 'queue'
            assert get('account',
                       cluster='c1',
                       queue='q',
                       override_configs={
                           'pbs': {
                               'cluster_configs': {
                                   'c1': {
                                       'queue_configs': {
                                           'q': {
                                               'quota': {
                                                   'account': 'task'
                                               }
                                           }
                                       }
                                   }
                               }
                           }
                       }) == 'task'
        finally:
            (skypilot_config._GLOBAL_CONFIG_PATH,
             skypilot_config._PROJECT_CONFIG_PATH,
             skypilot_config._global_config_context) = saved
            env.stop()
            skypilot_config.reload_config()


class TestInfraDisplay:

    def test_queue_is_hidden_in_infra_string(self):
        info = infra_utils.InfraInfo('pbs', 'mycluster', 'gpuq')
        assert info.formatted_str() == 'pbs (mycluster)'

    def test_optimizer_hides_instance_type(self):
        resources = resources_lib.Resources(cloud=clouds.PBS(),
                                            instance_type='4CPU--16GB',
                                            region='c1')
        with mock.patch.object(pbs_cloud.PBS,
                               'validate_region_zone',
                               return_value=('c1', None)):
            simple = resources_utils.format_resource(resources,
                                                     simplified_only=True)
        assert '4CPU--16GB' not in simple

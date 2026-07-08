"""Tests for per-workspace remote_identity resolution and schema."""
from unittest import mock

import jsonschema
import pytest

from sky import skypilot_config
from sky.clouds import kubernetes as kubernetes_cloud
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import config_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import yaml_utils


def _make_config(config_dict):
    return config_utils.Config(config_dict)


class TestWorkspaceRemoteIdentityResolution:
    """Test that remote_identity resolves workspace-first, then global."""

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_workspace_overrides_global_aws(self, mock_config, mock_workspace):
        """AWS workspace remote_identity takes precedence over global."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'aws': {
                'remote_identity': 'arn:aws:iam::123:role/global-role',
            },
            'workspaces': {
                'team-a': {
                    'aws': {
                        'remote_identity': 'arn:aws:iam::123:role/team-a-role',
                    }
                }
            }
        })

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='aws',
            keys=('remote_identity',),
            region=None,
            default_value=None)
        assert result == 'arn:aws:iam::123:role/team-a-role'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_falls_back_to_global_when_no_workspace_override(
            self, mock_config, mock_workspace):
        """No workspace override — falls back to global."""
        mock_workspace.return_value = 'team-b'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'remote_identity': 'global-sa',
            },
            'workspaces': {
                'team-b': {
                    'kubernetes': {}
                }
            }
        })

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='kubernetes',
            keys=('remote_identity',),
            region=None,
            default_value=None)
        assert result == 'global-sa'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_returns_default_when_no_config(self, mock_config, mock_workspace):
        """No workspace or global config — returns default_value."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({})

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='gcp',
            keys=('remote_identity',),
            region=None,
            default_value='LOCAL_CREDENTIALS')
        assert result == 'LOCAL_CREDENTIALS'

    @mock.patch.object(skypilot_config, 'get_active_workspace')
    @mock.patch.object(skypilot_config, '_get_loaded_config')
    def test_k8s_workspace_remote_identity(self, mock_config, mock_workspace):
        """K8s workspace remote_identity resolves correctly."""
        mock_workspace.return_value = 'team-a'
        mock_config.return_value = _make_config({
            'kubernetes': {
                'remote_identity': 'default-sa',
            },
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': 'team-a-sa',
                    }
                }
            }
        })

        result = skypilot_config.get_effective_workspace_region_config(
            cloud='kubernetes',
            keys=('remote_identity',),
            region=None,
            default_value=None)
        assert result == 'team-a-sa'


class TestWorkspaceRemoteIdentitySchema:
    """Test that workspace schema accepts remote_identity fields."""

    def _get_schema(self):
        return schemas.get_config_schema()

    def test_aws_remote_identity_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'aws': {
                        'remote_identity': 'arn:aws:iam::123:role/team-a',
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_gcp_remote_identity_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'gcp': {
                        'remote_identity': 'SERVICE_ACCOUNT',
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_k8s_remote_identity_string_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': 'team-a-sa',
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_k8s_remote_identity_dict_in_workspace(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': {
                            'eks-cluster': 'team-a-eks-sa',
                            'onprem-cluster': 'team-a-onprem-sa',
                        },
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_k8s_remote_identity_in_context_configs(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'context_configs': {
                            'my-cluster': {
                                'remote_identity': 'cluster-specific-sa',
                            }
                        }
                    }
                }
            }
        }
        jsonschema.validate(config, schema)

    def test_invalid_type_rejected(self):
        schema = self._get_schema()
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {
                        'remote_identity': 12345,
                    }
                }
            }
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, schema)


class TestWorkspaceRemoteIdentityCallSite:
    """Prove the call-site switch actually changes provisioning behavior.

    TestWorkspaceRemoteIdentityResolution above exercises the resolver
    (get_effective_workspace_region_config) in isolation, but that function
    predates this change. This class drives the real config all the way
    through Kubernetes.make_deploy_resources_variables() and asserts the
    service account that lands on the pod spec.

    Scope: this guards the Kubernetes call site
    (sky/clouds/kubernetes.py make_deploy_resources_variables) specifically.
    Reverting *that* call site (get_effective_workspace_region_config ->
    get_effective_region_config) makes the first test below fail, because the
    workspace block would no longer be consulted. The AWS / credential-expiry
    call sites in backend_utils.py are not independently unit-covered here;
    they are exercised by the schema + resolver tests above and by the
    test_workspace_k8s_remote_identity / test_workspace_multiple_aws_profiles
    smoke tests.
    """

    def _make_resources(self):
        resources = mock.MagicMock()
        resources.instance_type = '2CPU--4GB'
        resources.accelerators = None
        resources.use_spot = False
        resources.region = 'test-context'
        resources.zone = None
        resources.cluster_config_overrides = {}
        resources.image_id = None
        resources.requires_fuse = False
        resources.network_tier = resources_utils.NetworkTier.BEST
        # setattr avoids MagicMock's auto-assertion detection on the name.
        setattr(resources, 'assert_launchable', lambda: resources)
        return resources

    def _deploy_vars(self, tmp_path, config_dict, active_workspace):
        """Load a real config, then call make_deploy_resources_variables.

        Uses real config loading (write to tmp file + reload) so that the
        workspace-aware resolution at the call site is actually exercised; the
        only thing mocked about resolution is the active workspace selection.
        The remaining mocks stand in for cluster/network introspection that is
        unrelated to remote_identity.
        """
        config_path = tmp_path / 'config.yaml'
        config_path.write_text(yaml_utils.dump_yaml_str(config_dict))

        saved_global = skypilot_config._GLOBAL_CONFIG_PATH
        saved_project = skypilot_config._PROJECT_CONFIG_PATH
        saved_ctx = skypilot_config._global_config_context
        try:
            skypilot_config._GLOBAL_CONFIG_PATH = str(config_path)
            skypilot_config._PROJECT_CONFIG_PATH = str(tmp_path /
                                                       'nonexistent.yaml')
            skypilot_config._global_config_context = (
                skypilot_config.ConfigContext())
            skypilot_config.reload_config()

            region = mock.MagicMock()
            region.name = 'test-context'

            net_type = (
                kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE)
            port_mode = mock.MagicMock()
            port_mode.value = 'portforward'

            with mock.patch.object(
                    skypilot_config, 'get_active_workspace',
                    return_value=active_workspace), \
                 mock.patch.object(
                     kubernetes_utils, 'get_kubernetes_nodes',
                     return_value=[]), \
                 mock.patch.object(
                     kubernetes_utils,
                     'get_current_kube_config_context_name',
                     return_value='test-context'), \
                 mock.patch.object(
                     kubernetes_utils, 'get_kube_config_context_namespace',
                     return_value='default'), \
                 mock.patch.object(
                     kubernetes_utils, 'get_accelerator_label_keys',
                     return_value=[]), \
                 mock.patch.object(
                     kubernetes_utils, 'is_kubeconfig_exec_auth',
                     return_value=(False, None)), \
                 mock.patch(
                     'sky.provision.kubernetes.network_utils.get_port_mode',
                     return_value=port_mode), \
                 mock.patch('sky.catalog.get_image_id_from_tag',
                            return_value='test-image:latest'), \
                 mock.patch.object(
                     kubernetes_cloud.Kubernetes, '_detect_network_type',
                     return_value=(net_type, None)):
                cloud = kubernetes_cloud.Kubernetes()
                return cloud.make_deploy_resources_variables(
                    resources=self._make_resources(),
                    cluster_name=resources_utils.ClusterName(
                        display_name='test-cluster',
                        name_on_cloud='test-cluster'),
                    region=region,
                    zones=None,
                    num_nodes=1,
                    dryrun=False)
        finally:
            skypilot_config._GLOBAL_CONFIG_PATH = saved_global
            skypilot_config._PROJECT_CONFIG_PATH = saved_project
            skypilot_config._global_config_context = saved_ctx

    def test_workspace_remote_identity_reaches_pod_service_account(
            self, tmp_path):
        """team-b's override lands as the pod's serviceAccountName."""
        config = {
            'workspaces': {
                # No override -> default identity.
                'team-a': {
                    'kubernetes': {}
                },
                # Own service account.
                'team-b': {
                    'kubernetes': {
                        'remote_identity': 'team-b-sa',
                    }
                },
            }
        }
        deploy_vars = self._deploy_vars(tmp_path,
                                        config,
                                        active_workspace='team-b')
        assert deploy_vars['k8s_service_account_name'] == 'team-b-sa'

    def test_workspace_without_override_uses_default_sa(self, tmp_path):
        """Control group: team-a does not leak team-b's identity."""
        config = {
            'workspaces': {
                'team-a': {
                    'kubernetes': {}
                },
                'team-b': {
                    'kubernetes': {
                        'remote_identity': 'team-b-sa',
                    }
                },
            }
        }
        deploy_vars = self._deploy_vars(tmp_path,
                                        config,
                                        active_workspace='team-a')
        assert (deploy_vars['k8s_service_account_name'] ==
                kubernetes_utils.DEFAULT_SERVICE_ACCOUNT_NAME)
        assert deploy_vars['k8s_service_account_name'] != 'team-b-sa'

"""`remote_identity: NONE`: pods get no Kubernetes API credentials.

Drives the real config layers (server file, client override, task override)
through Kubernetes.make_deploy_resources_variables(); only cluster and network
introspection is mocked. Every refusal is paired with the same override
without NONE being accepted, so a test cannot pass by refusing everything.
"""
import pickle
from unittest import mock

import pytest

from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky import skypilot_config
from sky.clouds import kubernetes as kubernetes_cloud
from sky.provision.azure import config as azure_config
from sky.provision.kubernetes import config as kubernetes_config
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import yaml_utils

_CTX = 'test-context'
_NONE = schemas.RemoteIdentityOptions.NONE.value
_DEFAULT_SA = kubernetes_utils.DEFAULT_SERVICE_ACCOUNT_NAME
_TOKEN_VOLUME = {
    'name': 'tok',
    'projected': {
        'sources': [{
            'serviceAccountToken': {
                'path': 'token'
            }
        }]
    }
}


@pytest.fixture
def server_config(tmp_path, monkeypatch):
    """Loads `config` as the server's own config for the test."""

    def load(config):
        path = tmp_path / 'config.yaml'
        path.write_text(yaml_utils.dump_yaml_str(config))
        monkeypatch.setattr(skypilot_config, '_GLOBAL_CONFIG_PATH', str(path))
        monkeypatch.setattr(skypilot_config, '_PROJECT_CONFIG_PATH',
                            str(tmp_path / 'nonexistent.yaml'))
        monkeypatch.setattr(skypilot_config, '_global_config_context',
                            skypilot_config.ConfigContext())
        skypilot_config.reload_config()

    yield load
    monkeypatch.undo()
    skypilot_config.reload_config()


def _resources(task_config=None, kubernetes_identity=None):
    resources = mock.MagicMock()
    resources.instance_type = '2CPU--4GB'
    resources.accelerators = None
    resources.use_spot = False
    resources.region = _CTX
    resources.zone = None
    resources.cluster_config_overrides = task_config or {}
    resources.image_id = None
    resources.requires_fuse = False
    resources.kubernetes_identity = kubernetes_identity
    resources.network_tier = resources_utils.NetworkTier.BEST
    setattr(resources, 'assert_launchable', lambda: resources)
    return resources


def _deploy_vars(task_config=None,
                 client_config=None,
                 display_name='test-cluster',
                 kubernetes_identity=None):
    region = mock.MagicMock()
    region.name = _CTX
    port_mode = mock.MagicMock()
    port_mode.value = 'portforward'
    net = kubernetes_utils.KubernetesHighPerformanceNetworkType.NONE
    with mock.patch.object(kubernetes_utils, 'get_kubernetes_nodes',
                           return_value=[]), \
            mock.patch.object(kubernetes_utils,
                              'get_current_kube_config_context_name',
                              return_value=_CTX), \
            mock.patch.object(kubernetes_utils,
                              'get_kube_config_context_namespace',
                              return_value='default'), \
            mock.patch.object(kubernetes_utils, 'get_accelerator_label_keys',
                              return_value=[]), \
            mock.patch.object(kubernetes_utils, 'is_kubeconfig_exec_auth',
                              return_value=(False, None)), \
            mock.patch('sky.provision.kubernetes.network_utils.get_port_mode',
                       return_value=port_mode), \
            mock.patch('sky.catalog.get_image_id_from_tag',
                       return_value='test-image:latest'), \
            mock.patch.object(kubernetes_cloud.Kubernetes,
                              '_detect_network_type',
                              return_value=(net, None)), \
            skypilot_config.override_skypilot_config(client_config):
        return kubernetes_cloud.Kubernetes().make_deploy_resources_variables(
            resources=_resources(task_config, kubernetes_identity),
            cluster_name=resources_utils.ClusterName(display_name=display_name,
                                                     name_on_cloud='c-1'),
            region=region,
            zones=None,
            num_nodes=1,
            dryrun=False)


def _identity(task_config=None, client_config=None, **kwargs):
    v = _deploy_vars(task_config, client_config, **kwargs)
    return (v['k8s_service_account_name'], v['k8s_automount_sa_token'],
            v['k8s_remote_identity_none'])


def _k8s(**fields):
    return {'kubernetes': fields}


_NONE_IDENTITY = (_DEFAULT_SA, 'false', True)
_DEFAULT_IDENTITY = (_DEFAULT_SA, 'true', False)


class TestResolution:

    def test_server_none_gives_the_pod_no_token(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        assert _identity() == _NONE_IDENTITY

    def test_default_keeps_the_token(self, server_config):
        server_config({})
        assert _identity() == _DEFAULT_IDENTITY

    def test_none_for_this_context_only(self, server_config):
        server_config(_k8s(context_configs={_CTX: {'remote_identity': _NONE}}))
        assert _identity() == _NONE_IDENTITY

    def test_context_pattern_dict(self, server_config):
        server_config(_k8s(remote_identity={'test-*': _NONE}))
        assert _identity() == _NONE_IDENTITY

    def test_a_task_may_tighten_to_none(self, server_config):
        server_config({})
        assert _identity(task_config=_k8s(
            remote_identity=_NONE)) == _NONE_IDENTITY

    def test_a_client_may_tighten_to_none(self, server_config):
        server_config({})
        assert _identity(client_config=_k8s(
            remote_identity=_NONE)) == _NONE_IDENTITY


class TestNoneIsSticky:
    """A requester layer cannot loosen a NONE the server set."""

    @pytest.mark.parametrize('layer', ['task_config', 'client_config'])
    def test_overriding_server_none_is_refused(self, server_config, layer):
        server_config(_k8s(remote_identity=_NONE))
        with pytest.raises(exceptions.InvalidCloudConfigs,
                           match='cannot override it'):
            _identity(**{layer: _k8s(remote_identity='SERVICE_ACCOUNT')})

    @pytest.mark.parametrize('layer', ['task_config', 'client_config'])
    def test_the_same_override_without_none_is_accepted(self, server_config,
                                                        layer):
        server_config({})
        assert _identity(**{layer: _k8s(
            remote_identity='SERVICE_ACCOUNT')}) == _DEFAULT_IDENTITY

    def test_a_client_override_of_a_context_none_is_refused(
            self, server_config):
        """The merge lets client context_configs win; NONE must not."""
        server_config(_k8s(context_configs={_CTX: {'remote_identity': _NONE}}))
        with pytest.raises(exceptions.InvalidCloudConfigs,
                           match='cannot override it'):
            _identity(client_config=_k8s(
                context_configs={_CTX: {
                    'remote_identity': 'SERVICE_ACCOUNT'
                }}))


@pytest.mark.parametrize('spec,key', [
    ({
        'serviceAccountName': 'other-sa'
    }, 'serviceAccountName'),
    ({
        'automountServiceAccountToken': True
    }, 'automountServiceAccountToken'),
    ({
        'volumes': [_TOKEN_VOLUME]
    }, 'projected serviceAccountToken'),
])
class TestPodConfigCannotHandBackAToken:

    def test_refused_under_none(self, server_config, spec, key):
        server_config(_k8s(remote_identity=_NONE))
        with pytest.raises(exceptions.InvalidCloudConfigs, match=key):
            _identity(task_config=_k8s(pod_config={'spec': spec}))

    def test_refused_when_the_task_itself_chose_none(self, server_config, spec,
                                                     key):
        server_config({})
        with pytest.raises(exceptions.InvalidCloudConfigs, match=key):
            _identity(task_config=_k8s(remote_identity=_NONE,
                                       pod_config={'spec': spec}))

    def test_accepted_without_none(self, server_config, spec, key):
        del key
        server_config({})
        _identity(task_config=_k8s(pod_config={'spec': spec}))


class TestExemptions:

    def test_a_controller_ignores_none(self, server_config):
        """A controller keeps its identity, so its roles are bootstrapped."""
        server_config(_k8s(remote_identity=_NONE))
        v = _deploy_vars(display_name='sky-jobs-controller-abcd1234')
        assert v['k8s_service_account_name'] == (
            kubernetes_utils.CONTROLLER_SERVICE_ACCOUNT_NAME)
        assert v['k8s_automount_sa_token'] == 'true'
        assert v['k8s_remote_identity_none'] is False
        assert v['k8s_is_controller'] is True

    def test_an_in_process_identity_overrides_none(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        assert _identity(kubernetes_identity='skypilot-proxy-agent') == (
            'skypilot-proxy-agent', 'true', False)


class TestAutodownUnsupportedUnderNone:

    def _unsupported(self, kubernetes_identity=None, task_config=None):
        with mock.patch.object(kubernetes_utils, 'get_spot_label',
                               return_value=(None, None)), \
                mock.patch.object(
                    kubernetes_cloud.Kubernetes, '_detect_network_type',
                    return_value=(kubernetes_utils.
                                  KubernetesHighPerformanceNetworkType.NONE,
                                  None)):
            return (
                kubernetes_cloud.Kubernetes._unsupported_features_for_resources(
                    _resources(task_config, kubernetes_identity), region=_CTX))

    def test_none_cannot_autodown(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        reason = self._unsupported()[
            clouds.CloudImplementationFeatures.AUTODOWN]
        assert 'remote_identity: NONE' in reason and 'sky down' in reason

    def test_a_task_choosing_none_cannot_autodown(self, server_config):
        server_config({})
        assert clouds.CloudImplementationFeatures.AUTODOWN in self._unsupported(
            task_config=_k8s(remote_identity=_NONE))

    def test_default_can_autodown(self, server_config):
        server_config({})
        assert clouds.CloudImplementationFeatures.AUTODOWN not in (
            self._unsupported())

    def test_an_exempt_cluster_can_autodown(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        assert clouds.CloudImplementationFeatures.AUTODOWN not in (
            self._unsupported(kubernetes_identity='skypilot-proxy-agent'))


class TestBootstrap:
    """Keyed on the identity, not the account name NONE keeps."""

    def _bootstrap(self, remote_identity_none):
        cfg = mock.MagicMock()
        cfg.provider_config = {
            'namespace': 'default',
            'context': _CTX,
            'remote_identity_none': remote_identity_none,
        }
        cfg.node_config = {'spec': {'serviceAccountName': _DEFAULT_SA}}
        names = ('_configure_services', '_configure_autoscaler_service_account',
                 '_configure_autoscaler_role',
                 '_configure_autoscaler_role_binding',
                 '_configure_autoscaler_cluster_role',
                 '_configure_autoscaler_cluster_role_binding',
                 '_configure_skypilot_system_namespace',
                 '_configure_fuse_mounting')
        patches = {n: mock.patch.object(kubernetes_config, n) for n in names}
        mocks = {n: p.start() for n, p in patches.items()}
        try:
            kubernetes_config.bootstrap_instances(_CTX, 'c-1', cfg)
        finally:
            for p in patches.values():
                p.stop()
        return mocks

    def test_none_creates_the_account_and_binds_nothing(self):
        mocks = self._bootstrap(True)
        mocks['_configure_autoscaler_service_account'].assert_called_once()
        for name in ('_configure_autoscaler_role',
                     '_configure_autoscaler_role_binding',
                     '_configure_autoscaler_cluster_role',
                     '_configure_autoscaler_cluster_role_binding'):
            mocks[name].assert_not_called()

    def test_the_same_account_name_without_none_gets_its_roles(self):
        mocks = self._bootstrap(False)
        mocks['_configure_autoscaler_role'].assert_called()
        mocks['_configure_autoscaler_role_binding'].assert_called()


class TestResourcesCarryTheIdentity:

    def test_copy_and_pickle_keep_it(self):
        r = resources_lib.Resources(cpus='1')
        r.set_kubernetes_identity('skypilot-proxy-agent')
        assert r.copy(cpus='2').kubernetes_identity == 'skypilot-proxy-agent'
        assert pickle.loads(
            pickle.dumps(r)).kubernetes_identity == 'skypilot-proxy-agent'

    def test_a_handle_pickled_before_it_existed_loads_without_it(self):
        r = resources_lib.Resources(cpus='1')
        state = r.__getstate__() if hasattr(r, '__getstate__') else dict(
            r.__dict__)
        state = dict(state)
        state['_version'] = 35
        state.pop('_kubernetes_identity', None)
        old = resources_lib.Resources.__new__(resources_lib.Resources)
        old.__setstate__(state)
        assert old.kubernetes_identity is None

    def test_a_requester_cannot_set_it_through_yaml(self):
        r = resources_lib.Resources(cpus='1')
        r.set_kubernetes_identity('skypilot-proxy-agent')
        config = r.to_yaml_config()
        assert not any('kubernetes_identity' in k for k in config)
        with pytest.raises(Exception):
            resources_lib.Resources.from_yaml_config({
                'cpus': '1',
                '_kubernetes_identity': 'skypilot-proxy-agent'
            })


class TestServerConfigView:

    def test_the_server_layer_survives_a_client_override(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        with skypilot_config.override_skypilot_config(
                _k8s(remote_identity='SERVICE_ACCOUNT')):
            assert skypilot_config.get_effective_region_config(
                'kubernetes', ('remote_identity',)) == 'SERVICE_ACCOUNT'
            assert skypilot_config.get_server_workspace_region_config(
                'kubernetes', ('remote_identity',)) == _NONE
            with skypilot_config.override_skypilot_config(
                    _k8s(provision_timeout=10)):
                # A nested override keeps the outermost server config.
                assert skypilot_config.get_server_workspace_region_config(
                    'kubernetes', ('remote_identity',)) == _NONE
        assert skypilot_config.get_server_workspace_region_config(
            'kubernetes', ('remote_identity',)) == _NONE


class TestSchema:

    def test_kubernetes_accepts_none(self):
        common_utils.validate_schema(_k8s(remote_identity=_NONE),
                                     schemas.get_config_schema(), 'err: ')

    def test_enum_clouds_do_not(self):
        with pytest.raises(exceptions.InvalidSkyPilotConfigError):
            common_utils.validate_schema({'gcp': {
                'remote_identity': _NONE
            }}, schemas.get_config_schema(), 'err: ')

    def test_azure_keeps_reading_it_as_an_identity_name(self):
        # pylint: disable=protected-access
        assert azure_config._resolve_custom_managed_identity(
            _NONE, 'sub', 'rg').endswith('/userAssignedIdentities/NONE')

"""`remote_identity: NONE`: pods get no Kubernetes API credentials.

Drives the real config layers (server file, client override, task override)
through Kubernetes.make_deploy_resources_variables(); only cluster and network
introspection is mocked. Every refusal is paired with the same override
without NONE being accepted, so a test cannot pass by refusing everything.
"""
import pickle
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky import core
from sky import exceptions
from sky import execution
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
_K8S = kubernetes_cloud.Kubernetes()
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
        # Another test may have left this set; it would replace the file below.
        monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                           raising=False)
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
    resources.remote_identity_none_at_launch = None
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

    @pytest.mark.parametrize('spelling', ['none', 'None'])
    def test_none_is_case_insensitive(self, server_config, spelling):
        # Not a service account literally named `none`, with a token.
        server_config(_k8s(remote_identity=spelling))
        assert _identity() == _NONE_IDENTITY

    def test_workspace_none(self, server_config):
        server_config({'workspaces': {'default': _k8s(remote_identity=_NONE)}})
        assert _identity() == _NONE_IDENTITY


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

    def test_overriding_a_workspace_none_is_refused(self, server_config):
        server_config({'workspaces': {'default': _k8s(remote_identity=_NONE)}})
        with pytest.raises(exceptions.InvalidCloudConfigs,
                           match='cannot override it'):
            _identity(task_config=_k8s(remote_identity='SERVICE_ACCOUNT'))


@pytest.mark.parametrize('spec,key', [
    ({
        'serviceAccountName': 'other-sa'
    }, 'serviceAccountName'),
    ({
        'serviceAccount': 'other-sa'
    }, 'serviceAccount'),
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
        assert _identity(kubernetes_identity='fixed-sa') == ('fixed-sa', 'true',
                                                             False)

    def test_only_a_string_identity_exempts(self, server_config):
        # A MagicMock attribute is not None; it must not read as an exemption.
        server_config(_k8s(remote_identity=_NONE))
        resources = _resources()
        resources.kubernetes_identity = mock.MagicMock()
        assert kubernetes_cloud.Kubernetes.remote_identity_is_none(
            _CTX, resources)


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

    def test_a_cluster_launched_before_none_can_still_autodown(
            self, server_config):
        # Its pods have a token; today's NONE does not take it away.
        server_config(_k8s(remote_identity=_NONE))
        resources = _resources()
        resources.remote_identity_none_at_launch = False
        assert not kubernetes_cloud.Kubernetes.remote_identity_is_none(
            _CTX, resources)

    def test_a_cluster_launched_under_none_still_cannot(self, server_config):
        # Its pods have no token, whatever today's config or client says.
        server_config({})
        resources = _resources()
        resources.remote_identity_none_at_launch = True
        assert kubernetes_cloud.Kubernetes.remote_identity_is_none(
            _CTX, resources)

    def test_an_exempt_cluster_can_autodown(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        assert clouds.CloudImplementationFeatures.AUTODOWN not in (
            self._unsupported(kubernetes_identity='fixed-sa'))

    def _autostop_down(self):
        resources = _resources()
        resources.cloud = kubernetes_cloud.Kubernetes()
        handle = mock.MagicMock(launched_resources=resources)
        backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
        with mock.patch.object(core.backend_utils, 'check_cluster_available',
                               return_value=handle), \
                mock.patch.object(core.backend_utils,
                                  'get_backend_from_handle',
                                  return_value=backend), \
                mock.patch.object(kubernetes_utils, 'get_spot_label',
                                  return_value=(None, None)), \
                mock.patch.object(
                    kubernetes_cloud.Kubernetes, '_detect_network_type',
                    return_value=(kubernetes_utils.
                                  KubernetesHighPerformanceNetworkType.NONE,
                                  None)):
            core.autostop('c1', idle_minutes=1, down=True)
        return backend

    def test_autostop_down_on_a_none_cluster_names_the_reason(
            self, server_config):
        # `sky autostop --down` must say why, not only that it is refused.
        server_config(_k8s(remote_identity=_NONE))
        with pytest.raises(exceptions.NotSupportedError) as e:
            self._autostop_down()
        assert 'remote_identity: NONE' in str(e.value)

    def test_autostop_down_without_none_is_scheduled(self, server_config):
        server_config({})
        backend = self._autostop_down()
        backend.set_autostop.assert_called_once()


class TestCheckedAtSubmit:
    """jobs launch / serve up refuse what the cluster launch would refuse, so
    the controller does not retry a refused launch forever."""

    def _resources_on(self,
                      task_config=None,
                      region=_CTX,
                      kubernetes_identity=None,
                      cloud=_K8S):
        resources = _resources(task_config, kubernetes_identity)
        resources.region = region
        resources.cloud = cloud
        return resources

    def _check(self, task_config=None, **kwargs):
        resources = self._resources_on(task_config, **kwargs)
        with mock.patch.object(kubernetes_cloud.Kubernetes,
                               'existing_allowed_contexts',
                               return_value=[_CTX]) as contexts:
            kubernetes_cloud.Kubernetes.check_resources_keep_server_none(
                resources)
        return contexts

    def _check_task(self, *alternatives):
        task = mock.MagicMock()
        task.resources = list(alternatives)
        with mock.patch.object(kubernetes_cloud.Kubernetes,
                               'existing_allowed_contexts',
                               return_value=[_CTX]):
            kubernetes_cloud.Kubernetes.check_task_keeps_server_none(task)

    @pytest.mark.parametrize('region', [_CTX, None],
                             ids=['context', 'any-context'])
    def test_a_task_loosening_server_none_is_refused(self, server_config,
                                                     region):
        server_config(_k8s(remote_identity=_NONE))
        with pytest.raises(exceptions.InvalidCloudConfigs,
                           match='cannot override it'):
            self._check(_k8s(remote_identity='SERVICE_ACCOUNT'), region=region)

    def test_a_token_pod_config_is_refused(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        with pytest.raises(exceptions.InvalidCloudConfigs,
                           match='would give them some'):
            self._check(
                _k8s(
                    pod_config={'spec': {
                        'automountServiceAccountToken': True
                    }}))

    def test_a_task_that_keeps_none_passes(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        self._check()

    def test_the_same_task_without_server_none_passes(self, server_config):
        server_config({})
        self._check(_k8s(remote_identity='SERVICE_ACCOUNT'))

    def test_an_unpinned_task_is_left_to_the_launch(self, server_config):
        # It may land on another cloud; contexts are not even listed, so a
        # server without the kubernetes package is unaffected.
        server_config(_k8s(remote_identity=_NONE))
        contexts = self._check(_k8s(remote_identity='SERVICE_ACCOUNT'),
                               region=None,
                               cloud=None)
        contexts.assert_not_called()

    def test_refused_only_when_every_alternative_is(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        loosening = _k8s(remote_identity='SERVICE_ACCOUNT')
        k8s = self._resources_on(loosening)
        self._check_task(k8s, self._resources_on(loosening, cloud=clouds.AWS()))
        with pytest.raises(exceptions.InvalidCloudConfigs,
                           match='cannot override it'):
            self._check_task(k8s, self._resources_on(loosening))

    def test_contexts_that_cannot_be_listed_are_left_to_the_launch(
            self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        resources = self._resources_on(_k8s(remote_identity='SERVICE_ACCOUNT'),
                                       region=None)
        with mock.patch.object(kubernetes_cloud.Kubernetes,
                               'existing_allowed_contexts',
                               side_effect=ImportError('no kubernetes')):
            kubernetes_cloud.Kubernetes.check_resources_keep_server_none(
                resources)

    def _check_request(self, client_config=None, contexts=None):
        side = {
            'side_effect': contexts
        } if isinstance(contexts, Exception) else {
            'return_value': [_CTX]
        }
        with mock.patch.object(kubernetes_cloud.Kubernetes,
                               'existing_allowed_contexts', **side), \
                skypilot_config.override_skypilot_config(client_config):
            kubernetes_cloud.Kubernetes.check_request_keeps_server_none()

    @pytest.mark.parametrize('client,match', [
        (_k8s(remote_identity='SERVICE_ACCOUNT'), 'cannot override it'),
        (_k8s(pod_config={'spec': {
            'automountServiceAccountToken': True
        }}), 'would give them some'),
    ],
                             ids=['remote_identity', 'pod_config'])
    def test_a_request_config_loosening_server_none_is_refused(
            self, server_config, client, match):
        # Whatever the task: this config goes to the controller with the job.
        server_config(_k8s(remote_identity=_NONE))
        with pytest.raises(exceptions.InvalidCloudConfigs, match=match):
            self._check_request(client)

    def test_a_request_config_that_keeps_none_passes(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        self._check_request()

    def test_the_same_request_config_without_server_none_passes(
            self, server_config):
        server_config({})
        self._check_request(_k8s(remote_identity='SERVICE_ACCOUNT'))

    def test_a_request_check_without_contexts_passes(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        self._check_request(_k8s(remote_identity='SERVICE_ACCOUNT'),
                            contexts=ImportError('no kubernetes'))

    def test_another_cloud_is_not_checked(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        self._check(_k8s(remote_identity='SERVICE_ACCOUNT'), cloud=clouds.AWS())

    def test_an_exempt_cluster_is_not_checked(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        self._check(_k8s(remote_identity='SERVICE_ACCOUNT'),
                    kubernetes_identity='fixed-sa')


class TestLeakGuard:
    """The jobs controller skips its autodown guard only for NONE clusters."""

    def _handle(self):
        handle = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
        handle.launched_resources = _resources()
        handle.launched_resources.cloud = kubernetes_cloud.Kubernetes()
        return handle

    def test_skipped_for_a_none_cluster(self, server_config):
        server_config(_k8s(remote_identity=_NONE))
        assert execution._autodown_needs_identity_it_lacks(self._handle())

    def test_kept_for_a_cluster_with_an_identity(self, server_config):
        server_config({})
        assert not execution._autodown_needs_identity_it_lacks(self._handle())


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
        r.set_kubernetes_identity('fixed-sa')
        r.set_remote_identity_none_at_launch(True)
        for copied in (r.copy(cpus='2'), pickle.loads(pickle.dumps(r))):
            assert copied.kubernetes_identity == 'fixed-sa'
            assert copied.remote_identity_none_at_launch is True

    def test_a_handle_pickled_before_it_existed_loads_without_it(self):
        r = resources_lib.Resources(cpus='1')
        state = r.__getstate__() if hasattr(r, '__getstate__') else dict(
            r.__dict__)
        state = dict(state)
        state['_version'] = 35
        state.pop('_kubernetes_identity', None)
        state.pop('_remote_identity_none', None)
        old = resources_lib.Resources.__new__(resources_lib.Resources)
        old.__setstate__(state)
        assert old.kubernetes_identity is None
        assert old.remote_identity_none_at_launch is None

    def test_a_requester_cannot_set_it_through_yaml(self):
        r = resources_lib.Resources(cpus='1')
        r.set_kubernetes_identity('fixed-sa')
        config = r.to_yaml_config()
        assert not any('kubernetes_identity' in k for k in config)
        with pytest.raises(exceptions.InvalidSkyPilotConfigError):
            resources_lib.Resources.from_yaml_config({
                'cpus': '1',
                '_kubernetes_identity': 'fixed-sa'
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

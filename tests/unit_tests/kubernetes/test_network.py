"""Tests for `sky/provision/kubernetes/network.py`."""

import types
from unittest.mock import patch

import pytest

from sky.adaptors import kubernetes
from sky.provision.kubernetes import network
from sky.provision.kubernetes import network_utils

# What get_ingress_settings returns when kubernetes.ingress is unset.
_DEFAULT_SETTINGS = {
    'class_name': 'nginx',
    'controller_service': 'ingress-nginx-controller',
    'controller_namespace': 'ingress-nginx',
}


class TestOpenPortsUsingIngress:
    """Tests for `_open_ports_using_ingress`."""

    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.merge_custom_metadata')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.create_or_replace_namespaced_ingress')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.create_or_replace_namespaced_service')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.fill_ingress_template')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.get_ingress_controller_service')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.ingress_controller_exists')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_namespace_from_config')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_url_path_uses_provider_namespace_not_kubeconfig_default(
            self, mock_get_context, mock_get_ns_from_config, mock_kubeconfig_ns,
            mock_ingress_exists, mock_controller_svc, mock_fill_template,
            mock_create_service, mock_create_ingress, mock_merge_metadata):
        """The Ingress URL path must reference the same namespace as the Service.

        Regression: when a workspace/per-context override sets
        `provider_config['namespace']` to something other than the kubeconfig
        context's default namespace, the URL path embedded in the Ingress rule
        must match the Service's actual namespace, otherwise nginx routes to a
        non-existent service. Both must agree by construction (same variable).
        """
        provider_config = {
            'context': 'shared-ctx',
            'namespace': 'team-a',
        }
        mock_get_context.return_value = 'shared-ctx'
        mock_get_ns_from_config.return_value = 'team-a'
        mock_kubeconfig_ns.return_value = 'kubeconfig-default'
        mock_ingress_exists.return_value = True
        mock_fill_template.return_value = {
            'services_spec': {},
            'ingress_spec': {
                'metadata': {}
            },
        }

        network._open_ports_using_ingress(  # pylint: disable=protected-access
            cluster_name_on_cloud='cluster0',
            ports=[8080],
            provider_config=provider_config,
        )

        mock_fill_template.assert_called_once()
        call_kwargs = mock_fill_template.call_args.kwargs
        assert call_kwargs['namespace'] == 'team-a', (
            'Service is created with the provider_config namespace.')
        service_details = call_kwargs['service_details']
        assert len(service_details) == 1
        _, _, url_path = service_details[0]
        assert 'team-a' in url_path, (
            f'URL path must reference the provider_config namespace, '
            f'got: {url_path!r}')
        assert 'kubeconfig-default' not in url_path, (
            f'URL path must not fall back to the kubeconfig context default '
            f'when provider_config has an explicit namespace, '
            f'got: {url_path!r}')
        mock_kubeconfig_ns.assert_not_called()

    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.merge_custom_metadata')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.create_or_replace_namespaced_ingress')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.create_or_replace_namespaced_service')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.fill_ingress_template')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.get_ingress_controller_service')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.ingress_controller_exists')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_namespace_from_config')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_url_path_namespace_matches_service_namespace_for_all_ports(
            self, mock_get_context, mock_get_ns_from_config, mock_kubeconfig_ns,
            mock_ingress_exists, mock_controller_svc, mock_fill_template,
            mock_create_service, mock_create_ingress, mock_merge_metadata):
        """Multiple ports all share the same namespace in their URL paths."""
        provider_config = {
            'context': 'shared-ctx',
            'namespace': 'team-b',
        }
        mock_get_context.return_value = 'shared-ctx'
        mock_get_ns_from_config.return_value = 'team-b'
        mock_kubeconfig_ns.return_value = 'kubeconfig-default'
        mock_ingress_exists.return_value = True
        mock_fill_template.return_value = {
            'services_spec': {},
            'ingress_spec': {
                'metadata': {}
            },
        }

        network._open_ports_using_ingress(  # pylint: disable=protected-access
            cluster_name_on_cloud='cluster0',
            ports=[8080, 8081, 8082],
            provider_config=provider_config,
        )

        call_kwargs = mock_fill_template.call_args.kwargs
        for _, _, url_path in call_kwargs['service_details']:
            assert 'team-b' in url_path, (
                f'Every port URL path must use the resolved namespace, '
                f'got: {url_path!r}')

    @patch('sky.provision.kubernetes.network.network_utils'
           '.ingress_controller_exists')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.get_ingress_settings')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_checks_the_configured_ingress_class(self, mock_get_context,
                                                 mock_settings,
                                                 mock_ingress_exists):
        """The IngressClass we look for is the one we will write, not nginx."""
        mock_get_context.return_value = 'ctx'
        mock_settings.return_value = {'class_name': 'traefik'}
        mock_ingress_exists.return_value = False

        with pytest.raises(Exception, match='traefik'):
            network._open_ports_using_ingress(  # pylint: disable=protected-access
                cluster_name_on_cloud='cluster0',
                ports=[8080],
                provider_config={
                    'context': 'ctx',
                    'namespace': 'default'
                },
            )

        mock_ingress_exists.assert_called_once_with('ctx', 'traefik')

    @patch('sky.provision.kubernetes.network.network_utils'
           '.create_or_replace_namespaced_ingress')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.fill_ingress_template')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.get_ingress_controller_service')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.ingress_controller_exists')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_raises_when_controller_service_missing(self, mock_get_context,
                                                    mock_ingress_exists,
                                                    mock_controller_svc,
                                                    mock_fill_template,
                                                    mock_create_ingress):
        """An IngressClass alone is not enough: endpoints need the Service."""
        mock_get_context.return_value = 'ctx'
        mock_ingress_exists.return_value = True
        mock_controller_svc.return_value = None

        with pytest.raises(Exception,
                           match='ingress-nginx/ingress-nginx-controller'):
            network._open_ports_using_ingress(  # pylint: disable=protected-access
                cluster_name_on_cloud='cluster0',
                ports=[8080],
                provider_config={
                    'context': 'ctx',
                    'namespace': 'default'
                },
            )

        # Nothing is created: Ingress objects that cannot resolve are worse
        # than a failed launch.
        mock_fill_template.assert_not_called()
        mock_create_ingress.assert_not_called()


class TestQueryPortsForIngress:
    """Tests for `_query_ports_for_ingress`."""

    @patch('sky.provision.kubernetes.network.logger')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.get_ingress_external_ip_and_ports')
    def test_warns_when_endpoints_cannot_be_resolved(self, mock_get_ip,
                                                     mock_logger):
        """Status polling must not raise, but it must say why it is empty."""
        mock_get_ip.return_value = (None, None)

        result = network._query_ports_for_ingress(  # pylint: disable=protected-access
            cluster_name_on_cloud='cluster0',
            ports=[8080],
            provider_config={
                'context': 'ctx',
                'namespace': 'default'
            },
        )

        assert result == {}
        warning = mock_logger.warning.call_args[0][0]
        assert 'ingress-nginx/ingress-nginx-controller' in warning

    @patch('sky.provision.kubernetes.network.network_utils'
           '.get_ingress_external_ip_and_ports')
    def test_overrides_are_passed_through(self, mock_get_ip):
        """The endpoint lookup must see the overrides open_ports used.

        Otherwise a cluster launched with a per-launch ingress override has
        its Ingress created against one controller and its endpoints
        resolved from another.
        """
        mock_get_ip.return_value = ('1.2.3.4', (80, 443))
        overrides = {'kubernetes': {'ingress': {'controller_service': 'other'}}}

        network._query_ports_for_ingress(  # pylint: disable=protected-access
            cluster_name_on_cloud='cluster0',
            ports=[8080],
            provider_config={
                'context': 'ctx',
                'namespace': 'default',
                'cluster_config_overrides': overrides,
            },
        )

        assert mock_get_ip.call_args.args[1] == overrides


def _fake_region_config(ingress=None):
    """Stub for skypilot_config.get_effective_region_config."""

    def fake_get(cloud,
                 keys,
                 region=None,
                 default_value=None,
                 override_configs=None,
                 merge_dicts=False):
        del cloud, region, override_configs, merge_dicts  # Unused.
        if keys == ('ingress',):
            return default_value if ingress is None else ingress
        return default_value

    return fake_get


def _service(name, ip='1.2.3.4'):
    return types.SimpleNamespace(
        metadata=types.SimpleNamespace(name=name, annotations=None),
        spec=types.SimpleNamespace(external_i_ps=None, ports=[]),
        status=types.SimpleNamespace(load_balancer=types.SimpleNamespace(
            ingress=[types.SimpleNamespace(ip=ip, hostname=None)])),
    )


@patch('sky.provision.kubernetes.network_utils.kubernetes_utils'
       '.get_cleaned_context_and_cloud_str',
       return_value=('ctx', 'kubernetes'))
@patch('sky.provision.kubernetes.network_utils.skypilot_config'
       '.get_effective_region_config')
class TestIngressSettings:
    """`kubernetes.ingress` falls back to the ingress-nginx defaults."""

    def test_defaults_when_unset(self, mock_get, mock_ctx):
        mock_get.side_effect = _fake_region_config()
        assert network_utils.get_ingress_settings('ctx') == _DEFAULT_SETTINGS

    def test_partial_config_keeps_other_defaults(self, mock_get, mock_ctx):
        mock_get.side_effect = _fake_region_config({'class_name': 'traefik'})
        assert network_utils.get_ingress_settings('ctx') == {
            **_DEFAULT_SETTINGS,
            'class_name': 'traefik',
        }


@patch('sky.provision.kubernetes.network_utils.kubernetes_utils'
       '.get_cleaned_context_and_cloud_str',
       return_value=('ctx', 'kubernetes'))
@patch('sky.provision.kubernetes.network_utils.skypilot_config'
       '.get_effective_region_config')
class TestFillIngressTemplate:
    """Generated Ingresses carry the configured ingressClassName."""

    @staticmethod
    def _render():
        return network_utils.fill_ingress_template(
            namespace='ns',
            context='ctx',
            service_details=[('svc', 8080, 'skypilot/ns/c/8080')],
            ingress_name='ing',
            selector_key='k',
            selector_value='v',
        )

    def test_default_class_is_nginx(self, mock_get, mock_ctx):
        mock_get.side_effect = _fake_region_config()
        content = self._render()
        assert content['ingress_spec']['spec']['ingressClassName'] == 'nginx'

    def test_configured_class_name_is_rendered(self, mock_get, mock_ctx):
        mock_get.side_effect = _fake_region_config({'class_name': 'traefik'})
        content = self._render()
        assert content['ingress_spec']['spec']['ingressClassName'] == 'traefik'


class TestGetIngressExternalIpAndPorts:
    """Endpoints resolve from the configured controller Service."""

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    @patch('sky.provision.kubernetes.network_utils.get_ingress_settings')
    def test_looks_up_the_configured_service(self, mock_settings, mock_api):
        mock_settings.return_value = {
            'class_name': 'traefik',
            'controller_service': 'traefik',
            'controller_namespace': 'traefik-system',
        }
        read_service = mock_api.return_value.read_namespaced_service
        read_service.return_value = _service('traefik', '10.0.0.2')

        ip, ports = network_utils.get_ingress_external_ip_and_ports('ctx')

        assert (ip, ports) == ('10.0.0.2', None)
        # Read by name, so the call names both the Service and its namespace.
        assert read_service.call_args.args[:2] == ('traefik', 'traefik-system')

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    @patch('sky.provision.kubernetes.network_utils.get_ingress_settings')
    def test_missing_namespace_is_not_an_error(self, mock_settings, mock_api):
        """A 404 means "not installed", not a crash."""
        mock_settings.return_value = _DEFAULT_SETTINGS
        api_exception = kubernetes.kubernetes.client.ApiException(status=404)
        mock_api.return_value.read_namespaced_service.side_effect = (
            api_exception)

        assert network_utils.get_ingress_controller_service('ctx') is None

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    @patch('sky.provision.kubernetes.network_utils.get_ingress_settings')
    def test_other_api_errors_propagate(self, mock_settings, mock_api):
        mock_settings.return_value = _DEFAULT_SETTINGS
        api_exception = kubernetes.kubernetes.client.ApiException(status=403)
        mock_api.return_value.read_namespaced_service.side_effect = (
            api_exception)

        with pytest.raises(kubernetes.kubernetes.client.ApiException):
            network_utils.get_ingress_controller_service('ctx')

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    @patch('sky.provision.kubernetes.network_utils.get_ingress_settings')
    def test_missing_service(self, mock_settings, mock_api):
        mock_settings.return_value = _DEFAULT_SETTINGS
        mock_api.return_value.read_namespaced_service.side_effect = (
            kubernetes.kubernetes.client.ApiException(status=404))

        assert network_utils.get_ingress_external_ip_and_ports('ctx') == (None,
                                                                          None)

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    @patch('sky.provision.kubernetes.network_utils.get_ingress_settings')
    def test_overrides_reach_the_service_lookup(self, mock_settings, mock_api):
        """Per-launch overrides must select the Service endpoints come from."""
        mock_settings.return_value = _DEFAULT_SETTINGS
        mock_api.return_value.read_namespaced_service.return_value = _service(
            'ingress-nginx-controller', '10.0.0.1')
        overrides = {'kubernetes': {'ingress': {'controller_service': 'other'}}}

        network_utils.get_ingress_external_ip_and_ports('ctx', overrides)

        assert mock_settings.call_args.args[1] == overrides

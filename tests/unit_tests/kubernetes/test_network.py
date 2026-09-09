"""Tests for `sky/provision/kubernetes/network.py`."""

from unittest.mock import patch

import pytest

from sky import exceptions
from sky.provision.kubernetes import network


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
           '.ingress_controller_exists')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_namespace_from_config')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_url_path_uses_provider_namespace_not_kubeconfig_default(
            self, mock_get_context, mock_get_ns_from_config, mock_kubeconfig_ns,
            mock_ingress_exists, mock_fill_template, mock_create_service,
            mock_create_ingress, mock_merge_metadata):
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
           '.ingress_controller_exists')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_kube_config_context_namespace')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_namespace_from_config')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_url_path_namespace_matches_service_namespace_for_all_ports(
            self, mock_get_context, mock_get_ns_from_config, mock_kubeconfig_ns,
            mock_ingress_exists, mock_fill_template, mock_create_service,
            mock_create_ingress, mock_merge_metadata):
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


class TestCleanupPortsUsingIngress:
    """Tests for `_cleanup_ports_for_ingress`."""

    @patch('sky.provision.kubernetes.network.network_utils'
           '.delete_namespaced_ingress')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.delete_namespaced_service')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_namespace_from_config')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_already_deleted_service_does_not_strand_the_rest(
            self, mock_get_context, mock_get_ns_from_config,
            mock_delete_service, mock_delete_ingress):
        """One already-deleted service must not abort the whole cleanup.

        Regression: `network_utils.delete_namespaced_service` turns a 404
        into `PortDoesNotExistError`, so a single port service that was
        already gone raised out of the loop. The remaining port services
        and the shared ingress were then never deleted. The caller in
        `cloud_vm_ray_backend` catches that error, logs at debug level and
        marks ports as cleaned up, so those resources leak silently.
        """
        provider_config = {'context': 'ctx', 'namespace': 'ns'}
        mock_get_context.return_value = 'ctx'
        mock_get_ns_from_config.return_value = 'ns'

        def _delete(context, namespace, service_name):
            del context, namespace  # Unused.
            if service_name.endswith('--8080'):
                raise exceptions.PortDoesNotExistError(
                    'Port 8080 does not exist.')

        mock_delete_service.side_effect = _delete

        network._cleanup_ports_for_ingress(  # pylint: disable=protected-access
            cluster_name_on_cloud='cluster0',
            ports=[8080, 8081, 8082],
            provider_config=provider_config,
        )

        attempted = [
            call.kwargs['service_name']
            for call in mock_delete_service.call_args_list
        ]
        assert attempted == [
            'cluster0--skypilot-svc--8080',
            'cluster0--skypilot-svc--8081',
            'cluster0--skypilot-svc--8082',
        ], ('Every port service must still be attempted after an earlier one '
            f'is found already deleted, got: {attempted}')
        mock_delete_ingress.assert_called_once()

    @patch('sky.provision.kubernetes.network.network_utils'
           '.delete_namespaced_ingress')
    @patch('sky.provision.kubernetes.network.network_utils'
           '.delete_namespaced_service')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_namespace_from_config')
    @patch('sky.provision.kubernetes.network.kubernetes_utils'
           '.get_context_from_config')
    def test_genuine_service_delete_failure_still_propagates(
            self, mock_get_context, mock_get_ns_from_config,
            mock_delete_service, mock_delete_ingress):
        """Only "already gone" is tolerated; real failures must surface.

        Tolerating every exception would hide a service that still exists
        but could not be deleted (RBAC, API server outage), which is the
        case teardown must not silently report as cleaned up.
        """
        provider_config = {'context': 'ctx', 'namespace': 'ns'}
        mock_get_context.return_value = 'ctx'
        mock_get_ns_from_config.return_value = 'ns'
        mock_delete_service.side_effect = RuntimeError('api server is down')

        with pytest.raises(RuntimeError, match='api server is down'):
            network._cleanup_ports_for_ingress(  # pylint: disable=protected-access
                cluster_name_on_cloud='cluster0',
                ports=[8080, 8081],
                provider_config=provider_config,
            )

        mock_delete_ingress.assert_not_called()

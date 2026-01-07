"""Tests for Kubernetes network utils."""
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import exceptions
from sky.provision.kubernetes import network_utils
from sky.utils import kubernetes_enums


class TestGetPortMode:
    """Tests for get_port_mode function."""

    def test_kind_context_returns_ingress(self):
        """Test that Kind context always returns INGRESS mode."""
        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes_utils.'
                'get_current_kube_config_context_name',
                return_value='kind-skypilot'):
            result = network_utils.get_port_mode(None, 'test-context')
            assert result == kubernetes_enums.KubernetesPortMode.INGRESS

    def test_explicit_mode_loadbalancer(self):
        """Test explicit LOADBALANCER mode."""
        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes_utils.'
                'get_current_kube_config_context_name',
                return_value='gke_project_zone_cluster'):
            result = network_utils.get_port_mode('loadbalancer', 'test-context')
            assert result == kubernetes_enums.KubernetesPortMode.LOADBALANCER

    def test_explicit_mode_ingress(self):
        """Test explicit INGRESS mode."""
        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes_utils.'
                'get_current_kube_config_context_name',
                return_value='gke_project_zone_cluster'):
            result = network_utils.get_port_mode('ingress', 'test-context')
            assert result == kubernetes_enums.KubernetesPortMode.INGRESS

    def test_explicit_mode_podip(self):
        """Test explicit PODIP mode."""
        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes_utils.'
                'get_current_kube_config_context_name',
                return_value='gke_project_zone_cluster'):
            result = network_utils.get_port_mode('podip', 'test-context')
            assert result == kubernetes_enums.KubernetesPortMode.PODIP

    def test_default_mode_from_config(self):
        """Test default mode is read from config."""
        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes_utils.'
                'get_current_kube_config_context_name',
                return_value='gke_project_zone_cluster'), \
             patch('sky.provision.kubernetes.network_utils.skypilot_config.'
                   'get_effective_region_config',
                   return_value='ingress'):
            result = network_utils.get_port_mode(None, 'test-context')
            assert result == kubernetes_enums.KubernetesPortMode.INGRESS

    def test_invalid_mode_raises_error(self):
        """Test that invalid mode raises ValueError."""
        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes_utils.'
                'get_current_kube_config_context_name',
                return_value='gke_project_zone_cluster'):
            with pytest.raises(ValueError) as excinfo:
                network_utils.get_port_mode('invalid_mode', 'test-context')
            assert 'invalid port mode' in str(excinfo.value).lower()


class TestCreateOrReplaceNamespacedIngress:
    """Tests for create_or_replace_namespaced_ingress function."""

    def test_creates_new_ingress_when_not_exists(self):
        """Test that new ingress is created when it doesn't exist."""
        mock_networking_api = MagicMock()
        mock_networking_api.read_namespaced_ingress.side_effect = \
            network_utils.kubernetes.kubernetes.client.ApiException(status=404)

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            network_utils.create_or_replace_namespaced_ingress(
                namespace='default',
                context='test-context',
                ingress_name='test-ingress',
                ingress_spec={'apiVersion': 'networking.k8s.io/v1'})

            mock_networking_api.create_namespaced_ingress.assert_called_once()

    def test_replaces_existing_ingress(self):
        """Test that existing ingress is replaced."""
        mock_networking_api = MagicMock()
        mock_networking_api.read_namespaced_ingress.return_value = MagicMock()

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            network_utils.create_or_replace_namespaced_ingress(
                namespace='default',
                context='test-context',
                ingress_name='test-ingress',
                ingress_spec={'apiVersion': 'networking.k8s.io/v1'})

            mock_networking_api.replace_namespaced_ingress.assert_called_once()

    def test_raises_on_api_error(self):
        """Test that API errors (non-404) are raised."""
        mock_networking_api = MagicMock()
        mock_networking_api.read_namespaced_ingress.side_effect = \
            network_utils.kubernetes.kubernetes.client.ApiException(status=500)

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            with pytest.raises(
                    network_utils.kubernetes.kubernetes.client.ApiException):
                network_utils.create_or_replace_namespaced_ingress(
                    namespace='default',
                    context='test-context',
                    ingress_name='test-ingress',
                    ingress_spec={})


class TestDeleteNamespacedIngress:
    """Tests for delete_namespaced_ingress function."""

    def test_deletes_existing_ingress(self):
        """Test that existing ingress is deleted."""
        mock_networking_api = MagicMock()

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            network_utils.delete_namespaced_ingress(namespace='default',
                                                    context='test-context',
                                                    ingress_name='test-ingress')

            mock_networking_api.delete_namespaced_ingress.assert_called_once()

    def test_raises_port_does_not_exist_on_404(self):
        """Test that 404 raises PortDoesNotExistError."""
        mock_networking_api = MagicMock()
        mock_networking_api.delete_namespaced_ingress.side_effect = \
            network_utils.kubernetes.kubernetes.client.ApiException(status=404)

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            with pytest.raises(exceptions.PortDoesNotExistError):
                network_utils.delete_namespaced_ingress(
                    namespace='default',
                    context='test-context',
                    ingress_name='cluster-name--8080')

    def test_raises_on_other_api_errors(self):
        """Test that non-404 API errors are raised."""
        mock_networking_api = MagicMock()
        mock_networking_api.delete_namespaced_ingress.side_effect = \
            network_utils.kubernetes.kubernetes.client.ApiException(status=500)

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            with pytest.raises(
                    network_utils.kubernetes.kubernetes.client.ApiException):
                network_utils.delete_namespaced_ingress(
                    namespace='default',
                    context='test-context',
                    ingress_name='test-ingress')


class TestCreateOrReplaceNamespacedService:
    """Tests for create_or_replace_namespaced_service function."""

    def test_creates_new_service_when_not_exists(self):
        """Test that new service is created when it doesn't exist."""
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_service.side_effect = \
            network_utils.kubernetes.kubernetes.client.ApiException(status=404)

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            network_utils.create_or_replace_namespaced_service(
                namespace='default',
                context='test-context',
                service_name='test-service',
                service_spec={'apiVersion': 'v1'})

            mock_core_api.create_namespaced_service.assert_called_once()

    def test_replaces_existing_service(self):
        """Test that existing service is replaced."""
        mock_core_api = MagicMock()
        mock_core_api.read_namespaced_service.return_value = MagicMock()

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            network_utils.create_or_replace_namespaced_service(
                namespace='default',
                context='test-context',
                service_name='test-service',
                service_spec={'apiVersion': 'v1'})

            mock_core_api.replace_namespaced_service.assert_called_once()


class TestDeleteNamespacedService:
    """Tests for delete_namespaced_service function."""

    def test_deletes_existing_service(self):
        """Test that existing service is deleted."""
        mock_core_api = MagicMock()

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            network_utils.delete_namespaced_service(context='test-context',
                                                    namespace='default',
                                                    service_name='test-service')

            mock_core_api.delete_namespaced_service.assert_called_once()

    def test_raises_port_does_not_exist_on_404(self):
        """Test that 404 raises PortDoesNotExistError."""
        mock_core_api = MagicMock()
        mock_core_api.delete_namespaced_service.side_effect = \
            network_utils.kubernetes.kubernetes.client.ApiException(status=404)

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            with pytest.raises(exceptions.PortDoesNotExistError):
                network_utils.delete_namespaced_service(
                    context='test-context',
                    namespace='default',
                    service_name='cluster-name--8080')


class TestIngressControllerExists:
    """Tests for ingress_controller_exists function."""

    def test_returns_true_when_nginx_exists(self):
        """Test returns True when nginx ingress class exists."""
        mock_networking_api = MagicMock()
        mock_ingress_class = MagicMock()
        mock_ingress_class.metadata.name = 'nginx'
        mock_networking_api.list_ingress_class.return_value.items = [
            mock_ingress_class
        ]

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            result = network_utils.ingress_controller_exists('test-context')
            assert result is True

    def test_returns_false_when_no_matching_class(self):
        """Test returns False when no matching ingress class exists."""
        mock_networking_api = MagicMock()
        mock_ingress_class = MagicMock()
        mock_ingress_class.metadata.name = 'traefik'
        mock_networking_api.list_ingress_class.return_value.items = [
            mock_ingress_class
        ]

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            result = network_utils.ingress_controller_exists('test-context')
            assert result is False

    def test_returns_false_when_no_ingress_classes(self):
        """Test returns False when no ingress classes exist."""
        mock_networking_api = MagicMock()
        mock_networking_api.list_ingress_class.return_value.items = []

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            result = network_utils.ingress_controller_exists('test-context')
            assert result is False

    def test_custom_ingress_class_name(self):
        """Test with custom ingress class name."""
        mock_networking_api = MagicMock()
        mock_ingress_class = MagicMock()
        mock_ingress_class.metadata.name = 'custom-ingress'
        mock_networking_api.list_ingress_class.return_value.items = [
            mock_ingress_class
        ]

        with patch(
                'sky.provision.kubernetes.network_utils.kubernetes.'
                'networking_api',
                return_value=mock_networking_api):
            result = network_utils.ingress_controller_exists(
                'test-context', ingress_class_name='custom-ingress')
            assert result is True


class TestGetIngressExternalIpAndPorts:
    """Tests for get_ingress_external_ip_and_ports function."""

    def test_returns_none_when_no_ingress_service(self):
        """Test returns (None, None) when no ingress service exists."""
        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_service.return_value.items = []

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip, ports = network_utils.get_ingress_external_ip_and_ports(
                'test-context')
            assert ip is None
            assert ports is None

    def test_returns_load_balancer_ip(self):
        """Test returns load balancer IP when available."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.metadata.name = 'ingress-nginx-controller'
        mock_service.status.load_balancer.ingress = [MagicMock(ip='1.2.3.4')]
        mock_core_api.list_namespaced_service.return_value.items = [
            mock_service
        ]

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip, ports = network_utils.get_ingress_external_ip_and_ports(
                'test-context')
            assert ip == '1.2.3.4'
            assert ports is None

    def test_returns_load_balancer_hostname(self):
        """Test returns load balancer hostname when IP is not available."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.metadata.name = 'ingress-nginx-controller'
        mock_service.status.load_balancer.ingress = [
            MagicMock(ip=None, hostname='lb.example.com')
        ]
        mock_core_api.list_namespaced_service.return_value.items = [
            mock_service
        ]

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip, ports = network_utils.get_ingress_external_ip_and_ports(
                'test-context')
            assert ip == 'lb.example.com'
            assert ports is None

    def test_returns_external_ip_and_node_ports(self):
        """Test returns external IP and node ports when no load balancer."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.metadata.name = 'ingress-nginx-controller'
        mock_service.metadata.annotations = None
        mock_service.status.load_balancer.ingress = None
        mock_service.spec.external_i_ps = ['10.0.0.1']

        mock_http_port = MagicMock()
        mock_http_port.name = 'http'
        mock_http_port.node_port = 30080

        mock_https_port = MagicMock()
        mock_https_port.name = 'https'
        mock_https_port.node_port = 30443

        mock_service.spec.ports = [mock_http_port, mock_https_port]
        mock_core_api.list_namespaced_service.return_value.items = [
            mock_service
        ]

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip, ports = network_utils.get_ingress_external_ip_and_ports(
                'test-context')
            assert ip == '10.0.0.1'
            assert ports == (30080, 30443)

    def test_returns_annotation_ip(self):
        """Test returns IP from annotation when no external IP."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.metadata.name = 'ingress-nginx-controller'
        mock_service.metadata.annotations = {
            'skypilot.co/external-ip': '5.6.7.8'
        }
        mock_service.status.load_balancer.ingress = None
        mock_service.spec.external_i_ps = None

        mock_http_port = MagicMock()
        mock_http_port.name = 'http'
        mock_http_port.node_port = 30080

        mock_https_port = MagicMock()
        mock_https_port.name = 'https'
        mock_https_port.node_port = 30443

        mock_service.spec.ports = [mock_http_port, mock_https_port]
        mock_core_api.list_namespaced_service.return_value.items = [
            mock_service
        ]

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip, ports = network_utils.get_ingress_external_ip_and_ports(
                'test-context')
            assert ip == '5.6.7.8'
            assert ports == (30080, 30443)

    def test_returns_localhost_when_no_ip_available(self):
        """Test returns localhost when no IP is available."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.metadata.name = 'ingress-nginx-controller'
        mock_service.metadata.annotations = None
        mock_service.status.load_balancer.ingress = None
        mock_service.spec.external_i_ps = None

        mock_http_port = MagicMock()
        mock_http_port.name = 'http'
        mock_http_port.node_port = 30080

        mock_https_port = MagicMock()
        mock_https_port.name = 'https'
        mock_https_port.node_port = 30443

        mock_service.spec.ports = [mock_http_port, mock_https_port]
        mock_core_api.list_namespaced_service.return_value.items = [
            mock_service
        ]

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip, ports = network_utils.get_ingress_external_ip_and_ports(
                'test-context')
            assert ip == 'localhost'
            assert ports == (30080, 30443)


class TestGetLoadbalancerIp:
    """Tests for get_loadbalancer_ip function."""

    def test_returns_ip_immediately(self):
        """Test returns IP when immediately available."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.status.load_balancer.ingress = [MagicMock(ip='1.2.3.4')]
        mock_core_api.read_namespaced_service.return_value = mock_service

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip = network_utils.get_loadbalancer_ip(context='test-context',
                                                   namespace='default',
                                                   service_name='test-service',
                                                   timeout=0)
            assert ip == '1.2.3.4'

    def test_returns_hostname_when_no_ip(self):
        """Test returns hostname when IP is not available."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.status.load_balancer.ingress = [
            MagicMock(ip=None, hostname='lb.example.com')
        ]
        mock_core_api.read_namespaced_service.return_value = mock_service

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip = network_utils.get_loadbalancer_ip(context='test-context',
                                                   namespace='default',
                                                   service_name='test-service',
                                                   timeout=0)
            assert ip == 'lb.example.com'

    def test_returns_none_when_no_ingress(self):
        """Test returns None when no ingress is assigned."""
        mock_core_api = MagicMock()
        mock_service = MagicMock()
        mock_service.status.load_balancer.ingress = None
        mock_core_api.read_namespaced_service.return_value = mock_service

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip = network_utils.get_loadbalancer_ip(context='test-context',
                                                   namespace='default',
                                                   service_name='test-service',
                                                   timeout=0)
            assert ip is None


class TestGetPodIp:
    """Tests for get_pod_ip function."""

    def test_returns_pod_ip(self):
        """Test returns pod IP when available."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.status.pod_ip = '10.0.0.5'
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip = network_utils.get_pod_ip(context='test-context',
                                          namespace='default',
                                          pod_name='test-pod')
            assert ip == '10.0.0.5'

    def test_returns_none_when_no_ip(self):
        """Test returns None when pod has no IP."""
        mock_core_api = MagicMock()
        mock_pod = MagicMock()
        mock_pod.status.pod_ip = None
        mock_core_api.read_namespaced_pod.return_value = mock_pod

        with patch('sky.provision.kubernetes.network_utils.kubernetes.core_api',
                   return_value=mock_core_api):
            ip = network_utils.get_pod_ip(context='test-context',
                                          namespace='default',
                                          pod_name='test-pod')
            assert ip is None

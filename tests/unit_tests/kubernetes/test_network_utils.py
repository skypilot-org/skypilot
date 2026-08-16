"""Tests for `sky/provision/kubernetes/network_utils.py`."""

from unittest import mock
from unittest.mock import patch

from sky.provision.kubernetes import network_utils


def _mock_ingress_controller_service(with_load_balancer_ingress: bool):
    service = mock.MagicMock()
    service.metadata.name = 'ingress-nginx-controller'
    service.metadata.annotations = None
    service.spec.external_i_ps = None
    http_port = mock.MagicMock(node_port=30080)
    http_port.name = 'http'
    https_port = mock.MagicMock(node_port=30443)
    https_port.name = 'https'
    service.spec.ports = [http_port, https_port]
    if with_load_balancer_ingress:
        service.status.load_balancer.ingress = [
            mock.MagicMock(ip='1.2.3.4', hostname=None)
        ]
    else:
        service.status.load_balancer.ingress = None
    return service


class TestGetIngressExternalIpAndPorts:
    """Tests for `get_ingress_external_ip_and_ports`."""

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    def test_controller_in_default_namespace(self, mock_core_api):
        """Fast path: the service is found in the default namespace."""
        api = mock_core_api.return_value
        api.list_namespaced_service.return_value.items = [
            _mock_ingress_controller_service(with_load_balancer_ingress=True)
        ]

        ip, ports = network_utils.get_ingress_external_ip_and_ports(
            context=None)

        assert ip == '1.2.3.4'
        assert ports is None
        api.list_namespaced_service.assert_called_once()
        assert api.list_namespaced_service.call_args.args[0] == 'ingress-nginx'
        api.list_service_for_all_namespaces.assert_not_called()

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    def test_controller_in_custom_namespace(self, mock_core_api):
        """The controller deployed in a custom namespace is still found.

        Regression test for #9150: `sky status --endpoints` reported no
        endpoints when ingress-nginx was deployed in a namespace other than
        the default `ingress-nginx`, because only that namespace was
        searched for the controller service.
        """
        api = mock_core_api.return_value
        api.list_namespaced_service.return_value.items = []
        api.list_service_for_all_namespaces.return_value.items = [
            _mock_ingress_controller_service(with_load_balancer_ingress=False)
        ]

        ip, ports = network_utils.get_ingress_external_ip_and_ports(
            context=None)

        assert ip == 'localhost'
        assert ports == (30080, 30443)
        call_kwargs = api.list_service_for_all_namespaces.call_args.kwargs
        assert call_kwargs['field_selector'] == (
            'metadata.name=ingress-nginx-controller')

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    def test_no_permission_to_list_all_namespaces(self, mock_core_api):
        """Without cluster-wide list permission, report no endpoints."""
        api = mock_core_api.return_value
        api.list_namespaced_service.return_value.items = []
        api.list_service_for_all_namespaces.side_effect = (
            network_utils.kubernetes.kubernetes.client.ApiException(
                status=403, reason='Forbidden'))

        assert network_utils.get_ingress_external_ip_and_ports(
            context=None) == (None, None)

    @patch('sky.provision.kubernetes.network_utils.kubernetes.core_api')
    def test_controller_not_deployed(self, mock_core_api):
        """No controller service anywhere: report no endpoints."""
        api = mock_core_api.return_value
        api.list_namespaced_service.return_value.items = []
        api.list_service_for_all_namespaces.return_value.items = []

        assert network_utils.get_ingress_external_ip_and_ports(
            context=None) == (None, None)

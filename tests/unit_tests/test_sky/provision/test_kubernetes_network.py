"""Tests for sky.provision.kubernetes.network."""
from unittest import mock

from sky.provision.kubernetes import network
from sky.provision.kubernetes import network_utils


def test_get_ingress_namespace_returns_default_when_unset(monkeypatch):
    """Without config, get_ingress_namespace() falls back to 'ingress-nginx'."""
    monkeypatch.setattr(
        'sky.skypilot_config.get_effective_region_config',
        lambda cloud, region, keys, default_value, **kwargs: default_value,
    )
    assert network_utils.get_ingress_namespace('ctx') == 'ingress-nginx'


def test_get_ingress_namespace_reads_configured_value(monkeypatch):
    """get_ingress_namespace() returns the user-configured namespace."""

    def fake_get(cloud, region, keys, default_value, **kwargs):
        assert cloud == 'kubernetes'
        assert region == 'my-ctx'
        assert keys == ('ingress_namespace',)
        return 'custom-ns'

    monkeypatch.setattr('sky.skypilot_config.get_effective_region_config',
                        fake_get)
    assert network_utils.get_ingress_namespace('my-ctx') == 'custom-ns'


def test_query_ports_for_ingress_uses_configured_namespace(monkeypatch):
    """_query_ports_for_ingress passes the configured namespace to the
    ingress lookup (instead of the hardcoded 'ingress-nginx')."""
    from sky.provision.kubernetes import utils as kubernetes_utils
    monkeypatch.setattr(kubernetes_utils,
                        'get_current_kube_config_context_name',
                        lambda: 'fallback-ctx')
    monkeypatch.setattr(kubernetes_utils, 'get_kube_config_context_namespace',
                        lambda ctx: 'workload-ns')
    monkeypatch.setattr(network_utils, 'get_ingress_namespace',
                        lambda context: 'custom-ingress-ns')

    captured = {}

    def fake_get_ingress(context, namespace='ingress-nginx'):
        captured['context'] = context
        captured['namespace'] = namespace
        return None, None  # no external ip -> early return

    monkeypatch.setattr(network_utils, 'get_ingress_external_ip_and_ports',
                        fake_get_ingress)

    result = network._query_ports_for_ingress(
        cluster_name_on_cloud='test-cluster',
        ports=[8080],
        provider_config={
            'context': 'my-ctx',
            'namespace': 'workload-ns',
        },
    )

    assert result == {}
    assert captured['context'] == 'my-ctx'
    assert captured['namespace'] == 'custom-ingress-ns'

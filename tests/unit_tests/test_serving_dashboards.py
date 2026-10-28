"""Checks for the serving-engine Grafana dashboards in the Helm chart."""
import json
import pathlib

_MANIFESTS = pathlib.Path(__file__).parents[2] / 'charts/skypilot/manifests'


def _load(name):
    return json.loads((_MANIFESTS / name).read_text())


def _panel_ids(dashboard):
    return {panel['id'] for panel in dashboard['panels']}


def test_sglang_dashboard_keeps_vllm_panel_ids():
    # A client can embed the same panel id from either dashboard.
    vllm = _load('vllm-serving-dashboard.json')
    sglang = _load('sglang-serving-dashboard.json')
    # Finish reason, prefill/decode time and max generation tokens have no
    # SGLang series.
    assert _panel_ids(sglang) == _panel_ids(vllm) - {11, 15, 16}
    assert sglang['uid'] == 'skypilot-sglang-serving'
    assert sglang['templating'] == vllm['templating']


def test_sglang_dashboard_reads_only_sglang_series():
    text = (_MANIFESTS / 'sglang-serving-dashboard.json').read_text()
    assert 'vllm' not in text.lower()
    assert 'sglang:token_usage' in text


def test_autoscaling_dashboard_reads_both_engines():
    text = (_MANIFESTS / 'autoscaling-dashboard.json').read_text()
    for vllm_series, sglang_series in (
        ('vllm:kv_cache_usage_perc', 'sglang:token_usage'),
        ('vllm:num_requests_waiting', 'sglang:num_queue_reqs'),
        ('vllm:request_success_total', 'sglang:num_requests_total'),
    ):
        assert vllm_series in text and sglang_series in text

"""/gpu-metrics carries workload KSM series; /endpoints-metrics federates
serving-engine metrics.

The serving dashboards (vLLM Serving, Autoscaling) read these series from the
central Prometheus: vllm:* via /endpoints-metrics, and Deployment replica
counts + the HPA target via the /gpu-metrics federation.
"""
from sky.metrics import utils as metrics_utils
from sky.server import metrics as server_metrics


def test_endpoint_metrics_carries_autoscaling_dashboard_series():
    joined = '\n'.join(metrics_utils.ENDPOINT_METRICS_MATCH_PATTERNS)
    # Serving-engine series + replica panels + the HPA threshold line.
    assert 'vllm:' in joined
    assert 'kube_deployment_' in joined
    assert 'kube_horizontalpodautoscaler_spec_target_metric' in joined


def test_gpu_metrics_keeps_gpu_semantics():
    # The workload KSM series exist solely for endpoint observability and
    # ride /endpoints-metrics, not the GPU federation.
    joined = '\n'.join(metrics_utils.GPU_METRICS_MATCH_PATTERNS)
    assert 'kube_deployment_' not in joined
    assert 'kube_horizontalpodautoscaler' not in joined


def test_no_per_pod_phase_series():
    # kube_pod_status_phase is the expensive per-pod family and has no
    # consumer; it must not ride either federation.
    for pats in (metrics_utils.GPU_METRICS_MATCH_PATTERNS,
                 metrics_utils.ENDPOINT_METRICS_MATCH_PATTERNS):
        assert 'kube_pod_status_phase' not in '\n'.join(pats)


def test_endpoint_metrics_route_registered():
    paths = {
        getattr(r, 'path', None) for r in server_metrics.metrics_app.routes
    }
    assert '/endpoints-metrics' in paths
    assert '/gpu-metrics' in paths
    assert hasattr(metrics_utils, 'get_endpoint_metrics_for_context')

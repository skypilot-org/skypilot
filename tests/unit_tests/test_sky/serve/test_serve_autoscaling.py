import time

from sky import backends
from sky import global_user_state
from sky import resources
from sky.serve import autoscalers
from sky.serve import constants as serve_constants
from sky.serve import controller as serve_controller
from sky.serve import replica_managers
from sky.serve import serve_utils
from sky.serve.service_spec import SkyServiceSpec
from sky.utils import common_utils


class DummyReplicaManager:

    def __init__(self, service_name, spec, version):
        self.service_name = service_name
        self.spec = spec
        self.version = version


def _build_request_service_spec(**overrides) -> SkyServiceSpec:
    data = {
        'readiness_path': '/',
        'initial_delay_seconds': serve_constants.DEFAULT_INITIAL_DELAY_SECONDS,
        'readiness_timeout_seconds':
            serve_constants.DEFAULT_READINESS_PROBE_TIMEOUT_SECONDS,
        'min_replicas': 2,
        'max_replicas': 10,
        'num_overprovision': 1,
        'target_qps_per_replica': 5,
    }
    data.update(overrides)
    return SkyServiceSpec(**data)


def _make_ready_replica(replica_id: int,
                        version: int) -> replica_managers.ReplicaInfo:
    info = replica_managers.ReplicaInfo(replica_id=replica_id,
                                        cluster_name=f'replica-{replica_id}',
                                        replica_port='8080',
                                        is_spot=False,
                                        location=None,
                                        version=version,
                                        resources_override=None)
    info.status_property.sky_launch_status = common_utils.ProcessStatus.SUCCEEDED
    info.status_property.service_ready_now = True
    info.status_property.first_ready_time = time.time()
    return info


def _build_request_autoscaler(**overrides) -> autoscalers.Autoscaler:
    spec = _build_request_service_spec(**overrides)
    return autoscalers.Autoscaler.from_spec('svc', spec)


def _set_requests(autoscaler: autoscalers.Autoscaler, count: int) -> None:
    autoscaler.request_timestamps = [0.0] * count


def _make_handle(accelerator: str) -> backends.CloudVmRayResourceHandle:
    launched_resources = resources.Resources(accelerators={accelerator: 1})
    return backends.CloudVmRayResourceHandle(
        cluster_name='dummy',
        cluster_name_on_cloud='dummy',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=launched_resources,
    )


def test_controller_autoscaler_latest_version_initialized(monkeypatch):
    """Controller init syncs autoscaler latest_version on restart."""

    monkeypatch.setattr(serve_controller.replica_managers,
                        'SkyPilotReplicaManager', DummyReplicaManager)

    spec = _build_request_service_spec()
    controller = serve_controller.SkyServeController('svc',
                                                     spec,
                                                     version=3,
                                                     host='127.0.0.1',
                                                     port=1234)
    assert controller._autoscaler.latest_version == 3


def test_controller_restart_no_scale_churn(monkeypatch):
    """Controller restart should not trigger scale-up churn."""

    monkeypatch.setattr(serve_controller.replica_managers,
                        'SkyPilotReplicaManager', DummyReplicaManager)

    spec = _build_request_service_spec()
    controller = serve_controller.SkyServeController('svc',
                                                     spec,
                                                     version=3,
                                                     host='127.0.0.1',
                                                     port=1234)

    replica_infos = [
        _make_ready_replica(1, version=3),
        _make_ready_replica(2, version=3),
        _make_ready_replica(3, version=3),
    ]
    decisions = controller._autoscaler.generate_scaling_decisions(
        replica_infos, active_versions=[3])
    scale_ups = [
        decision for decision in decisions
        if decision.operator == autoscalers.AutoscalerDecisionOperator.SCALE_UP
    ]
    assert scale_ups == []


def test_request_rate_hysteresis_delays_scale_down():
    """Downscale waits for hysteresis threshold before applying."""
    autoscaler = _build_request_autoscaler(min_replicas=1,
                                           max_replicas=5,
                                           downscale_delay_seconds=40)
    autoscaler.target_num_replicas = 2
    _set_requests(autoscaler, 300)

    autoscaler._set_target_num_replicas_with_hysteresis()
    assert autoscaler.target_num_replicas == 2
    autoscaler._set_target_num_replicas_with_hysteresis()
    assert autoscaler.target_num_replicas == 1


def test_request_rate_hysteresis_delays_scale_up():
    """Upscale waits for hysteresis threshold before applying."""
    autoscaler = _build_request_autoscaler(min_replicas=1,
                                           max_replicas=5,
                                           upscale_delay_seconds=40)
    autoscaler.target_num_replicas = 1
    _set_requests(autoscaler, 301)

    autoscaler._set_target_num_replicas_with_hysteresis()
    assert autoscaler.target_num_replicas == 1
    autoscaler._set_target_num_replicas_with_hysteresis()
    assert autoscaler.target_num_replicas == 2


def test_scale_to_zero_uses_faster_decision_interval():
    """Scale-to-zero uses the shorter decision interval."""
    autoscaler = _build_request_autoscaler(min_replicas=0,
                                           max_replicas=3,
                                           num_overprovision=0)
    _set_requests(autoscaler, 0)
    autoscaler._set_target_num_replicas_with_hysteresis()
    assert autoscaler.get_final_target_num_replicas() == 0
    assert (autoscaler.get_decision_interval() ==
            serve_constants.AUTOSCALER_NO_REPLICA_DECISION_INTERVAL_SECONDS)


def test_overprovision_scales_up_extra_replica():
    """Overprovision adds an extra scale-up beyond target."""
    autoscaler = _build_request_autoscaler(min_replicas=2,
                                           max_replicas=5,
                                           num_overprovision=1)
    _set_requests(autoscaler, 0)
    replicas = [
        _make_ready_replica(1, version=autoscaler.latest_version),
        _make_ready_replica(2, version=autoscaler.latest_version),
    ]
    decisions = autoscaler._generate_scaling_decisions(replicas)
    scale_ups = [
        decision for decision in decisions
        if decision.operator == autoscalers.AutoscalerDecisionOperator.SCALE_UP
    ]
    assert len(scale_ups) == 1


def test_instance_aware_autoscaler_scales_up(monkeypatch):
    """Instance-aware autoscaler scales up when demand exceeds capacity."""
    spec = _build_request_service_spec(
        load_balancing_policy='instance_aware_least_load',
        target_qps_per_replica={
            'A100': 10,
            'V100': 5
        },
        upscale_delay_seconds=20,
        num_overprovision=0,
    )
    autoscaler = autoscalers.Autoscaler.from_spec('svc', spec)
    _set_requests(autoscaler, 1200)

    handle_map = {
        'replica-1': _make_handle('A100'),
        'replica-2': _make_handle('V100'),
    }

    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        lambda name: handle_map.get(name))

    replicas = [
        _make_ready_replica(1, version=autoscaler.latest_version),
        _make_ready_replica(2, version=autoscaler.latest_version),
    ]
    decisions = autoscaler._generate_scaling_decisions(replicas)
    scale_ups = [
        decision for decision in decisions
        if decision.operator == autoscalers.AutoscalerDecisionOperator.SCALE_UP
    ]
    assert len(scale_ups) == 1


def test_fallback_autoscaler_keeps_base_ondemand():
    """Fallback autoscaler provisions base on-demand replicas."""
    spec = _build_request_service_spec(
        base_ondemand_fallback_replicas=1,
        min_replicas=1,
        target_qps_per_replica=5,
        num_overprovision=0,
    )
    autoscaler = autoscalers.Autoscaler.from_spec('svc', spec)
    _set_requests(autoscaler, 300)
    decisions = autoscaler._generate_scaling_decisions([])
    scale_ups = [
        decision for decision in decisions
        if decision.operator == autoscalers.AutoscalerDecisionOperator.SCALE_UP
    ]

    assert len(scale_ups) == 1
    assert (scale_ups[0].target ==
            autoscalers.FallbackRequestRateAutoscaler.ONDEMAND_OVERRIDE)


def test_select_outdated_replicas_rolling_update():
    """Rolling updates scale down all old replicas once new replicas are ready."""
    autoscaler = _build_request_autoscaler(min_replicas=2,
                                           max_replicas=4,
                                           num_overprovision=0)
    autoscaler.latest_version = 3
    autoscaler.update_mode = serve_utils.UpdateMode.ROLLING

    latest_ready = _make_ready_replica(1, version=3)
    latest_ready_two = _make_ready_replica(2, version=3)
    old_ready = _make_ready_replica(3, version=2)
    old_provisioning = replica_managers.ReplicaInfo(replica_id=4,
                                                    cluster_name='replica-4',
                                                    replica_port='8080',
                                                    is_spot=False,
                                                    location=None,
                                                    version=2,
                                                    resources_override=None)
    old_provisioning.status_property.sky_launch_status = (
        common_utils.ProcessStatus.RUNNING)

    replica_infos = [
        latest_ready,
        latest_ready_two,
        old_ready,
        old_provisioning,
    ]
    selected = autoscaler._select_outdated_replicas_to_scale_down(
        replica_infos, active_versions=[2, 3])

    assert set(selected) == {3, 4}


def test_select_outdated_replicas_blue_green():
    """Blue-green updates only scale down versions below active min."""
    autoscaler = _build_request_autoscaler(min_replicas=1,
                                           max_replicas=3,
                                           num_overprovision=0)
    autoscaler.latest_version = 4
    autoscaler.update_mode = serve_utils.UpdateMode.BLUE_GREEN

    old_replica = _make_ready_replica(1, version=2)
    active_replica = _make_ready_replica(2, version=3)
    replica_infos = [old_replica, active_replica]
    selected = autoscaler._select_outdated_replicas_to_scale_down(
        replica_infos, active_versions=[3])

    assert selected == [1]


def test_instance_aware_autoscaler_scales_down(monkeypatch):
    """Instance-aware autoscaler scales down when demand drops."""
    spec = _build_request_service_spec(
        load_balancing_policy='instance_aware_least_load',
        target_qps_per_replica={
            'A100': 10,
            'V100': 5
        },
        downscale_delay_seconds=20,
        min_replicas=1,
        num_overprovision=0,
    )
    autoscaler = autoscalers.Autoscaler.from_spec('svc', spec)
    autoscaler.target_num_replicas = 2
    _set_requests(autoscaler, 5)

    handle_map = {
        'replica-1': _make_handle('A100'),
        'replica-2': _make_handle('V100'),
    }

    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        lambda name: handle_map.get(name))

    replicas = [
        _make_ready_replica(1, version=autoscaler.latest_version),
        _make_ready_replica(2, version=autoscaler.latest_version),
    ]
    decisions = autoscaler._generate_scaling_decisions(replicas)
    scale_downs = [
        decision for decision in decisions if decision.operator ==
        autoscalers.AutoscalerDecisionOperator.SCALE_DOWN
    ]
    assert len(scale_downs) == 1


def test_fallback_autoscaler_dynamic_ondemand():
    """Dynamic fallback adds on-demand when spot capacity is insufficient."""
    spec = _build_request_service_spec(
        dynamic_ondemand_fallback=True,
        base_ondemand_fallback_replicas=0,
        min_replicas=2,
        target_qps_per_replica=5,
        num_overprovision=0,
    )
    autoscaler = autoscalers.Autoscaler.from_spec('svc', spec)
    _set_requests(autoscaler, 300)

    spot_replica = _make_ready_replica(1, version=autoscaler.latest_version)
    spot_replica.is_spot = True
    decisions = autoscaler._generate_scaling_decisions([spot_replica])
    scale_ups = [
        decision for decision in decisions
        if decision.operator == autoscalers.AutoscalerDecisionOperator.SCALE_UP
    ]

    targets = [decision.target for decision in scale_ups]
    assert len(targets) == 2
    assert autoscalers.FallbackRequestRateAutoscaler.SPOT_OVERRIDE in targets
    assert (autoscalers.FallbackRequestRateAutoscaler.ONDEMAND_OVERRIDE
            in targets)


def test_request_rate_target_clips_to_max():
    """Request rate target clips to max_replicas."""
    autoscaler = _build_request_autoscaler(min_replicas=1,
                                           max_replicas=3,
                                           target_qps_per_replica=1,
                                           num_overprovision=0)
    _set_requests(autoscaler, 1000)
    assert autoscaler._calculate_target_num_replicas() == 3


def test_request_rate_target_clips_to_min():
    """Request rate target clips to min_replicas."""
    autoscaler = _build_request_autoscaler(min_replicas=2,
                                           max_replicas=5,
                                           target_qps_per_replica=10,
                                           num_overprovision=0)
    _set_requests(autoscaler, 0)
    assert autoscaler._calculate_target_num_replicas() == 2

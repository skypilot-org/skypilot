"""Tests for explicit Nebius spot opt-in using the public SDK."""
# pylint: disable=protected-access
from unittest import mock

import pytest

pytest.importorskip('nebius')

# pylint: disable=wrong-import-position
from sky.adaptors import nebius
from sky.catalog.data_fetchers import fetch_nebius
from sky.provision.nebius import utils
from sky.utils import resources_utils


@pytest.fixture
def instance_service(monkeypatch):
    service = mock.Mock()
    monkeypatch.setattr(nebius, 'sdk', lambda: None)
    monkeypatch.setattr(nebius, 'sync_call', lambda response: response)
    monkeypatch.setattr(nebius.compute(), 'InstanceServiceClient',
                        lambda _: service)
    return service


@pytest.mark.parametrize('use_spot', [False, True])
@pytest.mark.parametrize('node_type', ['head', 'worker'])
def test_launch_serializes_spot_opt_in(instance_service, monkeypatch, use_spot,
                                       node_type):
    compute = nebius.compute()
    instance_service.get_by_name.return_value = compute.Instance(
        metadata=nebius.nebius_common().ResourceMetadata(id='instance-id'),
        status=compute.InstanceStatus(
            state=compute.InstanceStatus.InstanceState.STARTING))
    monkeypatch.setattr(utils, 'get_project_by_region', lambda _: 'project-id')
    monkeypatch.setattr(utils, 'get_subnet_id', lambda *_: 'subnet-id')

    instance_id = utils.launch(cluster_name_on_cloud='cluster',
                               node_type=node_type,
                               platform='gpu-h100-sxm',
                               preset='1gpu-16vcpu-200gb',
                               region='eu-north1',
                               image_id_or_family='computeimage-test',
                               disk_size=100,
                               user_data='',
                               associate_public_ip_address=False,
                               filesystems=[],
                               disk_tier=resources_utils.DiskTier.MEDIUM,
                               use_spot=use_spot)

    assert instance_id == 'instance-id'
    request = instance_service.create.call_args.args[0]
    restored = compute.CreateInstanceRequest.FromString(
        request.SerializeToString())
    spec = restored.spec
    assert spec.check_presence('preemptible') == use_spot
    assert spec.check_presence('follows_spot_price') == use_spot
    expected_mode = 'follows_spot_price' if use_spot else None
    assert spec.which_field_in_oneof('pricing_model') == expected_mode
    if use_spot:
        assert spec.preemptible.on_preemption == (
            compute.PreemptibleSpec.PreemptionPolicy.STOP)
        assert spec.recovery_policy == compute.InstanceRecoveryPolicy.FAIL
    else:
        assert spec.recovery_policy == compute.InstanceRecoveryPolicy.RECOVER


@pytest.mark.parametrize('use_spot', [False, True])
def test_estimate_serializes_matching_pricing_mode(use_spot):
    request = fetch_nebius._make_estimate_batch_request('project-id',
                                                        'gpu-h100-sxm',
                                                        '1gpu-16vcpu-200gb',
                                                        use_spot)
    packed = request.resource_specs[0].spec
    restored = nebius.compute().CreateInstanceRequest.FromString(packed.value)
    assert restored.spec.check_presence('preemptible') == use_spot
    assert restored.spec.check_presence('follows_spot_price') == use_spot
    expected_mode = 'follows_spot_price' if use_spot else None
    assert restored.spec.which_field_in_oneof('pricing_model') == expected_mode


@pytest.mark.parametrize('mode', [
    'legacy', 'follows_spot_price', 'spot_pricing_policy', 'on_demand',
    'regular'
])
def test_restart_requires_existing_opt_in(instance_service, mode):
    compute = nebius.compute()
    spec = compute.InstanceSpec()
    if mode not in ('on_demand', 'regular'):
        spec.preemptible = compute.PreemptibleSpec(
            on_preemption=compute.PreemptibleSpec.PreemptionPolicy.STOP)
    if mode == 'follows_spot_price':
        spec.follows_spot_price = compute.FollowsSpotPriceSpec()
    elif mode == 'spot_pricing_policy':
        spec.spot_pricing_policy = compute.SpotPricingPolicySpec(id='policy-id')
    elif mode == 'on_demand':
        spec.on_demand = compute.OnDemandSpec()
    original_spec = spec.SerializeToString()
    stopped = compute.Instance(
        spec=spec,
        status=compute.InstanceStatus(
            state=compute.InstanceStatus.InstanceState.STOPPED))
    running = compute.Instance(
        spec=spec,
        status=compute.InstanceStatus(
            state=compute.InstanceStatus.InstanceState.RUNNING))
    instance_service.get.side_effect = [stopped, running]

    if mode == 'legacy':
        with pytest.raises(ValueError, match='explicit spot pricing opt-in'):
            utils.start('instance-id')
        instance_service.start.assert_not_called()
        assert instance_service.get.call_count == 1
    else:
        utils.start('instance-id')
        instance_service.start.assert_called_once()
        assert instance_service.start.call_args.args[0].id == 'instance-id'
    instance_service.update.assert_not_called()
    instance_service.delete.assert_not_called()
    assert stopped.spec.SerializeToString() == original_spec

"""Tests for the Nebius catalog fetcher billing API integration."""
# pylint: disable=protected-access
import decimal
from types import SimpleNamespace

from google.protobuf import empty_pb2
import pytest

from sky.catalog.data_fetchers import fetch_nebius

_FILTER_AGGREGATION_UNIT_HOUR = 'FILTER_AGGREGATION_UNIT_HOUR'
_PREEMPTIBLE_ON_PREEMPTION_STOP = 'STOP'


class _Proto:
    """Minimal object that behaves like generated SDK message wrappers."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _PackedAny:
    """Fake Any wrapper that preserves the original message for assertions."""

    def __init__(self, message):
        self.message = message


class _Future:
    """Fake Nebius SDK future."""

    def __init__(self, response):
        self._response = response

    def wait(self):
        return self._response


class _FakeFilterAggregationUnit(_Proto):
    """Fake billing filter with the SDK enum namespace."""

    FilterAggregationUnitValue = SimpleNamespace(
        FILTER_AGGREGATION_UNIT_HOUR=_FILTER_AGGREGATION_UNIT_HOUR)


class _FakePreemptibleSpec(_Proto):
    """Fake compute preemptible spec with the SDK enum namespace."""

    PreemptionPolicy = SimpleNamespace(STOP=_PREEMPTIBLE_ON_PREEMPTION_STOP)


def _response(cost='1.25',
              unit='hour',
              cost_type='general',
              include_total_costs=True):
    if not include_total_costs:
        return _Proto(total_costs=[])

    total_cost = _Proto(aggregation_unit=_Proto(unit=unit))
    if cost_type == 'general':
        total_cost.general = _Proto(total=_Proto(cost=cost))
    elif cost_type == 'range':
        total_cost.range = _Proto()
        total_cost.WhichOneof = lambda _: 'range'
    elif cost_type == 'unset':
        total_cost.WhichOneof = lambda _: None

    return _Proto(total_costs=[total_cost])


def _fake_platform():
    preset = SimpleNamespace(
        name='1gpu-16vcpu-200gb',
        resources=SimpleNamespace(gpu_count=1,
                                  vcpu_count=16,
                                  memory_gibibytes=200),
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name='gpu-h100-sxm'),
        spec=SimpleNamespace(presets=[preset], gpu_memory_gibibytes=80),
    )


def _install_fake_nebius_modules(monkeypatch, responses):

    class FakeCalculatorServiceClient:
        """Fake CalculatorServiceClient that records estimate requests."""

        requests = []

        def __init__(self, sdk):
            self.sdk = sdk

        def estimate_batch(self, request, timeout):
            del timeout
            self.requests.append(request)
            return _Future(responses.pop(0))

    fake_billing = SimpleNamespace(
        CalculatorServiceClient=FakeCalculatorServiceClient,
        ResourceSpec=_Proto,
        EstimateBatchRequest=_Proto,
        FilterAggregationUnit=_FakeFilterAggregationUnit,
    )
    fake_compute = SimpleNamespace(
        CreateInstanceRequest=_Proto,
        InstanceSpec=_Proto,
        ResourcesSpec=_Proto,
        PreemptibleSpec=_FakePreemptibleSpec,
    )
    fake_common = SimpleNamespace(ResourceMetadata=_Proto)

    monkeypatch.setattr(fetch_nebius, 'billing', lambda: fake_billing)
    monkeypatch.setattr(fetch_nebius, 'compute', lambda: fake_compute)
    monkeypatch.setattr(fetch_nebius, 'nebius_common', lambda: fake_common)
    monkeypatch.setattr(fetch_nebius.nebius, 'sdk', lambda: object())
    monkeypatch.setattr(fetch_nebius, '_pack_any', lambda message:
                        _PackedAny(message))

    return FakeCalculatorServiceClient


def test_pack_any_packs_wrapped_protobuf_message():
    protobuf_message = empty_pb2.Empty()
    wrapped_message = SimpleNamespace(__pb2_message__=protobuf_message)

    packed = fetch_nebius._pack_any(wrapped_message)

    unpacked = empty_pb2.Empty()
    assert packed.type_url == 'type.googleapis.com/google.protobuf.Empty'
    assert packed.Unpack(unpacked)


def test_estimate_platforms_uses_billing_v1_request_shape(monkeypatch):
    service_cls = _install_fake_nebius_modules(
        monkeypatch, [_response('2.95'), _response('1.23')])

    presets = fetch_nebius._estimate_platforms([_fake_platform()],
                                               parent_id='project-e00public',
                                               region='eu-north1')

    assert len(presets) == 1
    assert presets[0].price_hourly == decimal.Decimal('2.95')
    assert presets[0].spot_price == decimal.Decimal('1.23')
    assert len(service_cls.requests) == 2

    regular_request, spot_request = service_cls.requests
    for request in (regular_request, spot_request):
        hour_filter = (
            request.filter_aggregation_unit.filter_aggregation_unit_values)
        assert hour_filter == [_FILTER_AGGREGATION_UNIT_HOUR]
        assert len(request.resource_specs) == 1
        packed_spec = request.resource_specs[0].spec
        assert isinstance(packed_spec, _PackedAny)
        create_request = packed_spec.message
        assert create_request.metadata.parent_id == 'project-e00public'
        assert create_request.spec.resources.platform == 'gpu-h100-sxm'
        assert create_request.spec.resources.preset == '1gpu-16vcpu-200gb'

    regular_spec = regular_request.resource_specs[0].spec.message.spec
    assert not hasattr(regular_spec, 'preemptible')

    spot_spec = spot_request.resource_specs[0].spec.message.spec
    assert spot_spec.preemptible.on_preemption == (
        _PREEMPTIBLE_ON_PREEMPTION_STOP)


def test_get_hourly_total_cost_reads_hourly_general_total():
    assert fetch_nebius._get_hourly_total_cost(
        _response('3.50')) == decimal.Decimal('3.50')


@pytest.mark.parametrize(
    'response, match',
    [
        (_response(include_total_costs=False), 'did not return total costs'),
        (_response(unit='month'), 'exactly one hourly total cost'),
        (_response(cost_type='range'), 'range hourly total cost'),
        (_response(cost=''), 'missing cost'),
        (_response(cost_type='unset'), 'no general cost'),
    ],
)
def test_get_hourly_total_cost_validates_invalid_responses(response, match):
    with pytest.raises(ValueError, match=match):
        fetch_nebius._get_hourly_total_cost(response)

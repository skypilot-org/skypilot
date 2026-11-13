"""Tests for Vast offer selection in sky.provision.vast.utils.launch."""
from typing import Any, Dict, List
from unittest import mock

import pytest

from sky.provision.vast import utils


def _offer(offer_id: int, gpu_name: str, num_gpus: int) -> Dict[str, Any]:
    return {
        'id': offer_id,
        'gpu_name': gpu_name,
        'num_gpus': num_gpus,
        'min_bid': 0.1,
    }


@pytest.fixture
def fake_vast(monkeypatch):
    sdk = mock.MagicMock()
    sdk.client.api_key = 'test-key'
    sdk.create_instance.return_value = {'new_contract': 42}
    sdk.show_instance.return_value = {'id': 42}
    monkeypatch.setattr(utils.vast, 'vast', lambda: sdk)
    return sdk


def _launch(instance_type: str = '1x-RTX_3090-8-32768') -> str:
    return utils.launch(name='test-cluster',
                        instance_type=instance_type,
                        region='Europe, DE',
                        disk_size=100,
                        image_name='docker-image',
                        ports=None,
                        preemptible=False,
                        secure_only=False)


def test_mismatching_first_offer_is_skipped(fake_vast):
    # What an SDK that dropped the gpu_name/num_gpus filters returns: the
    # top-scored offer is a different GPU.
    fake_vast.search_offers.return_value = [
        _offer(1, 'RTX 5090', 1),
        _offer(2, 'RTX 3090', 2),
        _offer(3, 'RTX 3090', 1),
    ]
    assert _launch() == 42
    assert fake_vast.create_instance.call_args.kwargs['id'] == 3


def test_matching_first_offer_is_used(fake_vast):
    fake_vast.search_offers.return_value = [
        _offer(7, 'RTX 3090', 1),
        _offer(8, 'RTX 3090', 1),
    ]
    _launch()
    assert fake_vast.create_instance.call_args.kwargs['id'] == 7


@pytest.mark.parametrize('offers', [
    [],
    [_offer(1, 'H100 SXM', 1),
     _offer(2, 'RTX 3090', 4)],
])
def test_no_matching_offer_raises(fake_vast, offers: List[Dict[str, Any]]):
    fake_vast.search_offers.return_value = offers
    with pytest.raises(RuntimeError, match='could not find an offer'):
        _launch()
    fake_vast.create_instance.assert_not_called()


def test_search_error_code_raises(fake_vast):
    fake_vast.search_offers.return_value = 400
    with pytest.raises(RuntimeError, match='could not find an offer'):
        _launch()
    fake_vast.create_instance.assert_not_called()


@pytest.mark.parametrize('offer,gpu_stub,num_gpus,expected', [
    (_offer(1, 'RTX 3090', 1), 'RTX_3090', 1, True),
    (_offer(1, 'H100 SXM', 8), 'H100_SXM', 8, True),
    (_offer(1, 'RTX 3090', 1), 'RTX_3090', 2, False),
    (_offer(1, 'RTX 3090 Ti', 1), 'RTX_3090', 1, False),
    ({
        'id': 1
    }, 'RTX_3090', 1, False),
])
def test_offer_matches(offer, gpu_stub, num_gpus, expected):
    assert utils._offer_matches(offer, gpu_stub, num_gpus) is expected

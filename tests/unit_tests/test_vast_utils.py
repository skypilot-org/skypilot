"""Tests for Vast provisioning utilities."""
from unittest import mock

import pytest

from sky.provision.vast import utils as vast_utils


def _capture_query(instance_type: str, region: str, disk_size: int = 60) -> str:
    """Runs launch() far enough to capture the offer query it sends."""
    mock_sdk = mock.MagicMock()
    # No offers -> launch() raises before touching the network any further.
    mock_sdk.search_offers.return_value = []
    with mock.patch.object(vast_utils.vast, 'vast', return_value=mock_sdk):
        with pytest.raises(RuntimeError):
            vast_utils.launch(name='test',
                              instance_type=instance_type,
                              region=region,
                              disk_size=disk_size,
                              image_name='ubuntu',
                              ports=None,
                              preemptible=False,
                              secure_only=False)
    mock_sdk.search_offers.assert_called_once()
    return mock_sdk.search_offers.call_args.kwargs['query']


def test_offer_query_values_are_unquoted():
    """Quoted values silently empty the whole query.

    The Vast SDK parses this query with pyparsing using
    `Word(alphanums + '_')`, which accepts no quotes, spaces or dots. Because
    `georegion`/`chunked` are always set, the query goes through
    `preprocess_search_query()`, whose parser stops at the first quoted token
    and returns an empty string -- dropping every filter, so the search returns
    offers of every GPU type and an arbitrary one gets launched.
    """
    query = _capture_query('1x-RTX_3090-32-65536', 'Denmark, DK, EU')

    assert '"' not in query and "'" not in query, query
    # The GPU name keeps the underscore form the instance type carries; a
    # space would break the same parser that a quote does.
    assert 'gpu_name=RTX_3090' in query, query
    # Region is the trailing two-letter code, unquoted so that georegion can
    # expand it.
    assert 'geolocation=EU' in query, query
    # No '.' in the RAM figure, for the same reason.
    assert 'cpu_ram>=64' in query, query
    assert 'num_gpus=1' in query, query
    assert 'disk_space>=60' in query, query


def test_offer_query_handles_multi_word_gpu_names():
    query = _capture_query('2x-RTX_4000Ada-32-65536', 'Somewhere, US, NA')

    assert '"' not in query and "'" not in query, query
    assert 'gpu_name=RTX_4000Ada' in query, query
    assert 'num_gpus=2' in query, query
    assert 'geolocation=NA' in query, query


def test_offer_query_secure_only_adds_datacenter_filters():
    mock_sdk = mock.MagicMock()
    mock_sdk.search_offers.return_value = []
    with mock.patch.object(vast_utils.vast, 'vast', return_value=mock_sdk):
        with pytest.raises(RuntimeError):
            vast_utils.launch(name='test',
                              instance_type='1x-RTX_3090-32-65536',
                              region='Denmark, DK, EU',
                              disk_size=60,
                              image_name='ubuntu',
                              ports=None,
                              preemptible=False,
                              secure_only=True)
    query = mock_sdk.search_offers.call_args.kwargs['query']
    assert 'datacenter=true' in query, query
    assert 'hosting_type>=1' in query, query
    assert '"' not in query, query


def test_offer_must_match_requested_gpu():
    """An offer for another GPU is never launched.

    Even when the query lets one through.

    The query is re-parsed by the SDK, whose grammar has changed between
    versions and cannot express a GPU name containing '-' in any released
    version. If the gpu_name filter is dropped or truncated, the search
    returns other GPUs -- picking offer [0] would then launch the wrong card.
    """
    mock_sdk = mock.MagicMock()
    mock_sdk.search_offers.return_value = [
        {
            'id': 1,
            'gpu_name': 'RTX PRO 6000 Max-Q'
        },
        {
            'id': 2,
            'gpu_name': 'H100 SXM'
        },
    ]
    with mock.patch.object(vast_utils.vast, 'vast', return_value=mock_sdk):
        with pytest.raises(RuntimeError, match='no offer for GPU'):
            vast_utils.launch(name='test',
                              instance_type='1x-RTX_3090-32-65536',
                              region='Denmark, DK, EU',
                              disk_size=60,
                              image_name='ubuntu',
                              ports=None,
                              preemptible=False,
                              secure_only=False)
    mock_sdk.create_instance.assert_not_called()


def test_matching_offer_is_selected_out_of_a_mixed_list():
    mock_sdk = mock.MagicMock()
    mock_sdk.search_offers.return_value = [
        {
            'id': 1,
            'gpu_name': 'H100 SXM'
        },
        {
            'id': 2,
            'gpu_name': 'RTX 3090'
        },
    ]
    with mock.patch.object(vast_utils.vast, 'vast', return_value=mock_sdk):
        vast_utils.launch(name='test',
                          instance_type='1x-RTX_3090-32-65536',
                          region='Denmark, DK, EU',
                          disk_size=60,
                          image_name='ubuntu',
                          ports=None,
                          preemptible=False,
                          secure_only=False)
    # The RTX 3090 offer, not the H100 that happened to come first.
    assert mock_sdk.create_instance.call_args.kwargs['id'] == 2

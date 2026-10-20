"""sdk.api_cancel: ids name what to cancel, all_users means every request."""
from unittest import mock

import pytest

from sky.client import sdk
from sky.utils import common_utils


@pytest.fixture
def sent_body():
    """The scope fields of the body api_cancel POSTs to /api/cancel."""
    with mock.patch.object(sdk.server_common,
                           'make_authenticated_request') as request, \
         mock.patch.object(sdk.server_common, 'get_request_id',
                           return_value='cancel-req'), \
         mock.patch.object(common_utils, 'get_user_hash',
                           return_value='abcdef12'):
        yield lambda: {
            k: request.call_args.kwargs['json'][k]
            for k in ('request_ids', 'user_id')
        }


@pytest.mark.parametrize('request_ids', ['0d35ffa7', ['0d35ffa7', 'a9d59602']])
def test_request_ids_with_all_users_are_rejected(sent_body, request_ids):
    # Same rule as `sky api cancel <id> -u`: before, all_users was ignored
    # and only the named ids were cancelled.
    del sent_body
    with pytest.raises(ValueError, match='one of request_ids or all_users'):
        sdk.api_cancel(request_ids=request_ids, all_users=True)


@pytest.mark.parametrize('request_ids', ['0d35ffa7', ['0d35ffa7', 'a9d59602']])
def test_request_ids_are_sent(sent_body, request_ids):
    sdk.api_cancel(request_ids=request_ids)
    expected = [request_ids] if isinstance(request_ids, str) else request_ids
    assert sent_body()['request_ids'] == expected


def test_no_arguments_cancels_all_of_the_callers_requests(sent_body):
    sdk.api_cancel()
    assert sent_body() == {'request_ids': None, 'user_id': 'abcdef12'}


def test_all_users_cancels_every_request(sent_body):
    sdk.api_cancel(all_users=True)
    assert sent_body() == {'request_ids': None, 'user_id': None}

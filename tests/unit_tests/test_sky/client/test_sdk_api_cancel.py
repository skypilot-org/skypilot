"""sdk.api_cancel: which user scope the cancel body carries."""
from unittest import mock

import pytest

from sky.client import sdk
from sky.utils import common_utils


@pytest.fixture
def sent_body():
    """The JSON body api_cancel POSTs to /api/cancel."""
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
def test_request_ids_are_not_scoped_to_the_caller(sent_body, request_ids):
    # The server decides who may cancel a named request (admins: any, others:
    # their own). Sending the caller's hash here kept an admin from
    # cancelling another user's or the system's request by id.
    sdk.api_cancel(request_ids=request_ids)
    expected = [request_ids] if isinstance(request_ids, str) else request_ids
    assert sent_body() == {'request_ids': expected, 'user_id': None}


def test_request_ids_with_all_users_are_not_scoped(sent_body):
    sdk.api_cancel(request_ids=['0d35ffa7'], all_users=True)
    assert sent_body() == {'request_ids': ['0d35ffa7'], 'user_id': None}


def test_no_request_ids_cancels_all_of_the_callers_requests(sent_body):
    sdk.api_cancel()
    assert sent_body() == {'request_ids': None, 'user_id': 'abcdef12'}


def test_all_users_cancels_every_request(sent_body):
    sdk.api_cancel(all_users=True)
    assert sent_body() == {'request_ids': None, 'user_id': None}

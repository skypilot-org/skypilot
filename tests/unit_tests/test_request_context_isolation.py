"""A request context set by one unit test must not reach the next one.

The two tests run in file order on one worker. If xdist puts them on
different workers the second passes on its own, so it can never fail for a
reason other than a leak.
"""
from sky import models
from sky.utils import common_utils


def test_request_context_is_set_within_the_test():
    common_utils.set_request_context(client_entrypoint='sky status',
                                     client_command='status',
                                     using_remote_api_server=False,
                                     user=models.User(id='abcd1234',
                                                      name='alice'),
                                     request_id='request-id')

    assert common_utils.is_in_request_context()
    assert common_utils.get_current_request_actor() == (
        'alice (request ID: request-id)')


def test_request_context_does_not_outlive_the_test_that_set_it():
    assert not common_utils.is_in_request_context()
    assert common_utils.get_current_request_actor() is None

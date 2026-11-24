"""The serve and jobs SDKs send the client's config from a fresh process."""
from unittest import mock

import pytest

from sky import skypilot_config
from sky.jobs.client import sdk as jobs_sdk
from sky.serve.client import sdk as serve_sdk
from sky.server import common as server_common
from sky.server import versions
from sky.utils import annotations
from sky.utils import config_utils


class _Sent(Exception):
    pass


@pytest.mark.parametrize('call', [
    lambda: serve_sdk.down('svc-*'),
    lambda: serve_sdk.status(None),
    lambda: jobs_sdk.pool_down('pool-*'),
    lambda: jobs_sdk.pool_status(None),
    lambda: jobs_sdk.queue(refresh=False),
    lambda: jobs_sdk.cancel(name='job'),
],
                         ids=[
                             'serve.down', 'serve.status', 'pool_down',
                             'pool_status', 'jobs.queue', 'jobs.cancel'
                         ])
def test_first_call_sends_client_config(call):
    # A process starts out as if it were the server, which sends no client
    # config; the request would then run in the server's default workspace.
    assert annotations.is_on_api_server
    sent = mock.Mock(side_effect=_Sent)
    with mock.patch.object(server_common, 'check_server_healthy_or_start_fn'), \
         mock.patch.object(versions, 'get_remote_api_version',
                           return_value=None), \
         mock.patch.object(skypilot_config, 'to_dict',
                           return_value=config_utils.Config(
                               {'active_workspace': 'ws-a'})), \
         mock.patch.object(server_common, 'make_authenticated_request', sent):
        with pytest.raises(_Sent):
            call()
    body = sent.call_args.kwargs['json']
    assert body['override_skypilot_config'] == {'active_workspace': 'ws-a'}

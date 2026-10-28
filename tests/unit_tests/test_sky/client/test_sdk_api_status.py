"""sdk.api_status: the cluster_name filter and its old-server warning."""
import logging
from unittest import mock

import pytest

from sky.client import sdk


@pytest.fixture
def sent_params():
    """The query params api_status sends to /api/status."""
    response = mock.MagicMock()
    response.json.return_value = []
    with mock.patch.object(sdk.server_common, 'is_api_server_local',
                           return_value=False), \
         mock.patch.object(sdk.server_common,
                           'make_authenticated_request',
                           return_value=response) as request, \
         mock.patch.object(sdk.server_common, 'handle_request_error'):
        yield lambda: request.call_args.kwargs['params']


def _warnings(caplog):
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and 'ignored' in r.getMessage()
    ]


@pytest.mark.parametrize('version', [None, 38, 60])
def test_cluster_name_is_sent_without_warning(sent_params, caplog, version):
    """An unknown version (e.g. no health check has run in this process) is
    not an old one; the flag is sent, so there is nothing to warn about."""
    with mock.patch.object(sdk.versions,
                           'get_remote_api_version',
                           return_value=version), \
         caplog.at_level(logging.WARNING):
        sdk.api_status(cluster_name='c1')
    assert sent_params()['cluster_name'] == 'c1'
    assert not _warnings(caplog)


def test_old_server_warns_that_cluster_name_is_ignored(sent_params, caplog):
    with mock.patch.object(sdk.versions,
                           'get_remote_api_version',
                           return_value=37), \
         caplog.at_level(logging.WARNING):
        sdk.api_status(cluster_name='c1')
    assert sent_params()['cluster_name'] == 'c1'
    assert _warnings(caplog)

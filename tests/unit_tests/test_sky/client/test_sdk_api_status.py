"""sdk.api_status: the cluster_name filter and its old-server warning."""
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


def _api_status_with_server_version(version):
    """Call api_status(cluster_name=...); return the logger.warning mock.

    The warning is checked on the module's logger directly rather than with
    caplog: whether the logger propagates to pytest's handler depends on
    logging setup done by other tests.
    """
    with mock.patch.object(sdk.versions,
                           'get_remote_api_version',
                           return_value=version), \
         mock.patch.object(sdk.logger, 'warning') as warning:
        sdk.api_status(cluster_name='c1')
    return warning


@pytest.mark.parametrize('version', [None, 38, 60])
def test_cluster_name_is_sent_without_warning(sent_params, version):
    """An unknown version (e.g. no health check has run in this process) is
    not an old one; the flag is sent, so there is nothing to warn about."""
    warning = _api_status_with_server_version(version)
    assert sent_params()['cluster_name'] == 'c1'
    warning.assert_not_called()


def test_old_server_warns_that_cluster_name_is_ignored(sent_params):
    warning = _api_status_with_server_version(37)
    assert sent_params()['cluster_name'] == 'c1'
    warning.assert_called_once()
    assert 'ignored' in warning.call_args.args[0]

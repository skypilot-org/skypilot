"""sdk.api_status: health check first, then the cluster_name old-server check."""
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


def _api_status_after_health_check(server_api_version):
    """Call api_status(cluster_name=...) against a server of that version.

    Before the health check no version is known; the health check records
    `server_api_version`, as the real one does.

    Returns:
        (health check mock, logger.warning mock). The warning is checked on
        the module's logger directly rather than with caplog: whether the
        logger propagates to pytest's handler depends on logging setup done by
        other tests.
    """
    remote_api_version = mock.MagicMock(return_value=None)

    def health_check(*args, **kwargs):
        del args, kwargs
        remote_api_version.return_value = server_api_version
        return sdk.server_common.ApiServerStatus.HEALTHY, mock.MagicMock()

    with mock.patch.object(sdk.server_common,
                           'check_server_healthy',
                           side_effect=health_check) as check, \
         mock.patch.object(sdk.versions, 'get_remote_api_version',
                           remote_api_version), \
         mock.patch.object(sdk.logger, 'warning') as warning:
        sdk.api_status(cluster_name='c1')
    return check, warning


@pytest.mark.parametrize('server_api_version', [38, 60])
def test_version_is_fetched_before_the_flag_check(sent_params,
                                                  server_api_version):
    """The health check runs first, so a recent server is known as such and
    no warning is logged (with no version known it would warn)."""
    check, warning = _api_status_after_health_check(server_api_version)
    check.assert_called_once()
    assert sent_params()['cluster_name'] == 'c1'
    warning.assert_not_called()


@pytest.mark.parametrize('server_api_version', [37, None])
def test_old_or_unknown_server_warns_that_cluster_name_is_ignored(
        sent_params, server_api_version):
    _, warning = _api_status_after_health_check(server_api_version)
    assert sent_params()['cluster_name'] == 'c1'
    warning.assert_called_once()
    assert 'ignored' in warning.call_args.args[0]


def test_no_local_server_returns_empty_without_a_health_check():
    """api_status does not start a local server, unlike the decorated calls:
    with none running there is nothing to check."""
    with mock.patch.object(sdk.server_common, 'is_api_server_local',
                           return_value=True), \
         mock.patch.object(sdk, '_local_api_server_running',
                           return_value=False), \
         mock.patch.object(sdk.server_common,
                           'check_server_healthy') as check:
        assert sdk.api_status(cluster_name='c1') == []
    check.assert_not_called()

"""Unit tests for sky.utils.cloud_api_retries."""
# pylint: disable=missing-class-docstring,protected-access
from unittest import mock

import pytest

from sky import exceptions
from sky.utils import cloud_api_retries


def _fetch_error(
    msg: str = 'Failed to query kubernetes cluster status: '
    '(503) Reason: Service Unavailable'
) -> exceptions.ClusterStatusFetchingError:
    return exceptions.ClusterStatusFetchingError(msg)


class TestWithCloudApiRetries:

    def test_returns_immediately_on_success(self):
        fn = mock.Mock(return_value=('UP', 'handle'))
        with mock.patch.object(cloud_api_retries.time, 'sleep') as sleep:
            assert cloud_api_retries.with_cloud_api_retries(fn) == ('UP',
                                                                    'handle')
        fn.assert_called_once()
        sleep.assert_not_called()

    def test_succeeds_after_transient_failures(self):
        # Fail with a transient 503 four times, then succeed on the fifth
        # attempt (the default budget). The healthy status must be returned
        # and the transient error must NOT propagate -- i.e. it never reaches
        # the controller catch-all that would mark the job FAILED_CONTROLLER.
        fn = mock.Mock(side_effect=[
            _fetch_error(),
            _fetch_error(),
            _fetch_error(),
            _fetch_error(),
            ('UP', 'handle'),
        ])
        with mock.patch.object(cloud_api_retries.time, 'sleep') as sleep:
            assert cloud_api_retries.with_cloud_api_retries(fn) == ('UP',
                                                                    'handle')
        assert fn.call_count == 5
        assert sleep.call_count == 4

    def test_raises_after_exhausting_retries(self):
        # A sustained outage still surfaces the error once the retry budget
        # is exhausted, so the caller can decide how to handle it.
        fn = mock.Mock(side_effect=_fetch_error())
        with mock.patch.object(cloud_api_retries.time, 'sleep'):
            with pytest.raises(exceptions.ClusterStatusFetchingError):
                cloud_api_retries.with_cloud_api_retries(fn)
        assert fn.call_count == cloud_api_retries._DEFAULT_MAX_RETRIES

    def test_respects_custom_max_retries(self):
        fn = mock.Mock(side_effect=[_fetch_error(), ('UP', 'handle')])
        with mock.patch.object(cloud_api_retries.time, 'sleep'):
            assert cloud_api_retries.with_cloud_api_retries(
                fn, max_retries=2) == ('UP', 'handle')
        assert fn.call_count == 2

    def test_does_not_retry_non_retryable(self):
        # A genuine bug (e.g. a ValueError) must not be retried or swallowed.
        fn = mock.Mock(side_effect=ValueError('not retryable'))
        with mock.patch.object(cloud_api_retries.time, 'sleep') as sleep:
            with pytest.raises(ValueError, match='not retryable'):
                cloud_api_retries.with_cloud_api_retries(fn)
        fn.assert_called_once()
        sleep.assert_not_called()

    @pytest.mark.parametrize('bad_max_retries', [0, -1, -100])
    def test_invalid_max_retries(self, bad_max_retries):
        with pytest.raises(ValueError, match='max_retries must be'):
            cloud_api_retries.with_cloud_api_retries(
                mock.Mock(), max_retries=bad_max_retries)

    def test_summarize_single_line(self):
        e = _fetch_error('line one\nline two')
        summary = cloud_api_retries.summarize(e)
        assert summary == 'ClusterStatusFetchingError: line one'
        assert '\n' not in summary

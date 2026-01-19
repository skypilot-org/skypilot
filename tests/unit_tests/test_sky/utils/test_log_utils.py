"""Tests for sky.utils.log_utils module."""

import sys
import time

import pytest

from sky.utils import log_utils

# Using time.time() results in different workers getting different values,
# and apparently pytest-xdist cannot handle that.
NOW = 1758240750.0


@pytest.mark.parametrize(
    "start,end,absolute",
    [
        # start=None cases
        (None, None, False),
        (None, NOW, False),
        (None, None, True),
        # start < 0 cases
        (-1.0, None, False),
        (-100.0, NOW, False),
        (-1.0, None, True),
        # start=0 and end=0 cases
        (0.0, 0.0, False),
        (0.0, 0.0, True),
    ])
def test_readable_time_duration_returns_dash(start, end, absolute):
    """Test cases where readable_time_duration should return '-'."""
    result = log_utils.readable_time_duration(start=start,
                                              end=end,
                                              absolute=absolute)
    assert result == '-'


@pytest.mark.parametrize(
    "start_time,end_time,expected_relative,expected_absolute", [
        (NOW, NOW + 10, 'a few secs before', '10s'),
        (NOW, NOW + 60, '1 min before', '1m'),
        (NOW, NOW + 60 * 2 + 1, '2 mins before', '2m 1s'),
        (NOW, NOW + 3600, '1 hr before', '1h'),
        (NOW, NOW + 3600 * 24, '1 day before', '1d'),
        (NOW, NOW + 3600 * 25 + 51, '1 day before', '1d 1h 51s'),
        (NOW, NOW + 3600 * 24 * 7, '1 week before', '1w'),
        (NOW, NOW + 3600 * 24 * 30, '1 month before', '1mo'),
        (NOW, NOW + 3600 * 24 * 365, '1 year before', '1 year'),
        (NOW, NOW + 3600 * 24 * 365 * 2, '2 years before', '2 years'),
        (NOW, NOW + 0.5, '< 1 sec', '< 1s'),
    ])
def test_readable_time_duration_exact_cases(start_time, end_time,
                                            expected_relative,
                                            expected_absolute):
    """Test time duration cases with exact expected results."""
    # Test relative formatting
    result = log_utils.readable_time_duration(start=start_time,
                                              end=end_time,
                                              absolute=False)
    assert result == expected_relative

    # Test absolute formatting
    result_abs = log_utils.readable_time_duration(start=start_time,
                                                  end=end_time,
                                                  absolute=True)
    assert result_abs == expected_absolute


def test_readable_time_end_at_none():
    """Test end_at defaults to current time when it is None."""
    # Using time.time() with parametrize() results in different workers
    # getting different values, and pytest-xdist doesn't like that.
    # So we move this into a separate test.
    now = time.time() - 50
    result = log_utils.readable_time_duration(start=now,
                                              end=None,
                                              absolute=False)
    assert result == '50 secs ago'
    result_abs = log_utils.readable_time_duration(start=now,
                                                  end=None,
                                                  absolute=True)
    assert result_abs == '50s'

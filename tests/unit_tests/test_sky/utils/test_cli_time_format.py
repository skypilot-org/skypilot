"""Tests for sky jobs queue SUBMITTED column time format."""

from sky.utils import log_utils


class TestReadableTimeDurationAgo:
    """sky jobs queue: SUBMITTED uses readable_time_duration_ago().

    Format: precise time with ' ago' suffix, limited to 2 units by default.
    E.g., '1h 12m ago', '1d 2h ago'
    """

    NOW = 1758240750.0

    def test_submitted_72_mins_ago(self):
        """Test that 72 minutes shows as '1h 12m ago'."""
        submitted_at = self.NOW - (72 * 60)
        result = log_utils.readable_time_duration_ago(submitted_at,
                                                      end=self.NOW)
        assert result == '1h 12m ago', (
            f'Expected \'1h 12m ago\' but got \'{result}\'')

    def test_submitted_90_mins_ago(self):
        """Test that 90 minutes shows as '1h 30m ago'."""
        submitted_at = self.NOW - (90 * 60)
        result = log_utils.readable_time_duration_ago(submitted_at,
                                                      end=self.NOW)
        assert result == '1h 30m ago', (
            f'Expected \'1h 30m ago\' but got \'{result}\'')

    def test_submitted_26_hours_ago(self):
        """Test that 26 hours shows as '1d 2h ago'."""
        submitted_at = self.NOW - (26 * 3600)
        result = log_utils.readable_time_duration_ago(submitted_at,
                                                      end=self.NOW)
        assert result == '1d 2h ago', (
            f'Expected \'1d 2h ago\' but got \'{result}\'')

    def test_submitted_exact_hour(self):
        """Test that exactly 1 hour shows as '1h ago'."""
        submitted_at = self.NOW - 3600
        result = log_utils.readable_time_duration_ago(submitted_at,
                                                      end=self.NOW)
        assert result == '1h ago', f'Expected \'1h ago\' but got \'{result}\''

    def test_precision_1(self):
        """Test that precision=1 shows only 1 unit."""
        submitted_at = self.NOW - (26 * 3600 + 30 * 60)
        result = log_utils.readable_time_duration_ago(submitted_at,
                                                      end=self.NOW,
                                                      precision=1)
        assert result == '1d ago', f'Expected \'1d ago\' but got \'{result}\''

    def test_invalid_returns_dash(self):
        """Test that invalid submitted_at returns '-'."""
        result = log_utils.readable_time_duration_ago(-1, end=self.NOW)
        assert result == '-', f'Expected \'-\' but got \'{result}\''

    def test_none_returns_dash(self):
        """Test that None submitted_at returns '-'."""
        result = log_utils.readable_time_duration_ago(None, end=self.NOW)
        assert result == '-', f'Expected \'-\' but got \'{result}\''

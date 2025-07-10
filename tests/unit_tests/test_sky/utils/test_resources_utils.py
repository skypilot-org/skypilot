"""Tests for resources_utils.py."""
import unittest

from sky.utils import resources_utils


class TestParseTimeMinutes(unittest.TestCase):
    """Test cases for parse_time_minutes function."""

    def test_pure_number(self):
        """Test parsing pure numbers (assumed to be in minutes)."""
        self.assertEqual(resources_utils.parse_time_minutes('30'), 30)
        self.assertEqual(resources_utils.parse_time_minutes('0'), 0)
        self.assertEqual(resources_utils.parse_time_minutes('120'), 120)

    def test_minutes(self):
        """Test parsing time strings with minute units."""
        self.assertEqual(resources_utils.parse_time_minutes('30m'), 30)
        self.assertEqual(resources_utils.parse_time_minutes('45M'), 45)
        self.assertEqual(resources_utils.parse_time_minutes('0m'), 0)

    def test_hours(self):
        """Test parsing time strings with hour units."""
        self.assertEqual(resources_utils.parse_time_minutes('1h'), 60)
        self.assertEqual(resources_utils.parse_time_minutes('2H'), 120)
        self.assertEqual(resources_utils.parse_time_minutes('0.5h'), 30)
        # Test rounding up
        self.assertEqual(resources_utils.parse_time_minutes('1.7h'), 102)

    def test_days(self):
        """Test parsing time strings with day units."""
        self.assertEqual(resources_utils.parse_time_minutes('1d'), 1440)
        self.assertEqual(resources_utils.parse_time_minutes('2D'), 2880)
        self.assertEqual(resources_utils.parse_time_minutes('0.5d'), 720)
        # Test rounding up
        self.assertEqual(resources_utils.parse_time_minutes('1.2d'), 1728)

    def test_invalid_format(self):
        """Test invalid time format handling."""
        invalid_inputs = [
            '',  # Empty string
            'abc',  # Non-numeric string
            '30x',  # Invalid unit
            'm30',  # Unit before number
            '1.2.3h',  # Multiple decimal points
            'h',  # Missing number
            '30mm',  # Duplicate unit
            '-30m',  # Negative time
            '30m2h',  # Multiple units
        ]
        for invalid_input in invalid_inputs:
            with self.assertRaises(
                    ValueError, msg=f'Should fail for input: {invalid_input}'):
                resources_utils.parse_time_minutes(invalid_input)

    def test_case_insensitive(self):
        """Test that unit parsing is case-insensitive."""
        self.assertEqual(resources_utils.parse_time_minutes('30m'), 30)
        self.assertEqual(resources_utils.parse_time_minutes('30M'), 30)
        self.assertEqual(resources_utils.parse_time_minutes('1h'), 60)
        self.assertEqual(resources_utils.parse_time_minutes('1H'), 60)
        self.assertEqual(resources_utils.parse_time_minutes('1d'), 1440)
        self.assertEqual(resources_utils.parse_time_minutes('1D'), 1440)

    def test_decimal_values(self):
        """Test handling of decimal values."""
        test_cases = [
            ('1.5h', 90),  # 1.5 hours = 90 minutes
            ('0.5d', 720),  # 0.5 days = 12 hours = 720 minutes
            ('2.7h', 162),  # 2.7 hours = 162 minutes
            ('0.1d', 144),  # 0.1 days = 144 minutes
        ]
        for input_str, expected in test_cases:
            self.assertEqual(resources_utils.parse_time_minutes(input_str),
                             expected, f'Failed for input: {input_str}')

    def test_rounding_behavior(self):
        """Test that results are properly rounded up."""
        test_cases = [
            ('1.01h', 61),  # 60.6 minutes rounds to 61
            ('0.01d', 15),  # 14.4 minutes rounds to 15
            ('1.99h', 120),  # 119.4 minutes rounds to 120
            ('0.99d', 1426),  # 1425.6 minutes rounds to 1426
        ]
        for input_str, expected in test_cases:
            self.assertEqual(resources_utils.parse_time_minutes(input_str),
                             expected, f'Failed for input: {input_str}')

"""Tests for resources_utils.py."""
import unittest

import pytest

from sky import clouds
from sky import resources as resources_lib
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


@pytest.mark.parametrize('cloud', [clouds.Slurm(), clouds.Kubernetes()])
def test_no_instance_type_shown_slurm_kubernetes(cloud):
    """Test that Slurm/Kubernetes resources don't show instance type name."""
    resource = resources_lib.Resources(cloud=cloud,
                                       instance_type='2CPU--4GB',
                                       cpus=2,
                                       memory=4)
    simple, full = resources_utils.format_resource(resource,
                                                   simplified_only=False)

    # Instance type should NOT appear in output
    assert '2CPU--4GB' not in simple
    assert '2CPU--4GB' not in full
    # But CPUs and memory should be shown
    assert 'cpus=2' in simple
    assert 'mem=4' in simple


def test_aws_instance_type_shown():
    """Test that AWS resources do show instance type name."""
    resource = resources_lib.Resources(cloud=clouds.AWS(),
                                       instance_type='m5.large',
                                       cpus=2,
                                       memory=8)
    simple, _ = resources_utils.format_resource(resource, simplified_only=False)

    assert 'm5.large' in simple


class TestNormalizeLocalDisk:
    """Tests for normalize_local_disk function."""

    # Valid inputs - full format (mode:size[+])
    def test_full_format_nvme_with_plus(self):
        assert resources_utils.normalize_local_disk(
            'nvme:1000+') == 'nvme:1000+'

    def test_full_format_nvme_exact(self):
        assert resources_utils.normalize_local_disk('nvme:500') == 'nvme:500'

    def test_full_format_ssd_with_plus(self):
        assert resources_utils.normalize_local_disk('ssd:1000+') == 'ssd:1000+'

    def test_full_format_ssd_exact(self):
        assert resources_utils.normalize_local_disk('ssd:500') == 'ssd:500'

    def test_full_format_float_size(self):
        assert resources_utils.normalize_local_disk(
            'nvme:1000.5+') == 'nvme:1000.5+'

    # Valid inputs - mode only (defaults to 100+)
    def test_mode_only_nvme(self):
        assert resources_utils.normalize_local_disk('nvme') == 'nvme:100+'

    def test_mode_only_ssd(self):
        assert resources_utils.normalize_local_disk('ssd') == 'ssd:100+'

    def test_mode_with_plus_nvme_invalid(self):
        # nvme+ is invalid - ambiguous syntax
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('nvme+')

    def test_mode_with_plus_ssd_invalid(self):
        # ssd+ is invalid - ambiguous syntax
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('ssd+')

    # Valid inputs - size only (defaults to nvme)
    def test_size_only_with_plus(self):
        assert resources_utils.normalize_local_disk('1000+') == 'nvme:1000+'

    def test_size_only_exact(self):
        assert resources_utils.normalize_local_disk('500') == 'nvme:500'

    def test_size_only_float(self):
        assert resources_utils.normalize_local_disk('1000.5') == 'nvme:1000.5'

    # Case insensitivity
    def test_uppercase_mode(self):
        assert resources_utils.normalize_local_disk(
            'NVME:1000+') == 'nvme:1000+'

    def test_mixed_case(self):
        assert resources_utils.normalize_local_disk('NvMe:500') == 'nvme:500'

    # Whitespace handling
    def test_leading_trailing_whitespace(self):
        assert resources_utils.normalize_local_disk(
            '  nvme:1000+  ') == 'nvme:1000+'

    # Invalid inputs - bad mode
    def test_invalid_mode(self):
        with pytest.raises(ValueError, match='Invalid local_disk mode'):
            resources_utils.normalize_local_disk('hdd:1000+')

    def test_invalid_mode_typo(self):
        with pytest.raises(ValueError, match='Invalid local_disk mode'):
            resources_utils.normalize_local_disk('nmve:1000+')

    # Invalid inputs - bad size
    def test_negative_size(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('nvme:-100')

    def test_zero_size(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('nvme:0')

    def test_non_numeric_size(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('nvme:abc')

    def test_empty_size(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('nvme:')

    # Invalid inputs - bad format
    def test_too_many_colons(self):
        with pytest.raises(ValueError, match='Invalid local_disk format'):
            resources_utils.normalize_local_disk('nvme:1000:extra')

    def test_empty_string(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('')

    def test_just_colon(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk(':')

    def test_weird_device_name(self):
        with pytest.raises(ValueError, match='Invalid local_disk'):
            resources_utils.normalize_local_disk('fakessd')


class TestParseLocalDiskStr:
    """Tests for parse_local_disk_str function."""

    def test_nvme_with_plus(self):
        mode, size, at_least = resources_utils.parse_local_disk_str(
            'nvme:1000+')
        assert mode == 'nvme'
        assert size == 1000.0
        assert at_least is True

    def test_nvme_exact(self):
        mode, size, at_least = resources_utils.parse_local_disk_str('nvme:500')
        assert mode == 'nvme'
        assert size == 500.0
        assert at_least is False

    def test_ssd_with_plus(self):
        mode, size, at_least = resources_utils.parse_local_disk_str('ssd:2000+')
        assert mode == 'ssd'
        assert size == 2000.0
        assert at_least is True

    def test_ssd_exact(self):
        mode, size, at_least = resources_utils.parse_local_disk_str('ssd:750')
        assert mode == 'ssd'
        assert size == 750.0
        assert at_least is False

    def test_float_size(self):
        mode, size, at_least = resources_utils.parse_local_disk_str(
            'nvme:1000.5+')
        assert mode == 'nvme'
        assert size == 1000.5
        assert at_least is True


class TestLocalDiskSatisfied:
    """Tests for local_disk_satisfied function."""

    # None handling
    def test_requested_none(self):
        assert resources_utils.local_disk_satisfied(None, 'nvme:1000') is True

    def test_launched_none_requested_set(self):
        assert resources_utils.local_disk_satisfied('nvme:500+', None) is False

    def test_both_none(self):
        assert resources_utils.local_disk_satisfied(None, None) is True

    # Mode compatibility - nvme requested
    def test_nvme_requested_nvme_launched(self):
        assert resources_utils.local_disk_satisfied('nvme:500+',
                                                    'nvme:1000') is True

    def test_nvme_requested_ssd_launched(self):
        # nvme requested but only ssd available - should fail
        assert resources_utils.local_disk_satisfied('nvme:500+',
                                                    'ssd:1000') is False

    # Mode compatibility - ssd requested
    def test_ssd_requested_ssd_launched(self):
        assert resources_utils.local_disk_satisfied('ssd:500+',
                                                    'ssd:1000') is True

    def test_ssd_requested_nvme_launched(self):
        # ssd requested, nvme available - nvme satisfies ssd
        assert resources_utils.local_disk_satisfied('ssd:500+',
                                                    'nvme:1000') is True

    # Size - at_least matching
    def test_at_least_sufficient(self):
        assert resources_utils.local_disk_satisfied('nvme:500+',
                                                    'nvme:1000') is True

    def test_at_least_exact(self):
        assert resources_utils.local_disk_satisfied('nvme:500+',
                                                    'nvme:500') is True

    def test_at_least_insufficient(self):
        assert resources_utils.local_disk_satisfied('nvme:1000+',
                                                    'nvme:500') is False

    # Size - exact matching
    def test_exact_match(self):
        assert resources_utils.local_disk_satisfied('nvme:500',
                                                    'nvme:500') is True

    def test_exact_close_enough(self):
        # Within 1.0 GB tolerance
        assert resources_utils.local_disk_satisfied('nvme:500',
                                                    'nvme:500.5') is True

    def test_exact_too_different(self):
        # More than 1.0 GB difference
        assert resources_utils.local_disk_satisfied('nvme:500',
                                                    'nvme:502') is False

    def test_exact_larger_launched(self):
        # Exact match requested, but launched is larger - should fail
        assert resources_utils.local_disk_satisfied('nvme:500',
                                                    'nvme:1000') is False

    def test_exact_smaller_launched(self):
        # Exact match requested, but launched is smaller - should fail
        assert resources_utils.local_disk_satisfied('nvme:1000',
                                                    'nvme:500') is False

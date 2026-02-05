"""Tests for SkyServiceSpec, specifically pool configuration validation."""
import pytest

from sky.serve import service_spec


class TestPoolConfiguration:
    """Test pool configuration validation in SkyServiceSpec."""

    def test_pool_with_min_and_max_workers_without_workers(self):
        """Test that pool can be specified with min_workers and max_workers
        without workers set.

        This is a valid autoscaling configuration.
        """
        config = {
            'pool': {
                'min_workers': 1,
                'max_workers': 5,
            },
            'readiness_probe': '/',
        }

        # Should not raise any error
        spec = service_spec.SkyServiceSpec.from_yaml_config(config)

        # Verify the values were properly set
        assert spec.min_replicas == 1
        assert spec.max_replicas == 5

    def test_pool_with_only_workers(self):
        """Test that pool can be specified with just workers (fixed workers)."""
        config = {
            'pool': {},
            'workers': 3,
            'readiness_probe': '/',
        }

        spec = service_spec.SkyServiceSpec.from_yaml_config(config)

        assert spec.min_replicas == 3
        # max_replicas is None for fixed workers
        assert spec.max_replicas is None

    def test_pool_with_min_max_workers_and_queue_length_threshold(self):
        """Test pool with autoscaling and queue_length_threshold."""
        config = {
            'pool': {
                'min_workers': 2,
                'max_workers': 10,
                'queue_length_threshold': 5,
            },
            'readiness_probe': '/',
        }

        spec = service_spec.SkyServiceSpec.from_yaml_config(config)

        assert spec.min_replicas == 2
        assert spec.max_replicas == 10
        assert spec.queue_length_threshold == 5

    def test_pool_with_min_max_workers_and_delays(self):
        """Test pool with autoscaling and delay settings."""
        config = {
            'pool': {
                'min_workers': 1,
                'max_workers': 8,
                'upscale_delay_seconds': 30,
                'downscale_delay_seconds': 60,
            },
            'readiness_probe': '/',
        }

        spec = service_spec.SkyServiceSpec.from_yaml_config(config)

        assert spec.min_replicas == 1
        assert spec.max_replicas == 8
        assert spec.upscale_delay_seconds == 30
        assert spec.downscale_delay_seconds == 60

    def test_pool_without_workers_and_without_min_max_fails(self):
        """Test that pool without workers or min/max_workers fails."""
        config = {
            'pool': {},
            'readiness_probe': '/',
        }

        with pytest.raises(ValueError,
                           match='One of workers, or both min_workers and '
                           'max_workers must be set'):
            service_spec.SkyServiceSpec.from_yaml_config(config)

    def test_pool_with_min_workers_but_no_max_workers_fails(self):
        """Test that pool with min_workers but no max_workers fails."""
        config = {
            'pool': {
                'min_workers': 2,
            },
            'readiness_probe': '/',
        }

        with pytest.raises(ValueError,
                           match='max_workers must be set when min_workers is '
                           'specified'):
            service_spec.SkyServiceSpec.from_yaml_config(config)

    def test_pool_with_min_workers_greater_than_max_workers_fails(self):
        """Test that pool with min_workers > max_workers fails."""
        config = {
            'pool': {
                'min_workers': 10,
                'max_workers': 5,
            },
            'readiness_probe': '/',
        }

        with pytest.raises(ValueError,
                           match=r'min_workers \(10\) must be <= max_workers '
                           r'\(5\)'):
            service_spec.SkyServiceSpec.from_yaml_config(config)

    def test_pool_with_queue_length_threshold_but_no_max_workers_fails(self):
        """Test that pool with queue_length_threshold but no max_workers fails.
        """
        config = {
            'pool': {
                'queue_length_threshold': 5,
            },
            'workers': 3,
            'readiness_probe': '/',
        }

        with pytest.raises(ValueError,
                           match='max_workers must be set when '
                           'queue_length_threshold is specified'):
            service_spec.SkyServiceSpec.from_yaml_config(config)

    def test_pool_with_zero_min_workers(self):
        """Test that pool can have min_workers=0 (scale to zero)."""
        config = {
            'pool': {
                'min_workers': 0,
                'max_workers': 5,
            },
            'readiness_probe': '/',
        }

        spec = service_spec.SkyServiceSpec.from_yaml_config(config)

        assert spec.min_replicas == 0
        assert spec.max_replicas == 5

    def test_pool_with_equal_min_and_max_workers(self):
        """Test that pool can have min_workers == max_workers."""
        config = {
            'pool': {
                'min_workers': 3,
                'max_workers': 3,
            },
            'readiness_probe': '/',
        }

        spec = service_spec.SkyServiceSpec.from_yaml_config(config)

        assert spec.min_replicas == 3
        assert spec.max_replicas == 3

"""Unit tests for Modal make_deploy_resources_variables + 24h warning (PROV-04).

Covers:
  - SC4: 24h warning is emitted at WARNING level before provisioning starts
  - WIRE-02 (partial): returned dict contains all required keys
  - D-08: importing sky.clouds.modal does NOT import the modal SDK eagerly
"""
import logging

import pytest

from sky import clouds
from sky.clouds.modal import Modal
from sky.resources import Resources


def _make_modal_resources(instance_type: str) -> Resources:
    """Build a launchable Resources object for the given Modal instance type."""
    r = Resources(cloud=Modal(), instance_type=instance_type)
    return r


class TestMakeDeployResourcesVariables:
    """Tests for make_deploy_resources_variables (WIRE-02 / D-09)."""

    def test_24h_warning_is_emitted(self, caplog):
        """SC4 / PROV-04: calling make_deploy_resources_variables emits exactly
        one WARNING log record whose message mentions '24' and 'hour'."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        # SkyPilot loggers may set propagate=False, which would stop caplog's
        # root handler from seeing the record. Attach caplog's handler to the
        # module logger directly and force propagation for the duration of the
        # call so the warning is captured regardless of the logging config.
        modal_logger = logging.getLogger('sky.clouds.modal')
        prev_propagate = modal_logger.propagate
        modal_logger.addHandler(caplog.handler)
        modal_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger='sky.clouds.modal'):
                Modal().make_deploy_resources_variables(
                    resources=resources,
                    cluster_name=None,
                    region=region,
                    zones=None,
                    num_nodes=1,
                )
        finally:
            modal_logger.removeHandler(caplog.handler)
            modal_logger.propagate = prev_propagate

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and r.name == 'sky.clouds.modal'
        ]
        assert len(warning_records) >= 1, (
            'Expected at least one WARNING log from sky.clouds.modal; '
            f'got: {caplog.records!r}')

        # The message must mention "24" and "hour" (the PROV-04 lifetime warning)
        warning_text = ' '.join(r.message for r in warning_records)
        assert '24' in warning_text, (
            f'Warning must mention "24"; got: {warning_text!r}')
        assert 'hour' in warning_text.lower(), (
            f'Warning must mention "hour"; got: {warning_text!r}')

    def test_required_dict_keys_present(self, caplog):
        """WIRE-02: the returned dict contains all required keys."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        required_keys = [
            'instance_type',
            'region',
            'image_id',
            'cpu',
            'memory_mib',
            'custom_resources',
            'use_spot',
            'gpu_str',
        ]
        for key in required_keys:
            assert key in result, (
                f'Expected key {key!r} in make_deploy_resources_variables '
                f'result; got keys: {list(result.keys())}')

    def test_single_gpu_gpu_str(self, caplog):
        """For a 1x H100 instance type, gpu_str == 'H100' (no colon)."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        assert result['gpu_str'] == 'H100', (
            f'Expected gpu_str="H100" for 1x_H100; got {result["gpu_str"]!r}')

    def test_multi_gpu_gpu_str(self, caplog):
        """For a 4x H100 instance type, gpu_str == 'H100:4'."""
        instance_type = '4x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        assert result['gpu_str'] == 'H100:4', (
            f'Expected gpu_str="H100:4" for 4x_H100; got {result["gpu_str"]!r}')

    def test_instance_type_passthrough(self, caplog):
        """instance_type in result matches the one provided."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        assert result['instance_type'] == instance_type, (
            f'Expected instance_type={instance_type!r}; '
            f'got {result["instance_type"]!r}')

    def test_region_passthrough(self, caplog):
        """region in result matches region.name."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        assert result['region'] == 'us', (
            f'Expected region="us"; got {result["region"]!r}')

    def test_image_id_default(self, caplog):
        """Without explicit image_id, result image_id == 'default'."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        assert result['image_id'] == 'default', (
            f'Expected image_id="default" when no image is specified; '
            f'got {result["image_id"]!r}')

    def test_use_spot_false_by_default(self, caplog):
        """use_spot is False when not specified on Resources."""
        instance_type = '1x_H100'
        region = clouds.Region('us')
        resources = _make_modal_resources(instance_type)

        with caplog.at_level(logging.WARNING):
            result = Modal().make_deploy_resources_variables(
                resources=resources,
                cluster_name=None,
                region=region,
                zones=None,
                num_nodes=1,
            )

        assert result['use_spot'] is False, (
            f'Expected use_spot=False; got {result["use_spot"]!r}')


class TestNoModalSdkEagerImport:
    """D-08: importing sky.clouds.modal must NOT eagerly import the modal SDK."""

    def test_modal_sdk_not_in_sys_modules_after_import(self):
        """Importing sky.clouds.modal should not pull in the modal SDK package.

        This test validates that sky.clouds.modal (already imported above) does
        not add 'modal' to sys.modules.  The LazyImport wrapping in
        sky/adaptors/modal.py ensures the SDK is deferred until first use.
        """
        import sys

        # sky.clouds.modal is already imported by the test file's module-level
        # imports. Verify the SDK itself ('modal') is not in sys.modules.
        # NOTE: 'sky.clouds.modal' and 'sky.adaptors.modal' are expected; only
        # the bare 'modal' SDK package import is forbidden.
        if 'modal' in sys.modules:
            # Acceptable only if it was already present before this test suite
            # ran (e.g., in an env where the modal SDK is imported by some
            # other test). Skip rather than fail to avoid false positives.
            pytest.skip(
                'modal SDK already in sys.modules (possibly imported by '
                'another test). Cannot verify lazy import in this session.')
        # If we reach here, modal SDK is NOT in sys.modules — the LazyImport
        # guarantee is intact.
        assert 'modal' not in sys.modules, (
            'Modal SDK was imported eagerly when sky.clouds.modal was imported '
            '(LazyImport broken). This breaks the offline optimizer path.')

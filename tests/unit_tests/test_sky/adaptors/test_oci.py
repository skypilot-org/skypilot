"""Tests for OCI adaptor."""
import logging

import pytest

from sky import credentials_check
from sky.adaptors import oci
from sky.utils import log_utils


def test_oci_circuit_breaker_logging():
    """Test that OCI circuit breaker logging is properly configured."""
    # Get the circuit breaker logger
    logger = logging.getLogger('oci.circuit_breaker')

    # Create a handler that captures log records
    log_records = []
    test_handler = logging.Handler()
    test_handler.emit = lambda record: log_records.append(record)
    logger.addHandler(test_handler)

    # Create a null handler to suppress logs during import
    null_handler = logging.NullHandler()
    logger.addHandler(null_handler)

    try:
        # Verify logger starts at WARNING level (set by adaptor initialization)
        initial_level = logger.getEffectiveLevel()
        print(
            f'Initial logger level: {initial_level} (WARNING={logging.WARNING})'
        )
        assert initial_level == logging.WARNING, (
            'OCI circuit breaker logger should be set to WARNING before initialization'
        )

        # Force OCI module import through LazyImport by accessing a module attribute
        print('Attempting to import OCI module...')
        try:
            # This will trigger LazyImport's load_module for the actual OCI module
            _ = oci.oci.config.DEFAULT_LOCATION
        except (ImportError, AttributeError) as e:
            # Expected when OCI SDK is not installed
            print(f'Import/Attribute error as expected: {e}')
            pass

        # Verify logger level after import attempt
        after_level = logger.getEffectiveLevel()
        print(
            f'Logger level after import: {after_level} (WARNING={logging.WARNING})'
        )
        assert after_level == logging.WARNING, (
            'OCI circuit breaker logger should remain at WARNING after initialization'
        )

        # Verify no circuit breaker logs were emitted
        circuit_breaker_logs = [
            record for record in log_records
            if 'Circuit breaker' in record.getMessage()
        ]
        assert not circuit_breaker_logs, (
            'No circuit breaker logs should be emitted during initialization')
    finally:
        # Clean up the handlers
        logger.removeHandler(test_handler)
        logger.removeHandler(null_handler)

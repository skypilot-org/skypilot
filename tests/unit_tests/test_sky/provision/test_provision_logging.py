"""Tests for sky.provision.logging."""
import logging

import pytest

from sky.provision import logging as provision_logging


@pytest.fixture(name='provision_logger')
def fixture_provision_logger():
    """Starts from the default (propagating), whatever ran before."""
    logger = logging.getLogger('sky.provision')
    original = logger.propagate
    logger.propagate = True
    yield logger
    logger.propagate = original


def test_setup_provision_logging_restores_propagation(provision_logger,
                                                      tmp_path):
    """Logs outside a provision must reach the request log again afterwards.

    A long-lived API server worker runs many launches; leaving propagation off
    after the first one silently drops every later sky.provision.* INFO line
    logged outside a provision (e.g. during deploy-variable rendering).
    """
    with provision_logging.setup_provision_logging(str(tmp_path)):
        assert provision_logger.propagate is False
    assert provision_logger.propagate is True


def test_overlapping_provisions_restore_after_the_last(provision_logger,
                                                       tmp_path):
    """Threads in one process overlap, and exit in any order."""
    first = provision_logging.setup_provision_logging(str(tmp_path / 'a'))
    second = provision_logging.setup_provision_logging(str(tmp_path / 'b'))
    first.__enter__()  # pylint: disable=unnecessary-dunder-call
    second.__enter__()  # pylint: disable=unnecessary-dunder-call
    first.__exit__(None, None, None)
    # The second provision is still running: its logs stay out of the console.
    assert provision_logger.propagate is False
    second.__exit__(None, None, None)
    assert provision_logger.propagate is True

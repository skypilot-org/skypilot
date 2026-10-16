"""Tests for sky.provision.logging."""
import logging

import pytest

from sky import sky_logging
from sky.provision import logging as provision_logging
from sky.skylet import constants


@pytest.fixture(name='provision_logger')
def fixture_provision_logger():
    """Starts from the default (propagating), whatever ran before."""
    logger = logging.getLogger('sky.provision')
    original = logger.propagate
    logger.propagate = True
    # A test that fails mid-overlap must not leave the count raised.
    provision_logging._active_provisions = 0  # pylint: disable=protected-access
    yield logger
    provision_logging._active_provisions = 0  # pylint: disable=protected-access
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


class _Collect(logging.Handler):

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_handler_on_both_loggers_writes_each_record_once(provision_logger):
    handler = _Collect()
    sky_logging.attach_to_sky_and_provision(handler)
    try:
        child = logging.getLogger('sky.provision.test')
        child.warning('propagating')
        provision_logger.propagate = False
        child.warning('not propagating')
    finally:
        sky_logging.detach_from_sky_and_provision(handler)
    assert handler.messages == ['propagating', 'not propagating']


def test_request_debug_log_has_no_duplicates(provision_logger, tmp_path,
                                             monkeypatch):
    del provision_logger  # Propagating, as outside any provision.
    monkeypatch.setenv(constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING, 'true')
    monkeypatch.setattr(sky_logging, 'DEBUG_LOG_DIR', str(tmp_path))
    with sky_logging.add_debug_log_handler('req'):
        logging.getLogger('sky.provision.test').warning('marker-6414')
    assert (tmp_path / 'req.log').read_text().count('marker-6414') == 1


def test_setup_failure_is_not_masked(provision_logger, tmp_path):
    """A failure before the handlers exist surfaces as itself."""
    not_a_dir = tmp_path / 'file'
    not_a_dir.write_text('')
    with pytest.raises(OSError):
        with provision_logging.setup_provision_logging(str(not_a_dir / 'x')):
            pass
    assert provision_logger.propagate is True

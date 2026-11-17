"""Tests for nebius adaptor log filters (grpc.aio poller noise)."""
# pylint: disable=protected-access
import logging

import pytest

# pylint: disable=wrong-import-position
from sky.adaptors import nebius


@pytest.fixture
def asyncio_logger_without_filters():
    """Snapshot and restore the 'asyncio' logger's filters and level."""
    asyncio_logger = logging.getLogger('asyncio')
    original_filters = list(asyncio_logger.filters)
    original_level = asyncio_logger.level
    original_propagate = asyncio_logger.propagate
    asyncio_logger.setLevel(logging.DEBUG)
    yield asyncio_logger
    asyncio_logger.filters = original_filters
    asyncio_logger.setLevel(original_level)
    asyncio_logger.propagate = original_propagate


def test_grpc_poller_filter_downgrades_poller_records(
        asyncio_logger_without_filters):
    filter_ = nebius._NebiusGrpcPollerFilter()
    record = logging.LogRecord(
        name='asyncio',
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg='Exception in callback '
        'PollerCompletionQueue._handle_events(<_UnixSelectorEventLoop>)()',
        args=(),
        exc_info=(BlockingIOError,
                  BlockingIOError(11, 'Resource temporarily '
                                  'unavailable'), None),
    )
    assert not filter_.filter(record)


def test_grpc_poller_filter_keeps_other_asyncio_records(
        asyncio_logger_without_filters):
    filter_ = nebius._NebiusGrpcPollerFilter()
    record = logging.LogRecord(name='asyncio',
                               level=logging.ERROR,
                               pathname=__file__,
                               lineno=1,
                               msg='Exception in callback my_callback()',
                               args=(),
                               exc_info=None)
    assert filter_.filter(record)


def test_set_nebius_loggers_blocks_poller_noise_from_root(
        asyncio_logger_without_filters, caplog):
    nebius._set_nebius_loggers()
    with caplog.at_level(logging.DEBUG, logger='sky.adaptors.nebius'):
        asyncio_logger_without_filters.error(
            'Exception in callback '
            'PollerCompletionQueue._handle_events(<...>)()')
        asyncio_logger_without_filters.error('Exception in callback other()')
    # The poller ERROR record must not reach any handler, while the
    # unrelated asyncio error keeps propagating.
    poller_errors = [
        r for r in caplog.records if
        'PollerCompletionQueue' in r.getMessage() and r.levelno >= logging.ERROR
    ]
    assert not poller_errors
    assert 'Exception in callback other()' in caplog.text

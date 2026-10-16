"""Shared fixtures for the unit test suite."""
import sys

import pytest

from sky.utils import context


@pytest.fixture(autouse=True)
def _isolate_context():
    """Reset the SkyPilotContext ContextVar around every unit test.

    ``context.initialize()`` only does ``_CONTEXT.set(...)``. A test that
    calls it from the main thread and never resets the ContextVar leaks its
    SkyPilotContext into every later test on the same worker.

    The leak is not inert. ``skypilot_config._get_config_context()`` returns
    the *context's own* config snapshot whenever the ContextVar is set, and
    falls back to the process-global config only when it is not. So a test
    that reloads the config from another thread writes the global while the
    main thread keeps reading the snapshot the leaked context was built
    with, and the reload looks like it never happened.

    Which test pays for the leak depends on how ``--dist worksteal`` happens
    to order the suite, so the failures move between files and between runs.
    Resetting here removes the coupling instead of chasing it one victim at
    a time.
    """
    # pylint: disable=protected-access
    token = context._CONTEXT.set(None)
    try:
        yield
    finally:
        context._CONTEXT.reset(token)


@pytest.fixture(autouse=True)
def _clear_sa_token_cache():
    """Drop service-account auth rows cached by an earlier test.

    The cache is per process, so a token one test authenticated would
    otherwise skip the DB lookups another test mocks for the same token.
    """
    cache = sys.modules.get('sky.server.auth.sa_token_cache')
    if cache is not None:
        cache.clear()
    yield

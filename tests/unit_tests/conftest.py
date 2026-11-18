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

    The process-scope fallback store, ``context._PROCESS_GLOBAL_VARS``, leaks
    the same way: with no context set, ``context.set_context_var()`` writes
    there, so a test that runs ``common_utils.set_request_context()`` (e.g.
    via ``executor.override_request_env_and_config()``) leaves its user and
    request ID behind, and every later test on the worker thinks it runs
    inside that API request. Restore it too.
    """
    # pylint: disable=protected-access
    process_vars = dict(context._PROCESS_GLOBAL_VARS)
    token = context._CONTEXT.set(None)
    try:
        yield
    finally:
        context._CONTEXT.reset(token)
        context._PROCESS_GLOBAL_VARS.clear()
        context._PROCESS_GLOBAL_VARS.update(process_vars)


@pytest.fixture(autouse=True)
def _restore_tracebacklimit():
    """Restore ``sys.tracebacklimit`` after every unit test.

    ``ux_utils.print_exception_no_traceback()`` sets it to 0 and, by design,
    leaves it at 0 when the exception escapes, so the CLI prints the error
    without a traceback. In the test process that value outlives the test:
    every later test on the same worker gets frame-less tracebacks, and
    ``traceback.format_exc()`` drops its ``Traceback (most recent call
    last):`` header. Tests that assert on a formatted traceback then fail or
    pass depending on how ``--dist worksteal`` ordered the suite.
    """
    had_limit = hasattr(sys, 'tracebacklimit')
    original = getattr(sys, 'tracebacklimit', None)
    yield
    if had_limit:
        sys.tracebacklimit = original
    elif hasattr(sys, 'tracebacklimit'):
        del sys.tracebacklimit


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

"""Unit tests for sky.server.state shutdown callback API.

The shutdown-callback mechanism is the integration point between OSS
signal handlers (Server.handle_exit / SlowStartMultiprocess.handle_term)
and out-of-tree plugins that host their own in-process state (e.g. the
HA plugin's /api/readyz). The contract:

* set_shutting_down() is idempotent — multiple SIGTERM paths in the
  same process must not fire callbacks twice.
* Callbacks registered AFTER shutdown has already started must still
  fire (late registration during a racing shutdown is otherwise lost).
* A failing callback does not block subsequent callbacks — we're on a
  signal-handling path with no good way to surface errors.
"""
import pytest

from sky.server import state


@pytest.fixture(autouse=True)
def _reset_state():
    state.reset_shutting_down_for_tests()
    yield
    state.reset_shutting_down_for_tests()


class TestSetShuttingDown:

    def test_initial_state_is_false(self):
        assert state.is_shutting_down() is False

    def test_set_flips_flag(self):
        state.set_shutting_down()
        assert state.is_shutting_down() is True

    def test_fires_registered_callbacks_in_order(self):
        calls = []
        state.register_shutdown_callback(lambda: calls.append('a'))
        state.register_shutdown_callback(lambda: calls.append('b'))
        state.register_shutdown_callback(lambda: calls.append('c'))
        state.set_shutting_down()
        assert calls == ['a', 'b', 'c']

    def test_idempotent_no_double_fire(self):
        """Multiple paths can call set_shutting_down (e.g. Server.handle_exit
        in a worker AND SlowStartMultiprocess.handle_term in main if they
        share a process). Callbacks must fire exactly once."""
        calls = []
        state.register_shutdown_callback(lambda: calls.append('fired'))
        state.set_shutting_down()
        state.set_shutting_down()
        state.set_shutting_down()
        assert calls == ['fired']

    def test_failing_callback_does_not_block_others(self):
        calls = []

        def boom():
            raise RuntimeError('bad callback')

        state.register_shutdown_callback(lambda: calls.append('first'))
        state.register_shutdown_callback(boom)
        state.register_shutdown_callback(lambda: calls.append('third'))
        state.set_shutting_down()  # Must not raise.
        assert calls == ['first', 'third']


class TestRegisterShutdownCallback:

    def test_late_registration_fires_immediately(self):
        """Plugins that register their callback during a racing shutdown
        must not lose the notification — fire synchronously when the
        flag is already set."""
        state.set_shutting_down()
        calls = []
        state.register_shutdown_callback(lambda: calls.append('late'))
        assert calls == ['late']

    def test_late_registration_failing_callback_swallowed(self):
        """Same swallow-and-continue semantics on the late path."""
        state.set_shutting_down()

        def boom():
            raise RuntimeError('late and bad')

        # Should not raise.
        state.register_shutdown_callback(boom)

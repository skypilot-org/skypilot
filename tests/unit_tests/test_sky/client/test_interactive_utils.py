"""Tests for client-side interactive SSH authentication utilities."""
import asyncio
import os
import pty
import termios
import threading
from unittest import mock

import pytest

from sky.client import interactive_utils
from sky.utils import context_utils


def test_interactive_auth_websocket_bridge_and_terminal_handling():
    """Test client-side interactive authentication via websocket bridge."""
    stdin_master, stdin_slave = pty.openpty()
    stdout_master, stdout_slave = pty.openpty()

    try:
        initial_settings = termios.tcgetattr(stdin_slave)

        class MockWebsocket:

            def __init__(self):
                self.sent = []
                self.to_send = [b'Verification code: ', b'OK\n']
                self.ready = threading.Event()  # Signals: reader is set up
                self.data_received = threading.Event(
                )  # Signals: stdin data sent
                self.settings_during = None  # Capture settings after setraw

            async def __aenter__(self):
                # Capture terminal settings AFTER setraw was called
                self.settings_during = termios.tcgetattr(stdin_slave)
                return self

            async def __aexit__(self, *args):
                pass

            async def send(self, data):
                self.sent.append(data)
                self.data_received.set()

            def __aiter__(self):
                return self

            async def __anext__(self):
                # Signal readiness on first iteration.
                if not self.ready.is_set():
                    self.ready.set()

                if not self.to_send:
                    # Wait for stdin data before completing
                    await asyncio.to_thread(self.data_received.wait, 5.0)
                    raise StopAsyncIteration
                return self.to_send.pop(0)

        mock_ws = MockWebsocket()

        stdin_file = os.fdopen(os.dup(stdin_slave), 'r')
        stdout_file = os.fdopen(os.dup(stdout_slave), 'w')

        def simulate_user():
            """Simulate user typing password."""
            if not mock_ws.ready.wait(timeout=5.0):
                return
            os.write(stdin_master, b'123456\n')
            mock_ws.data_received.wait(timeout=5.0)

        with mock.patch('sys.stdin', stdin_file), \
             mock.patch('sys.stdout', stdout_file), \
             mock.patch('sky.client.interactive_utils.websockets.connect',
                        return_value=mock_ws), \
             mock.patch('sky.server.common.get_server_url',
                        return_value='http://test'), \
             mock.patch('sky.client.service_account_auth.get_service_account_headers',
                        return_value={}), \
             mock.patch('sky.server.common.get_cookie_header_for_url',
                        return_value={}):

            assert os.isatty(stdin_file.fileno()), "stdin must be a tty"

            user_thread = threading.Thread(target=simulate_user)
            user_thread.start()

            asyncio.run(
                interactive_utils._handle_interactive_auth_websocket('test'))

            user_thread.join(timeout=5.0)

        # stdin -> websocket
        assert b'123456\n' in b''.join(mock_ws.sent), "stdin->websocket failed"

        # websocket -> stdout
        stdout_output = os.read(stdout_master, 4096)
        assert b'Verification code:' in stdout_output, "websocket->stdout failed"

        # setraw was called (settings changed during execution)
        assert mock_ws.settings_during != initial_settings, "setraw not called"

        # Terminal settings restored
        final_settings = termios.tcgetattr(stdin_slave)
        assert final_settings == initial_settings, "Terminal not restored!"

    finally:
        for fd in [stdin_master, stdin_slave, stdout_master, stdout_slave]:
            try:
                os.close(fd)
            except OSError:
                pass

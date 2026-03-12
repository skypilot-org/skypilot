"""Client-side OAuth module."""
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import threading
import time
from typing import Dict, Optional

from sky.server import constants as server_constants


class _AuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback."""

    def __init__(self, token_container: Dict[str, Optional[str]],
                 remote_endpoint: str, *args, **kwargs):
        self.token_container = token_container
        self.remote_endpoint = remote_endpoint
        super().__init__(*args, **kwargs)

    def do_POST(self):  # pylint: disable=invalid-name
        """Handle POST request for OAuth callback."""
        data = self.rfile.read(int(self.headers['Content-Length']))

        if data:
            token = data.decode('utf-8')
            self.token_container['token'] = token

            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Access-Control-Allow-Origin',
                             self.remote_endpoint)
            self.end_headers()
        else:
            # Send error response
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.send_header('Access-Control-Allow-Origin',
                             self.remote_endpoint)
            self.end_headers()

    def log_message(self, *args):  # pylint: disable=unused-argument
        """Suppress default HTTP server logging."""
        pass


def start_local_auth_server(
        port: int,
        token_store: Dict[str, Optional[str]],
        remote_endpoint: str,
        timeout: int = server_constants.AUTH_SESSION_TIMEOUT_SECONDS
) -> HTTPServer:
    """Start a local HTTP server to handle OAuth callback.

    Args:
        port: Port to bind the server to.
        token_container: Dict to store the received token.
        remote_endpoint: The endpoint of the SkyPilot API server that will send
          the token, needed for CORS.
        timeout: Timeout in seconds to wait for the callback.

    Returns:
        The HTTP server instance.
    """

    def handler_factory(*args, **kwargs):
        return _AuthCallbackHandler(token_store, remote_endpoint, *args,
                                    **kwargs)

    server = HTTPServer(('localhost', port), handler_factory)
    server.timeout = timeout

    def serve_until_token():
        """Serve requests until token is received or timeout."""
        start_time = time.time()
        while (token_store['token'] is None and
               time.time() - start_time < timeout):
            server.handle_request()

    # Start server in a separate thread
    server_thread = threading.Thread(target=serve_until_token, daemon=True)
    server_thread.start()

    return server

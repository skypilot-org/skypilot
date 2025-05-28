"""Client-side OAuth module."""
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import threading
import time
from typing import Dict, Optional
from urllib import parse as urlparse

AUTH_TIMEOUT = 300  # 5 minutes


class _AuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback."""

    def __init__(self, token_container: Dict[str, Optional[str]], *args,
                 **kwargs):
        self.token_container = token_container
        super().__init__(*args, **kwargs)

    def do_GET(self):  # pylint: disable=invalid-name
        """Handle GET request for OAuth callback."""
        parsed_url = urlparse.urlparse(self.path)
        query_params = urlparse.parse_qs(parsed_url.query)

        if 'token' in query_params:
            token = query_params['token'][0]
            self.token_container['token'] = token

            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            success_html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>SkyPilot Authentication</title>
                <style>
                    body { 
                        font-family: Arial, sans-serif; 
                        text-align: center; 
                        padding: 50px; 
                        background-color: #f5f5f5; 
                    }
                    .container { 
                        background: white; 
                        padding: 30px; 
                        border-radius: 10px; 
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                        max-width: 500px; 
                        margin: 0 auto; 
                    }
                    .success { color: #28a745; }
                    .logo { font-size: 24px; margin-bottom: 20px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h2 class="success">Authentication Successful!</h2>
                    <p>You have successfully authenticated with SkyPilot.</p>
                    <p>You can now close this window and return to your 
                       terminal.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode('utf-8'))
        else:
            # Send error response
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            error_html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>SkyPilot Authentication Error</title>
                <style>
                    body { 
                        font-family: Arial, sans-serif; 
                        text-align: center; 
                        padding: 50px; 
                        background-color: #f5f5f5; 
                    }
                    .container { 
                        background: white; 
                        padding: 30px; 
                        border-radius: 10px; 
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                        max-width: 500px; 
                        margin: 0 auto; 
                    }
                    .error { color: #dc3545; }
                    .logo { font-size: 24px; margin-bottom: 20px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h2 class="error">Authentication Failed</h2>
                    <p>No authentication token was received.</p>
                </div>
            </body>
            </html>
            """
            self.wfile.write(error_html.encode('utf-8'))

    def log_message(self, *args):  # pylint: disable=unused-argument
        """Suppress default HTTP server logging."""
        pass


def start_local_auth_server(port: int,
                            token_store: Dict[str, Optional[str]],
                            timeout: int = AUTH_TIMEOUT) -> HTTPServer:
    """Start a local HTTP server to handle OAuth callback.

    Args:
        port: Port to bind the server to.
        token_container: Dict to store the received token.
        timeout: Timeout in seconds to wait for the callback.

    Returns:
        The HTTP server instance.
    """

    def handler_factory(*args, **kwargs):
        return _AuthCallbackHandler(token_store, *args, **kwargs)

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

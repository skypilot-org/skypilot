"""Utilities for building middlewares."""
import http
from typing import List, Tuple, Type

import fastapi
import starlette.middleware.base

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def websocket_aware(
        middleware_cls: Type[starlette.middleware.base.BaseHTTPMiddleware]):
    """Decorator to adapt BaseHTTPMiddleware to handle WebSockets.

    It assembles an HTTP-style request like the HTTP upgrade request during
    websocket handshake and then delegates it to the real HTTP middleware.
    """

    class WebSocketAwareMiddleware:
        """WebSocket-aware middleware wrapper."""

        def __init__(self, app, *args, **kwargs):
            self.app = app
            self.middleware = middleware_cls(*args, app=app, **kwargs)

        async def __call__(self, scope, receive, send):
            scope_type = scope.get('type')
            if scope_type == 'http':
                await self.middleware(scope, receive, send)
            elif scope_type == 'websocket':
                await self._handle_websocket(scope, receive, send)
            elif scope_type == 'lifespan':
                await self.app(scope, receive, send)
            else:
                raise ValueError(f'Invalid scope type: {scope_type}')

        async def _handle_websocket(self, scope, receive, send):
            """Handle websocket connection by delegating to HTTP middleware."""
            decision, headers = await self._run_websocket_dispatch(scope)
            if decision == 'accept':
                await self._call_with_accept_headers(scope, receive, send,
                                                     headers)
            elif decision == 'unauthorized':
                await send({
                    'type': 'websocket.close',
                    'code': 4401,
                    'reason': 'Unauthorized',
                })
            elif decision == 'forbidden':
                await send({
                    'type': 'websocket.close',
                    'code': 4403,
                    'reason': 'Forbidden',
                })
            else:
                await send({
                    'type': 'websocket.close',
                    'code': 1011,
                    'reason': 'Internal Server Error',
                })

        async def _run_websocket_dispatch(
                self, scope) -> Tuple[str, List[Tuple[bytes, bytes]]]:
            http_scope = self._build_http_scope(scope)
            http_receive = self._http_receive_adapter()
            request = fastapi.Request(http_scope, receive=http_receive)
            call_next_called = False
            stub_response = fastapi.Response(status_code=http.HTTPStatus.OK)

            async def call_next(req):
                del req
                nonlocal call_next_called
                call_next_called = True
                return stub_response

            try:
                response = await self.middleware.dispatch(request, call_next)
            except Exception as e:  # pylint: disable=broad-except
                logger.error('Exception occurred in middleware dispatch for '
                             f'WebSocket scope: {e}')
                return 'error', []

            if response is None:
                response = stub_response

            status_code = response.status_code

            if call_next_called and 200 <= status_code < 400:
                # Capture mutated headers from the HTTP middleware if any.
                return 'accept', response.raw_headers

            if status_code == http.HTTPStatus.UNAUTHORIZED:
                return 'unauthorized', []
            if status_code == http.HTTPStatus.FORBIDDEN:
                return 'forbidden', []

            return 'error', []

        async def _call_with_accept_headers(self, scope, receive, send,
                                            headers: List[Tuple[bytes, bytes]]):
            if not headers:
                await self.app(scope, receive, send)
                return

            first_accept_sent = False

            async def send_wrapper(message):
                nonlocal first_accept_sent
                if (message['type'] == 'websocket.accept' and
                        not first_accept_sent):
                    first_accept_sent = True
                    additional_headers: List[Tuple[bytes, bytes]] = message.get(
                        'headers', [])
                    additional_headers.extend(headers)
                    message['headers'] = additional_headers
                await send(message)

            await self.app(scope, receive, send_wrapper)

        @staticmethod
        def _build_http_scope(scope):
            state = scope.setdefault('state', {})
            scheme = scope.get('scheme', 'ws')
            if scheme == 'ws':
                http_scheme = 'http'
            elif scheme == 'wss':
                http_scheme = 'https'
            else:
                http_scheme = scheme
            http_scope = dict(scope)
            http_scope['type'] = 'http'
            http_scope['scheme'] = http_scheme
            http_scope['method'] = 'GET'
            http_scope['http_version'] = scope.get('http_version', '1.1')
            http_scope['state'] = state
            return http_scope

        @staticmethod
        def _http_receive_adapter():
            sent = False

            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {
                        'type': 'http.request',
                        'body': b'',
                        'more_body': False,
                    }
                return {
                    'type': 'http.disconnect',
                }

            return receive

    WebSocketAwareMiddleware.__name__ = middleware_cls.__name__
    WebSocketAwareMiddleware.__qualname__ = middleware_cls.__qualname__
    WebSocketAwareMiddleware.__module__ = middleware_cls.__module__
    WebSocketAwareMiddleware.__doc__ = middleware_cls.__doc__
    return WebSocketAwareMiddleware

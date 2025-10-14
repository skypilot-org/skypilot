"""Reverse proxy for SkyPilot API server applications.

This module provides a simple async reverse proxy that routes requests to
different backend applications based on the request path. It accepts all
traffic on a single port and distributes it to the appropriate backend.
"""

import asyncio

import aiohttp
import fastapi
from fastapi import responses

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Port configuration
PROXY_PORT = 46580  # Main entry point
MAIN_APP_PORT = 46581  # Main API server
K8S_SSH_PROXY_PORT = 46582  # Kubernetes SSH proxy

# Create the reverse proxy FastAPI application
app = fastapi.FastAPI(debug=True)


async def _proxy_request(
    request: fastapi.Request,
    target_url: str,
) -> responses.Response:
    """Forward an HTTP request to a backend server.

    Args:
        request: The incoming FastAPI request.
        target_url: The target backend URL to forward to.

    Returns:
        Response from the backend server.
    """
    # Prepare headers, removing host header as it will be set by aiohttp
    headers = dict(request.headers)
    headers.pop('host', None)
    # Remove connection-related headers that shouldn't be forwarded
    headers.pop('connection', None)
    headers.pop('keep-alive', None)
    headers.pop('proxy-connection', None)
    headers.pop('upgrade', None)

    # Prepare the target URL with query parameters
    query_string = str(request.url.query)
    if query_string:
        target_url = f'{target_url}?{query_string}'

    session = None
    backend_response = None
    is_streaming = False

    try:
        # Create a session that will be kept alive for streaming
        session = aiohttp.ClientSession()

        # Forward the request
        backend_response = await session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=await request.body(),
            allow_redirects=False,
        )

        # Prepare response headers
        response_headers = {}
        for key, value in backend_response.headers.items():
            # Skip hop-by-hop headers
            lower_key = key.lower()
            if lower_key not in ('connection', 'keep-alive',
                                 'proxy-authenticate', 'proxy-authorization',
                                 'te', 'trailers', 'transfer-encoding',
                                 'upgrade'):
                response_headers[key] = value

        # Check if this is a streaming response
        content_type = backend_response.headers.get('content-type', '')
        is_streaming = ('text/plain' in content_type or
                        'text/event-stream' in content_type or
                        backend_response.headers.get('transfer-encoding')
                        == 'chunked')

        if is_streaming:
            # Stream the response
            async def stream_generator():
                try:
                    async for chunk in backend_response.content.iter_any():
                        if chunk:
                            try:
                                yield chunk
                            except (asyncio.CancelledError, GeneratorExit,
                                    StopAsyncIteration):
                                logger.debug('Client disconnected from stream')
                                break
                except asyncio.CancelledError:
                    # Task was cancelled (e.g., client disconnect)
                    logger.debug('Stream task cancelled')
                except Exception as e:  # pylint: disable=broad-except
                    # Unexpected error while streaming
                    logger.error(f'Error in stream generator: {e}',
                                 exc_info=True)
                finally:
                    # Always clean up resources
                    try:
                        backend_response.close()
                    except Exception as e:  # pylint: disable=broad-except
                        logger.debug(f'Error closing backend response: {e}')
                    try:
                        await session.close()
                    except Exception as e:  # pylint: disable=broad-except
                        logger.debug(f'Error closing session: {e}')

            return responses.StreamingResponse(
                content=stream_generator(),
                status_code=backend_response.status,
                headers=response_headers,
            )
        else:
            # For non-streaming responses, read all content and close
            try:
                content = await backend_response.read()
                return responses.Response(
                    content=content,
                    status_code=backend_response.status,
                    headers=response_headers,
                )
            finally:
                backend_response.close()
                await session.close()

    except aiohttp.ClientError as e:
        logger.error(f'Error proxying request to {target_url}: {e}')
        return responses.JSONResponse(
            status_code=502,
            content={'detail': f'Bad Gateway: {str(e)}'},
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Unexpected error proxying request: {e}', exc_info=True)
        return responses.JSONResponse(
            status_code=500,
            content={'detail': f'Internal Server Error: {str(e)}'},
        )
    finally:
        # Ensure cleanup happens even if there's an exception
        if backend_response is not None and not is_streaming:
            try:
                backend_response.close()
            except Exception:  # pylint: disable=broad-except
                pass
        if session is not None and not is_streaming:
            try:
                await session.close()
            except Exception:  # pylint: disable=broad-except
                pass


async def _proxy_websocket(
    websocket: fastapi.WebSocket,
    target_url: str,
) -> None:
    """Forward a WebSocket connection to a backend server.

    Args:
        websocket: The incoming WebSocket connection.
        target_url: The target backend WebSocket URL.
    """
    await websocket.accept()

    async with aiohttp.ClientSession() as session:
        try:
            # Prepare headers
            headers = dict(websocket.headers)
            headers.pop('host', None)

            async with session.ws_connect(
                    target_url,
                    headers=headers,
            ) as backend_ws:
                # Create tasks for bidirectional forwarding
                async def forward_to_backend():
                    try:
                        async for message in websocket.iter_bytes():
                            await backend_ws.send_bytes(message)
                    except Exception as e:  # pylint: disable=broad-except
                        logger.debug(f'Client to backend forwarding ended: {e}')

                async def forward_to_client():
                    try:
                        async for msg in backend_ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                await websocket.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                await websocket.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f'WebSocket error: '
                                             f'{backend_ws.exception()}')
                                break
                    except Exception as e:  # pylint: disable=broad-except
                        logger.debug(f'Backend to client forwarding ended: {e}')

                # Run both forwarding tasks concurrently
                await asyncio.gather(
                    forward_to_backend(),
                    forward_to_client(),
                    return_exceptions=True,
                )
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error in WebSocket proxy: {e}', exc_info=True)
        finally:
            try:
                await websocket.close()
            except Exception:  # pylint: disable=broad-except
                pass


@app.api_route(
    '/{path:path}',
    methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
async def proxy_http(request: fastapi.Request, path: str) -> responses.Response:
    """Route HTTP requests to the appropriate backend.

    Routes /kubernetes-pod-ssh-proxy to the K8s SSH proxy app,
    all other requests go to the main API server.

    Args:
        request: The incoming request.
        path: The request path.

    Returns:
        Response from the backend server.
    """
    # Determine target based on path
    if path.startswith('kubernetes-pod-ssh-proxy'):
        target_host = f'http://127.0.0.1:{K8S_SSH_PROXY_PORT}'
    else:
        target_host = f'http://127.0.0.1:{MAIN_APP_PORT}'

    target_url = f'{target_host}/{path}'
    return await _proxy_request(request, target_url)


@app.websocket('/{path:path}')
async def proxy_websocket(websocket: fastapi.WebSocket, path: str) -> None:
    """Route WebSocket connections to the appropriate backend.

    Routes /kubernetes-pod-ssh-proxy to the K8s SSH proxy app,
    all other websockets go to the main API server.

    Args:
        websocket: The incoming WebSocket connection.
        path: The request path.
    """
    # Determine target based on path
    if path.startswith('kubernetes-pod-ssh-proxy'):
        target_host = f'ws://127.0.0.1:{K8S_SSH_PROXY_PORT}'
    else:
        target_host = f'ws://127.0.0.1:{MAIN_APP_PORT}'

    # Reconstruct query string if present
    query_string = str(websocket.url.query)
    target_url = f'{target_host}/{path}'
    if query_string:
        target_url = f'{target_url}?{query_string}'

    await _proxy_websocket(websocket, target_url)


@app.get('/proxy/health')
async def health() -> dict:
    """Health check endpoint for the reverse proxy."""
    return {
        'status': 'healthy',
        'service': 'reverse-proxy',
        'proxy_port': PROXY_PORT,
        'main_app_port': MAIN_APP_PORT,
        'k8s_ssh_proxy_port': K8S_SSH_PROXY_PORT,
    }

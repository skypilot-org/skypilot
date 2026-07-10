"""TCP proxy that brings chaos."""
import argparse
import asyncio
from typing import Optional
from urllib import parse

from sky.server import common

# Global connection counter
connection_counter = 0


def _host_header_value(target_host: str, target_port: Optional[int]) -> bytes:
    if target_port in (None, 80):
        return target_host.encode()
    return f'{target_host}:{target_port}'.encode()


def _rewrite_host_header(header_block: bytes, host_value: bytes) -> bytes:
    """Point the request's Host header at the upstream.

    The client connects to the proxy's loopback address, so it sends
    `Host: 127.0.0.1:<port>`. A Host-routing reverse proxy in front of the
    upstream (e.g. an nginx virtual host) would not recognize that authority
    and serve a 404, so rewrite it to the upstream's host before forwarding.
    """
    lines = header_block.split(b'\r\n')
    for i, line in enumerate(lines):
        if line.lower().startswith(b'host:'):
            lines[i] = b'Host: ' + host_value
            break
    return b'\r\n'.join(lines)


def _content_length(header_block: bytes) -> Optional[int]:
    for line in header_block.split(b'\r\n'):
        if line.lower().startswith(b'content-length:'):
            try:
                return int(line.split(b':', 1)[1].strip())
            except ValueError:
                return None
    return None


def _is_chunked(header_block: bytes) -> bool:
    for line in header_block.split(b'\r\n'):
        if (line.lower().startswith(b'transfer-encoding:') and
                b'chunked' in line.lower()):
            return True
    return False


async def _read_until(reader, buffer: bytes, delimiter: bytes):
    """Read until `delimiter` appears in the buffer.

    Returns (consumed, remaining) where `consumed` includes the delimiter, or
    (None, buffer) on EOF before the delimiter is seen.
    """
    while delimiter not in buffer:
        data = await reader.read(4096)
        if not data:
            return None, buffer
        buffer += data
    idx = buffer.find(delimiter) + len(delimiter)
    return buffer[:idx], buffer[idx:]


async def _forward_exact(reader, writer, buffer: bytes, n: int) -> bytes:
    """Forward exactly `n` bytes, draining `buffer` first. Returns leftover."""
    remaining = n
    if buffer:
        chunk = buffer[:remaining]
        writer.write(chunk)
        await writer.drain()
        buffer = buffer[len(chunk):]
        remaining -= len(chunk)
    while remaining > 0:
        data = await reader.read(min(4096, remaining))
        if not data:
            break
        writer.write(data)
        await writer.drain()
        remaining -= len(data)
    return buffer


async def _forward_raw(reader, writer, buffer: bytes = b''):
    """Forward bytes verbatim until EOF."""
    if buffer:
        writer.write(buffer)
        await writer.drain()
    while True:
        data = await reader.read(4096)
        if not data:
            break
        writer.write(data)
        await writer.drain()


async def forward_with_host_rewrite(reader, writer, host_value: bytes):
    """Forward an HTTP/1.1 client->server stream, rewriting Host per request.

    Bodies are framed via Content-Length. A chunked body is not reframed; the
    rest of the connection is forwarded verbatim after its (already rewritten)
    request line. Anything that isn't an HTTP/1.x request (e.g. an HTTP/2
    connection preface) is also forwarded verbatim, since it can't be reframed.
    """
    try:
        buffer = b''
        while True:
            header_block, buffer = await _read_until(reader, buffer,
                                                     b'\r\n\r\n')
            if header_block is None:
                if buffer:
                    writer.write(buffer)
                    await writer.drain()
                break
            request_line = header_block.split(b'\r\n', 1)[0]
            if not (request_line.endswith(b' HTTP/1.1') or
                    request_line.endswith(b' HTTP/1.0')):
                # Not HTTP/1.x; the CRLF framing this parser relies on does not
                # apply, so forward the rest of the connection verbatim.
                writer.write(header_block)
                await writer.drain()
                await _forward_raw(reader, writer, buffer)
                break
            writer.write(_rewrite_host_header(header_block, host_value))
            await writer.drain()
            if _is_chunked(header_block):
                await _forward_raw(reader, writer, buffer)
                break
            content_length = _content_length(header_block)
            if content_length:
                buffer = await _forward_exact(reader, writer, buffer,
                                              content_length)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f'client->server error: {e}')
    finally:
        writer.close()


async def handle_client(local_reader, local_writer, target_host, target_port,
                        interval, connection_id):
    # Print connection info
    client_addr = local_writer.get_extra_info('peername')
    print(
        f'Connection {connection_id}: Client {client_addr[0]}:{client_addr[1]}')

    try:
        remote_reader, remote_writer = await asyncio.open_connection(
            target_host, target_port)
    except Exception as e:
        print(f'Failed to connect to target {target_host}:{target_port} - {e}')
        local_writer.close()
        await local_writer.wait_closed()
        return

    async def forward(reader, writer, direction):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f'{direction} error: {e}')
        finally:
            writer.close()

    host_value = _host_header_value(target_host, target_port)
    task1 = asyncio.create_task(
        forward_with_host_rewrite(local_reader, remote_writer, host_value))
    task2 = asyncio.create_task(
        forward(remote_reader, local_writer, 'server->client'))

    # Wait for either the interval to expire or tasks to complete naturally
    _, pending = await asyncio.wait([task1, task2],
                                    timeout=interval,
                                    return_when=asyncio.FIRST_COMPLETED)

    # Check if connection is still active (tasks haven't completed)
    if pending:
        print(
            f'Connection {connection_id}: Closing connection after {interval}s')
        # Cancel remaining tasks
        for task in pending:
            task.cancel()
        # Close writers
        if not local_writer.is_closing():
            local_writer.close()
        if not remote_writer.is_closing():
            remote_writer.close()

    # Wait for all tasks to complete and close connections
    await asyncio.gather(task1, task2, return_exceptions=True)
    if not local_writer.is_closing():
        local_writer.close()
    if not remote_writer.is_closing():
        remote_writer.close()

    # Ensure connections are properly closed
    try:
        await local_writer.wait_closed()
    except Exception:
        pass
    try:
        await remote_writer.wait_closed()
    except Exception:
        pass


async def main(local_port, interval):
    global connection_counter

    server_url = common.get_server_url()
    parsed = parse.urlparse(server_url)
    target_host = parsed.hostname
    target_port = parsed.port or 80  # Assume HTTP port if not specified

    async def client_handler(reader, writer):
        global connection_counter
        connection_counter += 1
        await handle_client(reader, writer, target_host, target_port, interval,
                            connection_counter)

    server = await asyncio.start_server(client_handler, '127.0.0.1', local_port)
    addrs = ', '.join(str(sock.getsockname()) for sock in server.sockets)
    print(
        f'Serving proxy on {addrs}, forwarding to {target_host}:{target_port}, '
        f'disconnect interval={interval}s')

    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='TCP proxy that brings chaos')
    parser.add_argument('--port',
                        type=int,
                        required=True,
                        help='Local listening port')
    parser.add_argument('--interval',
                        type=int,
                        required=True,
                        help='Seconds before disconnect')
    args = parser.parse_args()

    asyncio.run(main(args.port, args.interval))

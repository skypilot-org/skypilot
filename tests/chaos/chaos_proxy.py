"""TCP proxy that brings chaos."""
import argparse
import asyncio

from sky.server import common

# Global connection counter
connection_counter = 0


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

    task1 = asyncio.create_task(
        forward(local_reader, remote_writer, 'client->server'))
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
    target_host, target_port = server_url.split('://')[1].split(':')

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

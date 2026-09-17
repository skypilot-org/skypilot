"""Wait for the Ray head's GCS endpoint before starting a worker raylet."""

import argparse
import socket
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--timeout', default=900, type=int)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    last_error = 'no connection attempt made'
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((args.host, args.port), timeout=5):
                print(f'Ray head is reachable at {args.host}:{args.port}',
                      flush=True)
                return
        except OSError as error:
            last_error = str(error)
            print(
                f'Waiting for Ray head at {args.host}:{args.port}: '
                f'{last_error}',
                flush=True,
            )
            time.sleep(5)

    raise TimeoutError(
        f'Ray head {args.host}:{args.port} was not reachable within '
        f'{args.timeout}s; last error: {last_error}')


if __name__ == '__main__':
    main()

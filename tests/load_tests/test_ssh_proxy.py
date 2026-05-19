#!/usr/bin/env python3
# SSH Proxy Benchmark

import argparse
import concurrent.futures
from dataclasses import dataclass
from dataclasses import field
import logging
import statistics
import subprocess
import sys
import time
from typing import List, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)
logger.handlers.clear()
logger.propagate = False
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.flush = sys.stdout.flush  # type: ignore
stream_handler.setFormatter(sky_logging.FORMATTER)
logger.addHandler(stream_handler)


class SSHClient:
    """A persistent SSH client for executing commands."""

    def __init__(self, cluster: str):
        self.cluster = cluster
        self.process = None

    def connect(self) -> float:
        """Establish persistent SSH connection.

        Returns the connection latency in seconds, or -1.0 on failure.
        """
        start_time = time.time()
        try:
            # Start SSH with persistent connection
            self.process = subprocess.Popen(
                ['ssh', '-T', self.cluster],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0  # Unbuffered for real-time communication
            )

            # Test the connection with a simple command
            test_result = self._execute_command('echo "CONNECTION_TEST_OK"')
            if test_result[1] and 'CONNECTION_TEST_OK' in test_result[2]:
                latency = time.time() - start_time
                logger.info(f'SSH connection established to {self.cluster} '
                            f'in {latency:.4f}s')
                return latency
            else:
                logger.error(f'SSH connection test failed to {self.cluster}, '
                             f'output: {test_result[2]}')
                self.disconnect()
                return -1.0

        except Exception as e:
            logger.error(f'Failed to establish SSH connection to '
                         f'{self.cluster}: {e}')
            self.disconnect()
            return -1.0

    def _execute_command(self,
                         command: str,
                         timeout: int = 120) -> Tuple[float, bool, str]:
        """Execute command on persistent connection and measure latency."""
        if not self.process:
            return 0.0, False, "Not connected"

        try:
            # Create a unique command wrapper to detect completion
            cmd_id = str(time.time()).replace('.', '')
            wrapped_cmd = (f'{command}; echo "CMD_COMPLETE_{cmd_id}_$?"')

            start_time = time.time()

            self.process.stdin.write(wrapped_cmd + '\n')
            self.process.stdin.flush()

            # Read output until we see our completion marker
            output_lines = []
            while True:
                try:
                    # Use a short timeout for readline to avoid hanging
                    line = self.process.stdout.readline()
                    if not line:
                        break

                    output_lines.append(line.strip())

                    # Check for completion marker
                    if line.strip().startswith(f'CMD_COMPLETE_{cmd_id}_'):
                        end_time = time.time()
                        exit_code = line.strip().split('_')[-1]
                        latency = end_time - start_time
                        success = exit_code == '0'

                        # Join all output except the completion marker
                        output = '\n'.join(output_lines[:-1])
                        return latency, success, output

                    # Timeout check
                    if time.time() - start_time > timeout:
                        logger.error(f'Command timeout: {command}')
                        return time.time() - start_time, False, "Timeout"

                except Exception as e:
                    logger.error(f'Error reading command output: {e}')
                    return time.time() - start_time, False, str(e)

        except BrokenPipeError as e:
            # Cannot write the packet, continue to retry
            return 0.0, False, "BrokenPipeError"

        except Exception as e:
            logger.error(f'Error executing command {command}: {e}')
            return 0.0, False, str(e)

        return 0.0, False, self.process.stderr.read()

    def execute_command(self, command: str) -> Tuple[float, bool]:
        """Execute command and return latency and success status."""
        latency, success, _ = self._execute_command(command)
        return latency, success

    def disconnect(self):
        """Close the SSH connection."""
        if self.process:
            try:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process.stderr.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception as e:
                logger.error(f'Error disconnecting SSH: {e}')
                try:
                    self.process.kill()
                except Exception:
                    pass
            finally:
                self.process = None


def generate_echo_command(size_bytes: int) -> str:
    """Generate echo command with specified byte size."""
    text_size = max(1, size_bytes)
    text = 'A' * text_size
    return f'echo "{text}"'


@dataclass
class WorkerResult:
    """Results from a single worker thread."""
    connection_latency: float  # -1.0 means failure
    command_results: List[Tuple[float, bool]] = field(default_factory=list)


def worker_thread(cluster: str, size_bytes: int, num: int,
                  thread_id: int) -> WorkerResult:
    """Worker function that maintains persistent SSH connection."""
    ssh_client = SSHClient(cluster)

    logger.info(f"Thread {thread_id}: Establishing SSH connection")

    # Establish persistent connection and measure latency
    conn_latency = ssh_client.connect()
    if conn_latency < 0:
        logger.error(f"Thread {thread_id}: Failed to connect to {cluster}")
        return WorkerResult(connection_latency=-1.0,
                            command_results=[(0.0, False) for _ in range(num)])

    result = WorkerResult(connection_latency=conn_latency)

    logger.info(
        f"Thread {thread_id}: Starting {num} commands on persistent connection")

    try:
        command = generate_echo_command(size_bytes)
        for i in range(num):
            try:
                latency, success = ssh_client.execute_command(command)
            except BrokenPipeError as e:
                logger.error(
                    f"Thread {thread_id} disconnected at command {i+1}: {e}")
                break
            result.command_results.append((latency, success))
            if not success:
                logger.error(
                    f"Thread {thread_id}, command {i+1}: Command failed")

        logger.info(f"Thread {thread_id}: Completed {num} commands")

    finally:
        # Always disconnect
        ssh_client.disconnect()
        logger.info(f"Thread {thread_id}: SSH connection closed")

    return result


def _print_latency_stats(name: str, latencies: List[float]):
    """Print min/max/mean/median/stddev for a list of latencies."""
    if not latencies:
        print(f"  No data for {name}.")
        return
    print(f"  Minimum: {min(latencies):.4f}s")
    print(f"  Maximum: {max(latencies):.4f}s")
    print(f"  Mean: {statistics.mean(latencies):.4f}s")
    print(f"  Median: {statistics.median(latencies):.4f}s")
    if len(latencies) > 1:
        print(f"  Std Dev: {statistics.stdev(latencies):.4f}s")


def print_statistics(worker_results: List[WorkerResult], parallelism: int):
    """Calculate and print benchmark statistics."""
    # Connection latencies (exclude failures)
    conn_latencies = [
        r.connection_latency
        for r in worker_results
        if r.connection_latency >= 0
    ]
    conn_failures = sum(1 for r in worker_results if r.connection_latency < 0)

    # Command latencies
    all_cmd_results = []
    for r in worker_results:
        all_cmd_results.extend(r.command_results)

    cmd_latencies = [lat for lat, success in all_cmd_results if success]
    total_commands = len(all_cmd_results)
    successful_commands = sum(1 for _, success in all_cmd_results if success)
    failed_commands = total_commands - successful_commands

    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Parallelism: {parallelism}")
    print()

    # Connection stats
    print("CONNECTION ESTABLISHMENT:")
    print(f"  Successful connections: {len(conn_latencies)}/{parallelism}")
    if conn_failures:
        print(f"  Failed connections: {conn_failures}")
    _print_latency_stats("connection", conn_latencies)
    print()

    # Command stats
    print("COMMAND EXECUTION:")
    print(f"  Total commands: {total_commands}")
    print(f"  Successful: {successful_commands}")
    print(f"  Failed: {failed_commands}")
    if total_commands > 0:
        print(f"  Success rate: "
              f"{(successful_commands / total_commands) * 100:.2f}%")
    _print_latency_stats("command", cmd_latencies)

    print("=" * 60)


def main():
    """Main function to run the SSH benchmark."""
    parser = argparse.ArgumentParser(
        description='SSH Proxy Benchmark Tool - Measure SSH data transfer '
        'latency with persistent connections',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic benchmark with 5 parallel clients, 10 commands each, 100 bytes
  python test_ssh_proxy.py -c my-cluster -p 5 -n 10

  # Test with larger data size and higher parallelism
  python test_ssh_proxy.py -c web-server -p 20 -n 100 --size 1024

  # Quick test with minimal load and small data size
  python test_ssh_proxy.py -c test-box -p 2 -n 5 --size 50
        """)

    parser.add_argument('-c',
                        '--cluster',
                        required=True,
                        help='Name of the cluster/host to SSH into')

    parser.add_argument('-p',
                        '--parallelism',
                        type=int,
                        default=5,
                        help='Number of parallel SSH clients (default: 5).')

    parser.add_argument(
        '-n',
        '--num',
        type=int,
        default=10,
        help='Number of commands each client should execute (default: 10)')

    parser.add_argument(
        '--size',
        type=int,
        default=1024,
        help='Size in bytes for echo command data (default: 1024)')

    args = parser.parse_args()

    if args.parallelism <= 0:
        print("Error: parallelism must be positive")
        sys.exit(1)

    if args.num <= 0:
        print("Error: num must be positive")
        sys.exit(1)

    if args.size <= 0:
        print("Error: size must be positive")
        sys.exit(1)

    print("SSH Proxy Benchmark Starting...")
    print(f"Cluster: {args.cluster}")
    print(f"Data size: {args.size} bytes")
    print(f"Parallelism: {args.parallelism}")
    print(f"Commands per client: {args.num}")
    print(f"Total commands: {args.parallelism * args.num}")
    print()

    # Test basic SSH connectivity first
    print("Testing SSH connectivity...")
    test_client = SSHClient(args.cluster)
    test_conn_latency = test_client.connect()
    if test_conn_latency < 0:
        print(f"Error: Cannot establish SSH connection to {args.cluster}. "
              f"Please check:")
        print("  1. Cluster name is correct")
        print("  2. SSH keys are properly configured")
        print("  3. Cluster is running and accessible")
        sys.exit(1)
    test_command = generate_echo_command(args.size)
    test_latency, test_success = test_client.execute_command(test_command)
    test_client.disconnect()

    if not test_success:
        print(f"Error: Test command failed on {args.cluster}")
        sys.exit(1)

    print(f"SSH connectivity test passed (connection: "
          f"{test_conn_latency:.4f}s, echo: {test_latency:.4f}s)")
    print()

    # Run concurrent benchmark
    print("Starting concurrent benchmark with persistent connections...")
    start_time = time.time()

    worker_results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.parallelism) as executor:
        # Submit all worker threads
        futures = []
        for thread_id in range(args.parallelism):
            future = executor.submit(worker_thread, args.cluster, args.size,
                                     args.num, thread_id)
            futures.append(future)
        for future in concurrent.futures.as_completed(futures):
            try:
                worker_results.append(future.result())
            except Exception as exc:
                logger.error(f'Thread generated an exception: {exc}')

    end_time = time.time()
    total_duration = end_time - start_time

    print(f"\nBenchmark completed in {total_duration:.2f} seconds")

    # Print detailed statistics
    print_statistics(worker_results, args.parallelism)


if __name__ == '__main__':
    main()

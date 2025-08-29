#!/usr/bin/env python3
# SSH Proxy Benchmark

import threading
import asyncio
import argparse
import concurrent.futures
import statistics
import subprocess
import sys
import time
from typing import List, Tuple

from sky import sky_logging
from test_hybrid_load import hybrid_load

logger = sky_logging.init_logger(__name__)


class SSHClient:
    """A persistent SSH client for executing commands."""

    def __init__(self, cluster: str):
        self.cluster = cluster
        self.process = None

    def connect(self) -> bool:
        """Establish persistent SSH connection."""
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
                logger.info(f'SSH connection established to {self.cluster}')
                return True
            else:
                logger.error(f'SSH connection test failed to {self.cluster}, '
                             f'output: {test_result[2]}')
                self.disconnect()
                return False

        except Exception as e:
            logger.error(f'Failed to establish SSH connection to '
                         f'{self.cluster}: {e}')
            self.disconnect()
            return False

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

        except BrokenPipeError:
            # The connection has been closed, just break
            raise
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


def worker_thread(cluster: str, size_bytes: int, num: int,
                  thread_id: int) -> List[Tuple[float, bool]]:
    """Worker function that maintains persistent SSH connection."""
    results = []
    ssh_client = SSHClient(cluster)

    logger.info(f"Thread {thread_id}: Establishing SSH connection")

    # Establish persistent connection
    if not ssh_client.connect():
        logger.error(f"Thread {thread_id}: Failed to connect to {cluster}")
        # Return failed results for all commands
        return [(0.0, False) for _ in range(num)]

    logger.info(
        f"Thread {thread_id}: Starting {num} commands on persistent connection")

    try:
        command = generate_echo_command(size_bytes)
        for i in range(num):
            latency, success = ssh_client.execute_command(command)
            results.append((latency, success))
            if not success:
                logger.error(
                    f"Thread {thread_id}, command {i+1}: Command failed")

        logger.info(f"Thread {thread_id}: Completed {num} commands")

    finally:
        # Always disconnect
        ssh_client.disconnect()
        logger.info(f"Thread {thread_id}: SSH connection closed")

    return results


def print_statistics(all_results: List[Tuple[float, bool]], parallelism: int):
    """Calculate and print benchmark statistics."""
    successes = [result[1] for result in all_results]

    latencies = [lat for lat, success in all_results if success]

    total_commands = len(all_results)
    successful_commands = sum(successes)
    failed_commands = total_commands - successful_commands
    success_rate = (successful_commands / total_commands) * 100

    with open('results.txt', 'a', encoding='utf-8') as f:
        f.write("\n" + "=" * 60)
        f.write("BENCHMARK RESULTS")
        f.write("=" * 60)
        f.write(f"Total commands executed: {total_commands}")
        f.write(f"Successful commands: {successful_commands}")
        f.write(f"Failed commands: {failed_commands}")
        f.write(f"Success rate: {success_rate:.2f}%")
        f.write(f"Parallelism: {parallelism}")
        f.write()

    if latencies:
        f.write("LATENCY STATISTICS (successful commands only):")
        f.write(f"  Minimum: {min(latencies):.4f}s")
        f.write(f"  Maximum: {max(latencies):.4f}s")
        f.write(f"  Mean: {statistics.mean(latencies):.4f}s")
        f.write(f"  Median: {statistics.median(latencies):.4f}s")
        if len(latencies) > 1:
            f.write(f"  Std Dev: {statistics.stdev(latencies):.4f}s")
    else:
        f.write("No successful commands to calculate latency statistics.")

    f.write("=" * 60)


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

    print("Launch hybrid load...")
    exit = asyncio.Event()
    # Function to run hybrid_load in a separate thread
    def run_hybrid_load():
        asyncio.run(hybrid_load(exit))
    load_thread = threading.Thread(target=run_hybrid_load)
    load_thread.start()

    print("SSH Proxy Benchmark Starting...")
    print(f"Cluster: {args.cluster}")
    print(f"Data size: {args.size} bytes")
    print(f"Parallelism: {args.parallelism}")
    print(f"Commands per client: {args.num}")
    print(f"Total commands: {args.parallelism * args.num}")
    print()

    try:
        # Test basic SSH connectivity first
        print("Testing SSH connectivity...")
        test_client = SSHClient(args.cluster)
        if not test_client.connect():
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

        print(f"SSH connectivity test passed (echo latency: "
            f"{test_latency:.4f}s)")
        print()

        # Run concurrent benchmark
        print("Starting concurrent benchmark with persistent connections...")
        start_time = time.time()

        all_results = []
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
                    thread_results = future.result()
                    all_results.extend(thread_results)
                except Exception as exc:
                    logger.error(f'Thread generated an exception: {exc}')

        end_time = time.time()
        total_duration = end_time - start_time

        print(f"\nBenchmark completed in {total_duration:.2f} seconds")

        # Print detailed statistics
        print_statistics(all_results, args.parallelism)
    finally:
        exit.set()
        logger.info("Stopping hybrid load...")
        load_thread.join()

if __name__ == '__main__':
    main()

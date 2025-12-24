#!/usr/bin/env python3
"""
Benchmark script to test load on the SkyPilot server.

This script launches N threads, each running workloads defined in bash scripts.

Usage:
    python workload_benchmark.py -t 8 -r 3 -s workloads/basic.sh --cloud aws
"""

import argparse
import concurrent.futures
import os
import random
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional
import uuid

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Thread-safe statistics collection
stats_lock = threading.Lock()
execution_stats: Dict[str, List[float]] = {}


class BenchmarkRunner:
    """Main benchmark runner class."""

    def __init__(self,
                 threads: int,
                 repeats: int,
                 script: str,
                 cloud: str = 'aws',
                 output_dir: Optional[str] = None):
        self.threads = threads
        self.repeats = repeats
        self.workload = script
        self.cloud = cloud
        self.output_dir = output_dir or 'benchmark_logs'
        self.workload_script = self._get_workload_script_path()

        # Create output directory
        os.makedirs(self.output_dir, exist_ok=True)

        # Validate workload script exists
        if not os.path.exists(self.workload_script):
            raise FileNotFoundError(
                f"Workload script not found: {self.workload_script}")

    def _get_workload_script_path(self) -> str:
        """Get the path to the workload script."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, self.workload)

    def _generate_unique_id(self) -> str:
        """Generate a unique ID for this run."""
        unique_suffix = str(uuid.uuid4())[:8]
        return f"bench-{unique_suffix}"

    def _run_single_workload(self, thread_id: int, repeat_id: int,
                             unique_id: str) -> Dict[str, float]:
        """Run a single workload instance."""
        start_time = time.time()

        # Create log file for this specific run
        log_file = os.path.join(
            self.output_dir,
            f"thread_{thread_id}_repeat_{repeat_id}_{unique_id}.log")

        # Prepare environment variables for the workload script
        env = os.environ.copy()
        env.update({
            'BENCHMARK_UNIQUE_ID': unique_id,
            'BENCHMARK_CLOUD': self.cloud,
            'BENCHMARK_THREAD_ID': str(thread_id),
            'BENCHMARK_REPEAT_ID': str(repeat_id),
            'BENCHMARK_LOG_FILE': log_file
        })

        try:
            logger.info(f'Thread {thread_id}, Repeat {repeat_id}: '
                        f'Starting workload, log file: {log_file}')

            # Run the workload script
            with open(log_file, 'w') as log_f:
                process = subprocess.run(
                    ['bash', self.workload_script],
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=3600,  # 1 hour timeout
                    text=True)

            end_time = time.time()
            duration = end_time - start_time

            if process.returncode == 0:
                logger.info(f'Thread {thread_id}, Repeat {repeat_id}: '
                            f'Completed successfully in {duration:.2f}s')
                return {
                    'success': True,
                    'duration': duration,
                    'thread_id': thread_id,
                    'repeat_id': repeat_id,
                    'unique_id': unique_id,
                    'log_file': log_file
                }
            else:
                logger.error(f'Thread {thread_id}, Repeat {repeat_id}: '
                             f'Failed with return code {process.returncode}')
                return {
                    'success': False,
                    'duration': duration,
                    'thread_id': thread_id,
                    'repeat_id': repeat_id,
                    'unique_id': unique_id,
                    'log_file': log_file,
                    'return_code': process.returncode
                }

        except subprocess.TimeoutExpired:
            end_time = time.time()
            duration = end_time - start_time
            logger.error(f'Thread {thread_id}, Repeat {repeat_id}: '
                         f'Timed out after {duration:.2f}s')
            return {
                'success': False,
                'duration': duration,
                'thread_id': thread_id,
                'repeat_id': repeat_id,
                'unique_id': unique_id,
                'log_file': log_file,
                'error': 'timeout'
            }
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            logger.error(f'Thread {thread_id}, Repeat {repeat_id}: '
                         f'Error: {str(e)}')
            return {
                'success': False,
                'duration': duration,
                'thread_id': thread_id,
                'repeat_id': repeat_id,
                'unique_id': unique_id,
                'log_file': log_file,
                'error': str(e)
            }

    def _worker_thread(self, thread_id: int) -> List[Dict[str, float]]:
        """Worker thread that runs multiple repeats of the workload."""
        results = []

        for repeat_id in range(self.repeats):
            unique_id = self._generate_unique_id()
            result = self._run_single_workload(thread_id, repeat_id, unique_id)
            results.append(result)

            # Update global statistics
            with stats_lock:
                if 'durations' not in execution_stats:
                    execution_stats['durations'] = []
                if 'successes' not in execution_stats:
                    execution_stats['successes'] = []

                execution_stats['durations'].append(result['duration'])
                execution_stats['successes'].append(result['success'])

        return results

    def run_benchmark(self) -> Dict:
        """Run the complete benchmark."""
        logger.info(f'Starting benchmark: {self.threads} threads, '
                    f'{self.repeats} repeats each, workload: {self.workload}')
        logger.info(f'Output directory: {self.output_dir}')
        logger.info(f'Cloud: {self.cloud}')

        start_time = time.time()
        all_results = []

        # Use ThreadPoolExecutor for better thread management
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.threads) as executor:

            # Submit all worker threads
            future_to_thread = {}
            for thread_id in range(self.threads):
                future = executor.submit(self._worker_thread, thread_id)
                future_to_thread[future] = thread_id
                time.sleep(random.uniform(0, 10))

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_thread):
                thread_id = future_to_thread[future]
                try:
                    thread_results = future.result()
                    all_results.extend(thread_results)
                    logger.info(f'Thread {thread_id} completed all repeats')
                except Exception as e:
                    logger.error(f'Thread {thread_id} failed: {str(e)}')

        end_time = time.time()
        total_duration = end_time - start_time

        # Calculate statistics
        stats = self._calculate_statistics(all_results, total_duration)

        # Print summary
        self._print_summary(stats)

        # Save detailed results
        self._save_results(all_results, stats)

        return {
            'results': all_results,
            'statistics': stats,
            'total_duration': total_duration
        }

    def _calculate_statistics(self, results: List[Dict],
                              total_duration: float) -> Dict:
        """Calculate benchmark statistics."""
        if not results:
            return {}

        durations = [r['duration'] for r in results]
        successes = [r for r in results if r['success']]
        failures = [r for r in results if not r['success']]

        return {
            'total_runs': len(results),
            'successful_runs': len(successes),
            'failed_runs': len(failures),
            'success_rate': len(successes) / len(results) * 100,
            'total_duration': total_duration,
            'avg_duration_per_run': sum(durations) / len(durations),
            'min_duration': min(durations),
            'max_duration': max(durations),
            'total_workload_time': sum(durations),
            'throughput_runs_per_second': len(results) / total_duration,
            'concurrency_efficiency':
                (sum(durations) / total_duration) / self.threads * 100
        }

    def _print_summary(self, stats: Dict) -> None:
        """Print benchmark summary."""
        print("\n" + "=" * 60)
        print("BENCHMARK SUMMARY")
        print("=" * 60)
        print(f"Threads: {self.threads}")
        print(f"Repeats per thread: {self.repeats}")
        print(f"Workload: {self.workload}")
        print(f"Cloud: {self.cloud}")
        print(f"Output directory: {self.output_dir}")
        print("-" * 60)
        print(f"Total runs: {stats['total_runs']}")
        print(f"Successful runs: {stats['successful_runs']}")
        print(f"Failed runs: {stats['failed_runs']}")
        print(f"Success rate: {stats['success_rate']:.1f}%")
        print("-" * 60)

    def _save_results(self, results: List[Dict], stats: Dict) -> None:
        """Save detailed results to a file."""
        results_file = os.path.join(self.output_dir, "benchmark_results.txt")

        with open(results_file, 'w') as f:
            f.write("BENCHMARK RESULTS\n")
            f.write("=" * 60 + "\n")
            f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Threads: {self.threads}\n")
            f.write(f"Repeats per thread: {self.repeats}\n")
            f.write(f"Workload: {self.workload}\n")
            f.write(f"Cloud: {self.cloud}\n\n")

            # Statistics
            f.write("STATISTICS\n")
            f.write("-" * 30 + "\n")
            for key, value in stats.items():
                if isinstance(value, float):
                    f.write(f"{key}: {value:.2f}\n")
                else:
                    f.write(f"{key}: {value}\n")
            f.write("\n")

            # Detailed results
            f.write("DETAILED RESULTS\n")
            f.write("-" * 30 + "\n")
            for result in results:
                f.write(f"Thread {result['thread_id']}, "
                        f"Repeat {result['repeat_id']}: ")
                if result['success']:
                    f.write(f"SUCCESS ({result['duration']:.2f}s)\n")
                else:
                    f.write(f"FAILED ({result['duration']:.2f}s)")
                    if 'return_code' in result:
                        f.write(f" - Return code: {result['return_code']}")
                    if 'error' in result:
                        f.write(f" - Error: {result['error']}")
                    f.write("\n")
                f.write(f"  Log: {result['log_file']}\n")
                f.write(f"  ID: {result['unique_id']}\n\n")

        logger.info(f"Detailed results saved to: {results_file}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Benchmark script to test load on SkyPilot server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --threads 4 --repeats 2 --workload basic_workload
  %(prog)s -t 8 -r 3 -w comprehensive_workload --cloud aws
  %(prog)s -t 2 -r 1 -w basic_workload --output-dir custom_logs
        """)

    parser.add_argument('-t',
                        '--threads',
                        type=int,
                        default=4,
                        help='Number of parallel threads to run (default: 4)')

    parser.add_argument(
        '-r',
        '--repeats',
        type=int,
        default=1,
        help='Number of times each thread repeats the workload (default: 1)')

    parser.add_argument(
        '-s',
        '--script',
        type=str,
        default='workloads/baisc.sh',
        help='Name of the workload script to run (default: workloads/basic.sh)')

    parser.add_argument('--check',
                        action='store_true',
                        help='Exit with non-zero status on failed runs')
    parser.add_argument(
        '--detail',
        action='store_true',
        help='Print logs of failed runs after the benchmark finishes')

    parser.add_argument('-c',
                        '--cloud',
                        type=str,
                        default='aws',
                        choices=['aws', 'gcp', 'azure', 'kubernetes'],
                        help='Cloud provider to use (default: aws)')

    parser.add_argument(
        '-o',
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for logs (default: benchmark_logs)')

    args = parser.parse_args()

    try:
        # Validate conda environment
        result = subprocess.run(['conda', 'info', '--envs'],
                                capture_output=True,
                                text=True)
        if 'sky' not in result.stdout:
            logger.warning("Conda environment 'sky' not found. "
                           "Make sure to activate it before running workloads.")

        # Create and run benchmark
        runner = BenchmarkRunner(threads=args.threads,
                                 repeats=args.repeats,
                                 script=args.script,
                                 cloud=args.cloud,
                                 output_dir=args.output_dir)

        benchmark_result = runner.run_benchmark()

        # Exit with appropriate code
        stats = benchmark_result['statistics']
        results = benchmark_result['results']
        failed_runs = stats.get('failed_runs', 0) if stats else 0

        if args.detail and failed_runs:
            failed_results = [r for r in results if not r['success']]
            for result in failed_results:
                logger.info(
                    f"===== Failed run log: thread {result['thread_id']}, "
                    f"repeat {result['repeat_id']}, id {result['unique_id']} ====="
                )
                try:
                    with open(result['log_file'], 'r') as log_f:
                        print(log_f.read())
                except Exception as e:
                    logger.error(
                        f"Could not read log {result['log_file']}: {e}")

        if failed_runs > 0:
            logger.warning(f"{failed_runs} runs failed")
            if args.check:
                sys.exit(1)

        logger.info("All runs completed")
        sys.exit(0)

    except KeyboardInterrupt:
        logger.info("Benchmark interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Benchmark failed: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()

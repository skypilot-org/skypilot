#!/usr/bin/env python3
"""
Baseline-Benchmarks: Python vs. Rust

Dieses Skript vergleicht die Performance von Python- und Rust-Implementierungen
aller migrierten Funktionen.

Nutzung:
    python benchmarks/baseline_benchmarks.py
    python benchmarks/baseline_benchmarks.py --format json
    python benchmarks/baseline_benchmarks.py --iterations 10000
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sky.utils import rust_fallback

# ANSI Colors
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
BOLD = '\033[1m'
RESET = '\033[0m'


def benchmark(func: Callable, name: str, iterations: int = 1000) -> Tuple[float, float]:
    """
    F?hre Benchmark aus und messe Durchschnitt und Standardabweichung.
    
    Returns:
        (average_time_ms, stddev_ms)
    """
    times = []
    
    # Warmup
    for _ in range(10):
        func()
    
    # Actual benchmark
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms
    
    avg = sum(times) / len(times)
    variance = sum((t - avg) ** 2 for t in times) / len(times)
    stddev = variance ** 0.5
    
    return avg, stddev


def format_speedup(speedup: float) -> str:
    """Format speedup with color."""
    if speedup >= 10:
        return f"{GREEN}{speedup:.1f}x{RESET}"
    elif speedup >= 5:
        return f"{YELLOW}{speedup:.1f}x{RESET}"
    elif speedup >= 2:
        return f"{BLUE}{speedup:.1f}x{RESET}"
    else:
        return f"{speedup:.1f}x"


class BenchmarkSuite:
    """Suite von Benchmarks f?r Rust vs. Python."""
    
    def __init__(self, iterations: int = 1000):
        self.iterations = iterations
        self.results: List[Dict] = []
        self.rust_available = rust_fallback.is_rust_available()
        
        # Setup test data
        self.test_file = None
        self._setup_test_data()
    
    def _setup_test_data(self):
        """Erstelle Test-Daten."""
        # Create test file
        self.test_file = tempfile.NamedTemporaryFile(mode='w', delete=False)
        for i in range(1000):
            self.test_file.write(f"Line {i}\n")
        self.test_file.flush()
    
    def _cleanup(self):
        """Cleanup test data."""
        if self.test_file:
            os.unlink(self.test_file.name)
    
    def run_all(self):
        """F?hre alle Benchmarks aus."""
        print(f"\n{BOLD}{'='*70}{RESET}")
        print(f"{BOLD}SkyPilot Rust vs. Python Benchmark Suite{RESET}")
        print(f"{BOLD}{'='*70}{RESET}")
        print(f"Rust available: {'? Yes' if self.rust_available else '? No'}")
        print(f"Iterations per benchmark: {self.iterations}")
        print(f"{BOLD}{'='*70}{RESET}\n")
        
        # I/O Benchmarks
        self._run_io_benchmarks()
        
        # String Benchmarks
        self._run_string_benchmarks()
        
        # System Benchmarks
        self._run_system_benchmarks()
        
        # Process Benchmarks
        self._run_process_benchmarks()
        
        # Summary
        self._print_summary()
        
        # Cleanup
        self._cleanup()
    
    def _run_io_benchmarks(self):
        """I/O Benchmarks."""
        print(f"{BOLD}I/O Utilities{RESET}")
        print("-" * 70)
        
        # read_last_n_lines
        self._benchmark_function(
            name="read_last_n_lines",
            rust_func=lambda: rust_fallback.read_last_n_lines(self.test_file.name, 10),
            python_func=lambda: rust_fallback._python_read_last_n_lines(
                self.test_file.name, 10
            ),
        )
        
        # hash_file
        self._benchmark_function(
            name="hash_file (SHA256)",
            rust_func=lambda: rust_fallback.hash_file(self.test_file.name, "sha256"),
            python_func=lambda: rust_fallback._python_hash_file(
                self.test_file.name, "sha256"
            ),
        )
        
        # find_free_port
        self._benchmark_function(
            name="find_free_port",
            rust_func=lambda: rust_fallback.find_free_port(10000),
            python_func=lambda: rust_fallback._python_find_free_port(10000),
        )
        
        print()
    
    def _run_string_benchmarks(self):
        """String Benchmarks."""
        print(f"{BOLD}String Utilities{RESET}")
        print("-" * 70)
        
        # base36_encode
        self._benchmark_function(
            name="base36_encode",
            rust_func=lambda: rust_fallback.base36_encode("deadbeef"),
            python_func=lambda: rust_fallback._python_base36_encode("deadbeef"),
        )
        
        # format_float
        self._benchmark_function(
            name="format_float",
            rust_func=lambda: rust_fallback.format_float(1234567.89, 2),
            python_func=lambda: rust_fallback._python_format_float(1234567.89, 2),
        )
        
        # truncate_long_string
        long_str = "A" * 1000
        self._benchmark_function(
            name="truncate_long_string",
            rust_func=lambda: rust_fallback.truncate_long_string(long_str, 80),
            python_func=lambda: rust_fallback._python_truncate_long_string(long_str, 80),
        )
        
        print()
    
    def _run_system_benchmarks(self):
        """System Benchmarks."""
        print(f"{BOLD}System Utilities{RESET}")
        print("-" * 70)
        
        # get_cpu_count
        self._benchmark_function(
            name="get_cpu_count",
            rust_func=lambda: rust_fallback.get_cpu_count(),
            python_func=lambda: rust_fallback._python_get_cpu_count(),
        )
        
        # get_mem_size_gb
        self._benchmark_function(
            name="get_mem_size_gb",
            rust_func=lambda: rust_fallback.get_mem_size_gb(),
            python_func=lambda: rust_fallback._python_get_mem_size_gb(),
        )
        
        print()
    
    def _run_process_benchmarks(self):
        """Process Benchmarks."""
        print(f"{BOLD}Process Utilities{RESET}")
        print("-" * 70)
        
        # get_parallel_threads
        self._benchmark_function(
            name="get_parallel_threads",
            rust_func=lambda: rust_fallback.get_parallel_threads(),
            python_func=lambda: rust_fallback._python_get_parallel_threads(),
        )
        
        # is_process_alive
        current_pid = os.getpid()
        self._benchmark_function(
            name="is_process_alive",
            rust_func=lambda: rust_fallback.is_process_alive(current_pid),
            python_func=lambda: rust_fallback._python_is_process_alive(current_pid),
        )
        
        # get_max_workers_for_file_mounts
        self._benchmark_function(
            name="get_max_workers_for_file_mounts",
            rust_func=lambda: rust_fallback.get_max_workers_for_file_mounts(10, 100),
            python_func=lambda: rust_fallback._python_get_max_workers_for_file_mounts(
                10, 100
            ),
        )
        
        # estimate_fd_for_directory
        self._benchmark_function(
            name="estimate_fd_for_directory",
            rust_func=lambda: rust_fallback.estimate_fd_for_directory("/tmp"),
            python_func=lambda: rust_fallback._python_estimate_fd_for_directory("/tmp"),
        )
        
        print()
    
    def _benchmark_function(self, name: str, rust_func: Callable, python_func: Callable):
        """Benchmark einzelne Funktion."""
        # Python benchmark
        py_avg, py_std = benchmark(python_func, f"{name} (Python)", self.iterations)
        
        # Rust benchmark (if available)
        if self.rust_available:
            rust_avg, rust_std = benchmark(rust_func, f"{name} (Rust)", self.iterations)
            speedup = py_avg / rust_avg if rust_avg > 0 else 0
        else:
            rust_avg, rust_std = 0, 0
            speedup = 0
        
        # Store results
        result = {
            'name': name,
            'python_avg_ms': py_avg,
            'python_std_ms': py_std,
            'rust_avg_ms': rust_avg,
            'rust_std_ms': rust_std,
            'speedup': speedup,
        }
        self.results.append(result)
        
        # Print result
        if self.rust_available:
            print(
                f"  {name:30s}  "
                f"Python: {py_avg:8.4f}ms  "
                f"Rust: {rust_avg:8.4f}ms  "
                f"Speedup: {format_speedup(speedup)}"
            )
        else:
            print(f"  {name:30s}  Python: {py_avg:8.4f}ms  (Rust not available)")
    
    def _print_summary(self):
        """Print benchmark summary."""
        if not self.rust_available:
            print(f"{YELLOW}Note: Rust not available, skipping summary{RESET}")
            return
        
        print(f"{BOLD}Summary{RESET}")
        print("-" * 70)
        
        speedups = [r['speedup'] for r in self.results if r['speedup'] > 0]
        if speedups:
            avg_speedup = sum(speedups) / len(speedups)
            min_speedup = min(speedups)
            max_speedup = max(speedups)
            
            print(f"  Average speedup: {format_speedup(avg_speedup)}")
            print(f"  Min speedup:     {format_speedup(min_speedup)}")
            print(f"  Max speedup:     {format_speedup(max_speedup)}")
            print(f"  Functions:       {len(self.results)}")
        
        print()
    
    def export_json(self, filename: str = "benchmark_results.json"):
        """Export results to JSON."""
        with open(filename, 'w') as f:
            json.dump(
                {
                    'rust_available': self.rust_available,
                    'iterations': self.iterations,
                    'results': self.results,
                },
                f,
                indent=2,
            )
        print(f"Results exported to {filename}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark Python vs. Rust")
    parser.add_argument(
        '--iterations',
        type=int,
        default=1000,
        help='Number of iterations per benchmark (default: 1000)',
    )
    parser.add_argument(
        '--format',
        choices=['text', 'json'],
        default='text',
        help='Output format (default: text)',
    )
    parser.add_argument(
        '--output', type=str, default='benchmark_results.json', help='JSON output file'
    )
    
    args = parser.parse_args()
    
    suite = BenchmarkSuite(iterations=args.iterations)
    suite.run_all()
    
    if args.format == 'json':
        suite.export_json(args.output)


if __name__ == "__main__":
    main()

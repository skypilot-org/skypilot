#!/usr/bin/env python3
"""
Performance Report Generator

Erstellt detaillierten Performance-Report f?r Rust vs. Python.

Verwendung:
    python tools/performance_report.py                    # Console-Output
    python tools/performance_report.py --html report.html # HTML-Report
    python tools/performance_report.py --json report.json # JSON-Output
"""

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sky.utils import rust_fallback


class PerformanceReporter:
    """Generate performance reports."""
    
    def __init__(self, iterations: int = 1000):
        self.iterations = iterations
        self.results: List[Dict] = []
        self.backend_info = rust_fallback.get_backend_info()
        self.timestamp = datetime.now().isoformat()
    
    def benchmark_function(self, name: str, rust_func, python_func, *args):
        """Benchmark a single function."""
        
        # Warmup
        for _ in range(10):
            rust_func(*args)
            python_func(*args)
        
        # Python benchmark
        start = time.perf_counter()
        for _ in range(self.iterations):
            python_func(*args)
        python_time = (time.perf_counter() - start) / self.iterations
        
        # Rust benchmark
        start = time.perf_counter()
        for _ in range(self.iterations):
            rust_func(*args)
        rust_time = (time.perf_counter() - start) / self.iterations
        
        speedup = python_time / rust_time if rust_time > 0 else 0
        
        self.results.append({
            'name': name,
            'python_time_us': python_time * 1e6,
            'rust_time_us': rust_time * 1e6,
            'speedup': speedup,
            'iterations': self.iterations,
        })
        
        return speedup
    
    def run_all_benchmarks(self):
        """Run all available benchmarks."""
        
        print(f"Running benchmarks ({self.iterations} iterations each)...\n")
        
        # I/O Benchmarks
        test_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
        for i in range(1000):
            test_file.write(f"Line {i}\n")
        test_file.close()
        
        try:
            print("?? I/O Utilities...")
            self.benchmark_function(
                'read_last_n_lines',
                lambda: rust_fallback.read_last_n_lines(test_file.name, 10),
                lambda: rust_fallback._python_read_last_n_lines(test_file.name, 10),
            )
            
            self.benchmark_function(
                'hash_file',
                lambda: rust_fallback.hash_file(test_file.name, 'sha256'),
                lambda: rust_fallback._python_hash_file(test_file.name, 'sha256'),
            )
        finally:
            import os
            os.unlink(test_file.name)
        
        self.benchmark_function(
            'find_free_port',
            lambda: rust_fallback.find_free_port(10000),
            lambda: rust_fallback._python_find_free_port(10000),
        )
        
        # String Benchmarks
        print("?? String Utilities...")
        self.benchmark_function(
            'base36_encode',
            lambda: rust_fallback.base36_encode('deadbeef'),
            lambda: rust_fallback._python_base36_encode('deadbeef'),
        )
        
        self.benchmark_function(
            'format_float',
            lambda: rust_fallback.format_float(1234567.89, 2),
            lambda: rust_fallback._python_format_float(1234567.89, 2),
        )
        
        long_str = 'A' * 1000
        self.benchmark_function(
            'truncate_long_string',
            lambda: rust_fallback.truncate_long_string(long_str, 80),
            lambda: rust_fallback._python_truncate_long_string(long_str, 80),
        )
        
        # System Benchmarks
        print("?? System Utilities...")
        self.benchmark_function(
            'get_cpu_count',
            lambda: rust_fallback.get_cpu_count(),
            lambda: rust_fallback._python_get_cpu_count(),
        )
        
        self.benchmark_function(
            'get_mem_size_gb',
            lambda: rust_fallback.get_mem_size_gb(),
            lambda: rust_fallback._python_get_mem_size_gb(),
        )
        
        # Process Benchmarks
        print("??  Process Utilities...")
        self.benchmark_function(
            'get_parallel_threads',
            lambda: rust_fallback.get_parallel_threads(),
            lambda: rust_fallback._python_get_parallel_threads(),
        )
        
        import os
        current_pid = os.getpid()
        self.benchmark_function(
            'is_process_alive',
            lambda: rust_fallback.is_process_alive(current_pid),
            lambda: rust_fallback._python_is_process_alive(current_pid),
        )
        
        self.benchmark_function(
            'get_max_workers_for_file_mounts',
            lambda: rust_fallback.get_max_workers_for_file_mounts(10, 100),
            lambda: rust_fallback._python_get_max_workers_for_file_mounts(10, 100),
        )
        
        self.benchmark_function(
            'estimate_fd_for_directory',
            lambda: rust_fallback.estimate_fd_for_directory('/tmp'),
            lambda: rust_fallback._python_estimate_fd_for_directory('/tmp'),
        )
    
    def generate_console_report(self):
        """Generate console output."""
        
        print("\n" + "="*80)
        print(" "*25 + "PERFORMANCE REPORT")
        print("="*80)
        
        print(f"\nBackend: {self.backend_info['backend'].upper()}")
        print(f"Timestamp: {self.timestamp}")
        print(f"Iterations: {self.iterations}\n")
        
        print(f"{'Function':<35} {'Python (?s)':<15} {'Rust (?s)':<15} {'Speedup':<10}")
        print("-"*80)
        
        for result in self.results:
            speedup_str = f"{result['speedup']:.1f}x"
            if result['speedup'] >= 10:
                speedup_str = f"\033[92m{speedup_str}\033[0m"  # Green
            elif result['speedup'] >= 5:
                speedup_str = f"\033[93m{speedup_str}\033[0m"  # Yellow
            
            print(f"{result['name']:<35} "
                  f"{result['python_time_us']:>10.2f}     "
                  f"{result['rust_time_us']:>10.2f}     "
                  f"{speedup_str}")
        
        # Summary
        avg_speedup = sum(r['speedup'] for r in self.results) / len(self.results)
        max_speedup = max(r['speedup'] for r in self.results)
        max_func = max(self.results, key=lambda r: r['speedup'])['name']
        
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        print(f"Average Speedup: {avg_speedup:.1f}x")
        print(f"Maximum Speedup: {max_speedup:.1f}x ({max_func})")
        print(f"Functions Tested: {len(self.results)}")
    
    def generate_json_report(self, output_file: str):
        """Generate JSON report."""
        
        report = {
            'metadata': {
                'timestamp': self.timestamp,
                'backend': self.backend_info,
                'iterations': self.iterations,
            },
            'results': self.results,
            'summary': {
                'avg_speedup': sum(r['speedup'] for r in self.results) / len(self.results),
                'max_speedup': max(r['speedup'] for r in self.results),
                'total_functions': len(self.results),
            }
        }
        
        Path(output_file).write_text(json.dumps(report, indent=2))
        print(f"\n? JSON report written to: {output_file}")
    
    def generate_html_report(self, output_file: str):
        """Generate HTML report."""
        
        avg_speedup = sum(r['speedup'] for r in self.results) / len(self.results)
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>SkyPilot Rust Performance Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}
        .header h1 {{ margin: 0; }}
        .metadata {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        th, td {{
            padding: 15px;
            text-align: left;
        }}
        th {{
            background: #667eea;
            color: white;
        }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .speedup-high {{ color: #10b981; font-weight: bold; }}
        .speedup-med {{ color: #f59e0b; font-weight: bold; }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-top: 30px;
        }}
        .summary-card {{
            background: white;
            padding: 30px;
            border-radius: 10px;
            text-align: center;
        }}
        .summary-card h3 {{ margin-top: 0; color: #667eea; }}
        .summary-card .value {{
            font-size: 48px;
            font-weight: bold;
            color: #333;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>?? SkyPilot Rust Performance Report</h1>
        <p>Backend: {self.backend_info['backend'].upper()} | Generated: {self.timestamp}</p>
    </div>
    
    <div class="metadata">
        <h3>Test Configuration</h3>
        <p><strong>Iterations:</strong> {self.iterations}</p>
        <p><strong>Backend Version:</strong> {self.backend_info.get('version', 'N/A')}</p>
        <p><strong>Functions Tested:</strong> {len(self.results)}</p>
    </div>
    
    <table>
        <tr>
            <th>Function</th>
            <th>Python (?s)</th>
            <th>Rust (?s)</th>
            <th>Speedup</th>
        </tr>
"""
        
        for result in self.results:
            speedup_class = 'speedup-high' if result['speedup'] >= 10 else 'speedup-med'
            html += f"""
        <tr>
            <td>{result['name']}</td>
            <td>{result['python_time_us']:.2f}</td>
            <td>{result['rust_time_us']:.2f}</td>
            <td class="{speedup_class}">{result['speedup']:.1f}x</td>
        </tr>
"""
        
        max_result = max(self.results, key=lambda r: r['speedup'])
        
        html += f"""
    </table>
    
    <div class="summary">
        <div class="summary-card">
            <h3>Average Speedup</h3>
            <div class="value">{avg_speedup:.1f}x</div>
        </div>
        <div class="summary-card">
            <h3>Maximum Speedup</h3>
            <div class="value">{max_result['speedup']:.1f}x</div>
            <p>{max_result['name']}</p>
        </div>
        <div class="summary-card">
            <h3>Functions Tested</h3>
            <div class="value">{len(self.results)}</div>
        </div>
    </div>
</body>
</html>
"""
        
        Path(output_file).write_text(html)
        print(f"\n? HTML report written to: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Generate performance report")
    parser.add_argument('--iterations', type=int, default=1000,
                       help='Number of iterations per benchmark')
    parser.add_argument('--json', type=str, help='Output JSON file')
    parser.add_argument('--html', type=str, help='Output HTML file')
    
    args = parser.parse_args()
    
    # Check if Rust is available
    info = rust_fallback.get_backend_info()
    if info['backend'] != 'rust':
        print("??  Rust backend not available, results will be Python-only")
        print("Install: cd rust/skypilot-utils && maturin develop --release\n")
    
    reporter = PerformanceReporter(iterations=args.iterations)
    reporter.run_all_benchmarks()
    
    # Always generate console report
    reporter.generate_console_report()
    
    # Optional outputs
    if args.json:
        reporter.generate_json_report(args.json)
    
    if args.html:
        reporter.generate_html_report(args.html)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())

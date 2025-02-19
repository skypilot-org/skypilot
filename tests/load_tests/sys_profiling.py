"""
This script is used to profile the system usage of the SkyPilot API server.

Usage:
python tests/load_tests/sys_profiling.py
"""
from datetime import datetime

import psutil


def get_size_gb(bytes):
    """
    Convert bytes to GB
    """
    return bytes / (1024**3)


def monitor_system():
    # Initialize peak tracking variables
    interval = 0.2
    peak_cpu = 0
    peak_memory_percent = 0
    peak_memory_used = 0
    start_time = datetime.now()
    samples = 0
    total_cpu = 0
    total_memory = 0

    # Record baseline stats
    baseline_cpu = psutil.cpu_percent(interval=interval)
    baseline_memory = psutil.virtual_memory()
    baseline_memory_used = get_size_gb(baseline_memory.used)
    baseline_memory_percent = baseline_memory.percent

    try:
        while True:
            # CPU Usage
            cpu_percent = psutil.cpu_percent(interval=interval)

            # Memory Usage
            memory = psutil.virtual_memory()
            memory_total = f"{get_size_gb(memory.total):.2f}GB"
            memory_used = f"{get_size_gb(memory.used):.2f}GB"
            memory_percent = memory.percent

            # Update peak values
            peak_cpu = max(peak_cpu, cpu_percent)
            peak_memory_percent = max(peak_memory_percent, memory_percent)
            peak_memory_used = max(peak_memory_used, memory.used)

            # Update averages
            total_cpu += cpu_percent
            total_memory += memory_percent

            # Print current stats
            print("\n" + "=" * 50)
            print(f"CPU Usage: {cpu_percent}%")
            print(
                f"Memory Usage: {memory_used}/{memory_total} ({memory_percent}%)"
            )

            samples += 1

    except KeyboardInterrupt:
        # Calculate monitoring duration
        duration = datetime.now() - start_time
        hours = duration.total_seconds() / 3600

        # Calculate averages
        avg_cpu = total_cpu / samples
        avg_memory = total_memory / samples

        # Print summary statistics
        print("\n" + "=" * 50)
        print("MONITORING SUMMARY")
        print("=" * 50)
        print(
            f"Duration: {duration.total_seconds():.1f} seconds ({hours:.2f} hours)"
        )
        print("\nBASELINE USAGE:")
        print(f"Baseline CPU: {baseline_cpu}%")
        print(
            f"Baseline Memory: {baseline_memory_used:.2f}GB ({baseline_memory_percent}%)"
        )
        print("\nPEAK USAGE:")
        print(f"Peak CPU: {peak_cpu}%")
        print(
            f"Peak Memory: {get_size_gb(peak_memory_used):.2f}GB ({peak_memory_percent}%)"
        )
        print(
            f"Memory Delta: {get_size_gb(peak_memory_used - baseline_memory.used):.1f}GB"
        )
        print("\nAVERAGE USAGE:")
        print(f"Average CPU: {avg_cpu:.1f}%")
        print(f"Average Memory: {avg_memory:.1f}%")
        print("=" * 50)
        print("\nMonitoring stopped by user")


if __name__ == "__main__":
    print("Starting system monitoring... (Press Ctrl+C to stop)")
    monitor_system()

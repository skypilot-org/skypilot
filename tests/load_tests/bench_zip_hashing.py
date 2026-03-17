"""Benchmark: _compute_zip_blob_id vs common_utils.hash_file on zip files.

Usage:
    python tests/load_tests/bench_zip_hashing.py [--sizes 1024] [--num-files 200]
"""
import argparse
import os
import random
import statistics
import tempfile
import time
import zipfile

from sky.client.common import _compute_zip_blob_id
from sky.utils.common_utils import hash_file


def _make_zip(directory: str, num_files: int, total_size_mb: float) -> str:
    """Create a zip archive with random content.

    Files are split roughly evenly across num_files entries,
    with a few directory entries mixed in.
    """
    zip_path = os.path.join(directory, f'bench_{total_size_mb}mb.zip')
    per_file_bytes = max(1, int(total_size_mb * 1024 * 1024 / num_files))
    rng = random.Random(42)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i in range(num_files):
            # ~10% directory entries
            if rng.random() < 0.1:
                zf.writestr(f'dir_{i}/', '')
            else:
                data = rng.randbytes(per_file_bytes)
                zf.writestr(f'file_{i:04d}.bin', data)
    return zip_path


def _bench(fn, *args, warmup: int = 1, iterations: int = 5):
    """Run fn(*args) with warmup, return list of elapsed times in seconds."""
    for _ in range(warmup):
        fn(*args)
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn(*args)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
    return times


def main():
    parser = argparse.ArgumentParser(
        description='Benchmark zip hashing methods')
    parser.add_argument('--sizes',
                        default='1,10,100,500',
                        help='Comma-separated zip sizes in MB (default: '
                        '1,10,100,500)')
    parser.add_argument('--num-files',
                        type=int,
                        default=200,
                        help='Number of entries per zip (default: 200)')
    parser.add_argument('--iterations',
                        type=int,
                        default=5,
                        help='Timing iterations per method (default: 5)')
    args = parser.parse_args()

    sizes = [float(s) for s in args.sizes.split(',')]

    print(f'{"Size":>8s}  {"Method":<25s}  '
          f'{"Mean (s)":>9s}  {"Stdev (s)":>9s}  {"MB/s":>8s}')
    print('-' * 72)

    with tempfile.TemporaryDirectory() as tmpdir:
        for size_mb in sizes:
            zip_path = _make_zip(tmpdir, args.num_files, size_mb)
            actual_mb = os.path.getsize(zip_path) / (1024 * 1024)

            for label, fn, fn_args in [
                ('_compute_zip_blob_id', _compute_zip_blob_id, (zip_path,)),
                ('hash_file (sha256)', hash_file, (zip_path, 'sha256')),
            ]:
                times = _bench(fn, *fn_args, iterations=args.iterations)
                mean = statistics.mean(times)
                stdev = statistics.stdev(times) if len(times) > 1 else 0.0
                throughput = actual_mb / mean if mean > 0 else float('inf')
                print(f'{actual_mb:7.1f}M  {label:<25s}  '
                      f'{mean:9.4f}  {stdev:9.4f}  {throughput:7.1f}')
            print()


if __name__ == '__main__':
    main()

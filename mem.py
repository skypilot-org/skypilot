import tracemalloc
import psutil
import gc
import sys
from pympler import asizeof
from collections import defaultdict
import os

def top_mmap(n: int = 20):
    mmap_usage = defaultdict(int)
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 6 and os.path.isfile(parts[-1]):
                addr_range, perms, offset, dev, inode, path = parts[:6]
                start, end = [int(x, 16) for x in addr_range.split('-')]
                size = end - start
                mmap_usage[os.path.basename(path)] += size
    mmap_usage = sorted(mmap_usage.items(), key=lambda x: x[1], reverse=True)
    for name, size in mmap_usage[:n]:
        print(f"{name}: {size / 1024 / 1024:.1f} MB")
    print(f"Total mmap size: {sum(size for name, size in mmap_usage) / 1024 / 1024:.1f} MB")


proc = psutil.Process()
# tracemalloc.start()

def rss():
    gc.collect()
    print(f'{proc.memory_info().rss / 1024 / 1024:.2f}MB')


def tm():
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    print(f'Current: {current / 1024 / 1024:.2f}MB Peak: {peak / 1024 / 1024:.2f}MB')

def top(n: int = 20):
    gc.collect()
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')
    for stat in top_stats[:n]:
        print(stat)


def top_modules(n: int = 20):
    sizes = []
    for name, mod in sys.modules.items():
        size = asizeof.asizeof(mod)
        sizes.append((name, size))
    sizes.sort(key=lambda x: x[1], reverse=True)
    for name, size in sizes[:n]:
        print(f"{name}: {size / 1024 / 1024:.1f} MB")
    print(f"Total module size: {sum(size for name, size in sizes) / 1024 / 1024:.1f} MB")

print('mem utilties loaded')

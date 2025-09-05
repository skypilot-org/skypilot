from sky import core
from sky.utils import common
import gc
import psutil
import os
from collections import defaultdict
import re

proc = psutil.Process()

count = int(os.getenv("RUN_COUNT", "200"))

def rss():
    gc.collect()
    return f'RSS: {proc.memory_info().rss / 1024 / 1024:.2f}MB'

def parse_smaps(pid: int = None):
    if pid is None:
        pid = "self"
    path = f"/proc/{pid}/smaps"
    result = defaultdict(int)

    current_file = None
    with open(path) as f:
        for line in f:
            if re.match(r"^[0-9a-fA-F]+-[0-9a-fA-F]+", line):
                # 新的一段 VMA
                parts = line.strip().split()
                current_file = parts[-1] if len(parts) > 5 else "[anon]"
            elif line.startswith("Rss:"):
                val = int(line.split()[1]) * 1024
                result[current_file] += val
    return dict(result)

print(f'SKYPILOT_DISABLE_LRU_CACHE: {os.getenv("SKYPILOT_DISABLE_LRU_CACHE", "false")}')
for i in range(count):
    core.status(refresh=common.StatusRefreshMode.FORCE, all_users=True)
    print(f'========={i}th iteration==========')
    print(rss())
    mem_usage = parse_smaps()
    for k, v in sorted(mem_usage.items(), key=lambda x: -x[1])[:10]:
        print(f"{k:40} {v/1024/1024:.2f} MB")

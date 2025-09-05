from sky import core
from sky.utils import common
import gc
import psutil
import os

proc = psutil.Process()

count = int(os.getenv("RUN_COUNT", "200"))

def rss():
    gc.collect()
    print(f'RSS: {proc.memory_info().rss / 1024 / 1024:.2f}MB')

print(f'SKYPILOT_DISABLE_LRU_CACHE: {os.getenv("SKYPILOT_DISABLE_LRU_CACHE", "false")}')
for _ in range(count):
    core.status(refresh=common.StatusRefreshMode.FORCE, all_users=True)
    rss()

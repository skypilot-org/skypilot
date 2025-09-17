from sky import core
import psutil
from sky.provision.kubernetes import utils
from sky.utils import annotations
from sky.utils import common_utils
from sky import check

proc = psutil.Process()

print(f'RSS start: {proc.memory_info().rss / 1024 / 1024} MB')

def run(fn, *args, **kwargs):
    annotations.clear_request_level_cache()
    print(f'RSS before {fn.__name__}: {proc.memory_info().rss / 1024 / 1024} MB')
    fn(*args, **kwargs)
    common_utils.release_memory()
    print(f'RSS after {fn.__name__}: {proc.memory_info().rss / 1024 / 1024} MB')
    annotations.clear_request_level_cache()
    common_utils.release_memory()
    print(f'RSS after {fn.__name__} clear cache: {proc.memory_info().rss / 1024 / 1024} MB')

for i in range(10):
    run(utils.get_kubernetes_node_info, 'in-cluster')
    run(utils.get_kubernetes_node_info, 'nebius-mk8s-nebius-shopify-k8s-cluster')
    # run(core.realtime_kubernetes_gpu_availability)
    # run(core.cost_report, days=30)
    # run(check.check)

print(f'RSS end: {proc.memory_info().rss / 1024 / 1024} MB')

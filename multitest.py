from sky import core
from sky.provision.kubernetes import utils
from sky.utils import annotations
from sky.utils import common_utils
from sky import check

# print(utils.get_kubernetes_node_info('in-cluster'))
print(len(core.cost_report(days=30)))
annotations.clear_request_level_cache()
common_utils.release_memory()
# print(utils.get_kubernetes_node_info('in-cluster'))
print(len(core.cost_report(days=30)))

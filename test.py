from sky.provision.kubernetes import utils
from sky.utils import common_utils
from sky.utils import annotations

print(utils.get_kubernetes_node_info('in-cluster'))
annotations.clear_request_level_cache()
common_utils.release_memory()
print(utils.get_kubernetes_node_info('in-cluster'))

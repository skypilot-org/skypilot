from sky.provision.kubernetes import utils
from sky.utils import common_utils

print(utils.get_kubernetes_node_info('in-cluster'))
common_utils.release_memory()
print(utils.get_kubernetes_node_info('in-cluster'))

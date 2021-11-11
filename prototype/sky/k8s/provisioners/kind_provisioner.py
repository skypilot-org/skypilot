from typing import Dict

from sky.k8s.provisioners.base_k8s_provisioner import BaseK8sProvisioner
from sky.k8s.provisioners.utils import run_shell_command


class KindProvisioner(BaseK8sProvisioner):
    """
    Creates a kubernetes cluster the local machine using kind. Useful for debug/testing.
    """
    def create_cluster(self,
                       name: str,
                       node_types: Dict[str, int]):
        #TODO(romilb): We ignore node_types for now. Look into provisioning larger clusters with kind.
        ret_code, output = run_shell_command("kind create cluster")
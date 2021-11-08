from typing import Dict

from sky.k8s.provisioners.eks_provisioner import EKSProvisioner
from sky.k8s.workloads.k8s_workload_deployer import K8sWorkloadDeployer
from sky.k8s.workloads.ray_k8s_utils import get_ray_head_template_deployment
from sky.k8s.wrappers.sky_task import SkyRayTask


class SkyManager(object):
    def __init__(self, cloud='aws'):
        if cloud == 'aws':
            self.provisioner = EKSProvisioner(region='us-west-2')
        else:
            raise ValueError(f"Unknown cloud type {cloud}")

    def create_cluster(self,
                       cluster_name: str,
                       node_types: Dict[str, int],
                       kubeconfig_path: str,
                       overwrite_default_kubeconfig: bool = True
                       ):
        """
        :param cluster_name: Cluster name to use
        :param node_types: Dictionary mapping instance type to capacity.
        :param kubeconfig_path: Path where to write the kubeconfig for authentication
        """
        return self.provisioner.create_cluster(cluster_name, node_types, kubeconfig_path, overwrite_default_kubeconfig)

    def delete_cluster(self,
                       cluster_name: str):
        return self.provisioner.delete_cluster(cluster_name)

    def deploy_task(self,
                    kubeconfig_path: str,
                    task: SkyRayTask):
        deployer = K8sWorkloadDeployer(kubeconfig_path)
        head_deployment = get_ray_head_template_deployment(task.name,)
        deployer.deploy_k8s_objects()
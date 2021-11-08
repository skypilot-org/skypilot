import os
from shutil import copy2
from typing import Dict

from sky.k8s.provisioners.base_k8s_provisioner import BaseK8sProvisioner
from sky.k8s.provisioners.utils import run_shell_command
import tempfile
import logging

logger = logging.getLogger(__name__)

class EKS_CONSTANTS(object):
    # An eks YAML is constructed by appended nodegroup bodies to a config header.
    CONFIG_HEADER = r"""apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: {cluster_name}
  region: {cluster_region}

nodeGroups:
  """

    NODEGROUP_BODY = r"""- name: {ng_name}
    instanceType: {instance_type}
    desiredCapacity: {capacity}
    volumeSize: {volume_size}
    volumeType: {volume_type}
    labels:
      nodegroup-name: {ng_name}
    """

    KUBECTL_CFG_PATH = '~/.kube/config'

class EKSProvisioner(BaseK8sProvisioner):
    """
    Creates a kubernetes cluster on AWS using EKS as the provisioner.
    """

    def __init__(self,
                 region: str = 'us-west-2'):
        """
        :param region: Region to use for AWS
        """
        self.region = region

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
        # Get yaml for the EKS cluster spec
        eks_yaml = self.get_eks_yaml(cluster_name=cluster_name,
                                     cluster_region=self.region,
                                     node_types=node_types)

        # Write yaml to tmp dir
        yaml_path = os.path.join(tempfile.gettempdir(), 'sky_eks_cluster.yaml')
        logger.debug(f"Writing EKS yaml for {cluster_name} to {yaml_path}.")
        with open(yaml_path, 'w') as f:
            f.write(eks_yaml)

        # Create cluster with eksctl
        eksctl_cmd = f"eksctl create cluster -f {yaml_path} --kubeconfig {kubeconfig_path}"
        logger.debug(f"Running command {eksctl_cmd}")
        retcode, output = run_shell_command(eksctl_cmd)

        if retcode != 0:
            raise Exception(output)

        if overwrite_default_kubeconfig:
            if os.path.exists(EKS_CONSTANTS.KUBECTL_CFG_PATH):
                os.remove(EKS_CONSTANTS.KUBECTL_CFG_PATH)
            copy2(kubeconfig_path, EKS_CONSTANTS.KUBECTL_CFG_PATH)

        cluster_config = kubeconfig_path
        return retcode, output, cluster_config

    def delete_cluster(self,
                       cluster_name: str):
        eksctl_cmd = f"eksctl delete cluster --name={cluster_name}"
        retcode, output = run_shell_command(eksctl_cmd)
        if retcode == 0:
            return True
        else:
            return False

    @staticmethod
    def get_eks_yaml(cluster_name: str,
                     cluster_region: str,
                     node_types: Dict[str, int],
                     volume_size: int = 20,
                     volume_type: str = 'gp2'):
        """
        Generates a yaml for eksctl to create a cluster with.
        :param cluster_name: Name to assign the cluster in EKS/cloudformation
        :param cluster_region: AWS region to launch the cluster in. eg. us-west-2
        :param node_types: A dictionary of instance type mapping to the number of instances required for that type. Eg. {'m5.xlarge': 5}
        :param volume_size: size of storage volume in gb. eg. 20
        :param volume_type: EBS volume type. gp2, io1, io2.
        """
        # Header for the yaml
        header = EKS_CONSTANTS.CONFIG_HEADER.format(cluster_name=cluster_name, cluster_region=cluster_region)

        # populate node groups and later combine them with header
        ng_specs = ""
        for instance_type, capacity in node_types.items():
            ng_spec = EKS_CONSTANTS.NODEGROUP_BODY.format(ng_name='ng-' + instance_type.replace(".", ""),
                                                          instance_type=instance_type,
                                                          capacity=capacity,
                                                          volume_size=volume_size,
                                                          volume_type=volume_type)
            ng_specs += ng_spec + "\n  "

        yaml = header + ng_specs
        return yaml

if __name__ == '__main__':
    node_types = {"m5.xlarge": 1, "m5.2xlarge": 1}
    e=EKSProvisioner(region='us-west-2')
    e.create_cluster(cluster_name="my-cluster", node_types=node_types, kubeconfig_path='myclusterconfig.yaml')
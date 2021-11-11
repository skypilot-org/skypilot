from typing import Dict


class BaseK8sProvisioner(object):
    '''
    A k8s provisioner is responsible for creating a k8s cluster and returning the kubeconfig which can be used to auth with the cluster
    '''

    def __init__(self):
        pass

    def create_cluster(self,
                       name: str,
                       node_types: Dict[str, int]):
        """
        Creates a k8s cluster with the specified node types.
        :param name: Name of the cluster
        :param node_types: A dictionary of instance type mapping to the number of instances required for that type. Eg. {'m5.xlarge': 5}
        """
        raise NotImplementedError("Implement in a child class.")
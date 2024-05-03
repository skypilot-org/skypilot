from typing import List

import sky
from sky.adaptors import kubernetes


class AutoscalerAdapter:
    _REPR = 'AutoscalerAdapter'

    def __init__(self):
        pass

    def __repr__(self):
        return self._REPR

    def detect(self):
        # Detect the type of autoscaler in the cluster
        raise NotImplementedError

    def get_candidate_resources(self) -> List[sky.Resource]:
        # Get the resources (GPUs, CPUs, Memory) configured in the autoscaler nodepools
        raise NotImplementedError

class KarpenterAdapter(AutoscalerAdapter):
    #TODO(romilb): This supports only v1beta1 API of Karpenter
    _REPR = 'KarpenterAdapter'

    def detect(self):
        # Check if any nodepools are configured with Karpenter

        return True

    def get_candidate_resources(self) -> List[sky.Resource]:
        # Get the instance resources (GPUs, CPUs, Memory) configured in the autoscaler nodepools
        return [sky.Resource()]

    def get_nodepools(self):
        # Define the API parameters
        group = 'karpenter.sh'  # Karpenter API group
        version = 'v1beta1'  # API version
        plural = 'nodepools'  # The plural name of the resource

        try:
            # Fetch the node pools (cluster-scoped)
            node_pools = kubernetes.custom_objects_api().list_cluster_custom_object(group, version,
                                                                 plural)
            return node_pools
        except Exception as e:
            print("An error occurred: ", e)

    def get_resources_from_nodepools(self):
        node_pools = self.get_nodepools()
        for node_pool in node_pools.get('items', []):
            node_class_ref = node_pool.get('spec', {}).get('template',
                                                           {}).get(
                'spec', {}).get('nodeClassRef', {})
            requirements = node_pool.get('spec', {}).get('template',
                                                         {}).get('spec',
                                                                 {}).get(
                'requirements', [])
            print(
                f"NodePool Name: {node_pool.get('metadata', {}).get('name')}")
            print(f"Node Class Ref: {node_class_ref}")
            print("Requirements:")
            for requirement in requirements:
                print(
                    f"  - Key: {requirement.get('key')}, Operator: {requirement.get('operator')}, Values: {requirement.get('values')}")

AUTOSCALER_ADAPTERS_REGISTRY = [KarpenterAdapter]
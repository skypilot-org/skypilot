from typing import List, Optional

import sky
from sky import skypilot_config
from sky.adaptors import kubernetes


class AutoscalerAdapter:
    _REPR = 'AutoscalerAdapter'

    def __init__(self):
        pass

    def __repr__(self):
        return self._REPR

    def detect(self) -> bool:
        """Detect if the autoscaler is configured.

        Can use Kubernetes API or any other method to detect the autoscaler.

        Returns:
            bool: True if the autoscaler is configured, False otherwise.
        """
        raise NotImplementedError

    def get_candidate_resources(self) -> Optional[List[sky.Resources]]:
        """
        Get the list of all resources that can be provisioned by the autoscaler.

        Returns:
            List[sky.Resource]: List of resources that can be provisioned by
              the autoscaler. None if the autoscaler does not provide this
              information. In that case, SkyPilot must assume that the
              autoscaler can provision any resource.
        """
        raise NotImplementedError

    @property
    def label_formatter(self) -> Optional[sky.LabelFormatter]:
        """
        Get the label formatter for the autoscaler.

        Returns:
            sky.LabelFormatter: The label formatter for the autoscaler. None if
              the autoscaler does not provide this information.
        """
        raise NotImplementedError

    def check_resources_fit(self, resources: sky.Resources) -> bool:
        """
        Check if the resources fit the autoscaler configuration.

        Does not provide any guarantees about the availability of the resources.

        Returns:
            bool: True if the resources fit the autoscaler configuration, False otherwise.
        """
        raise NotImplementedError


class GenericAutoscalerAdapter(AutoscalerAdapter):
    # Uses skypilot_config to determine if an autoscaler is configured
    _REPR = 'GenericAutoscalerAdapter'

    def detect(self):
        return skypilot_config.get_nested(['kubernetes', 'autoscaling'], False)

    def get_candidate_resources(self) -> Optional[List[sky.Resources]]:
        # Since this is a generic autoscaler, we cannot determine the resources
        # that can be provisioned by the autoscaler.
        return None

    def check_resources_fit(self, resources: sky.Resources) -> bool:
        # Since this is a generic autoscaler, we cannot determine if the
        # resources fit the autoscaler configuration. Assume all resources fit.
        return True


class GKEAdapter(AutoscalerAdapter):
    _REPR = 'GKEAdapter'

    def detect(self):
        # Check if any nodepools are configured with GKE
        # This requires polling the gcloud container_v1 API to get the nodepools
        # This can be tricky if the user does not have the necessary permissions
        raise NotImplementedError

class KarpenterAdapter(AutoscalerAdapter):
    #TODO(romilb): This supports only v1beta1 API of Karpenter
    _REPR = 'KarpenterAdapter'

    def detect(self):
        # Check if any nodepools are configured with Karpenter
        node_pools = self._get_nodepools()
        if not node_pools:
            return False
        return True

    def get_candidate_resources(self) -> List[sky.Resources]:
        return [sky.Resource()]

    def check_resources_fit(self, resources: sky.Resources) -> bool:
        as_resources_list = self.get_candidate_resources()
        for as_resources in as_resources_list:
            if resources.less_demanding_than(as_resources):
                return True
        return False

    def _get_nodepools(self):
        # Define the API parameters
        group = 'karpenter.sh'
        version = 'v1beta1'
        plural = 'nodepools'
        try:
            # Fetch the node pools (cluster-scoped)
            node_pools = kubernetes.custom_objects_api().list_cluster_custom_object(group, version,
                                                                 plural)
            return node_pools
        except Exception as e:
            print("An error occurred: ", e)

    def _get_resources(self) -> List[sky.Resources]:
        node_pools = self._get_nodepools()
        resources = []
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
            resources.append(self._parse_requirements(requirements))
        return resources

    def _parse_requirements(self, requirements: Dict[str, Any]) -> sky.Resources:
        raise NotImplementedError

AUTOSCALER_ADAPTERS_REGISTRY = [KarpenterAdapter]
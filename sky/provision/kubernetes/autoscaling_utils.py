from typing import List

import sky


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

AUTOSCALER_ADAPTERS_REGISTRY = [KarpenterAdapter]
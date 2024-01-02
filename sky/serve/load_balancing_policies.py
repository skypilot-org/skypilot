"""LoadBalancingPolicy: Policy to select endpoint."""
import random
import typing
from typing import List, Optional, Tuple, Dict

from sky import sky_logging
from sky.serve.serve_utils import AcceleratorType

if typing.TYPE_CHECKING:
    import fastapi

logger = sky_logging.init_logger(__name__)


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""

    def __init__(self) -> None:
        self.ready_replicas: List[str] = []

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        raise NotImplementedError
    
    def update_policy_parameters(self, ilp_assignment_vector = None) -> None:
        raise NotImplementedError

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    def select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        raise NotImplementedError


class RoundRobinPolicy(LoadBalancingPolicy):
    """Round-robin load balancing policy."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.index = 0

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(ready_replicas) != set(self.ready_replicas):
            # If the autoscaler keeps scaling up and down the replicas,
            # we need this shuffle to not let the first replica have the
            # most of the load.
            random.shuffle(ready_replicas)
            self.ready_replicas = ready_replicas
            self.index = 0

    def select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        if not self.ready_replicas:
            return None
        ready_replica_url = self.ready_replicas[self.index]
        self.index = (self.index + 1) % len(self.ready_replicas)
        request_repr = ('<Request '
                        f'method="{request.method}" '
                        f'url="{request.url}" '
                        f'headers={dict(request.headers)} '
                        f'query_params={dict(request.query_params)}'
                        '>')
        logger.info(f'Selected replica {ready_replica_url} '
                    f'for request {request_repr}')
        return ready_replica_url

class HeteroGPUPolicy(LoadBalancingPolicy):
    """LB policy for heterogeneous GPU backends."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Ready replicas by accelerator type.
        self.ready_replicas = Dict[AcceleratorType, List[str]]
        # Round robin index tracking per accelerator type.
        self.indexes = Dict[AcceleratorType, int]
        # Request size bucket boundaries.
        self.bucket_size = [(0, 25), (25, 100), (100, 250), (250, 500),
                            (500, 1000), (1000, 2000), (2000, 4000)]
        # ILP solution's mapping from request size bucket to accelerator type.
        self.ilp_assigment_vector : List[AcceleratorType] = [None] * len(self.bucket_size)

    def set_ready_replicas(self, ready_replica_urls_accels: List[Tuple[str, AcceleratorType]]) -> None:
        # Separate the new ready replicas by accelerator type.
        new_ready_replicas = dict()
        for ip, accelerator in ready_replica_urls_accels:
            if accelerator not in new_ready_replicas:
                new_ready_replicas[accelerator] = [ip]
            else:
                new_ready_replicas[accelerator].append(ip)

        # Update the ready replicas.
        for accelerator in self.ready_replicas:
            if accelerator not in new_ready_replicas:
                # Remove any accelerator types no longer in the allocation pool.
                del self.ready_replicas[accelerator]
                del self.indexes[accelerator]
            elif set(new_ready_replicas[accelerator]) != set(self.ready_replicas[accelerator]):
                # If the autoscaler keeps scaling up and down the replicas,
                # we need this shuffle to not let the first replica have the
                # most of the load.
                ips = new_ready_replicas[accelerator][:]
                random.shuffle(ips)
                self.ready_replicas[accelerator] = ips
                self.indexes[accelerator] = 0
        
        # Add ready replicas for new accelerator types. 
        for accelerator in new_ready_replicas:
            if accelerator not in self.ready_replicas:
                # Add a new accelerator type to the 
                self.ready_replicas[accelerator] = new_ready_replicas[accelerator][:]
                self.indexes[accelerator] = 0

    def update_policy_parameters(self, ilp_assignment_vector = None) -> None:
        self.ilp_assigment_vector = ilp_assignment_vector

    # TODO(tgriggs): Requests should be partially redirected to fallback VMs when primary 
    # is still being provisioned.
    # TODO(tgriggs): Extend this to support bucket slices (instead of full buckets).
    def select_replica(self, request: 'fastapi.Request', input_token_length: int) -> Optional[str]:
        if not self.ready_replicas:
            return None

        # Get bucket index based on input token length.
        bucket_idx : int
        for idx, (lower, upper) in enumerate(self.bucket_size):
            if lower == 2000 and lower <= input_token_length or lower <= input_token_length < upper:
                bucket_idx = idx
                break

        # Get accelerator type based on bucket index.
        accelerator_type = self.ilp_assigment_vector[bucket_idx]

        ready_replica_url : str
        if accelerator_type is None or accelerator_type not in self.ready_replicas:
            # If accelerator type is unavailable, fallback to random accelerator type.
            logger.info(f'Request of size {input_token_length} mapped to {accelerator_type}, '
                        f'but none are allocated.')
            accelerator_type = random.choice(list(self.ready_replicas.keys()))

        # Choose replica of the chosen accelerator type.
        ready_replica_url = self.ready_replicas[accelerator_type][self.indexes[accelerator_type]]
        self.indexes[accelerator_type] = (self.indexes[accelerator_type] + 1) % len(self.indexes[accelerator_type])

        # Send the request.
        request_repr = ('<Request '
                        f'method="{request.method}" '
                        f'url="{request.url}" '
                        f'headers={dict(request.headers)} '
                        f'query_params={dict(request.query_params)}'
                        '>')
        logger.info(f'Selected replica {ready_replica_url} with accelerator {accelerator_type} '
                    f'for request {request_repr} with request size {input_token_length}')
        return ready_replica_url

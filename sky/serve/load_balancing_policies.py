"""LoadBalancingPolicy: Policy to select endpoint."""
import random
import typing
from typing import List, Optional

from sky import sky_logging

if typing.TYPE_CHECKING:
    import fastapi

logger = sky_logging.init_logger(__name__)


def _request_repr(request: 'fastapi.Request') -> str:
    return ('<Request '
            f'method="{request.method}" '
            f'url="{request.url}" '
            f'headers={dict(request.headers)} '
            f'query_params={dict(request.query_params)}>')


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""

    def __init__(self) -> None:
        self.ready_replicas: List[str] = []

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        raise NotImplementedError

    def select_replica(self, request: 'fastapi.Request',
                       disabled_urls: List[str]) -> Optional[str]:
        replica = self._select_replica(request, disabled_urls)
        if replica is not None:
            logger.info(f'Selected replica {replica} '
                        f'for request {_request_repr(request)}')
        else:
            logger.warning('No replica selected for request '
                           f'{_request_repr(request)}')
        return replica

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    def _select_replica(self, request: 'fastapi.Request',
                        disabled_urls: List[str]) -> Optional[str]:
        raise NotImplementedError


class RoundRobinPolicy(LoadBalancingPolicy):
    """Round-robin load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.index = 0

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        # If the autoscaler keeps scaling up and down the replicas,
        # we need this shuffle to not let the first replica have the
        # most of the load.
        random.shuffle(ready_replicas)
        self.ready_replicas = ready_replicas
        self.index = 0

    def _select_replica(self, request: 'fastapi.Request',
                        disabled_urls: List[str]) -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        check_disable = True
        if all(url in disabled_urls for url in self.ready_replicas):
            check_disable = False
        while True:
            ready_replica_url = self.ready_replicas[self.index]
            self.index = (self.index + 1) % len(self.ready_replicas)
            if not check_disable or ready_replica_url not in disabled_urls:
                return ready_replica_url

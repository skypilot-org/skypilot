"""LoadBalancingPolicy: Policy to select endpoint."""
import random
import typing
from typing import List, Optional
import rich, io
# from rich import print
from rich import console as rich_console

from sky import sky_logging
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import fastapi

logger = sky_logging.init_logger(__name__)


def _rich_format(*args):
    output_buffer = io.StringIO()
    console = rich_console.Console(file=output_buffer, color_system='truecolor')
    console.print(*args, sep=' ', end='')
    return output_buffer.getvalue()


def _request_repr(request: 'fastapi.Request') -> str:
    return ('<Request '
            f'method="{request.method}" '
            f'url="{request.url}" '
            f'headers={dict(request.headers)} '
            f'query_params={dict(request.query_params)}>')


class LoadBalancingPolicy:
    """Abstract class for load balancing policies."""

    def __init__(self) -> None:
        self.ready_replicas: List[str] = {}

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        raise NotImplementedError

    def select_replica(self, request: 'fastapi.Request', method, path) -> Optional[str]:
        replica = self._select_replica(request)
        maybe_body = f' with body {_rich_format(request)}' if request is not None else ''
        request_repr = f'{method.upper()} request to {path}{maybe_body}'
        if replica is not None:
            (url, resources) = replica
            print(f'Selected replica {ux_utils.BOLD}{resources}{ux_utils.RESET_BOLD} with endpoint {url} for {request_repr}')
        else:
            print(f'No available replica for {request_repr}')
        return replica

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        raise NotImplementedError


class RoundRobinPolicy(LoadBalancingPolicy):
    """Round-robin load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.index = 0

    def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        # print(ready_replicas)
        if set(self.ready_replicas.keys()) == set(ready_replicas.keys()):
            return
        print(f'Updating ready replicas to {_rich_format(ready_replicas)}')
        # If the autoscaler keeps scaling up and down the replicas,
        # we need this shuffle to not let the first replica have the
        # most of the load.
        # random.shuffle(ready_replicas)
        self.ready_replicas = ready_replicas
        self.index = 0

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        ready_replica_url = list(self.ready_replicas)[self.index]
        self.index = (self.index + 1) % len(self.ready_replicas)
        return ready_replica_url, self.ready_replicas[ready_replica_url]

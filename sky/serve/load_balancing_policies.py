"""LoadBalancingPolicy: Policy to select endpoint."""
import asyncio
import bisect
import collections
import dataclasses
import math
import random
import threading
import time
import typing
from typing import Dict, List, Optional
from urllib import parse

import aiohttp

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.serve import constants

if typing.TYPE_CHECKING:
    import fastapi

xxhash = adaptors_common.LazyImport('xxhash')
logger = sky_logging.init_logger(__name__)

# Define a registry for load balancing policies
LB_POLICIES = {}
DEFAULT_LB_POLICY = None
# Prior to #4439, the default policy was round_robin. We store the legacy
# default policy here to maintain backwards compatibility. Remove this after
# 2 minor release, i.e., 0.9.0.
LEGACY_DEFAULT_POLICY = 'round_robin'


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

    def __init_subclass__(cls, name: str, default: bool = False):
        LB_POLICIES[name] = cls
        if default:
            global DEFAULT_LB_POLICY
            assert DEFAULT_LB_POLICY is None, (
                'Only one policy can be default.')
            DEFAULT_LB_POLICY = name

    @classmethod
    def make_policy_name(cls, policy_name: Optional[str]) -> str:
        """Return the policy name."""
        assert DEFAULT_LB_POLICY is not None, 'No default policy set.'
        if policy_name is None:
            return DEFAULT_LB_POLICY
        return policy_name

    @classmethod
    def make(cls, policy_name: Optional[str] = None) -> 'LoadBalancingPolicy':
        """Create a load balancing policy from a name."""
        policy_name = cls.make_policy_name(policy_name)
        if policy_name not in LB_POLICIES:
            raise ValueError(f'Unknown load balancing policy: {policy_name}')
        return LB_POLICIES[policy_name]()

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        raise NotImplementedError

    def select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        replica = self._select_replica(request)
        if replica is not None:
            logger.info(f'Selected replica {replica} '
                        f'for request {_request_repr(request)}')
        else:
            logger.warning('No replica selected for request '
                           f'{_request_repr(request)}')
        return replica

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        raise NotImplementedError

    def pre_execute_hook(self, replica_url: str,
                         request: 'fastapi.Request') -> None:
        pass

    def post_execute_hook(self, replica_url: str,
                          request: 'fastapi.Request') -> None:
        pass


class RoundRobinPolicy(LoadBalancingPolicy, name='round_robin'):
    """Round-robin load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.index = 0

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        # If the autoscaler keeps scaling up and down the replicas,
        # we need this shuffle to not let the first replica have the
        # most of the load.
        random.shuffle(ready_replicas)
        self.ready_replicas = ready_replicas
        self.index = 0

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        ready_replica_url = self.ready_replicas[self.index]
        self.index = (self.index + 1) % len(self.ready_replicas)
        return ready_replica_url


class LeastLoadPolicy(LoadBalancingPolicy, name='least_load', default=True):
    """Least load load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.load_map: Dict[str, int] = collections.defaultdict(int)
        self.lock = threading.Lock()

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        with self.lock:
            self.ready_replicas = ready_replicas
            for r in self.ready_replicas:
                if r not in ready_replicas:
                    del self.load_map[r]
            for replica in ready_replicas:
                self.load_map[replica] = self.load_map.get(replica, 0)

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None
        with self.lock:
            return min(self.ready_replicas,
                       key=lambda replica: self.load_map.get(replica, 0))

    def pre_execute_hook(self, replica_url: str,
                         request: 'fastapi.Request') -> None:
        del request  # Unused.
        with self.lock:
            self.load_map[replica_url] += 1

    def post_execute_hook(self, replica_url: str,
                          request: 'fastapi.Request') -> None:
        del request  # Unused.
        with self.lock:
            self.load_map[replica_url] -= 1


async def _check_lb_latency(url: str) -> float:
    # TODO(tian): This only works for meta policy that applies to LB.
    # Not works for replica LB policy.
    path = constants.LB_HEALTH_ENDPOINT
    url = parse.urljoin(url, path)
    # TODO(tian): Hack. Dont use infinite loop.
    while True:
        start_time = time.perf_counter()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    await response.text()
            return time.perf_counter() - start_time
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error checking LB latency: {e}')
        await asyncio.sleep(1)


class ProximateFirstPolicy(LeastLoadPolicy, name='proximate_first'):
    """Proximate first load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.replica2latency: Dict[str, float] = {}
        self.max_load_threshold = 3

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        await super().set_ready_replicas(ready_replicas)
        self.replica2latency = {}
        # TODO(tian): Parallel this.
        for url in self.ready_replicas:
            self.replica2latency[url] = await _check_lb_latency(url)
        logger.info(f'Updated latencies: {self.replica2latency}')

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        del request  # Unused.
        if not self.ready_replicas:
            return None

        min_load = min(self.load_map.values())
        available_replicas = [
            replica for replica in self.ready_replicas
            if self.load_map.get(replica, 0) -
            min_load < self.max_load_threshold
        ]

        if not available_replicas:
            logger.error('All replicas exceed max load threshold of '
                         f'{self.max_load_threshold}. THIS SHOULD NOT HAPPEN.')
            available_replicas = self.ready_replicas

        return min(
            available_replicas,
            key=lambda replica: self.replica2latency.get(replica, float('inf')))


@dataclasses.dataclass
class RingEntry:
    hash_val: int
    replica: str


class ConsistentHashingPolicy(LeastLoadPolicy, name='consistent_hashing'):
    """Consistent hashing load balancing policy.

    Uses consistent hashing to map requests to replicas. This ensures that
    when replicas are added or removed, only a minimal portion of keys get
    remapped to different replicas.

    This class is ported from Envoy. Reference:
    source/extensions/load_balancing_policies/ring_hash/ring_hash_lb.cc
    """
    DEFAULT_MIN_RING_HASH_SIZE = 1024
    DEFAULT_MAX_RING_HASH_SIZE = 1024 * 1024 * 8

    def __init__(self) -> None:
        super().__init__()
        # Default hash key is 'x-hash-key' header
        self.hash_key = 'x-hash-key'
        # Ring of hash values to replica URLs
        self.hash_ring: List[RingEntry] = []
        self.max_load_threshold = 3

    def _hash_function(self, key: str) -> int:
        return xxhash.xxh64(key, seed=0).intdigest()

    def _build_ring(self) -> None:
        # TODO(tian): Support different weights for different replicas.
        normalized_host_weights = [1 / len(self.ready_replicas)] * len(
            self.ready_replicas)
        min_normalized_weight = min(normalized_host_weights)
        scale = min(
            math.ceil(min_normalized_weight * self.DEFAULT_MIN_RING_HASH_SIZE) /
            min_normalized_weight, self.DEFAULT_MAX_RING_HASH_SIZE)
        current_hashes = 0.0
        target_hashes = 0.0
        self.hash_ring = []
        for replica, weight in zip(self.ready_replicas,
                                   normalized_host_weights):
            target_hashes += scale * weight
            i = 0
            while current_hashes < target_hashes:
                hash_key = f'{replica}{i}'
                hash_val = self._hash_function(hash_key)
                self.hash_ring.append(RingEntry(hash_val, replica))
                current_hashes += 1
                i += 1
        self.hash_ring.sort(key=lambda x: x.hash_val)
        logger.info(f'Hash ring: {self.hash_ring}')

    def _select_replica_from_key(self, key: str) -> Optional[str]:
        if not self.hash_ring:
            return None
        key_hash = self._hash_function(key)
        logger.info(f'Key hash to find: {key_hash}')
        # TODO(tian): Avoid this O(n) scan on every request.
        min_load = min(self.load_map.values())
        hashes_in_ring = [
            entry.hash_val
            for entry in self.hash_ring
            if self.load_map.get(entry.replica, 0) -
            min_load < self.max_load_threshold
        ]
        if not hashes_in_ring:
            logger.error('All replicas exceed max load threshold of '
                         f'{self.max_load_threshold}. THIS SHOULD NOT HAPPEN.')
            hashes_in_ring = [entry.hash_val for entry in self.hash_ring]
        idx = bisect.bisect_right(hashes_in_ring, key_hash)
        if idx >= len(self.hash_ring):
            idx = 0
        logger.info(f'Selected replica {idx}: {self.hash_ring[idx]}')
        return self.hash_ring[idx].replica

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        """Update hash ring when replicas change."""
        if set(self.ready_replicas) == set(ready_replicas):
            return
        await super().set_ready_replicas(ready_replicas)
        self._build_ring()

    def _select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        if not self.ready_replicas:
            return None

        key = request.headers.get(self.hash_key)
        if not key:
            logger.error(
                f'No {self.hash_key} header found in request '
                f'{_request_repr(request)}. Falling back to least load.')
            return super()._select_replica(request)

        return self._select_replica_from_key(key)

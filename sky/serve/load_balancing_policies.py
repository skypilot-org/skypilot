"""LoadBalancingPolicy: Policy to select endpoint."""
import asyncio
import bisect
import collections
import dataclasses
import json
import math
import random
import typing
from typing import Dict, List, Optional

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.serve import prefix_tree
from sky.serve import serve_utils

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

    async def background_task(self) -> None:
        pass

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        raise NotImplementedError

    async def select_replica(self, request: 'fastapi.Request') -> Optional[str]:
        replica = await self._select_replica(request)
        if replica is not None:
            logger.info(f'Selected replica {replica} '
                        f'for request {_request_repr(request)}')
        else:
            logger.warning('No replica selected for request '
                           f'{_request_repr(request)}')
        return replica

    # TODO(tian): We should have an abstract class for Request to
    # compatible with all frameworks.
    async def _select_replica(self,
                              request: 'fastapi.Request') -> Optional[str]:
        raise NotImplementedError

    async def pre_execute_hook(self, replica_url: str,
                               request: 'fastapi.Request') -> None:
        pass

    async def post_execute_hook(self, replica_url: str,
                                request: 'fastapi.Request') -> None:
        pass

    async def select_replica_from_subset(self, request: 'fastapi.Request',
                                         available_replicas: List[str],
                                         **kwargs) -> Optional[str]:
        """Select a replica from a subset of available replicas.

        This is used when we want to select only from replicas that have
        capacity. Default implementation filters the ready_replicas to the
        available ones, selects a replica, then restores the original.
        """
        if not available_replicas:
            return None

        # Save original replicas
        original_replicas = self.ready_replicas.copy()

        # Temporarily set ready_replicas to only available ones
        self.ready_replicas = available_replicas

        # Select using the existing policy logic
        replica = await self._select_replica(request, **kwargs)

        # Restore original replicas
        self.ready_replicas = original_replicas

        return replica


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

    async def _select_replica(self, request: 'fastapi.Request',
                              **kwargs) -> Optional[str]:
        del request, kwargs  # Unused.
        if not self.ready_replicas:
            return None
        ready_replica_url = self.ready_replicas[self.index %
                                                len(self.ready_replicas)]
        self.index = (self.index + 1) % len(self.ready_replicas)
        return ready_replica_url


class LeastLoadPolicy(LoadBalancingPolicy, name='least_load', default=True):
    """Least load load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.load_map: Dict[str, int] = collections.defaultdict(int)
        self.lock = asyncio.Lock()

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        async with self.lock:
            self.ready_replicas = ready_replicas
            for r in self.ready_replicas:
                if r not in ready_replicas:
                    del self.load_map[r]
            for replica in ready_replicas:
                self.load_map[replica] = self.load_map.get(replica, 0)

    async def _select_replica(self, request: 'fastapi.Request',
                              **kwargs) -> Optional[str]:
        del request, kwargs  # Unused.
        if not self.ready_replicas:
            return None
        async with self.lock:
            return min(self.ready_replicas,
                       key=lambda replica: self.load_map.get(replica, 0))

    async def pre_execute_hook(self, replica_url: str,
                               request: 'fastapi.Request') -> None:
        del request  # Unused.
        async with self.lock:
            self.load_map[replica_url] += 1

    async def post_execute_hook(self, replica_url: str,
                                request: 'fastapi.Request') -> None:
        del request  # Unused.
        async with self.lock:
            self.load_map[replica_url] -= 1


class ProximateFirstPolicy(LeastLoadPolicy, name='proximate_first'):
    """Proximate first load balancing policy."""

    def __init__(self) -> None:
        super().__init__()
        self.replica2latency: Dict[str, float] = {}

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        await super().set_ready_replicas(ready_replicas)
        self.replica2latency = {}
        # TODO(tian): Parallel this.
        for url in self.ready_replicas:
            lat = await serve_utils.check_lb_latency(url)
            if lat is None:
                lat = float('inf')
            self.replica2latency[url] = lat
        logger.info(f'Updated latencies: {self.replica2latency}')

    async def _select_replica(self, request: 'fastapi.Request',
                              **kwargs) -> Optional[str]:
        del request, kwargs  # Unused.
        if not self.ready_replicas:
            return None

        return min(
            self.ready_replicas,
            key=lambda replica: self.replica2latency.get(replica, float('inf')))


@dataclasses.dataclass(order=True)
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
        hash_ring: List[RingEntry] = []
        for replica, weight in zip(self.ready_replicas,
                                   normalized_host_weights):
            target_hashes += scale * weight
            i = 0
            while current_hashes < target_hashes:
                hash_key = f'{replica}{i}'
                hash_val = self._hash_function(hash_key)
                hash_ring.append(RingEntry(hash_val, replica))
                current_hashes += 1
                i += 1
        hash_ring.sort(key=lambda x: x.hash_val)
        # logger.info(f'Hash ring: {hash_ring}')
        self.hash_ring = hash_ring

    def _bisect_ready(self, target_hash: int) -> Optional[int]:
        """Find the first entry >= target_hash that belongs to a ready replica.

        O(log N) bisection."""
        # Binary search using bisect_left
        idx = bisect.bisect_left(self.hash_ring, RingEntry(target_hash, ''))

        # Scan forward for the first valid entry
        n = len(self.hash_ring)
        for i in range(idx, n):
            if self.hash_ring[i].replica in self.ready_replicas:
                return i  # Found valid index

        # If nothing was found, wrap around to the beginning
        for i in range(0, idx):
            if self.hash_ring[i].replica in self.ready_replicas:
                return i  # Found valid index

        return None

    async def _select_replica_from_key(self, key: str) -> Optional[str]:
        if not self.hash_ring:
            return None
        key_hash = self._hash_function(key)
        logger.debug(f'Key hash to find: {key_hash}')
        idx = self._bisect_ready(key_hash)
        if idx is None:
            return None
        logger.debug(f'Selected replica {idx}: {self.hash_ring[idx]}')
        return self.hash_ring[idx].replica

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        """Update hash ring when replicas change."""
        if set(self.ready_replicas) == set(ready_replicas):
            return
        await super().set_ready_replicas(ready_replicas)
        self._build_ring()

    async def _select_replica(self, request: 'fastapi.Request',
                              **kwargs) -> Optional[str]:
        if not self.ready_replicas:
            return None

        key = request.headers.get(self.hash_key)
        if not key:
            logger.error(
                f'No {self.hash_key} header found in request '
                f'{_request_repr(request)}. Falling back to least load.')
            return await super()._select_replica(request, **kwargs)

        return await self._select_replica_from_key(key)


@dataclasses.dataclass
class PrefixTreeConfig:
    cache_threshold: float = 0.5
    balance_abs_threshold: int = 32
    balance_rel_threshold: float = 1.0001
    eviction_interval_secs: int = 60
    max_tree_size: int = 2**24


async def _get_text(request: 'fastapi.Request') -> Optional[str]:
    if request.method == 'POST':
        if request.url.path == '/v1/chat/completions':
            payload = await request.json()
            messages = payload.get('messages')
            if messages is not None:
                return json.dumps(messages)
        elif request.url.path == '/v1/completions':
            payload = await request.json()
            prompt = payload.get('prompt')
            if prompt is not None:
                return prompt
        elif request.url.path == '/test':
            payload = await request.json()
            text = payload.get('text')
            if text is not None:
                return text
    return None


class PrefixTreePolicy(LeastLoadPolicy, name='prefix_tree', default=False):
    """Prefix tree load balancing policy."""

    async def _background_eviction_thread(self) -> None:
        logger.info('Starting background eviction thread')
        while True:
            await asyncio.sleep(self.config.eviction_interval_secs)
            await self.tree.evict_replica_by_size(self.config.max_tree_size)

    def __init__(self) -> None:
        super().__init__()
        self.tree = prefix_tree.PrefixTree()
        self.config = PrefixTreeConfig()
        self.load_balancing_enabled: bool = False
        self.least_load_fallback: bool = True

    def disbale_least_load_fallback(self) -> None:
        self.least_load_fallback = False

    def enable_load_balancing(self) -> None:
        self.load_balancing_enabled = True

    async def background_task(self) -> None:
        await self._background_eviction_thread()

    async def set_ready_replicas(self, ready_replicas: List[str]) -> None:
        if set(self.ready_replicas) == set(ready_replicas):
            return
        # Update the tree first before we update self.ready_replicas.
        for replica in ready_replicas:
            if replica not in self.ready_replicas:
                await self.tree.insert('', replica)
        for replica in self.ready_replicas:
            if replica not in ready_replicas:
                await self.tree.remove_replica(replica)
        await super().set_ready_replicas(ready_replicas)

    async def _select_replica(self, request: 'fastapi.Request',
                              **kwargs) -> Optional[str]:
        if not self.ready_replicas:
            return None
        text = await _get_text(request)
        replica2load: Dict[str, int] = {
            r: self.load_map.get(r, 0) for r in self.ready_replicas
        }
        if text is None:
            logger.debug(f'No text found in request {_request_repr(request)}. '
                         'Falling back to least load.')
            return min(replica2load, key=lambda r: replica2load[r])
            # return await super()._select_replica(request, **kwargs)
        is_imbalanced = False
        min_replica = None
        if self.load_balancing_enabled:
            min_replica = min(replica2load, key=lambda r: replica2load[r])
            min_load = replica2load[min_replica]
            max_load = max(replica2load.values())
            if (max_load - min_load > self.config.balance_abs_threshold and
                    max_load > self.config.balance_rel_threshold * min_load):
                is_imbalanced = True
        if is_imbalanced:
            logger.debug('Load is imbalanced. Falling back to least load.')
            assert min_replica is not None
            return min_replica
        disabled_url = kwargs.get('disabled_url_in_low_match_rate', None)
        cache_threshold = kwargs.get('cache_threshold', None)
        # if len(text) < 1024:
        #     replica2load.pop(disabled_url, None)
        #     if not replica2load:
        #         return None
        #     return min(replica2load, key=replica2load.get)
        matched_text, replica = await self.tree.prefix_match(text, replica2load)
        matched_rate = len(matched_text) / len(text)
        logger.debug(f'Matched rate: {matched_rate} for request {text[:100]}.')
        if cache_threshold is None:
            cache_threshold = self.config.cache_threshold
        if not self.least_load_fallback or matched_rate > cache_threshold:
            # TODO(tian): Hack. Fix this.
            return_matched_rate = kwargs.get('return_matched_rate', False)
            if return_matched_rate:
                return replica, matched_rate, len(matched_text)  # type: ignore
            return replica
        # logger.info('Falling back to least char count load. '
        #             f'{self.tree.replica_char_count}')
        logger.debug('Falling back to least replica load. '
                     f'{replica2load}')
        replica2load.pop(disabled_url, None)
        if not replica2load:
            return None
        return min(replica2load, key=lambda r: replica2load[r])
        # return await self.tree.get_smallest_replica(self.ready_replicas,
        #                                             disabled_url)

    async def pre_execute_hook(self, replica_url: str,
                               request: 'fastapi.Request') -> None:
        await super().pre_execute_hook(replica_url, request)
        text = await _get_text(request)
        if text is None:
            logger.warning(f'No text found in request {_request_repr(request)} '
                           'when executing pre_execute_hook. Request json: '
                           f'{await request.body()}')
            return
        await self.tree.insert(text, replica_url)

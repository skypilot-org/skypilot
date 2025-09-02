"""Prefix Tree implementation, inherit from SGLang Router."""

import asyncio
import collections
import copy
import dataclasses
import heapq
import random
import time
from typing import (Deque, Dict, Iterable, List, Mapping, Optional, Set, Tuple,
                    Union)

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def _shared_prefix_length(s1: str, s2: str) -> int:
    min_len = min(len(s1), len(s2))
    for i in range(min_len):
        if s1[i] != s2[i]:
            return i
    return min_len


class PrefixTreeNode:
    """A node in approximate multi-replica prefix tree. Each node:
      - Stores text (string)
      - Maintains a dictionary of children, keyed by the next character
      - Records last-access timestamps for each replica that 'touches' this node
      - Knows its parent for easy upward traversals
    """
    # Use __slots__ to avoid the overhead of dynamic dispatching
    __slots__ = (
        'children',
        'text',
        'replica_last_access_time',
        'parent',
        'lock',
    )

    def __init__(
            self,
            text: str,
            parent: Optional['PrefixTreeNode'] = None,
            replica_last_access_time: Optional[Dict[str,
                                                    float]] = None) -> None:
        self.text = text
        self.children: Dict[str, 'PrefixTreeNode'] = {}
        self.replica_last_access_time: Dict[str, float] = {}
        if replica_last_access_time is not None:
            self.replica_last_access_time = copy.copy(replica_last_access_time)
        self.parent: Optional['PrefixTreeNode'] = parent
        self.lock = asyncio.Lock()

    async def replica_access(self, replica: str) -> None:
        async with self.lock:
            self.replica_last_access_time[replica] = time.time() * 1000.0

    async def get_replica_last_access_time(self,
                                           replica: str) -> Optional[float]:
        async with self.lock:
            return self.replica_last_access_time.get(replica, None)

    async def get_replica_last_access_time_dict(self) -> Dict[str, float]:
        async with self.lock:
            return self.replica_last_access_time

    async def remove_replica(self, replica: str) -> None:
        async with self.lock:
            self.replica_last_access_time.pop(replica)

    async def get_all_replicas(self) -> Set[str]:
        async with self.lock:
            return set(self.replica_last_access_time.keys())

    async def is_empty(self) -> bool:
        async with self.lock:
            return not self.children and not self.replica_last_access_time

    async def get_parent(self) -> Optional['PrefixTreeNode']:
        async with self.lock:
            return self.parent

    async def set_parent(self, parent: 'PrefixTreeNode') -> None:
        async with self.lock:
            self.parent = parent

    async def get_child(self, char: str) -> Optional['PrefixTreeNode']:
        async with self.lock:
            return self.children.get(char)

    async def get_children(self) -> Dict[str, 'PrefixTreeNode']:
        async with self.lock:
            return self.children

    async def set_child(self, char: str, node: 'PrefixTreeNode') -> None:
        async with self.lock:
            self.children[char] = node

    async def remove_child(self, char: str) -> Optional['PrefixTreeNode']:
        async with self.lock:
            return self.children.pop(char)

    async def get_text(self) -> str:
        async with self.lock:
            return self.text

    async def set_text(self, text: str) -> None:
        async with self.lock:
            self.text = text

    async def node_to_string(self, prefix: str, is_last: bool) -> str:
        """Generate a string representation of this node and its children."""
        result = []

        # Add prefix and branch character
        branch = '└── ' if is_last else '├── '
        result.append(f'{prefix}{branch}\'{self.text}\' [')

        # Add replica information with timestamps
        replica_info = []
        for replica, timestamp_ms in self.replica_last_access_time.items():
            # Convert milliseconds to seconds and remaining milliseconds
            seconds = int(timestamp_ms / 1000)
            millis = int(timestamp_ms % 1000)

            # Calculate hours, minutes, seconds
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60

            replica_info.append(
                f'{replica} | {hours:02}:{minutes:02}:{seconds:02}.{millis:03}')

        result.append(', '.join(replica_info))
        result.append(']\n')

        # Process children
        child_items = list((await self.get_children()).items())
        child_count = len(child_items)

        for i, (_, child) in enumerate(child_items):
            is_last_child = i == child_count - 1
            new_prefix = f'{prefix}{"    " if is_last else "│   "}'

            result.append(await child.node_to_string(new_prefix, is_last_child))

        return ''.join(result)


@dataclasses.dataclass(order=True)
class EvictionEntry:
    timestamp: float
    replica: str = dataclasses.field(compare=False)
    node: PrefixTreeNode = dataclasses.field(compare=False)


class PrefixTree:
    """Approximate multi-replica prefix tree."""

    def __init__(self) -> None:
        # Root: empty string
        self.root = PrefixTreeNode('')
        self.replica_char_count: Dict[str, int] = collections.defaultdict(int)
        self.tree_lock = asyncio.Lock()

    async def insert(self, text: str, replica: str) -> None:
        """Insert a text into the tree."""
        logger.debug(f'insert: {text[:100]} with replica: {replica}')
        async with self.tree_lock:
            current_idx = 0
            text_len = len(text)
            current_node = self.root
            await current_node.replica_access(replica)
            # Create an entry in replica_char_count for the replica.
            _ = self.replica_char_count[replica]
            prev_node = current_node

            while current_idx < text_len:
                first_char = text[current_idx]
                current_node = prev_node
                remaining_text = text[current_idx:]

                matched_node = await current_node.get_child(first_char)
                if matched_node is None:
                    # Make a new node for the remainder
                    self.replica_char_count[replica] += len(remaining_text)
                    logger.debug(f'replica: {replica}, '
                                 f'create new node: {remaining_text}')
                    new_node = PrefixTreeNode(remaining_text, current_node)
                    await new_node.replica_access(replica)
                    await current_node.set_child(first_char, new_node)
                    break
                else:
                    # Matched
                    logger.debug(f'replica: {replica}, '
                                 f'matched node: {matched_node.get_text()}')
                    matched_node_text = await matched_node.get_text()
                    shared_count = _shared_prefix_length(
                        matched_node_text, remaining_text)
                    logger.debug(f'replica: {replica}, '
                                 f'shared_count: {shared_count}, '
                                 f'matched_node_text: {matched_node_text}, '
                                 f'remaining_text: {remaining_text}')
                    if shared_count < len(matched_node_text):
                        # Partial Match. Split the matched node
                        shared_text = matched_node_text[:shared_count]
                        unique_text = matched_node_text[shared_count:]
                        logger.debug(f'replica: {replica}, '
                                     f'split node: {shared_text}'
                                     f' + {unique_text}')
                        new_node = PrefixTreeNode(
                            shared_text, current_node, await
                            matched_node.get_replica_last_access_time_dict())
                        await new_node.set_child(unique_text[0], matched_node)
                        await current_node.set_child(first_char, new_node)
                        await matched_node.set_text(unique_text)
                        await matched_node.set_parent(new_node)
                        prev_node = new_node
                    else:
                        # All matched.
                        assert len(matched_node_text) == shared_count
                        logger.debug(f'replica: {replica}, '
                                     f'matched_node_text: {matched_node_text}, '
                                     f'shared_count: {shared_count}')
                        prev_node = matched_node
                    if await prev_node.get_replica_last_access_time(replica
                                                                   ) is None:
                        self.replica_char_count[replica] += shared_count
                    await prev_node.replica_access(replica)
                    current_idx += shared_count

    async def prefix_match(
        self,
        text: str,
        available2load: Optional[Dict[str, int]] = None
    ) -> Tuple[str, Optional[str]]:
        """Find the longest prefix of text that matches a node in the tree.

        Args:
            text: The text to match.
            available2load: Dict of replicas to the current load of the replica.
              If None, all replicas are considered.

        Returns:
            matched_text: The longest prefix of text that matches.
            replica: The replica that has accessed the matched node.
        """
        logger.debug(f'prefix_match: {text[:100]} with '
                     f'available2load: {available2load}')
        current_idx = 0
        text_len = len(text)
        succ_node = self.root
        current_node = succ_node
        available_replica_set = (set(available2load.keys())
                                 if available2load is not None else None)

        while current_idx < text_len:
            first_char = text[current_idx]
            remaining_text = text[current_idx:]
            current_node = succ_node
            matched_node = await current_node.get_child(first_char)
            if matched_node is None:
                break
            # If available_replica_set is not None, check if the matched node
            # has any available replicas.
            if available_replica_set is not None:
                if not ((await matched_node.get_all_replicas()) &
                        available_replica_set):
                    break
            succ_node = matched_node
            shared_count = _shared_prefix_length(await matched_node.get_text(),
                                                 remaining_text)
            current_idx += shared_count
            if shared_count < len(await matched_node.get_text()):
                # Partial match, stop here
                break
        current_node = succ_node
        replica = None
        if available_replica_set is None:
            all_replicas = list(await current_node.get_all_replicas())
            if all_replicas:
                replica = random.choice(all_replicas)
        else:
            available_replicas = list((await current_node.get_all_replicas()) &
                                      available_replica_set)
            if available_replicas:
                assert available2load is not None
                min_load = float('inf')
                min_load_replicas = []

                for r in available_replicas:
                    load = available2load[r]
                    if load < min_load:
                        min_load = load
                        min_load_replicas = [r]
                    elif load == min_load:
                        min_load_replicas.append(r)

                replica = random.choice(min_load_replicas)

        # Update the last access time for the replica on this path.
        # NOTE(tian): Seems like we don't need this as this will be updated
        # later in the insert function called by pre_execute_hook.
        # if replica is not None:
        #     reverse_node: Optional[PrefixTreeNode] = current_node
        #     while reverse_node is not None:
        #         await reverse_node.replica_access(replica)
        #         reverse_node = await reverse_node.get_parent()

        return text[:current_idx], replica

    async def _leaf_of(self, node: PrefixTreeNode) -> Iterable[str]:
        candidates = set((await
                          node.get_replica_last_access_time_dict()).keys())
        for child in (await node.get_children()).values():
            for replica in await child.get_replica_last_access_time_dict():
                candidates.discard(replica)
        return candidates

    async def evict_replica_by_size(self, max_size: int) -> None:
        async with self.tree_lock:
            stack: List[PrefixTreeNode] = [self.root]
            pq: List[EvictionEntry] = []
            while stack:
                node = stack.pop()
                for child in (await node.get_children()).values():
                    stack.append(child)
                for replica in await self._leaf_of(node):
                    replica_access = await node.get_replica_last_access_time(
                        replica)
                    if replica_access is not None:
                        entry = EvictionEntry(replica_access, replica, node)
                        heapq.heappush(pq, entry)

            logger.info('Before eviction - Used size per replica:')
            logger.info(', '.join([
                f'{replica}: {size}'
                for replica, size in self.replica_char_count.items()
            ]))

            while pq:
                entry = heapq.heappop(pq)
                replica_usage = self.replica_char_count[entry.replica]
                if replica_usage <= max_size:
                    continue
                node_text_len = len(await entry.node.get_text())
                # If after removing the whole node, the replica usage is smaller
                # than max_size, we only shrink the text size on the node but
                # keep the node in the tree.
                if replica_usage - node_text_len < max_size:
                    removed_size = replica_usage - max_size
                    remaining_size = node_text_len - removed_size
                    await entry.node.set_text(
                        (await entry.node.get_text())[:remaining_size])
                    self.replica_char_count[entry.replica] -= removed_size
                    continue
                if await entry.node.get_replica_last_access_time(entry.replica
                                                                ) is not None:
                    self.replica_char_count[entry.replica] -= node_text_len
                await entry.node.remove_replica(entry.replica)
                parent = await entry.node.get_parent()
                if parent is None:
                    continue
                # Remove empty nodes
                if await entry.node.is_empty():
                    removed_child = await parent.remove_child(
                        (await entry.node.get_text())[0])
                    assert removed_child is entry.node
                    # Delete the removed node to save memory
                    del removed_child
                # Add parent to queue if it becomes a leaf
                if entry.replica in await self._leaf_of(parent):
                    replica_access = await parent.get_replica_last_access_time(
                        entry.replica)
                    if replica_access is not None:
                        entry = EvictionEntry(replica_access, entry.replica,
                                              parent)
                        heapq.heappush(pq, entry)

            logger.info('After eviction - Used size per replica:')
            logger.info(', '.join([
                f'{replica}: {size}'
                for replica, size in self.replica_char_count.items()
            ]))

    async def remove_replica(self, replica: str) -> None:
        """Remove a replica from the tree."""
        async with self.tree_lock:
            stack: List[PrefixTreeNode] = [self.root]
            queue: Deque[PrefixTreeNode] = collections.deque()

            # 1. Find all the leaves for the tenant
            while stack:
                current_node = stack.pop()
                for child in (await current_node.get_children()).values():
                    stack.append(child)
                if replica in await self._leaf_of(current_node):
                    queue.append(current_node)

            # 2. Start from the leaves and traverse up to the root,
            # removing the replica from each node
            while queue:
                current_node = queue.pop()
                await current_node.remove_replica(replica)
                # Remove empty nodes
                parent = await current_node.get_parent()
                if parent is not None:
                    if await current_node.is_empty():
                        removed_child = await parent.remove_child(
                            (await current_node.get_text())[0])
                        assert removed_child is current_node
                        del removed_child
                    # Add parent to queue if it becomes a leaf
                    if replica in await self._leaf_of(parent):
                        queue.append(parent)

            # 3. Remove the replica from the replica_char_count map
            self.replica_char_count.pop(replica)

    async def get_smallest_replica(
            self,
            available_replicas: Optional[List[str]] = None,
            disabled_url: Optional[str] = None) -> Optional[str]:
        """Get the smallest replica in the tree."""
        if disabled_url is not None:
            logger.info(f'disabled_url: {disabled_url} in get_smallest_replica')
        async with self.tree_lock:
            if not self.replica_char_count:
                return None
            available_replica_to_char_count: Mapping[str, Union[int, float]]
            if available_replicas is None:
                if disabled_url is None:
                    available_replica_to_char_count = self.replica_char_count
                else:
                    available_replica_to_char_count = {
                        r: self.replica_char_count.get(r, float('inf'))
                        for r in self.replica_char_count
                        if r != disabled_url
                    }
            else:
                available_replica_to_char_count = {
                    r: self.replica_char_count.get(r, float('inf'))
                    for r in available_replicas
                    if r != disabled_url
                }
            if not available_replica_to_char_count:
                return None
            return min(available_replica_to_char_count.items(),
                       key=lambda x: x[1])[0]

    async def get_used_size_per_replica(self) -> Dict[str, int]:
        """Perform a DFS to traverse all nodes and calculate the total size
        used by each replica."""
        async with self.tree_lock:
            used_size_per_replica: Dict[str, int] = collections.defaultdict(int)
            stack: List[PrefixTreeNode] = [self.root]
            while stack:
                current_node = stack.pop()
                for replica in await current_node.get_all_replicas():
                    used_size_per_replica[replica] += len(
                        await current_node.get_text())
                for child in (await current_node.get_children()).values():
                    stack.append(child)
            return used_size_per_replica

    async def pretty_print(self) -> None:
        """Print a pretty representation of the tree."""
        async with self.tree_lock:
            if not await self.root.get_children():
                return

            result = []
            child_items = list((await self.root.get_children()).items())
            child_count = len(child_items)

            for i, (_, child) in enumerate(child_items):
                is_last = i == child_count - 1
                result.append(await child.node_to_string(prefix='',
                                                         is_last=is_last))
            print('Tree structure:')
            print(''.join(result))
            print(f'replica_char_count: {self.replica_char_count}')

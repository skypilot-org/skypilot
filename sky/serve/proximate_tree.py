"""Proximate Tree implementation, inherit from SGLang Router."""

import collections
import copy
import dataclasses
import heapq
import threading
import time
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def _shared_prefix_length(s1: str, s2: str) -> int:
    min_len = min(len(s1), len(s2))
    for i in range(min_len):
        if s1[i] != s2[i]:
            return i
    return min_len


class ProximateTreeNode:
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
            parent: Optional['ProximateTreeNode'] = None,
            replica_last_access_time: Optional[Dict[str,
                                                    float]] = None) -> None:
        self.text = text
        self.children: Dict[str, 'ProximateTreeNode'] = {}
        self.replica_last_access_time: Dict[str, float] = {}
        if replica_last_access_time is not None:
            self.replica_last_access_time = copy.copy(replica_last_access_time)
        self.parent: Optional['ProximateTreeNode'] = parent
        self.lock = threading.Lock()

    def replica_access(self, replica: str) -> None:
        with self.lock:
            self.replica_last_access_time[replica] = time.time() * 1000.0

    def get_replica_last_access_time(self, replica: str) -> Optional[float]:
        with self.lock:
            return self.replica_last_access_time.get(replica, None)

    def get_replica_last_access_time_dict(self) -> Dict[str, float]:
        with self.lock:
            return self.replica_last_access_time

    def remove_replica(self, replica: str) -> None:
        with self.lock:
            self.replica_last_access_time.pop(replica)

    def get_all_replicas(self) -> List[str]:
        with self.lock:
            return list(self.replica_last_access_time.keys())

    def is_empty(self) -> bool:
        with self.lock:
            return not self.children and not self.replica_last_access_time

    def get_parent(self) -> Optional['ProximateTreeNode']:
        with self.lock:
            return self.parent

    def set_parent(self, parent: 'ProximateTreeNode') -> None:
        with self.lock:
            self.parent = parent

    def get_child(self, char: str) -> Optional['ProximateTreeNode']:
        with self.lock:
            return self.children.get(char)

    def get_children(self) -> Dict[str, 'ProximateTreeNode']:
        with self.lock:
            return self.children

    def set_child(self, char: str, node: 'ProximateTreeNode') -> None:
        with self.lock:
            self.children[char] = node

    def remove_child(self, char: str) -> Optional['ProximateTreeNode']:
        with self.lock:
            return self.children.pop(char)

    def get_text(self) -> str:
        with self.lock:
            return self.text

    def set_text(self, text: str) -> None:
        with self.lock:
            self.text = text

    def node_to_string(self, prefix: str, is_last: bool) -> str:
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
        child_items = list(self.get_children().items())
        child_count = len(child_items)

        for i, (_, child) in enumerate(child_items):
            is_last_child = i == child_count - 1
            new_prefix = f'{prefix}{"    " if is_last else "│   "}'

            result.append(child.node_to_string(new_prefix, is_last_child))

        return ''.join(result)


@dataclasses.dataclass(order=True)
class EvictionEntry:
    timestamp: float
    replica: str = dataclasses.field(compare=False)
    node: ProximateTreeNode = dataclasses.field(compare=False)


class ProximateTree:
    """Approximate multi-replica prefix tree."""

    def __init__(self) -> None:
        # Root: empty string
        self.root = ProximateTreeNode('')
        self.replica_char_count: Dict[str, int] = collections.defaultdict(int)
        self.tree_lock = threading.RLock()

    def insert(self, text: str, replica: str) -> None:
        """Insert a text into the tree."""
        with self.tree_lock:
            current_idx = 0
            text_len = len(text)
            current_node = self.root
            current_node.replica_access(replica)
            prev_node = current_node

            while current_idx < text_len:
                first_char = text[current_idx]
                current_node = prev_node
                remaining_text = text[current_idx:]

                matched_node = current_node.get_child(first_char)
                if matched_node is None:
                    # Make a new node for the remainder
                    self.replica_char_count[replica] += len(remaining_text)
                    logger.debug(f'replica: {replica}, '
                                 f'create new node: {remaining_text}')
                    new_node = ProximateTreeNode(remaining_text, current_node)
                    new_node.replica_access(replica)
                    current_node.set_child(first_char, new_node)
                    break
                else:
                    # Matched
                    logger.debug(f'replica: {replica}, '
                                 f'matched node: {matched_node.get_text()}')
                    matched_node_text = matched_node.get_text()
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
                        new_node = ProximateTreeNode(
                            shared_text, current_node,
                            matched_node.replica_last_access_time)
                        new_node.set_child(unique_text[0], matched_node)
                        current_node.set_child(first_char, new_node)
                        matched_node.set_text(unique_text)
                        matched_node.set_parent(new_node)
                        prev_node = new_node
                    else:
                        # All matched.
                        assert len(matched_node_text) == shared_count
                        logger.debug(f'replica: {replica}, '
                                     f'matched_node_text: {matched_node_text}, '
                                     f'shared_count: {shared_count}')
                        prev_node = matched_node
                    if prev_node.get_replica_last_access_time(replica) is None:
                        self.replica_char_count[replica] += shared_count
                    prev_node.replica_access(replica)
                    current_idx += shared_count

    def prefix_match(self, text: str) -> Tuple[str, List[str]]:
        """Find the longest prefix of text that matches a node in the tree.

        Returns:
            matched_text: The longest prefix of text that matches.
            replicas: List of replicas that have accessed the matched node.
        """
        current_idx = 0
        text_len = len(text)
        succ_node = self.root
        current_node = succ_node

        while current_idx < text_len:
            first_char = text[current_idx]
            remaining_text = text[current_idx:]
            current_node = succ_node
            matched_node = current_node.get_child(first_char)
            if matched_node is None:
                break
            succ_node = matched_node
            shared_count = _shared_prefix_length(matched_node.get_text(),
                                                 remaining_text)
            current_idx += shared_count
            if shared_count < len(matched_node.get_text()):
                # Partial match, stop here
                break
        current_node = succ_node
        replicas = current_node.get_all_replicas()
        return text[:current_idx], replicas

    def _leaf_of(self, node: ProximateTreeNode) -> Iterable[str]:
        candidates = set(node.get_replica_last_access_time_dict().keys())
        for child in node.get_children().values():
            for replica in child.get_replica_last_access_time_dict():
                candidates.discard(replica)
        return candidates

    def evict_tenant_by_size(self, max_size: int) -> None:
        with self.tree_lock:
            stack: List[ProximateTreeNode] = [self.root]
            pq: List[EvictionEntry] = []
            while stack:
                node = stack.pop()
                for child in node.children.values():
                    stack.append(child)
                for replica in self._leaf_of(node):
                    replica_access = node.get_replica_last_access_time(replica)
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
                if entry.node.get_replica_last_access_time(
                        entry.replica) is not None:
                    self.replica_char_count[entry.replica] -= len(
                        entry.node.get_text())
                entry.node.remove_replica(entry.replica)
                parent = entry.node.get_parent()
                if parent is None:
                    continue
                # Remove empty nodes
                if entry.node.is_empty():
                    removed_child = parent.remove_child(
                        entry.node.get_text()[0])
                    assert removed_child is entry.node
                    # Delete the removed node to save memory
                    del removed_child
                # Add parent to queue if it becomes a leaf
                if entry.replica in self._leaf_of(parent):
                    replica_access = parent.get_replica_last_access_time(
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

    def remove_replica(self, replica: str) -> None:
        """Remove a replica from the tree."""
        with self.tree_lock:
            stack: List[ProximateTreeNode] = [self.root]
            queue: Deque[ProximateTreeNode] = collections.deque()

            # 1. Find all the leaves for the tenant
            while stack:
                current_node = stack.pop()
                for child in current_node.get_children().values():
                    stack.append(child)
                if replica in self._leaf_of(current_node):
                    queue.append(current_node)

            # 2. Start from the leaves and traverse up to the root,
            # removing the replica from each node
            while queue:
                current_node = queue.pop()
                current_node.remove_replica(replica)
                # Remove empty nodes
                parent = current_node.get_parent()
                if parent is not None:
                    if current_node.is_empty():
                        removed_child = parent.remove_child(
                            current_node.get_text()[0])
                        assert removed_child is current_node
                        del removed_child
                    # Add parent to queue if it becomes a leaf
                    if replica in self._leaf_of(parent):
                        queue.append(parent)

            # 3. Remove the replica from the replica_char_count map
            self.replica_char_count.pop(replica)

    def get_smallest_replica(self) -> Optional[str]:
        """Get the smallest replica in the tree."""
        with self.tree_lock:
            if not self.replica_char_count:
                return None
            return min(self.replica_char_count.items(), key=lambda x: x[1])[0]

    def get_used_size_per_replica(self) -> Dict[str, int]:
        """Perform a DFS to traverse all nodes and calculate the total size
        used by each replica."""
        with self.tree_lock:
            used_size_per_replica: Dict[str, int] = collections.defaultdict(int)
            stack: List[ProximateTreeNode] = [self.root]
            while stack:
                current_node = stack.pop()
                for replica in current_node.get_all_replicas():
                    used_size_per_replica[replica] += len(
                        current_node.get_text())
                for child in current_node.get_children().values():
                    stack.append(child)
            return used_size_per_replica

    def pretty_print(self) -> None:
        """Print a pretty representation of the tree."""
        with self.tree_lock:
            if not self.root.get_children():
                return

            result = []
            child_items = list(self.root.get_children().items())
            child_count = len(child_items)

            for i, (_, child) in enumerate(child_items):
                is_last = i == child_count - 1
                result.append(child.node_to_string(prefix='', is_last=is_last))
            print('Tree structure:')
            print(''.join(result))
            print(f'replica_char_count: {self.replica_char_count}')

"""Utility functions for resources."""
import itertools
from typing import List, Set, Union


def parse_ports(ports: List[Union[int, str]]) -> Set[int]:
    """Parse a list of ports into a set that containing no duplicates.

    For example, [1-3, 5-7] will be parsed to {1, 2, 3, 5, 6, 7}.
    """
    port_set = set()
    for p in ports:
        if isinstance(p, int):
            port_set.add(p)
        else:
            from_port, to_port = p.split('-')
            port_set.update(range(int(from_port), int(to_port) + 1))
    return port_set


def parse_port_set(port_set: Set[int]) -> List[Union[int, str]]:
    """Parse a set of ports into the skypilot ports format.

    This function will group consecutive ports together into a range,
    and keep the rest as is. For example, {1, 2, 3, 5, 6, 7} will be
    parsed to [1-3, 5-7].
    """
    ports: List[Union[int, str]] = []
    # Group consecutive ports together.
    # This algorithm is based on one observation: consecutive numbers
    # in a sorted list will have the same difference with their indices.
    # For example, in [1, 2, 3, 5, 6, 7], difference between value and index
    # is [1, 1, 1, 2, 2, 2], and the consecutive numbers are [1, 2, 3] and
    # [5, 6, 7].
    for _, group in itertools.groupby(enumerate(sorted(port_set)),
                                      lambda x: x[1] - x[0]):
        port = [g[1] for g in group]
        if len(port) == 1:
            ports.append(port[0])
        else:
            ports.append(f'{port[0]}-{port[-1]}')
    return ports


def simplify_ports(ports: List[Union[int, str]]) -> List[Union[int, str]]:
    """Simplify a list of ports.

    For example, [1, 2, 3, 5-6, 7] will be simplified to [1-3, 5-7].
    """
    return parse_port_set(parse_ports(ports))

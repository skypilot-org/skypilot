"""Tests for sky.utils.hostlist_utils."""
import pytest

from sky.utils import hostlist_utils


@pytest.mark.parametrize(
    'expr,expected',
    [
        ('node01', ['node01']),
        ('node01,node03', ['node01', 'node03']),
        ('node[02-03,06]', ['node02', 'node03', 'node06']),
        ('n[9-11],d[01-02]', ['n9', 'n10', 'n11', 'd01', 'd02']),
        # Zero padding follows the range start.
        ('n[01-3]', ['n01', 'n02', 'n03']),
        ('n[8-11]', ['n8', 'n9', 'n10', 'n11']),
        ('n[08-11]', ['n08', 'n09', 'n10', 'n11']),
        ('n[0-2]', ['n0', 'n1', 'n2']),
        # Several groups expand as a cartesian product, in order.
        ('rack[1-2]n[01-02]', ['rack1n01', 'rack1n02', 'rack2n01', 'rack2n02']),
        ('a[1-2]b[1-2]c', ['a1b1c', 'a1b2c', 'a2b1c', 'a2b2c']),
        ('[1-2]x', ['1x', '2x']),
        # Real-world shapes.
        ('ml-16-node-[001-002]', ['ml-16-node-001', 'ml-16-node-002']),
        ('np-0a4981[80-82,85],b-1',
         ['np-0a498180', 'np-0a498181', 'np-0a498182', 'np-0a498185', 'b-1']),
        ('ip-10-3-93-178', ['ip-10-3-93-178']),
        # Duplicates are dropped, first occurrence wins.
        ('a[1,1,2]', ['a1', 'a2']),
        ('a,a', ['a']),
        ('x[1-2],x[2-3]', ['x1', 'x2', 'x3']),
        # Stray commas / whitespace are tolerated.
        ('a,,b', ['a', 'b']),
        (',a', ['a']),
        ('a,', ['a']),
        (' node[1-2] ', ['node1', 'node2']),
        ('n[ 1-2 ]', ['n1', 'n2']),
        # Empty input.
        ('', []),
        ('   ', []),
        (None, []),
    ],
)
def test_expand_hostlist(expr, expected):
    assert hostlist_utils.expand_hostlist(expr) == expected


@pytest.mark.parametrize(
    'expr,message',
    [
        ('node[1-2', 'Unbalanced brackets'),
        ('node1-2]', 'Unbalanced brackets'),
        ('n[1-2]]', 'Unbalanced brackets'),
        ('[', 'Unbalanced brackets'),
        ('n[[1-2]]', 'Unbalanced brackets'),
        ('n[3-1]', 'start > stop'),
        ('n[]', 'Bad range'),
        ('n[1-2,]', 'Bad range'),
        ('n[a-b]', 'Bad range'),
        ('n[1-3]-[a,b]', 'Bad range'),
        ('n[1-2-3]', 'Bad range'),
    ],
)
def test_expand_hostlist_malformed(expr, message):
    with pytest.raises(hostlist_utils.HostlistError, match=message):
        hostlist_utils.expand_hostlist(expr)


def test_expand_hostlist_is_value_error():
    with pytest.raises(ValueError):
        hostlist_utils.expand_hostlist('node[1-2')


def test_expand_hostlist_size_guard():
    with pytest.raises(hostlist_utils.HostlistError, match='more than'):
        hostlist_utils.expand_hostlist('n[1-999999999]')
    with pytest.raises(hostlist_utils.HostlistError, match='more than'):
        hostlist_utils.expand_hostlist('a[1-400]b[1-400]')
    # Exactly at the limit is fine.
    assert len(
        hostlist_utils.expand_hostlist(
            f'n[1-{hostlist_utils.MAX_HOSTS}]')) == hostlist_utils.MAX_HOSTS
    # Overlapping ranges count distinct hosts, not range lengths.
    assert len(hostlist_utils.expand_hostlist('n[1-60000,1-60001]')) == 60001

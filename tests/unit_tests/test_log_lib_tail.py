"""Unit tests for sky.skylet.log_lib.tail_lines_from_end.

The seek-based tail reader is a hot path: it powers every dashboard
/jobs/logs request and is exercised at multi-GB log sizes by customers.
These tests pin the behavior against the reference deque-based reader
that was used pre-optimization, so any future regression that changes
which lines are returned is caught at unit-test time instead of on a
live customer log.
"""
# pylint: disable=redefined-outer-name
import collections
import os
import random
import tempfile

import pytest

from sky.skylet import log_lib


def _write_lines(path: str, lines: list) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        for line in lines:
            f.write(line)


def _deque_tail(path: str, tail: int, offset: int = 0) -> list:
    with open(path, 'r', encoding='utf-8') as f:
        full = list(collections.deque(f, maxlen=tail + offset))
    if offset >= len(full):
        return []
    if offset > 0:
        full = full[:-offset]
    return full[-tail:]


@pytest.fixture
def log_path():
    tf = tempfile.NamedTemporaryFile('w', delete=False, suffix='.log')
    tf.close()
    yield tf.name
    os.unlink(tf.name)


def test_tail_smaller_than_file(log_path):
    _write_lines(log_path, [f'line {i}\n' for i in range(1000)])
    lines, end_pos = log_lib.tail_lines_from_end(log_path, 200)
    assert len(lines) == 200
    assert lines[0].rstrip() == 'line 800'
    assert lines[-1].rstrip() == 'line 999'
    assert end_pos == os.path.getsize(log_path)


def test_tail_larger_than_file(log_path):
    _write_lines(log_path, [f'line {i}\n' for i in range(50)])
    lines, _ = log_lib.tail_lines_from_end(log_path, 5000)
    assert len(lines) == 50
    assert lines[0].rstrip() == 'line 0'


def test_offset_window(log_path):
    _write_lines(log_path, [f'line {i}\n' for i in range(1000)])
    lines, _ = log_lib.tail_lines_from_end(log_path, 200, offset=300)
    assert len(lines) == 200
    assert lines[0].rstrip() == 'line 500'
    assert lines[-1].rstrip() == 'line 699'


def test_offset_past_file_returns_empty(log_path):
    _write_lines(log_path, [f'line {i}\n' for i in range(100)])
    lines, _ = log_lib.tail_lines_from_end(log_path, 10, offset=1000)
    assert lines == []


def test_no_trailing_newline(log_path):
    content = [f'line {i}\n' for i in range(50)]
    content.append('line 50 (no trailing nl)')
    _write_lines(log_path, content)
    lines, _ = log_lib.tail_lines_from_end(log_path, 5)
    assert len(lines) == 5
    assert lines[-1] == 'line 50 (no trailing nl)'
    assert lines[-2].rstrip() == 'line 49'


def test_lines_span_multiple_blocks(log_path):
    # 200 KB lines force at least 3 backward reads at the 64 KB block size.
    content = [('x' * (200 * 1024)) + '\n' for _ in range(10)]
    _write_lines(log_path, content)
    lines, _ = log_lib.tail_lines_from_end(log_path, 3)
    assert len(lines) == 3
    for line in lines:
        assert line.rstrip().count('x') == 200 * 1024


@pytest.mark.parametrize('tail', [1, 200, 5000, 100_000])
@pytest.mark.parametrize('offset', [0, 1, 200, 5_000])
def test_matches_deque_reference(log_path, tail, offset):
    """Cross-check seek vs deque on a realistic-shaped log."""
    rng = random.Random(42)
    content = [
        f'{i:06d} ' + ('y' * rng.randint(20, 250)) + '\n' for i in range(50_000)
    ]
    _write_lines(log_path, content)
    expected = _deque_tail(log_path, tail, offset)
    actual, _ = log_lib.tail_lines_from_end(log_path, tail, offset)
    assert actual == expected


def test_empty_file(log_path):
    _write_lines(log_path, [])
    lines, end_pos = log_lib.tail_lines_from_end(log_path, 10)
    assert lines == []
    assert end_pos == 0

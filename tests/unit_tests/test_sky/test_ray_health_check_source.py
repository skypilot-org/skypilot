"""The health check must count live nodes, not `ray status` text.

Ray 2.56.1 leaves a node under `Active:` after the GCS has marked it dead
(measured: still listed 174s later), so the old ``(\\d+) node_`` count
over-reported after any node loss and the cluster was set to INIT.
"""
import re
import shlex

from sky.backends import backend_utils
from sky.provision import instance_setup

# Verbatim from a failing CI run: 4 node lines, 2 pods, both healthy.
RAY_STATUS_WITH_DEAD_NODES = """\
======== Autoscaler status: 2026-09-01 09:08:24.709885 ========
Node status
---------------------------------------------------------------
Active:
 1 node_c23344ee35db31deeec37ca824c276e46d2fb1cde61c234dbc2c1016
 1 node_a6751b484dbc8992cb3e7b9bf49c0cbbfe2dbffc98da2ccac5046670
 1 node_e760b5bf1eb34e437e6a210e370e088c7d34f1c4e3438e782a67a268
 1 node_4be03db65cb7c60baac5bf1804bc2b861e493e93c4a0ecac073bb615
Pending:
 (no pending nodes)
Recent failures:
 (no failures)
"""


def _counts(output):
    """What the health check derives from the command's output."""
    match = backend_utils._ALIVE_NODES_PATTERN.search(output)  # pylint: disable=protected-access
    assert match is not None, output
    alive = int(match.group(1))
    return min(alive, 1), max(alive - 1, 0)


def test_the_old_text_count_is_what_broke():
    """Pin the symptom, so the reason for the new source stays legible."""
    stale = backend_utils._LAUNCHED_LOCAL_WORKER_PATTERN.findall(  # pylint: disable=protected-access
        RAY_STATUS_WITH_DEAD_NODES)
    assert len(stale) == 4, 'the captured output should show 4 node lines'


def test_two_live_nodes_among_four_count_as_two():
    """The CI case: 2 pods up, 2 long-dead nodes still listed by `ray status`."""
    head, workers = _counts('SKY_ALIVE_NODES 2 4\n  abc True 10.0.0.1\n')
    assert head + workers == 2


def test_a_single_node_cluster_counts_as_one():
    head, workers = _counts('SKY_ALIVE_NODES 1 3\n')
    assert (head, workers) == (1, 0)


def test_the_command_asks_the_gcs_not_ray_status():
    cmd = instance_setup.RAY_ALIVE_NODE_COUNT_COMMAND
    assert 'ray.nodes()' in cmd and '"Alive"' in cmd
    assert 'ray status' not in cmd, (
        'the count must not come from `ray status` again')
    # It is spliced into a shell command, so it has to survive the shell.
    assert shlex.split(cmd)


def test_the_head_wait_does_not_depend_on_a_ray_message():
    """2.9.3 printed "No cluster status."; 2.56.1 raises instead.

    Keyed on a message, the wait silently became a no-op on 2.56.1.
    """
    cmd = instance_setup.RAY_HEAD_WAIT_INITIALIZED_COMMAND
    assert 'No cluster status.' not in cmd
    assert re.search(r'status\s*>\s*/dev/null 2>&1 && break', cmd), cmd
    # Bounded: a failing `ray status` takes ~35s to return on 2.56.1.
    assert '_sky_deadline' in cmd
    assert shlex.split(cmd)

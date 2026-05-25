"""Unit tests for the prologue invariants of ``_create_managed_job_v1``.

Covers the two block-ship #1 branches ported verbatim from the legacy
``_create_virtual_instance`` path:

1. COMPLETING-drain — before submitting a fresh sbatch, the function
   must wait up to ``_JOB_TERMINATION_TIMEOUT_SECONDS`` for any jobs in
   the COMPLETING state under ``cluster_name_on_cloud`` to drain. If
   they don't, it raises.

2. Existing-job reattach — after drain, the function queries for jobs
   in PENDING/RUNNING; if exactly one exists, it reattaches without
   calling ``submit_job`` again and returns a ``ProvisionRecord`` with
   the v1 runtime metadata shape. Two or more existing jobs trip an
   ``assert``.

Everything past the reattach branch (script staging / submission /
``_wait_for_job_nodes`` after a fresh submit) is out of scope here.
"""
# pylint: disable=protected-access,missing-class-docstring
# pylint: disable=import-outside-toplevel,unused-argument
from unittest import mock

import pytest

from sky.provision import common
from sky.provision.slurm import instance as slurm_instance

# ----------------------------- Fixtures ----------------------------- #


def _make_provider_config():
    """Minimal v1 provider_config the prologue actually reads."""
    return {
        'skypilot_runtime': 'managed_job_v1',
        'ssh': {
            'hostname': 'login.example.com',
            'port': '22',
            'user': 'slurmuser',
            'private_key': '/tmp/key',
            'proxycommand': None,
            'proxyjump': None,
            'identities_only': False,
        },
        'partition': 'gpu',
        'cluster': 'my-slurm',
        'provision_timeout': -1,
        # Fresh-submission branch reads these — the reattach tests don't
        # exercise that code path, but the prologue itself doesn't
        # consume them either.
        'setup': None,
        'run': 'echo hi',
        'envs': {},
        'workdir': None,
        'file_mounts': None,
        'container_image': None,
        'sbatch_options': {},
    }


def _make_provision_config(count: int = 1) -> common.ProvisionConfig:
    return common.ProvisionConfig(
        provider_config=_make_provider_config(),
        authentication_config={},
        docker_config={},
        node_config={},
        count=count,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )


class _FakeClock:
    """Deterministic monotonic clock so the COMPLETING-drain loop's
    ``time.time() - start_time`` bookkeeping advances by a known step
    each ``sleep`` call, without actually sleeping.

    Each ``time()`` call advances ``step`` seconds. ``sleep(s)`` adds
    ``s`` to the clock. We intentionally use a coarse step so the loop
    terminates in a finite number of iterations under tests.
    """

    def __init__(self, step: float = 0.0):
        self.now = 0.0
        self.step = step
        self.sleep_calls: list = []

    def time(self) -> float:
        t = self.now
        self.now += self.step
        return t

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


@pytest.fixture
def patched_module(monkeypatch):
    """Patch the slurm instance module so ``_create_managed_job_v1``'s
    prologue runs deterministically without touching SSH, the network,
    or real time.

    Returns a ``types.SimpleNamespace`` so tests can inspect the mocks
    after invocation.
    """
    import types

    client = mock.MagicMock(name='SlurmClient')
    # Default: ``get_job_nodes`` returns one node — used by the reattach
    # branch when constructing the ProvisionRecord.
    client.get_job_nodes.return_value = (['node001'], ['10.0.0.1'])

    slurm_factory = mock.MagicMock(name='slurm_module')
    slurm_factory.SlurmClient.return_value = client
    monkeypatch.setattr(slurm_instance, 'slurm', slurm_factory)

    # ``_wait_for_job_nodes`` is module-level — stub it so the reattach
    # branch doesn't try to drive a real squeue poll loop.
    wait_mock = mock.MagicMock(name='_wait_for_job_nodes')
    monkeypatch.setattr(slurm_instance, '_wait_for_job_nodes', wait_mock)

    # The reattach branch calls ``rich_utils.force_update_status`` to
    # update the spinner — silence it.
    monkeypatch.setattr(slurm_instance.rich_utils, 'force_update_status',
                        mock.MagicMock())

    # Fresh-submission scaffolding: short-circuit everything between the
    # reattach branch and ``submit_job`` so tests targeting the COMPLETING
    # drain (which fall through to submission) can stop execution
    # deterministically at ``submit_job``.
    runner_mock = mock.MagicMock(name='SSHCommandRunner_instance')
    runner_mock.get_remote_home_dir.return_value = '/home/slurmuser'
    runner_mock.run.return_value = (0, '', '')
    monkeypatch.setattr(slurm_instance.command_runner, 'SSHCommandRunner',
                        mock.MagicMock(return_value=runner_mock))
    monkeypatch.setattr(slurm_instance, '_v1_precondition_cleanup',
                        mock.MagicMock())
    monkeypatch.setattr(slurm_instance.slurm_utils, 'get_partition_info',
                        mock.MagicMock(return_value=mock.MagicMock()))
    monkeypatch.setattr(slurm_instance, '_build_sbatch_directives',
                        mock.MagicMock(return_value=[]))
    monkeypatch.setattr(slurm_instance, '_build_v1_sbatch_script',
                        mock.MagicMock(return_value='#!/bin/bash\n'))
    monkeypatch.setattr(slurm_instance.skypilot_config,
                        'get_effective_region_config', lambda **kwargs: None)

    clock = _FakeClock(step=0.0)
    monkeypatch.setattr(slurm_instance.time, 'sleep', clock.sleep)
    monkeypatch.setattr(slurm_instance.time, 'time', clock.time)

    return types.SimpleNamespace(
        client=client,
        slurm_factory=slurm_factory,
        wait_mock=wait_mock,
        clock=clock,
    )


# --------------------------- COMPLETING-drain --------------------------- #


class TestCompletingDrain:
    """The first prologue block of ``_create_managed_job_v1``
    (``instance.py:2043-2067``)."""

    def test_empty_queue_no_wait(self, patched_module):
        """Empty COMPLETING set at entry → no ``time.sleep`` call, and
        execution falls through to the existing-job reattach query."""
        client = patched_module.client
        # COMPLETING: empty. PENDING/RUNNING: empty too, so we fall
        # through to the fresh-submission path. We stop before that by
        # forcing a controlled failure on submit_job.
        client.query_jobs.side_effect = [
            [],  # completing
            [],  # pending/running
        ]
        # Stop execution before the fresh-submission scaffolding runs.
        client.submit_job.side_effect = RuntimeError('stop-here')

        with pytest.raises(RuntimeError, match='stop-here'):
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())

        # No sleeps at all when the queue is empty up front.
        assert patched_module.clock.sleep_calls == []

    def test_drains_on_second_poll(self, patched_module, monkeypatch):
        """One job in COMPLETING, drains by the second poll → exactly
        one sleep call, and execution proceeds past drain."""
        client = patched_module.client
        # First completing poll returns [1234]; second returns []. Then
        # the existing-job query returns []; we fail submit_job to stop
        # before the fresh-submission scaffolding.
        client.query_jobs.side_effect = [
            ['1234'],  # completing: first poll, one job
            [],  # completing: second poll, drained
            [],  # pending/running: no existing jobs
        ]
        client.submit_job.side_effect = RuntimeError('stop-here')

        # Use a tiny clock step so the loop terminates well within the
        # 60s budget after one drain iteration.
        patched_module.clock.step = 0.0

        with pytest.raises(RuntimeError, match='stop-here'):
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())

        # Exactly one sleep, at the polling interval.
        assert len(patched_module.clock.sleep_calls) == 1
        assert (patched_module.clock.sleep_calls[0] ==
                slurm_instance.POLL_INTERVAL_SECONDS)

    def test_timeout_raises(self, patched_module):
        """A job stuck in COMPLETING past the timeout → ``RuntimeError``
        with the documented message."""
        client = patched_module.client
        # Always return the same completing set.
        client.query_jobs.return_value = ['1234']
        # Each ``time()`` call advances by 31s — after the first sleep
        # we've consumed ~31s; after the second, ~62s, crossing the 60s
        # budget. (The loop checks ``time() - start_time`` *before*
        # sleeping, so we need two sleeps to cross.)
        patched_module.clock.step = 31.0

        with pytest.raises(RuntimeError) as exc_info:
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())

        msg = str(exc_info.value)
        assert 'completing state after' in msg
        assert (str(slurm_instance._JOB_TERMINATION_TIMEOUT_SECONDS) in msg)
        # submit_job must NOT have been called — drain failure aborts
        # before we get there.
        client.submit_job.assert_not_called()

    def test_multiple_jobs_all_drain(self, patched_module):
        """Multiple jobs in COMPLETING — all must drain before the loop
        exits. With both gone on the second poll, we proceed."""
        client = patched_module.client
        client.query_jobs.side_effect = [
            ['1234', '5678'],  # completing: two jobs
            [],  # completing: both drained
            [],  # pending/running: no existing jobs
        ]
        client.submit_job.side_effect = RuntimeError('stop-here')

        with pytest.raises(RuntimeError, match='stop-here'):
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())

        assert len(patched_module.clock.sleep_calls) == 1

    def test_multiple_jobs_one_stuck_raises(self, patched_module):
        """If one of multiple COMPLETING jobs stays stuck past the
        timeout, the function raises — the loop condition is "any
        completing", not "any specific job"."""
        client = patched_module.client
        # First poll: two jobs. Subsequent polls: still one stuck.
        client.query_jobs.side_effect = ([['1234', '5678']] +
                                         [['5678']] * 10  # one stays forever
                                        )
        patched_module.clock.step = 31.0

        with pytest.raises(RuntimeError) as exc_info:
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())
        assert 'completing state after' in str(exc_info.value)
        client.submit_job.assert_not_called()


# ----------------------- Existing-job reattach ----------------------- #


class TestExistingJobReattach:
    """The second prologue block (``instance.py:2069-2135``)."""

    def test_no_existing_falls_through_to_submission(self, patched_module):
        """Empty PENDING/RUNNING set → no reattach, falls through to the
        fresh-submission path (and we stop it there)."""
        client = patched_module.client
        client.query_jobs.side_effect = [
            [],  # completing: empty
            [],  # pending/running: empty
        ]
        # Stop execution before any post-submit work — we just want to
        # confirm submit_job was reached.
        client.submit_job.side_effect = RuntimeError('reached-submit')

        with pytest.raises(RuntimeError, match='reached-submit'):
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())

        client.submit_job.assert_called_once()
        # Reattach branch never invoked → ``_wait_for_job_nodes`` was
        # called only via the fresh path, but we raised before that.
        patched_module.wait_mock.assert_not_called()

    @pytest.mark.parametrize('state_label', ['pending', 'running'])
    def test_one_existing_reattaches(self, patched_module, state_label):
        """Exactly one job in PENDING (or RUNNING) → reattach: no
        ``submit_job`` call, ``ProvisionRecord`` points at the existing
        job_id, ``head_instance_id`` matches the (job_id, node) pair.

        PENDING vs RUNNING is just a squeue filter distinction — they
        funnel into the same branch via ``['pending', 'running']``.
        """
        del state_label  # only documents the semantics
        client = patched_module.client
        existing_job_id = '9999'
        client.query_jobs.side_effect = [
            [],  # completing: empty
            [existing_job_id],  # pending/running: one existing
        ]
        client.get_job_nodes.return_value = (['node007'], ['10.0.0.7'])

        record = slurm_instance._create_managed_job_v1('us-central1',
                                                       'mycluster',
                                                       'mycluster-abcd1234',
                                                       _make_provision_config())

        # Reattach contract: submit_job must NOT have been called.
        client.submit_job.assert_not_called()
        # _wait_for_job_nodes must have been called against the reattached
        # job_id.
        patched_module.wait_mock.assert_called_once()
        assert patched_module.wait_mock.call_args.args[1] == existing_job_id

        # ProvisionRecord shape.
        assert isinstance(record, common.ProvisionRecord)
        assert record.provider_name == 'slurm'
        assert record.region == 'us-central1'
        assert record.zone == 'gpu'  # partition
        assert record.cluster_name == 'mycluster-abcd1234'
        # head_instance_id must encode (job_id, node[0]).
        from sky.provision.slurm import utils as slurm_utils
        assert record.head_instance_id == slurm_utils.instance_id(
            existing_job_id, 'node007')
        assert record.created_instance_ids == [
            slurm_utils.instance_id(existing_job_id, 'node007')
        ]
        assert record.resumed_instance_ids == []

    def test_reattach_runtime_metadata_shape(self, patched_module):
        """Reattach must produce the same v1 runtime metadata shape as a
        fresh provision — this is what tells the OSS controller to skip
        post-provision phases on the reattached cluster."""
        client = patched_module.client
        client.query_jobs.side_effect = [
            [],
            ['4242'],
        ]
        client.get_job_nodes.return_value = (['n1'], ['10.0.0.1'])

        record = slurm_instance._create_managed_job_v1('us-central1',
                                                       'mycluster',
                                                       'mycluster-abcd1234',
                                                       _make_provision_config())

        meta = record.runtime_metadata
        assert isinstance(meta, common.ProvisionRuntimeMetadata)
        assert meta.has_ray is False
        assert meta.has_skylet is False
        assert meta.has_job_queue is False
        assert meta.ssh_available is False
        assert meta.runtime_setup_done is True
        assert meta.workdir_synced is True
        assert meta.file_mounts_synced is True
        assert meta.setup_done is True
        assert meta.run_started is True

    def test_multi_node_reattach_threads_all_nodes(self, patched_module):
        """A multi-node reattached job — all allocated nodes appear in
        ``created_instance_ids``; head_instance_id is the first."""
        client = patched_module.client
        client.query_jobs.side_effect = [
            [],
            ['5151'],
        ]
        client.get_job_nodes.return_value = (
            ['node01', 'node02', 'node03'],
            ['10.0.0.1', '10.0.0.2', '10.0.0.3'],
        )

        record = slurm_instance._create_managed_job_v1(
            'us-central1', 'mycluster', 'mycluster-abcd1234',
            _make_provision_config(count=3))

        from sky.provision.slurm import utils as slurm_utils
        assert record.head_instance_id == slurm_utils.instance_id(
            '5151', 'node01')
        assert record.created_instance_ids == [
            slurm_utils.instance_id('5151', 'node01'),
            slurm_utils.instance_id('5151', 'node02'),
            slurm_utils.instance_id('5151', 'node03'),
        ]
        client.submit_job.assert_not_called()

    def test_two_existing_jobs_assertion_fires(self, patched_module):
        """Two existing jobs trip the
        ``assert len(existing_jobs) == 1`` safety net — this is the
        single-job-per-name invariant. If it ever fires in production,
        something is very wrong upstream."""
        client = patched_module.client
        client.query_jobs.side_effect = [
            [],  # completing: empty
            ['1111', '2222'],  # pending/running: two — VIOLATION
        ]

        with pytest.raises(AssertionError) as exc_info:
            slurm_instance._create_managed_job_v1('us-central1', 'mycluster',
                                                  'mycluster-abcd1234',
                                                  _make_provision_config())

        assert 'Multiple jobs found' in str(exc_info.value)
        assert 'mycluster-abcd1234' in str(exc_info.value)
        # Must not have called submit_job — the assertion fires first.
        client.submit_job.assert_not_called()

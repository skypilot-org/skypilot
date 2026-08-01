"""Unit tests for JobGroup networking script generation and setup."""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from sky.jobs import job_group_networking


class TestWaitForNetworkingScript:
    """The wait script is only injected when in-group networking is
    required (inter_connection enabled), so failure to initialize
    networking must fail the job instead of silently continuing."""

    def test_fails_job_when_networking_not_ready(self):
        script = job_group_networking.generate_wait_for_networking_script(
            'group', ['peer1', 'peer2'])
        assert 'exit 1' in script
        assert 'inter_connection' in script
        # The old silent-fallthrough messaging must be gone.
        assert 'Continuing without full network setup' not in script

    def test_waits_for_all_peer_hostnames(self):
        script = job_group_networking.generate_wait_for_networking_script(
            'group', ['peer1', 'peer2'])
        assert 'peer1-0.group' in script
        assert 'peer2-0.group' in script

    def test_empty_without_peers(self):
        script = job_group_networking.generate_wait_for_networking_script(
            'group', [])
        assert script == ''


class TestPhase3VariableScoping:
    """Regression guard: names used after the Phase 3 networking block in
    ``_run_job_group`` must be bound on both branches of the
    inter_connection gate.

    An e2e run caught ``tasks_handles`` being defined only when networking
    was enabled, so every ``inter_connection: false`` group crashed the
    controller loop with UnboundLocalError once Phase 4 referenced it.
    """

    def test_names_used_after_phase3_are_bound_on_both_branches(self):
        import ast
        import inspect
        import textwrap

        from sky.jobs import controller as controller_lib

        src = textwrap.dedent(
            inspect.getsource(controller_lib.JobController._run_job_group))
        tree = ast.parse(src)

        # Find the `if not self._dag.inter_connection_enabled():` statement
        # guarding Phase 3.
        gate = None
        for node in ast.walk(tree):
            if isinstance(node,
                          ast.If) and 'inter_connection_enabled' in ast.dump(
                              node.test):
                gate = node
                break
        assert gate is not None, 'Phase 3 inter_connection gate not found'

        # Names assigned only inside the gate's branches.
        assigned_in_gate = set()
        for branch in (gate.body, gate.orelse):
            for stmt in branch:
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Name) and isinstance(
                            node.ctx, ast.Store):
                        assigned_in_gate.add(node.id)

        # Names read after the gate.
        gate_end = gate.end_lineno
        read_after = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and
                    isinstance(node.ctx, ast.Load) and node.lineno > gate_end):
                read_after.add(node.id)

        leaked = assigned_in_gate & read_after
        assert not leaked, (
            f'Names bound only inside the Phase 3 inter_connection gate but '
            f'read afterwards: {sorted(leaked)}. Bind them before the gate '
            'so both branches reach Phase 4.')


class TestWaitScriptDiagnostic:
    """The timeout diagnostic must report updater liveness truthfully."""

    def test_diagnostic_uses_pid_file_not_pgrep(self):
        # pgrep -f can match the probing shell's own command line (it
        # contains the pattern), reporting 'yes' for a dead updater --
        # exactly when someone is reading this output to debug.
        script = job_group_networking.generate_wait_for_networking_script(
            'group', ['peer1'])
        assert 'UPDATER_PID_FILE' in script
        assert 'kill -0' in script
        assert 'pgrep' not in script


class TestUpdaterScriptPidFile:
    """The updater records its PID so liveness checks (idempotent start,
    wait-script diagnostic) never rely on self-matching pgrep."""

    def test_updater_script_writes_pid_file(self):
        script = job_group_networking.generate_k8s_dns_updater_script(
            [('svc.cluster.local', 'peer-0.group')], 'group')
        assert ('echo $$ > "/tmp/skypilot-jobgroup-dns-updater-group.pid"'
                in script)


class TestUpdaterStartCommand:
    """The updater start command is idempotent via the PID file: healthy
    survivors keep their running updater on re-pushes, and repeated
    pushes never stack duplicate updater processes."""

    def _make_runner(self, returncode=0):
        runner = MagicMock()
        runner.rsync = MagicMock()
        runner.run = MagicMock(return_value=(returncode, '', ''))
        return runner

    @pytest.mark.asyncio
    async def test_start_is_pid_file_guarded(self):
        runner = self._make_runner()
        ok = await job_group_networking._start_k8s_dns_updater_on_node(
            runner, [('svc.cluster.local', 'peer-0.group')], 'group')
        assert ok is True
        run_cmd = runner.run.call_args[0][0]
        pid_file = '/tmp/skypilot-jobgroup-dns-updater-group.pid'
        marker = '/tmp/skypilot-jobgroup-network-ready-group'
        assert run_cmd.startswith(f'if [ -f {pid_file} ]')
        assert 'kill -0' in run_cmd
        # pgrep -f would match this very command line and always report
        # 'running' (the old start verification was vacuous because of
        # this).
        assert 'pgrep' not in run_cmd
        assert 'nohup' in run_cmd
        # The readiness marker is created on both branches: already-alive
        # and started-and-verified.
        assert run_cmd.count(f'touch {marker}') == 2

    @pytest.mark.asyncio
    async def test_returns_false_when_start_fails(self):
        runner = self._make_runner(returncode=1)
        ok = await job_group_networking._start_k8s_dns_updater_on_node(
            runner, [('svc.cluster.local', 'peer-0.group')], 'group')
        assert ok is False

    @pytest.mark.asyncio
    async def test_returncode_143_treated_as_success(self):
        # kubectl exec closing the connection SIGTERMs the shell; the
        # detached updater keeps running.
        runner = self._make_runner(returncode=143)
        ok = await job_group_networking._start_k8s_dns_updater_on_node(
            runner, [('svc.cluster.local', 'peer-0.group')], 'group')
        assert ok is True


class TestInlinePreludeGuard:
    """The inline task.run prelude must not stack a duplicate updater
    when the task is restarted on the same cluster
    (max_restarts_on_errors re-runs task.run on the same pod)."""

    def test_inline_start_is_pid_file_guarded(self):
        with mock.patch.object(job_group_networking,
                               '_generate_k8s_dns_mappings_from_runtime',
                               return_value=[('svc.cluster.local',
                                              'peer-0.group')]):
            script = (
                job_group_networking.generate_inline_networking_setup_script(
                    'group', [MagicMock()], 1))
        pid_file = '/tmp/skypilot-jobgroup-dns-updater-group.pid'
        assert f'if ! ([ -f {pid_file} ]' in script
        assert 'kill -0' in script
        # The marker is created whether or not a new updater started.
        assert script.strip().endswith(
            'touch /tmp/skypilot-jobgroup-network-ready-group')


class TestSetupNodeWithRetries:
    """Each node retries independently with its own budget; the final
    reason string is what surfaces in the ClusterSetUpError."""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        attempt = AsyncMock(return_value=True)
        reason = await job_group_networking._setup_node_with_retries(
            attempt, 'a-0', 'K8s DNS updater')
        assert reason is None
        assert attempt.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(job_group_networking,
                            '_SETUP_RETRY_INITIAL_BACKOFF_SECONDS', 0.001)
        attempt = AsyncMock(side_effect=[False, True])
        reason = await job_group_networking._setup_node_with_retries(
            attempt, 'a-0', 'K8s DNS updater')
        assert reason is None
        assert attempt.await_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_budget_reports_reason(self, monkeypatch):
        monkeypatch.setattr(job_group_networking,
                            '_SETUP_RETRY_INITIAL_BACKOFF_SECONDS', 0.001)
        attempt = AsyncMock(return_value=False)
        reason = await job_group_networking._setup_node_with_retries(
            attempt, 'a-0', 'K8s DNS updater')
        assert reason == 'K8s DNS updater failed'
        assert (attempt.await_count == job_group_networking._SETUP_MAX_ATTEMPTS)

    @pytest.mark.asyncio
    async def test_timeout_reports_reason(self, monkeypatch):
        monkeypatch.setattr(job_group_networking,
                            '_SETUP_ATTEMPT_TIMEOUT_SECONDS', 0.01)
        monkeypatch.setattr(job_group_networking,
                            '_SETUP_RETRY_INITIAL_BACKOFF_SECONDS', 0.001)

        async def hang():
            await asyncio.sleep(10)
            return True

        reason = await job_group_networking._setup_node_with_retries(
            hang, 'a-0', '/etc/hosts')
        assert reason is not None
        assert 'timed out' in reason

    @pytest.mark.asyncio
    async def test_exception_reports_reason(self, monkeypatch):
        monkeypatch.setattr(job_group_networking,
                            '_SETUP_RETRY_INITIAL_BACKOFF_SECONDS', 0.001)

        async def boom():
            raise RuntimeError('ssh broke')

        reason = await job_group_networking._setup_node_with_retries(
            boom, 'a-0', '/etc/hosts')
        assert reason is not None
        assert 'raised' in reason
        assert 'ssh broke' in reason


class TestInjectEtcHosts:
    """Failure aggregation: per-node failures are reported with their
    task name (so on_recovery can tell own-node from peer failures);
    handles in transition are skipped, broken handles are reported."""

    def _mk_task(self, name):
        task = MagicMock()
        task.name = name
        return task

    def _mk_handle(self, runners):
        handle = MagicMock()
        handle.get_command_runners.return_value = runners
        return handle

    def _patch_common(self, monkeypatch):
        monkeypatch.setattr(job_group_networking, '_is_kubernetes',
                            lambda h: True)
        monkeypatch.setattr(job_group_networking, '_generate_k8s_dns_mappings',
                            lambda *a: [('svc', 'host')])
        monkeypatch.setattr(job_group_networking, '_generate_hosts_entries',
                            lambda *a: '# header')
        monkeypatch.setattr(job_group_networking,
                            '_SETUP_RETRY_INITIAL_BACKOFF_SECONDS', 0.001)

        async def fake_start(runner, mappings, group):
            return runner.setup_ok

        monkeypatch.setattr(job_group_networking,
                            '_start_k8s_dns_updater_on_node', fake_start)

    @pytest.mark.asyncio
    async def test_reports_only_failed_nodes_with_task_name(self, monkeypatch):
        self._patch_common(monkeypatch)
        ok_runner = MagicMock()
        ok_runner.setup_ok = True
        bad_runner = MagicMock()
        bad_runner.setup_ok = False
        failures = await (
            job_group_networking.NetworkConfigurator._inject_etc_hosts(
                'group',
                [(self._mk_task('job-a'), self._mk_handle([ok_runner])),
                 (self._mk_task('job-b'), self._mk_handle([bad_runner]))]))
        assert failures == [('job-b', 'job-b-0', 'K8s DNS updater failed')]

    @pytest.mark.asyncio
    async def test_all_success_returns_empty(self, monkeypatch):
        self._patch_common(monkeypatch)
        runner = MagicMock()
        runner.setup_ok = True
        failures = await (
            job_group_networking.NetworkConfigurator._inject_etc_hosts(
                'group', [(self._mk_task('job-a'), self._mk_handle([runner]))]))
        assert failures == []

    @pytest.mark.asyncio
    async def test_none_handle_skipped_not_failed(self, monkeypatch):
        # A peer mid-recovery has no handle; its own recovery re-runs
        # setup, so this must not count as a failure.
        self._patch_common(monkeypatch)
        failures = await (
            job_group_networking.NetworkConfigurator._inject_etc_hosts(
                'group', [(self._mk_task('job-a'), None)]))
        assert failures == []

    @pytest.mark.asyncio
    async def test_runner_build_error_is_reported(self, monkeypatch):
        self._patch_common(monkeypatch)
        handle = MagicMock()
        handle.get_command_runners.side_effect = RuntimeError('bad handle')
        failures = await (
            job_group_networking.NetworkConfigurator._inject_etc_hosts(
                'group', [(self._mk_task('job-a'), handle)]))
        assert len(failures) == 1
        assert failures[0][0] == 'job-a'
        assert 'command runners' in failures[0][2]

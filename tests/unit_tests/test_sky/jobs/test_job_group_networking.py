"""Unit tests for JobGroup networking script generation."""
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

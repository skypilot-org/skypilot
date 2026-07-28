"""Unit tests for consolidation mode detection and invariants.

Pins the detector behavior and critical invariants that make the
consolidation mode controller startup gate (CHANGE 1) safe.
"""
import ast
import pathlib
from unittest import mock

import pytest

from sky.jobs import utils as managed_job_utils
import sky.jobs.controller as _jobs_controller_module
from sky.skylet import constants
from sky.utils import controller_utils


def _collect_identifiers(tree: ast.AST) -> set:
    """Collect every identifier referenced in an AST, by name or attribute.

    This must look at BOTH ast.Name.id (bare names, e.g. `foo`) AND
    ast.Attribute.attr (attribute accesses, e.g. the `bar` in `foo.bar`).
    Call sites in this codebase use the `module.function(...)` shape (e.g.
    `scheduler.maybe_start_controllers()`), where the function name only
    shows up as an Attribute.attr, never as a Name.id. A collector that only
    looks at Name.id can never detect such calls.
    """
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    return identifiers


@pytest.fixture
def _clear_consolidation_caches():  # pylint: disable=invalid-name
    """Clear consolidation mode detection caches before/after tests."""
    # Clear before test
    if hasattr(managed_job_utils.is_consolidation_mode, 'cache_clear'):
        managed_job_utils.is_consolidation_mode.cache_clear()
    # pylint: disable=protected-access
    if hasattr(controller_utils._effective_jobs_consolidation_with_warnings,
               'cache_clear'):
        controller_utils._effective_jobs_consolidation_with_warnings.cache_clear(
        )

    yield

    # Clear after test
    if hasattr(managed_job_utils.is_consolidation_mode, 'cache_clear'):
        managed_job_utils.is_consolidation_mode.cache_clear()
    # pylint: disable=protected-access
    if hasattr(controller_utils._effective_jobs_consolidation_with_warnings,
               'cache_clear'):
        controller_utils._effective_jobs_consolidation_with_warnings.cache_clear(
        )


class TestConsolidationModeDetector:
    """Tests for consolidation mode detection."""

    @pytest.mark.parametrize(
        'override_set,signal_present,expected',
        [
            (False, False,
             False),  # controller-VM scheduler subprocess, non-consolidation
            (False, True,
             True),  # API-server scheduler subprocess, consolidation
            (True, False,
             True),  # remote controller process (OVERRIDE forces True)
            (True, True, True),  # all env vars set
        ])
    def test_detector_matrix(self, override_set: bool, signal_present: bool,
                             expected: bool, tmp_path, monkeypatch,
                             _clear_consolidation_caches):  # pylint: disable=invalid-name
        """Test consolidation mode detection across all configurations."""
        # Set up environment
        if override_set:
            monkeypatch.setenv(constants.OVERRIDE_CONSOLIDATION_MODE, 'true')
        else:
            monkeypatch.delenv(constants.OVERRIDE_CONSOLIDATION_MODE,
                               raising=False)

        # Set up signal file
        signal_file = tmp_path / '.jobs_controller_consolidation_reloaded_signal'
        if signal_present:
            signal_file.touch()
        monkeypatch.setenv('HOME', str(tmp_path))

        # Also ensure IS_SKYPILOT_SERVER is not set for this test
        monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, raising=False)

        # Patch the constant to point to our temp signal file
        monkeypatch.setattr(
            'sky.jobs.constants.JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE',
            str(signal_file))

        # Test the detector
        result = managed_job_utils.is_consolidation_mode()
        assert result == expected, (
            f'override_set={override_set}, signal_present={signal_present}, '
            f'expected={expected}, got={result}')

    def test_override_consolidation_mode_not_in_controller_envs(
            self, _clear_consolidation_caches):  # pylint: disable=invalid-name
        """OVERRIDE_CONSOLIDATION_MODE must NOT be in controller_envs.

        If this var entered controller_envs, jobs-controller VM startup would
        see is_consolidation_mode()==True and gate would break all startup.

        NOTE: controller_only_vars_to_fill() returns an outer dict (with keys
        like 'sky_activate_python_env', 'controller_envs', etc). The env vars
        that actually get exported to the controller process live in the
        NESTED `controller_envs` dict, so the assertion must target that, not
        the outer dict (see sky/utils/controller_utils.py:679).
        """
        with mock.patch.object(
            controller_utils,
            '_get_cloud_dependencies_installation_commands',
            return_value=''), \
            mock.patch.object(
                controller_utils.plugin_utils,
                'get_plugin_mounts_and_commands',
                return_value=(None, None)):
            result = controller_utils.controller_only_vars_to_fill(
                controller_utils.Controllers.JOBS_CONTROLLER)

        controller_envs = result['controller_envs']
        assert controller_envs, 'controller_envs must not be empty'
        # Positive control: confirms we are looking at the right dict (this
        # key is set unconditionally in controller_only_vars_to_fill's
        # env_vars.update({...})), which is what makes the negative
        # assertion below meaningful rather than vacuous.
        assert constants.IS_SKYPILOT_SERVE_CONTROLLER in controller_envs, (
            f'{constants.IS_SKYPILOT_SERVE_CONTROLLER} should be a key of '
            'controller_envs; if not, this test is asserting against the '
            'wrong dict')

        # The override should NOT be in the nested controller_envs dict.
        assert constants.OVERRIDE_CONSOLIDATION_MODE not in controller_envs, (
            f'{constants.OVERRIDE_CONSOLIDATION_MODE} must not be in '
            'controller_envs to avoid breaking the jobs-controller VM path')

    def test_controller_module_never_reaches_from_scheduler_path(self):
        """Tripwire: controller.py has no DIRECT reference to
        maybe_start_controllers or submit_jobs.

        This proves only that -- a direct reference is absent from
        controller.py's source -- not that the from_scheduler=True path is
        unreachable from the controller process by some indirect route.
        controller.py does import managed_job_utils, and managed_job_utils
        itself calls scheduler.maybe_start_controllers(), but always with
        from_scheduler=False (see ha_recovery_for_consolidation_mode and
        similar), so that indirect path is fine.

        The invariant this is guarding: is_consolidation_mode() returns True
        unconditionally inside the controller process (start_controller()
        exports OVERRIDE_CONSOLIDATION_MODE there), so if controller.py ever
        gained a direct call to maybe_start_controllers or submit_jobs, the
        from_scheduler=True gate in maybe_start_controllers() -- meant only
        to protect the in-request submission path (submit_jobs() ->
        maybe_start_controllers(from_scheduler=True)), reached from this
        module's __main__ as spawned by
        sky/templates/jobs-controller.yaml.j2, never from the controller
        process itself -- would become reachable from a context where
        is_consolidation_mode() is forced True, defeating the gate
        documented in test_consolidation_mode_gate.py. This test is a cheap
        tripwire for that direct-call case, not a proof that the indirect
        route is safe; that's established by reading managed_job_utils
        (and pinned by test_daemon_event_path_starts_controller_uncgated in
        test_consolidation_mode_gate.py, which asserts the daemon path
        calls with from_scheduler falsy).
        """
        # Self-check: prove the detector actually finds the call shape used
        # in this codebase (`module.function(...)`, an Attribute access, not
        # a bare Name) before trusting it against the real file. This makes
        # it impossible for a future refactor of _collect_identifiers to
        # silently re-vacuum this test.
        probe = ast.parse('scheduler.maybe_start_controllers()')
        assert 'maybe_start_controllers' in _collect_identifiers(probe), (
            '_collect_identifiers must detect attribute-style calls like '
            'scheduler.maybe_start_controllers(); if this fails the '
            'reachability check below is vacuous')

        # Resolve the path from the imported module rather than hardcoding
        # an absolute path, so this works in CI (and anywhere else the repo
        # is checked out) and not just on one machine.
        controller_path = pathlib.Path(_jobs_controller_module.__file__)
        source_code = controller_path.read_text(encoding='utf-8')

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            pytest.skip(f'Could not parse controller.py: {e}')

        identifiers = _collect_identifiers(tree)

        # These two functions must not appear anywhere in controller.py,
        # whether as bare names or as `module.function` attribute accesses.
        forbidden = {'maybe_start_controllers', 'submit_jobs'}
        found = forbidden & identifiers

        assert not found, (
            f'controller.py must not reference {found}. These are in-request '
            'submission paths that would split-brain the pool in consolidation mode'
        )

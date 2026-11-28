"""Tests for the controller-process-tree environment variable.

The variable marks every jobs and serve controller process and everything
those processes start. It was renamed, so for one release both the new and the
legacy name are set, and either one is accepted when reading. These tests pin
both halves, because processes from different versions must agree during an
upgrade.
"""
import os
import subprocess
from unittest import mock

import pytest

from sky.jobs import scheduler
from sky.serve import controller as serve_controller
from sky.serve import serve_utils
from sky.skylet import constants


@pytest.fixture(name='no_controller_env')
def fixture_no_controller_env(monkeypatch):
    for name in constants.ENV_VARS_IN_CONTROLLER_PROCESS_TREE:
        monkeypatch.delenv(name, raising=False)


def test_names_are_the_new_and_the_legacy_one():
    assert constants.ENV_VARS_IN_CONTROLLER_PROCESS_TREE == (
        constants.ENV_VAR_IN_CONTROLLER_PROCESS_TREE,
        constants.LEGACY_ENV_VAR_IN_CONTROLLER_PROCESS_TREE,
    )
    assert (constants.LEGACY_ENV_VAR_IN_CONTROLLER_PROCESS_TREE ==
            'IS_SKYPILOT_JOB_CONTROLLER')
    # Clients do not forward SKYPILOT_SERVER_ vars with requests, and do not
    # forward the legacy name because it lacks the SKYPILOT_ prefix.
    assert constants.ENV_VAR_IN_CONTROLLER_PROCESS_TREE.startswith(
        constants.SKYPILOT_SERVER_ENV_VAR_PREFIX)
    assert not constants.LEGACY_ENV_VAR_IN_CONTROLLER_PROCESS_TREE.startswith(
        constants.SKYPILOT_ENV_VAR_PREFIX)


def test_not_in_controller_process_tree_without_either_name(no_controller_env):
    del no_controller_env
    assert constants.in_controller_process_tree() is False


@pytest.mark.parametrize('name', constants.ENV_VARS_IN_CONTROLLER_PROCESS_TREE)
@pytest.mark.parametrize('value', ['true', '1', ''])
def test_either_name_alone_marks_the_process_tree(monkeypatch,
                                                  no_controller_env, name,
                                                  value):
    """Presence counts, whatever the value, as before the rename."""
    del no_controller_env
    monkeypatch.setenv(name, value)
    assert constants.in_controller_process_tree() is True


def test_shell_exports_set_both_names(no_controller_env):
    del no_controller_env
    out = subprocess.run(
        ['bash', '-c', f'{constants.CONTROLLER_PROCESS_TREE_EXPORTS}env'],
        capture_output=True,
        text=True,
        check=True,
        env={
            'PATH': os.environ.get('PATH', '/usr/bin:/bin')
        }).stdout
    lines = out.splitlines()
    for name in constants.ENV_VARS_IN_CONTROLLER_PROCESS_TREE:
        assert f'{name}=true' in lines, name


def test_jobs_controller_command_exports_both_names(no_controller_env):
    """start_controller() bakes the exports into the command and leaves its
    own process environment alone (the leak #9942 fixed)."""
    del no_controller_env
    captured = {}

    def fake_launch(cmd, log_output=None, **kwargs):
        del log_output, kwargs
        captured['cmd'] = cmd
        return 12345

    with mock.patch.object(scheduler.subprocess_utils,
                           'launch_new_process_tree',
                           side_effect=fake_launch), \
            mock.patch.object(scheduler.psutil, 'Process') as process, \
            mock.patch.object(scheduler, '_append_controller_pid_record'):
        process.return_value.create_time.return_value = 1.0
        scheduler.start_controller()

    assert captured['cmd'].startswith(constants.CONTROLLER_PROCESS_TREE_EXPORTS)
    assert constants.in_controller_process_tree() is False


def test_serve_controller_sets_both_names(no_controller_env):
    del no_controller_env
    with mock.patch.object(serve_controller, 'SkyServeController'), \
            mock.patch.object(serve_controller.context_utils,
                              'hijack_sys_attrs'):
        serve_controller.run_controller('svc', mock.Mock(), 1, '127.0.0.1', 1)
    for name in constants.ENV_VARS_IN_CONTROLLER_PROCESS_TREE:
        assert os.environ.get(name) == 'true', name
        # The fixture restores only what it deleted; clean up what we set.
        del os.environ[name]


@pytest.mark.parametrize('consolidation', [True, False])
def test_serve_codegen_exports_both_names_only_in_consolidation(consolidation):
    with mock.patch.object(serve_utils,
                           'is_consolidation_mode',
                           return_value=consolidation):
        cmd = serve_utils.ServeCodeGen.wait_service_registration(
            service_name='svc', job_id=1, pool=False)
    assert cmd.startswith(
        constants.CONTROLLER_PROCESS_TREE_EXPORTS) is consolidation

"""Tests for the default queue admission wait of controller launches."""
import contextlib
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from sky import execution
from sky import skypilot_config
from sky import task as task_lib
from sky.utils import config_utils

_KEYS = ('kubernetes', 'kueue', 'admission_timeout')


def _task(job: Optional[Dict[str, Any]] = None) -> task_lib.Task:
    return task_lib.Task(run='echo hi', job=job)


_TIMEOUT_TASK = _task({'wait_for_scheduling_timeout': '2d'})


def _install(monkeypatch, config):
    monkeypatch.setattr(execution.skypilot_config, 'to_dict',
                        lambda: config_utils.Config(config))
    replaced = []

    @contextlib.contextmanager
    def fake_replace(new_config):
        replaced.append(new_config)
        yield

    monkeypatch.setattr(execution.skypilot_config,
                        'replace_skypilot_config_in_process', fake_replace)
    return replaced


class TestQueueAdmissionWait:

    @pytest.mark.parametrize('task,by_jobs,by_serve', [
        (_task(), False, False),
        (_TIMEOUT_TASK, False, False),
        (_task(), True, False),
        (_task({}), True, False),
        (_task({'wait_for_scheduling_timeout': None}), True, False),
    ])
    def test_launch_untouched(self, monkeypatch, task, by_jobs, by_serve):
        replaced = _install(monkeypatch, {})
        with execution._queue_admission_wait(task, by_jobs, by_serve):
            pass
        assert not replaced

    @pytest.mark.parametrize('task,by_jobs,by_serve', [
        (_task(), False, True),
        (_TIMEOUT_TASK, True, False),
    ])
    def test_defaults_to_indefinite_wait(self, monkeypatch, task, by_jobs,
                                         by_serve):
        replaced = _install(
            monkeypatch,
            {'kubernetes': {
                'kueue': {
                    'local_queue_name': 'team-a'
                }
            }})
        with execution._queue_admission_wait(task, by_jobs, by_serve):
            pass
        assert len(replaced) == 1
        assert replaced[0].get_nested(_KEYS, None) == -1
        # Sibling keys are preserved.
        assert replaced[0].get_nested(
            ('kubernetes', 'kueue', 'local_queue_name'), None) == 'team-a'

    @pytest.mark.parametrize('task,by_jobs,by_serve', [
        (_task(), False, True),
        (_TIMEOUT_TASK, True, False),
    ])
    def test_without_kubernetes_config(self, monkeypatch, task, by_jobs,
                                       by_serve):
        replaced = _install(monkeypatch, {})
        with execution._queue_admission_wait(task, by_jobs, by_serve):
            pass
        assert len(replaced) == 1
        assert replaced[0].get_nested(_KEYS, None) == -1

    @pytest.mark.parametrize('task,by_jobs,by_serve', [
        (_task(), False, True),
        (_TIMEOUT_TASK, True, False),
    ])
    def test_explicit_admission_timeout_wins(self, monkeypatch, task, by_jobs,
                                             by_serve):
        replaced = _install(
            monkeypatch, {'kubernetes': {
                'kueue': {
                    'admission_timeout': 3600
                }
            }})
        with execution._queue_admission_wait(task, by_jobs, by_serve):
            pass
        assert not replaced


# Each scope's explicit admission_timeout, with the real resolver: the
# default goes in below all of them.
_GLOBAL = {'kubernetes': {'kueue': {'admission_timeout': 3600}}}
_WORKSPACE = {
    'workspaces': {
        'ws': {
            'kubernetes': {
                'kueue': {
                    'admission_timeout': 3600
                }
            }
        }
    }
}
_CONTEXT = {
    'kubernetes': {
        'context_configs': {
            'ctx': {
                'kueue': {
                    'admission_timeout': 3600
                }
            }
        }
    }
}
_TASK_OVERRIDE = {'kubernetes': {'kueue': {'admission_timeout': 3600}}}


@pytest.fixture
def loaded_config():
    original = skypilot_config._get_loaded_config()

    def _load(config: Dict[str, Any]) -> None:
        skypilot_config._set_loaded_config(config_utils.Config(config))

    yield _load
    skypilot_config._set_loaded_config(original)


def _effective(override_configs: Optional[Dict[str, Any]] = None):
    return skypilot_config.get_effective_queue_admission_timeout(
        'kubernetes',
        region='ctx',
        workspace='ws',
        override_configs=override_configs)


@pytest.mark.parametrize('config,override_configs', [
    (_GLOBAL, None),
    (_WORKSPACE, None),
    (_CONTEXT, None),
    ({}, _TASK_OVERRIDE),
])
def test_wait_for_scheduling_timeout_keeps_explicit_scope(
        loaded_config, config, override_configs):
    loaded_config(config)
    with execution._queue_admission_wait(_TIMEOUT_TASK, True, False):
        assert _effective(override_configs) == 3600


def test_wait_for_scheduling_timeout_default_resolves_to_indefinite(
        loaded_config):
    loaded_config({})
    with execution._queue_admission_wait(_TIMEOUT_TASK, True, False):
        assert _effective() == -1
    # Restored after the launch.
    assert _effective() is None


def _effective_during_provision(task: task_lib.Task, *, by_jobs: bool,
                                by_serve: bool) -> List[Optional[int]]:
    """Runs _execute_dag's PROVISION stage and reads the effective
    admission_timeout from inside backend.provision()."""
    seen: List[Optional[int]] = []

    def _provision(*args, **kwargs):
        del args, kwargs
        seen.append(_effective())
        return None, False

    backend = mock.MagicMock()
    backend.provision.side_effect = _provision
    dag = mock.MagicMock()
    dag.__len__.return_value = 1
    dag.tasks = [task]
    execution._execute_dag(dag,
                           dryrun=True,
                           stream_logs=False,
                           handle=None,
                           backend=backend,
                           retry_until_up=False,
                           optimize_target=mock.MagicMock(),
                           stages=[execution.Stage.PROVISION],
                           cluster_name=None,
                           detach_setup=False,
                           no_setup=False,
                           clone_disk_from=None,
                           skip_unnecessary_provisioning=False,
                           resize=False,
                           _quiet_optimizer=True,
                           _is_launched_by_jobs_controller=by_jobs,
                           _is_launched_by_sky_serve_controller=by_serve,
                           _extra_launch_context={})
    return seen


@pytest.mark.parametrize('task,by_jobs,by_serve,expected', [
    (_TIMEOUT_TASK, True, False, -1),
    (_task(), False, True, -1),
    (_task(), True, False, None),
    (_TIMEOUT_TASK, False, False, None),
])
def test_effective_admission_timeout_during_provision(loaded_config, task,
                                                      by_jobs, by_serve,
                                                      expected):
    loaded_config({})
    assert _effective_during_provision(task, by_jobs=by_jobs,
                                       by_serve=by_serve) == [expected]
    assert _effective() is None

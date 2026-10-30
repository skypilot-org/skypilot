"""Tests for the autostop activity check (``autostop.activity_check``)."""
import shlex
import subprocess
import time
from unittest import mock

import pytest

from sky import resources as resources_lib
from sky.schemas.generated import autostopv1_pb2
from sky.skylet import autostop_lib
from sky.skylet import configs
from sky.skylet import events
from sky.skylet import runtime_utils
from sky.skylet import services
from sky.utils import common_utils
from sky.utils import schemas


@pytest.fixture
def skylet_db(tmp_path, monkeypatch):
    monkeypatch.setattr(configs, '_DB_PATH', None)
    monkeypatch.setattr(runtime_utils, 'get_runtime_dir_path',
                        lambda relpath: str(tmp_path / relpath.lstrip('/')))


class TestActivityCheckStorage:

    def test_round_trip_and_clear(self, skylet_db):
        assert autostop_lib.get_activity_check() is None
        autostop_lib.set_activity_check({'run': 'exit 0', 'timeout': 5})
        assert autostop_lib.get_activity_check() == {
            'run': 'exit 0',
            'timeout': 5
        }
        autostop_lib.set_activity_check(None)
        assert autostop_lib.get_activity_check() is None

    def test_set_autostop_keeps_stored_check(self, skylet_db):
        # `sky autostop -i N` after launch must not drop the check.
        autostop_lib.set_activity_check({'run': 'exit 0', 'timeout': 5})
        autostop_lib.set_autostop(10,
                                  'cloudvmray',
                                  autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
                                  down=True)
        assert autostop_lib.get_activity_check() == {
            'run': 'exit 0',
            'timeout': 5
        }


class TestActivityCheckReportsActive:

    @pytest.mark.parametrize('run,expected', [
        ('exit 0', True),
        ('exit 1', False),
        ('exit 2', True),
        ('kill -9 $$', True),
    ])
    def test_exit_code_contract(self, skylet_db, run, expected):
        autostop_lib.set_activity_check({'run': run, 'timeout': 5})
        assert autostop_lib.activity_check_reports_active() is expected

    def test_timeout_counts_as_active(self, skylet_db):
        autostop_lib.set_activity_check({'run': 'sleep 30', 'timeout': 1})
        start = time.monotonic()
        assert autostop_lib.activity_check_reports_active() is True
        assert time.monotonic() - start < 10

    def test_timeout_kills_the_whole_process_tree(self, skylet_db, tmp_path):
        marker = tmp_path / 'orphan'
        autostop_lib.set_activity_check({
            'run': f'(sleep 3; touch {marker}) & sleep 30',
            'timeout': 1
        })
        assert autostop_lib.activity_check_reports_active() is True
        time.sleep(4)
        assert not marker.exists()

    def test_backgrounded_child_does_not_hide_idle(self, skylet_db):
        autostop_lib.set_activity_check({
            'run': 'sleep 5 & exit 1',
            'timeout': 2
        })
        assert autostop_lib.activity_check_reports_active() is False

    def test_missing_timeout_uses_default(self, skylet_db):
        autostop_lib.set_activity_check({'run': 'exit 1'})
        assert autostop_lib.activity_check_reports_active() is False

    def test_no_check_is_idle(self, skylet_db):
        assert autostop_lib.activity_check_reports_active() is False


class TestStopEventUsesActivityCheck:

    def _tick(self, wait_for, check_active):
        config = autostop_lib.AutostopConfig(1, 0, 'cloudvmray', wait_for)
        with mock.patch.object(autostop_lib, 'get_autostop_config',
                               return_value=config), \
             mock.patch.object(events.psutil, 'boot_time', return_value=0), \
             mock.patch.object(events.job_lib, 'is_cluster_idle',
                               return_value=True), \
             mock.patch.object(events.managed_job_state,
                               'get_num_alive_jobs', return_value=0), \
             mock.patch.object(autostop_lib, 'has_active_ssh_sessions',
                               return_value=False), \
             mock.patch.object(autostop_lib,
                               'activity_check_reports_active',
                               return_value=check_active) as check, \
             mock.patch.object(autostop_lib, 'get_last_active_time',
                               return_value=time.time() - 3600), \
             mock.patch.object(autostop_lib,
                               'set_last_active_time_to_now') as reset, \
             mock.patch.object(events.StopEvent, '_stop_cluster') as stop:
            events.StopEvent.__new__(events.StopEvent)._run()
        return check, reset, stop

    def test_active_check_resets_timer(self):
        _, reset, stop = self._tick(autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
                                    True)
        reset.assert_called_once()
        stop.assert_not_called()

    def test_idle_check_lets_cluster_stop(self):
        _, reset, stop = self._tick(autostop_lib.AutostopWaitFor.JOBS, False)
        reset.assert_not_called()
        stop.assert_called_once()

    def test_wait_for_none_skips_check(self):
        check, _, stop = self._tick(autostop_lib.AutostopWaitFor.NONE, True)
        check.assert_not_called()
        stop.assert_called_once()


class TestSetAutostopRpc:

    def _call(self, request):
        context = mock.Mock()
        services.AutostopServiceImpl().SetAutostop(request, context)
        context.abort.assert_not_called()

    def _request(self, **kwargs):
        return autostopv1_pb2.SetAutostopRequest(
            idle_minutes=10,
            backend='cloudvmray',
            wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS_AND_SSH,
            **kwargs)

    def test_sets_clears_and_leaves_alone(self, skylet_db):
        self._call(
            self._request(activity_check=autostopv1_pb2.ActivityCheck(
                run='exit 0', timeout=7)))
        assert autostop_lib.get_activity_check() == {
            'run': 'exit 0',
            'timeout': 7
        }
        self._call(self._request())
        assert autostop_lib.get_activity_check() == {
            'run': 'exit 0',
            'timeout': 7
        }
        self._call(self._request(clear_activity_check=True))
        assert autostop_lib.get_activity_check() is None


class TestCodegen:

    def _render(self, **kwargs):
        return ' '.join(
            shlex.split(
                autostop_lib.AutostopCodeGen.set_autostop(
                    10, 'cloudvmray', autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
                    **kwargs)))

    def test_emits_set_activity_check_only_when_given(self):
        assert ("autostop_lib.set_activity_check({'run': 'exit 0', "
                "'timeout': 7})") in self._render(activity_check={
                    'run': 'exit 0',
                    'timeout': 7
                })
        assert 'autostop_lib.set_activity_check(None)' in self._render(
            activity_check={})
        assert 'set_activity_check' not in self._render()


class TestResourcesYaml:

    def test_round_trip(self):
        config = {
            'idle_minutes': 30,
            'down': True,
            'activity_check': {
                'run': 'exit 0',
                'timeout': 7
            },
        }
        r, = resources_lib.Resources.from_yaml_config({'autostop': config})
        assert r.autostop_config.activity_check == {
            'run': 'exit 0',
            'timeout': 7
        }
        assert r.to_yaml_config()['autostop']['activity_check'] == {
            'run': 'exit 0',
            'timeout': 7
        }

    def test_timeout_defaults(self):
        r, = resources_lib.Resources.from_yaml_config(
            {'autostop': {
                'activity_check': {
                    'run': 'exit 0'
                }
            }})
        assert r.autostop_config.activity_check == {
            'run': 'exit 0',
            'timeout': autostop_lib.DEFAULT_ACTIVITY_CHECK_TIMEOUT_SECONDS
        }

    def test_cli_override_leaves_stored_check_alone(self):
        # `sky launch -i N` on a YAML without `autostop` must not clear it.
        r = resources_lib.Resources()
        r.override_autostop_config(idle_minutes=30)
        assert r.autostop_config.activity_check is None

    def test_yaml_autostop_without_check_clears_it(self):
        r, = resources_lib.Resources.from_yaml_config(
            {'autostop': {
                'idle_minutes': 30
            }})
        assert r.autostop_config.activity_check == {}
        assert 'activity_check' not in r.to_yaml_config()['autostop']

    def test_schema_rejects_timeout_above_max(self):
        with pytest.raises(ValueError):
            common_utils.validate_schema(
                {
                    'autostop': {
                        'activity_check': {
                            'run': 'exit 0',
                            'timeout': 61
                        }
                    }
                }, schemas.get_resources_schema(), 'Invalid resources: ')

    def test_schema_rejects_missing_run(self):
        with pytest.raises(ValueError):
            common_utils.validate_schema(
                {'autostop': {
                    'activity_check': {
                        'timeout': 5
                    }
                }}, schemas.get_resources_schema(), 'Invalid resources: ')

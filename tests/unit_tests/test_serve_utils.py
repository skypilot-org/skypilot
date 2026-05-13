import pathlib
import tempfile
from unittest import mock

import pytest
import requests.exceptions as requests_exceptions

from sky import clouds
from sky.resources import Resources
from sky.serve import serve_utils

# String path for mock.patch — can't use the constant directly because
# mock.patch needs the dotted path to the attribute being patched.
_SIGNAL_FILE_CONST = (
    'sky.jobs.constants.JOBS_CONSOLIDATION_RELOADED_SIGNAL_FILE')


def test_task_fits():
    # Test exact fit.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test less CPUs than free.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=2, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test more CPUs than free.
    task_resources = Resources(cpus=2, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False

    # Test less  memory than free.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=2, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test more memory than free.
    task_resources = Resources(cpus=1, memory=2, cloud=clouds.AWS())
    free_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False

    # Test GPU exact fit.
    task_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    free_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test GPUs less than free.
    task_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    free_resources = Resources(accelerators='A10:2', cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is True

    # Test GPUs more than free.
    task_resources = Resources(accelerators='A10:2', cloud=clouds.AWS())
    free_resources = Resources(accelerators='A10:1', cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False

    # Test resources exhausted.
    task_resources = Resources(cpus=1, memory=1, cloud=clouds.AWS())
    free_resources = Resources(cpus=None, memory=None, cloud=clouds.AWS())
    assert serve_utils._task_fits(task_resources, free_resources) is False


def test_serve_preemption_skips_autostopping():
    """Verify serve preemption logic treats AUTOSTOPPING like UP (not preempted)."""
    from sky.utils import status_lib

    # AUTOSTOPPING should be treated as UP-like (not preempted)
    # is_cluster_up() should return True for AUTOSTOPPING
    up_status = status_lib.ClusterStatus.UP
    autostopping_status = status_lib.ClusterStatus.AUTOSTOPPING
    stopped_status = status_lib.ClusterStatus.STOPPED

    # AUTOSTOPPING should be in the same category as UP for preemption purposes
    not_preempted_statuses = {
        up_status,
        autostopping_status,
    }

    assert up_status in not_preempted_statuses
    assert autostopping_status in not_preempted_statuses
    assert stopped_status not in not_preempted_statuses


class TestIsConsolidationMode:
    """Tests for serve_utils.is_consolidation_mode(pool=...).

    Pool consolidation shares a cluster with managed jobs and must track the
    jobs signal file, not the `jobs.controller.consolidation_mode` config key.
    Serve consolidation (pool=False) is independent and remains config-driven.
    """

    def setup_method(self):
        serve_utils.is_consolidation_mode.cache_clear()

    @pytest.mark.parametrize('helper_result', [True, False])
    def test_pool_delegates_to_controller_utils_helper(self, helper_result,
                                                       monkeypatch):
        """pool=True routes through controller_utils.is_jobs_consolidation_mode
        with the pool extra validator, so the two readers share one source."""
        monkeypatch.delenv('IS_SKYPILOT_SERVER', raising=False)
        monkeypatch.delenv('IS_SKYPILOT_JOB_CONTROLLER', raising=False)
        with mock.patch('sky.utils.controller_utils.is_jobs_consolidation_mode',
                        return_value=helper_result) as mock_helper:
            assert serve_utils.is_consolidation_mode(pool=True) is helper_result
            mock_helper.assert_called_once_with(
                extra_validator=serve_utils._pool_consolidation_extra_validator)

    @pytest.mark.parametrize('arg,should_validate', [
        (False, True),
        (True, False),
    ])
    def test_pool_extra_validator_runs_pool_validator_only_when_off(
            self, arg, should_validate):
        """The extra validator supplied to the helper fires the pool-specific
        validator only when consolidation is off. The consolidated case is
        already covered by the jobs validator inside the helper."""
        validate_path = ('sky.serve.serve_utils.'
                         '_validate_consolidation_mode_config')
        with mock.patch(validate_path) as mock_validate:
            serve_utils._pool_consolidation_extra_validator(arg)
            if should_validate:
                mock_validate.assert_called_once_with(arg, pool=True)
            else:
                mock_validate.assert_not_called()

    @pytest.mark.parametrize('config_value,expected', [(True, True),
                                                       (False, False)])
    def test_serve_reads_config_only(self, config_value, expected, monkeypatch):
        """pool=False: reads serve config key; signal file must not affect."""
        monkeypatch.delenv('IS_SKYPILOT_JOB_CONTROLLER', raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            signal_file.touch()  # signal file present should not matter
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)), \
                    mock.patch('sky.serve.serve_utils.skypilot_config'
                              ) as mock_config:
                mock_config.get_nested.return_value = config_value
                assert serve_utils.is_consolidation_mode(pool=False) is expected
                mock_config.get_nested.assert_called_once_with(
                    ('serve', 'controller', 'consolidation_mode'),
                    default_value=False)

    @mock.patch.dict('os.environ', {'IS_SKYPILOT_JOB_CONTROLLER': '1'},
                     clear=False)
    def test_override_env_forces_true_for_serve(self):
        """OVERRIDE_CONSOLIDATION_MODE forces True in the serve (pool=False)
        branch. Pool case goes through the helper which has its own OVERRIDE
        short-circuit tested in controller_utils."""
        with mock.patch('sky.serve.serve_utils.skypilot_config'):
            assert serve_utils.is_consolidation_mode(pool=False) is True


# ---------------------------------------------------------------------------
# Tests for HA leader-aware controller URL routing
# ---------------------------------------------------------------------------
# pylint: disable=protected-access


class TestGetControllerUrl:
    """`_get_controller_url` should:
    - fall back to localhost when no controller_ip is recorded (
      pre-migration), so existing deployments are unaffected;
    - fall back to localhost when controller_ip equals our own POD_IP, so the
      pod that owns the controller doesn't pay for an extra cross-pod hop;
    - return http://<controller_ip>:<port> only when running on a different
      pod than the one hosting the controller.
    """

    def _patch_record(self, controller_ip):
        return mock.patch(
            'sky.serve.serve_utils.serve_state.'
            'get_service_from_name',
            return_value={
                'name': 'svc',
                'controller_pid': 1234,
                'controller_port': 20001,
                'controller_ip': controller_ip,
            })

    def test_no_record_returns_localhost(self):
        """Service row missing → fall back to localhost."""
        with mock.patch(
                'sky.serve.serve_utils.serve_state.'
                'get_service_from_name',
                return_value=None):
            assert serve_utils._get_controller_url(
                'svc', 20001) == 'http://localhost:20001'

    def test_controller_ip_none_returns_localhost(self):
        """Row exists but controller_ip not yet written (e.g. older row from
        before the migration) → localhost."""
        with self._patch_record(None):
            assert serve_utils._get_controller_url(
                'svc', 20001) == 'http://localhost:20001'

    def test_controller_ip_equals_self_returns_localhost(self, monkeypatch):
        """We are the controller's host pod → loopback is correct + faster."""
        monkeypatch.setenv('POD_IP', '10.0.0.5')
        with self._patch_record('10.0.0.5'):
            assert serve_utils._get_controller_url(
                'svc', 20001) == 'http://localhost:20001'

    def test_controller_ip_differs_returns_pod_ip(self, monkeypatch):
        """We are on a follower pod → route to controller pod's IP."""
        monkeypatch.setenv('POD_IP', '10.0.0.5')
        with self._patch_record('10.0.0.7'):
            assert serve_utils._get_controller_url(
                'svc', 20001) == 'http://10.0.0.7:20001'

    def test_no_pod_ip_env_routes_via_recorded_ip(self, monkeypatch):
        """No POD_IP env (non-K8s deploy) but a controller_ip is recorded.
        We can't decide we're the controller pod — route to recorded IP. This
        case shouldn't happen in practice (a pod recording controller_ip
        implies POD_IP env was injected when it started the controller), but
        the routing must remain deterministic."""
        monkeypatch.delenv('POD_IP', raising=False)
        with self._patch_record('10.0.0.7'):
            assert serve_utils._get_controller_url(
                'svc', 20001) == 'http://10.0.0.7:20001'


class TestControllerHttpRetry:

    def _patch_record(self, controller_ip):
        return mock.patch(
            'sky.serve.serve_utils.serve_state.'
            'get_service_from_name',
            return_value={
                'name': 'svc',
                'controller_pid': 1234,
                'controller_port': 20001,
                'controller_ip': controller_ip,
            })

    def test_post_succeeds_first_try(self):
        with self._patch_record(None):
            with mock.patch('sky.serve.serve_utils.requests.post',
                            return_value=mock.Mock(status_code=200)) as m:
                resp = serve_utils._post_to_controller_with_retry(
                    'svc', 20001, '/controller/update_service', json={})
                assert resp.status_code == 200
                assert m.call_count == 1

    def test_post_retries_then_succeeds(self):
        # First 2 calls raise, 3rd succeeds.
        side = [
            requests_exceptions.ConnectionError('refused'),
            requests_exceptions.ConnectionError('refused'),
            mock.Mock(status_code=200)
        ]
        with self._patch_record(None), \
             mock.patch('sky.serve.serve_utils.time.sleep'), \
             mock.patch('sky.serve.serve_utils.requests.post',
                        side_effect=side) as m:
            resp = serve_utils._post_to_controller_with_retry(
                'svc', 20001, '/controller/update_service', json={})
            assert resp.status_code == 200
            assert m.call_count == 3

    def test_post_exhausts_retries_and_raises(self):
        with self._patch_record(None), \
             mock.patch('sky.serve.serve_utils.time.sleep'), \
             mock.patch('sky.serve.serve_utils.requests.post',
                        side_effect=requests_exceptions.ConnectionError('refused')) as m:
            with pytest.raises(requests_exceptions.ConnectionError):
                serve_utils._post_to_controller_with_retry(
                    'svc', 20001, '/controller/update_service', json={})
            assert m.call_count == serve_utils._CONTROLLER_HTTP_RETRY_ATTEMPTS

    def test_get_succeeds_first_try(self):
        with self._patch_record(None):
            with mock.patch('sky.serve.serve_utils.requests.get',
                            return_value=mock.Mock(status_code=200)) as m:
                resp = serve_utils._get_to_controller_with_retry(
                    'svc', 20001, '/autoscaler/info')
                assert resp.status_code == 200
                assert m.call_count == 1

    def test_get_retries_url_is_re_resolved_each_attempt(self):
        """Between retries we re-call _get_controller_url so that if DB
        finished flipping during the backoff, we route to the new owner on
        the next try."""

        # Simulate DB flip mid-retry: first lookup says 10.0.0.7, second
        # lookup says 10.0.0.8.
        records = [
            {
                'name': 'svc',
                'controller_pid': 1,
                'controller_port': 20001,
                'controller_ip': '10.0.0.7'
            },
            {
                'name': 'svc',
                'controller_pid': 2,
                'controller_port': 20001,
                'controller_ip': '10.0.0.8'
            },
        ]
        urls_called = []

        def capture_get(url, **kwargs):  # pylint: disable=unused-argument
            urls_called.append(url)
            if len(urls_called) == 1:
                raise requests_exceptions.ConnectionError('refused')
            return mock.Mock(status_code=200)

        with mock.patch('sky.serve.serve_utils.serve_state.'
                        'get_service_from_name',
                        side_effect=records), \
             mock.patch('sky.serve.serve_utils.time.sleep'), \
             mock.patch('sky.serve.serve_utils.requests.get',
                        side_effect=capture_get):
            serve_utils._get_to_controller_with_retry('svc', 20001,
                                                      '/autoscaler/info')
        assert urls_called[0] == 'http://10.0.0.7:20001/autoscaler/info'
        assert urls_called[1] == 'http://10.0.0.8:20001/autoscaler/info'

    def test_log_levels_one_warn_per_cycle(self):
        with self._patch_record(None), \
             mock.patch('sky.serve.serve_utils.time.sleep'), \
             mock.patch(
                 'sky.serve.serve_utils.requests.get',
                 side_effect=requests_exceptions.ConnectionError('refused')), \
             mock.patch.object(serve_utils.logger, 'warning') as warn, \
             mock.patch.object(serve_utils.logger, 'debug') as debug:
            with pytest.raises(requests_exceptions.ConnectionError):
                serve_utils._get_to_controller_with_retry(
                    'svc', 20001, '/autoscaler/info')
        # Final-attempt failure → exactly one WARN.
        assert warn.call_count == 1, (
            f'expected exactly 1 WARN call, got {warn.call_count}: '
            f'{warn.call_args_list}')
        # Intermediate retry attempts log at DEBUG (N-1 of them). Filter
        # by message content so the assertion stays robust to other
        # DEBUG lines emitted on the same path (e.g. `_get_controller_url`
        # also emits one DEBUG per URL resolution — those are routing
        # diagnostics, not retry signals).
        retry_debug_calls = [
            c for c in debug.call_args_list
            if 'Connection to controller' in (c.args[0] if c.args else '')
        ]
        assert len(retry_debug_calls) == (
            serve_utils._CONTROLLER_HTTP_RETRY_ATTEMPTS -
            1), (f'expected {serve_utils._CONTROLLER_HTTP_RETRY_ATTEMPTS - 1} '
                 f'retry DEBUG calls, got {len(retry_debug_calls)}: '
                 f'{retry_debug_calls}')

    def test_default_timeout_is_passed_to_requests(self):
        """Without an explicit timeout, `requests` blocks forever. Cross-pod
        TCP connect to a dead remote pod can hang for tens of seconds, which
        is why `sky jobs pool status` was hanging. Verify we always inject
        the default timeout if caller didn't provide one."""
        captured = {}

        def capture(url, **kwargs):
            captured.update(kwargs)
            return mock.Mock(status_code=200)

        with self._patch_record(None), \
             mock.patch('sky.serve.serve_utils.requests.get',
                        side_effect=capture):
            serve_utils._get_to_controller_with_retry('svc', 20001,
                                                      '/autoscaler/info')
        assert 'timeout' in captured
        assert captured['timeout'] == (
            serve_utils._CONTROLLER_HTTP_TIMEOUT_SECONDS)

    def test_caller_supplied_timeout_wins(self):
        """If a call site explicitly passes timeout, don't override it."""
        captured = {}

        def capture(url, **kwargs):
            captured.update(kwargs)
            return mock.Mock(status_code=200)

        with self._patch_record(None), \
             mock.patch('sky.serve.serve_utils.requests.get',
                        side_effect=capture):
            serve_utils._get_to_controller_with_retry('svc',
                                                      20001,
                                                      '/autoscaler/info',
                                                      timeout=42)
        assert captured['timeout'] == 42

    def test_timeout_exception_triggers_retry(self):
        """`requests.exceptions.Timeout` (raised on connect/read timeout)
        must go through the same retry path as ConnectionError. Otherwise
        the first slow connect would propagate immediately and the user
        would see a hang from the timeout itself rather than a fast
        retry-and-fail."""
        side = [
            requests_exceptions.Timeout('connect timed out'),
            requests_exceptions.Timeout('connect timed out'),
            mock.Mock(status_code=200),
        ]
        with self._patch_record(None), \
             mock.patch('sky.serve.serve_utils.time.sleep'), \
             mock.patch('sky.serve.serve_utils.requests.get',
                        side_effect=side) as m:
            resp = serve_utils._get_to_controller_with_retry(
                'svc', 20001, '/autoscaler/info')
            assert resp.status_code == 200
            assert m.call_count == 3


class TestTerminateShuttingDownPurge:
    """SHUTTING_DOWN zombies (e.g. controller subprocess SIGKILL'd between
    `_cleanup`'s first step and the row removal) must be reachable via
    `--purge`. Plain `down` keeps its previous skip-already-scheduled
    behavior."""

    def _service_record(self, status):
        return {
            'name': 'svc',
            'status': status,
            'controller_pid': 1234,
            'controller_port': 20001,
            'controller_ip': None,
            'pool': True,
        }

    def test_purge_calls_terminate_failed_services_for_shutting_down(self):
        # pylint: disable=import-outside-toplevel
        from sky.serve import serve_state
        with mock.patch('sky.serve.serve_utils.serve_state.'
                        'get_glob_service_names',
                        return_value=['svc']), \
             mock.patch('sky.serve.serve_utils._get_service_status',
                        return_value=self._service_record(
                            serve_state.ServiceStatus.SHUTTING_DOWN)), \
             mock.patch(
                 'sky.serve.serve_utils.managed_job_state.'
                 'get_nonterminal_job_ids_by_pool',
                 return_value=[]), \
             mock.patch('sky.serve.serve_utils._terminate_failed_services',
                        return_value=None) as mock_purge:
            serve_utils.terminate_services(['svc'], purge=True, pool=True)
            mock_purge.assert_called_once()
            args = mock_purge.call_args[0]
            assert args[0] == 'svc'
            assert args[1] == serve_state.ServiceStatus.SHUTTING_DOWN

    def test_no_purge_skips_shutting_down_unchanged(self):
        # pylint: disable=import-outside-toplevel
        from sky.serve import serve_state
        with mock.patch('sky.serve.serve_utils.serve_state.'
                        'get_glob_service_names',
                        return_value=['svc']), \
             mock.patch('sky.serve.serve_utils._get_service_status',
                        return_value=self._service_record(
                            serve_state.ServiceStatus.SHUTTING_DOWN)), \
             mock.patch(
                 'sky.serve.serve_utils.managed_job_state.'
                 'get_nonterminal_job_ids_by_pool',
                 return_value=[]), \
             mock.patch('sky.serve.serve_utils._terminate_failed_services'
                       ) as mock_purge:
            serve_utils.terminate_services(['svc'], purge=False, pool=True)
            mock_purge.assert_not_called()


class TestTerminalStatuses:
    """`terminal_statuses` includes SHUTTING_DOWN so that callers like
    apply() can refuse to update a row that's either dying or already
    broken (CONTROLLER_FAILED / FAILED_CLEANUP / SHUTTING_DOWN)."""

    def test_includes_shutting_down(self):
        # pylint: disable=import-outside-toplevel
        from sky.serve import serve_state
        statuses = serve_state.ServiceStatus.terminal_statuses()
        assert serve_state.ServiceStatus.SHUTTING_DOWN in statuses
        assert serve_state.ServiceStatus.FAILED_CLEANUP in statuses
        assert serve_state.ServiceStatus.CONTROLLER_FAILED in statuses
        # Healthy states must NOT be in here, otherwise apply() would refuse
        # to update healthy pools.
        assert serve_state.ServiceStatus.READY not in statuses
        assert serve_state.ServiceStatus.CONTROLLER_INIT not in statuses


class TestStreamReplicaLogsZeroByteFallback:
    """`replica_<id>.log` is the teardown archive (only written by
    terminate_cluster's redirect_log or _download_and_stream_logs). Once
    the teardown path runs and crashes mid-flight (or terminate_cluster is
    invoked on a replica that never provisioned a cluster), a 0-byte
    `replica_<id>.log` is left on disk.

    Before the fix, `stream_replica_logs` checked `os.path.exists`
    only — so it would commit to the (empty) main log, print "", and
    return without ever consulting the launch log. Result: `sky jobs
    pool logs` returns blank for a perfectly alive replica whose real
    output is in `replica_<id>_launch.log`.

    Fix: also gate on `os.path.getsize > 0`. These tests pin that
    invariant.
    """

    def _patch_healthy(self):
        # _check_service_status_healthy returns None → continue past gate.
        return mock.patch('sky.serve.serve_utils._check_service_status_healthy',
                          return_value=None)

    def test_zero_byte_main_log_falls_through_to_launch_log(
            self, tmp_path, capsys):
        # Set up: 0-byte replica_1.log + 100-byte replica_1_launch.log
        # under a fake service dir. Patch the path generators to return
        # them. The function should print the launch log content, not "".
        main_log = tmp_path / 'replica_1.log'
        launch_log = tmp_path / 'replica_1_launch.log'
        main_log.touch()
        launch_log.write_text('LAUNCH-CONTENT\n')
        with self._patch_healthy(), \
             mock.patch(
                 'sky.serve.serve_utils.generate_replica_log_file_name',
                 return_value=str(main_log)), \
             mock.patch(
                 'sky.serve.serve_utils.generate_replica_launch_log_file_name',
                 return_value=str(launch_log)), \
             mock.patch(
                 'sky.serve.serve_utils.serve_state.get_replica_infos',
                 return_value=[mock.Mock(replica_id=1, status='READY')]), \
             mock.patch(
                 'sky.serve.serve_utils._follow_logs_with_provision_expanding',
                 return_value=iter(['LAUNCH-CONTENT\n'])), \
             mock.patch(
                 'sky.serve.serve_utils._get_service_status',
                 return_value={'status': 'READY'}):
            serve_utils.stream_replica_logs('svc',
                                            replica_id=1,
                                            follow=False,
                                            tail=None,
                                            pool=True)
        captured = capsys.readouterr()
        # The launch log content must have been emitted.
        assert 'LAUNCH-CONTENT' in captured.out, (
            f'launch log fallback failed; captured stdout: {captured.out!r}')

    def test_nonempty_main_log_still_used(self, tmp_path, capsys):
        """Sanity: when main log has content, we still use it (no
        regression to the original happy path)."""
        main_log = tmp_path / 'replica_1.log'
        launch_log = tmp_path / 'replica_1_launch.log'
        main_log.write_text('MAIN-CONTENT\n')
        # Launch log should NOT be touched in this case.
        launch_log.write_text('SHOULD-NOT-APPEAR\n')
        with self._patch_healthy(), \
             mock.patch(
                 'sky.serve.serve_utils.generate_replica_log_file_name',
                 return_value=str(main_log)), \
             mock.patch(
                 'sky.serve.serve_utils.generate_replica_launch_log_file_name',
                 return_value=str(launch_log)):
            serve_utils.stream_replica_logs('svc',
                                            replica_id=1,
                                            follow=False,
                                            tail=None,
                                            pool=True)
        captured = capsys.readouterr()
        assert 'MAIN-CONTENT' in captured.out
        assert 'SHOULD-NOT-APPEAR' not in captured.out


class TestHaRecoveryDefensiveOnAliveCheckException:
    """`ha_recovery_for_consolidation_mode` calls `_controller_process_alive`
    to decide whether to respawn the controller. If that call raises a
    transient psutil exception (AccessDenied / cmdline read race / etc.),
    the previous code FELL THROUGH to running the recovery script —
    effectively replacing a possibly-alive controller every iteration that
    hit the exception. The fix is to skip recovery for that round and
    revisit next iteration.
    """

    def test_skip_when_alive_check_raises(self, tmp_path, monkeypatch):
        # pylint: disable=import-outside-toplevel
        import psutil

        # Pretend pool 'svc' has a controller_pid recorded; alive check
        # raises AccessDenied (transient). Recovery script must NOT run.
        monkeypatch.setenv('POD_IP', '10.4.0.1')
        with mock.patch(
                'sky.serve.serve_utils.serve_state.get_glob_service_names',
                return_value=['svc']), \
             mock.patch(
                 'sky.serve.serve_utils._get_service_status',
                 return_value={'controller_pid': 1234,
                               'controller_ip': '10.4.0.1',
                               'status': 'READY'}), \
             mock.patch(
                 'sky.serve.serve_utils._controller_process_alive',
                 side_effect=psutil.AccessDenied(1234)) as mock_alive, \
             mock.patch(
                 'sky.serve.serve_utils.serve_state.get_ha_recovery_script',
                 return_value='dummy script') as mock_script, \
             mock.patch(
                 'sky.serve.serve_utils.command_runner.'
                 'LocalProcessCommandRunner') as mock_runner_cls, \
             mock.patch(
                 'sky.serve.serve_utils.skylet_constants.'
                 'HA_PERSISTENT_RECOVERY_LOG_PATH',
                 str(tmp_path / 'recovery_log_{}.log')):
            serve_utils.ha_recovery_for_consolidation_mode(pool=True)
            # alive was probed
            assert mock_alive.called
            # recovery script lookup or run must NOT happen — we skipped early
            mock_script.assert_not_called()
            mock_runner_cls.return_value.run.assert_not_called()

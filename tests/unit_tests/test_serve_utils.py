import pathlib
import tempfile
from unittest import mock

import pytest

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

    @pytest.mark.parametrize(
        'signal_exists,config_value,expected',
        [
            # Deploy-mode divergence regression: signal on, config None.
            # Before the fix this returned False; now matches managed jobs.
            (True, None, True),
            (False, None, False),
            # Signal file is authoritative — config is ignored for pool.
            (False, True, False),
            (True, False, True),
        ])
    def test_pool_follows_signal_file(self, signal_exists, config_value,
                                      expected):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            if signal_exists:
                signal_file.touch()
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)), \
                    mock.patch('sky.serve.serve_utils.skypilot_config'
                              ) as mock_config:
                mock_config.get_nested.return_value = config_value
                assert serve_utils.is_consolidation_mode(pool=True) is expected
                # Without IS_SKYPILOT_SERVER set, the config read + validation
                # block must be skipped. Proves the env-guard is effective.
                mock_config.get_nested.assert_not_called()

    @mock.patch.dict('os.environ', {'IS_SKYPILOT_SERVER': 'true'}, clear=False)
    @pytest.mark.parametrize(
        'signal_exists,config_value,expects_warning,validator_arg',
        [
            # Config unset: no warning, validator gets effective value.
            (True, None, False, True),
            (False, None, False, False),
            # Config matches signal: no warning, validator gets intended config.
            (True, True, False, True),
            (False, False, False, False),
            # Config mismatches signal: warning, validator gets intended config.
            (True, False, True, False),
            (False, True, True, True),
        ])
    def test_pool_warns_and_validates_with_server_env(self, signal_exists,
                                                      config_value,
                                                      expects_warning,
                                                      validator_arg):
        """With IS_SKYPILOT_SERVER set, pool branch reads jobs config key,
        warns on config-vs-signal mismatch, and validates against intent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            if signal_exists:
                signal_file.touch()
            validate_path = ('sky.serve.serve_utils.'
                             '_validate_consolidation_mode_config')
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)), \
                    mock.patch('sky.serve.serve_utils.skypilot_config'
                              ) as mock_config, \
                    mock.patch(validate_path) as mock_validate, \
                    mock.patch('sky.serve.serve_utils.logger') as mock_logger:
                mock_config.get_nested.return_value = config_value
                serve_utils.is_consolidation_mode(pool=True)
                mock_config.get_nested.assert_called_with(
                    ('jobs', 'controller', 'consolidation_mode'),
                    default_value=None)
                mock_validate.assert_called_once_with(validator_arg, True)
                assert mock_logger.warning.called is expects_warning

    @pytest.mark.parametrize('config_value,expected', [(True, True),
                                                       (False, False)])
    def test_serve_reads_config_only(self, config_value, expected):
        """pool=False: reads serve config key; signal file must not affect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            signal_file.touch()  # signal file present should not matter
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)), \
                    mock.patch('sky.serve.serve_utils.skypilot_config'
                              ) as mock_config:
                mock_config.get_nested.return_value = config_value
                assert serve_utils.is_consolidation_mode(pool=False) is expected
                mock_config.get_nested.assert_called_with(
                    ('serve', 'controller', 'consolidation_mode'),
                    default_value=False)

    @mock.patch.dict('os.environ', {'IS_SKYPILOT_JOB_CONTROLLER': '1'},
                     clear=False)
    @pytest.mark.parametrize('pool', [True, False])
    def test_override_env_forces_true(self, pool):
        """OVERRIDE_CONSOLIDATION_MODE forces True regardless of pool/serve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_file = pathlib.Path(tmpdir) / 'signal'
            with mock.patch(_SIGNAL_FILE_CONST, str(signal_file)), \
                    mock.patch('sky.serve.serve_utils.skypilot_config'
                              ) as mock_config:
                mock_config.get_nested.return_value = False
                assert serve_utils.is_consolidation_mode(pool=pool) is True

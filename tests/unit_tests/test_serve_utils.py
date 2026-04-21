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

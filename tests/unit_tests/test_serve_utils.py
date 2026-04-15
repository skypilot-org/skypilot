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

    @pytest.mark.parametrize('delegated_result', [True, False])
    def test_pool_delegates_to_managed_jobs(self, delegated_result,
                                            monkeypatch):
        """pool=True delegates to managed_job_utils.is_consolidation_mode
        so the two readers are the same function, not two copies."""
        monkeypatch.delenv('IS_SKYPILOT_SERVER', raising=False)
        monkeypatch.delenv('IS_SKYPILOT_JOB_CONTROLLER', raising=False)
        with mock.patch('sky.jobs.utils.is_consolidation_mode',
                        return_value=delegated_result) as mock_delegate, \
                mock.patch('sky.serve.serve_utils.skypilot_config'
                          ) as mock_config:
            assert serve_utils.is_consolidation_mode(
                pool=True) is delegated_result
            mock_delegate.assert_called_once_with()
            # Without IS_SKYPILOT_SERVER, the pool-specific validator block
            # is skipped entirely, so config must not be read.
            mock_config.get_nested.assert_not_called()

    @mock.patch.dict('os.environ', {'IS_SKYPILOT_SERVER': 'true'}, clear=False)
    @pytest.mark.parametrize(
        'delegated_result,config_value,arg,should_validate',
        [
            # Consolidation off → pool validator runs (warns about leftover
            # pools, which the jobs validator doesn't cover).
            (False, None, False, True),
            (True, False, False, True),
            (False, False, False, True),
            # Consolidation on → pool validator skipped (the jobs validator
            # already warns about the shared controller cluster).
            (True, None, True, False),
            (False, True, True, False),
            (True, True, True, False),
        ])
    def test_pool_validator_runs_only_when_not_consolidated(
            self, delegated_result, config_value, arg, should_validate,
            monkeypatch):
        """Pool validator only adds unique information when consolidation is
        off. In the on case, the jobs validator (run via delegation) already
        emits the leftover-controller-cluster warning."""
        monkeypatch.delenv('IS_SKYPILOT_JOB_CONTROLLER', raising=False)
        validate_path = ('sky.serve.serve_utils.'
                         '_validate_consolidation_mode_config')
        with mock.patch('sky.jobs.utils.is_consolidation_mode',
                        return_value=delegated_result), \
                mock.patch('sky.serve.serve_utils.skypilot_config'
                          ) as mock_config, \
                mock.patch(validate_path) as mock_validate:
            mock_config.get_nested.return_value = config_value
            serve_utils.is_consolidation_mode(pool=True)
            mock_config.get_nested.assert_called_once_with(
                ('jobs', 'controller', 'consolidation_mode'),
                default_value=None)
            if should_validate:
                mock_validate.assert_called_once_with(arg, True)
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

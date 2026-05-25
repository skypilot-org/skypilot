"""Unit tests for Slurm v1 detection, gating, and template_override.

Covers:
- ``is_managed_job_v1_provider_config`` marker reads.
- ``is_slurm_managed_jobs_v1_enabled`` env / config gates.
- ``_get_unsupported_v1_inputs`` per user input dimension.
- ``will_use_v1_template`` decision matrix.
- ``template_override`` happy + fallback paths and the warning gate.
"""
# pylint: disable=protected-access,missing-class-docstring
# pylint: disable=import-outside-toplevel,unused-argument
from unittest import mock

import pytest

from sky import clouds as sky_clouds
from sky.provision import TemplateSpec
from sky.provision.slurm import instance as slurm_instance

# ----------------------------- Fixtures ----------------------------- #


def _make_resource(cloud=None,
                   accelerator_type=None,
                   accelerator_count=0,
                   cluster_overrides=None,
                   docker_image=None):
    """Build a mock ``Resources`` with the attributes ``template_override``
    inspects."""
    if cloud is None:
        cloud = sky_clouds.Slurm()
    resource = mock.MagicMock(name='Resources')
    resource.cloud = cloud
    resource.cluster_config_overrides = cluster_overrides or {}
    resource.extract_docker_image = mock.MagicMock(return_value=docker_image)
    resource.accelerator_type = accelerator_type
    resource.accelerator_count = accelerator_count
    return resource


def _make_task(resources=None,
               workdir=None,
               file_mounts=None,
               local_to_remote_file_mounts=None,
               storage_mounts=None,
               num_nodes=1,
               setup=None,
               run=None,
               envs_and_secrets=None):
    """Build a mock ``Task`` for ``template_override``.

    ``local_to_remote_file_mounts`` is what
    ``get_local_to_remote_file_mounts()`` returns — distinct from
    ``file_mounts`` (the raw dict). A non-cloud-URI file mount appears
    in both; a cloud-URI one only in ``file_mounts``.
    """
    if resources is None:
        resources = [_make_resource()]
    task = mock.MagicMock(name='Task')
    task.resources = resources
    task.workdir = workdir
    task.file_mounts = file_mounts
    task.get_local_to_remote_file_mounts = mock.MagicMock(
        return_value=local_to_remote_file_mounts or {})
    task.storage_mounts = storage_mounts or {}
    task.num_nodes = num_nodes
    task.setup = setup
    task.run = run
    task.envs_and_secrets = envs_and_secrets or {}
    return task


# --------------------- is_managed_job_v1_provider_config -------------- #


class TestIsManagedJobV1ProviderConfig:

    def test_positive_marker(self):
        cfg = {'skypilot_runtime': 'managed_job_v1', 'other': 'value'}
        assert slurm_instance.is_managed_job_v1_provider_config(cfg) is True

    def test_wrong_marker(self):
        cfg = {'skypilot_runtime': 'legacy'}
        assert slurm_instance.is_managed_job_v1_provider_config(cfg) is False

    def test_missing_marker(self):
        cfg = {'partition': 'gpu'}
        assert slurm_instance.is_managed_job_v1_provider_config(cfg) is False

    def test_empty_dict(self):
        assert slurm_instance.is_managed_job_v1_provider_config({}) is False


# --------------------- is_slurm_managed_jobs_v1_enabled --------------- #


class TestIsSlurmManagedJobsV1Enabled:

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv('SKYPILOT_SLURM_DISABLE_V1_JOBS', raising=False)
        monkeypatch.setattr(slurm_instance.skypilot_config, 'get_nested',
                            lambda keys, default: default)
        assert slurm_instance.is_slurm_managed_jobs_v1_enabled() is True

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv('SKYPILOT_SLURM_DISABLE_V1_JOBS', '1')
        # Even if config says True, env wins.
        monkeypatch.setattr(slurm_instance.skypilot_config, 'get_nested',
                            lambda keys, default: True)
        assert slurm_instance.is_slurm_managed_jobs_v1_enabled() is False

    def test_env_other_values_do_not_disable(self, monkeypatch):
        # Only the literal '1' counts as a disable; '0', 'true', etc. don't.
        monkeypatch.setenv('SKYPILOT_SLURM_DISABLE_V1_JOBS', '0')
        monkeypatch.setattr(slurm_instance.skypilot_config, 'get_nested',
                            lambda keys, default: default)
        assert slurm_instance.is_slurm_managed_jobs_v1_enabled() is True

    def test_disabled_via_config(self, monkeypatch):
        monkeypatch.delenv('SKYPILOT_SLURM_DISABLE_V1_JOBS', raising=False)
        monkeypatch.setattr(slurm_instance.skypilot_config, 'get_nested',
                            lambda keys, default: False)
        assert slurm_instance.is_slurm_managed_jobs_v1_enabled() is False


# ----------------------- _get_unsupported_v1_inputs ------------------- #


class TestGetUnsupportedV1Inputs:

    def test_empty_task(self):
        task = _make_task()
        assert slurm_instance._get_unsupported_v1_inputs(task) == []

    def test_local_string_workdir_flagged(self):
        task = _make_task(workdir='/home/user/code')
        result = slurm_instance._get_unsupported_v1_inputs(task)
        assert len(result) == 1
        assert 'workdir' in result[0]
        assert '/home/user/code' in result[0]

    def test_git_dict_workdir_allowed(self):
        task = _make_task(workdir={
            'url': 'https://github.com/foo/bar.git',
            'ref': 'main'
        })
        # Dict (git) workdirs are not flagged.
        assert slurm_instance._get_unsupported_v1_inputs(task) == []

    def test_local_file_mounts_listed(self):
        task = _make_task(local_to_remote_file_mounts={
            '/remote/a': '/local/a',
            '/remote/b': '/local/b',
        })
        result = slurm_instance._get_unsupported_v1_inputs(task)
        assert len(result) == 2
        assert any('/local/a' in s and '/remote/a' in s for s in result)
        assert any('/local/b' in s and '/remote/b' in s for s in result)

    def test_cloud_uri_file_mounts_allowed(self):
        # ``get_local_to_remote_file_mounts`` filters cloud URIs out, so
        # an empty dict from that method means everything is cloud-URI.
        task = _make_task(
            file_mounts={
                '/data': 's3://bucket/key',
                '/gs': 'gs://b/k'
            },
            local_to_remote_file_mounts={},
        )
        assert slurm_instance._get_unsupported_v1_inputs(task) == []

    def test_storage_mounts_listed(self):
        storage = mock.MagicMock()
        storage.name = 'my-bucket'
        task = _make_task(storage_mounts={'/mnt/data': storage})
        result = slurm_instance._get_unsupported_v1_inputs(task)
        assert len(result) == 1
        assert 'storage_mounts' in result[0]
        assert 'my-bucket' in result[0]


# --------------------------- will_use_v1_template --------------------- #


class _V1GateFixture:
    """Helper that toggles all the v1 gates via monkeypatch."""

    def __init__(self, monkeypatch, *, v1_enabled=True):
        self.monkeypatch = monkeypatch
        monkeypatch.delenv('SKYPILOT_SLURM_DISABLE_V1_JOBS', raising=False)
        monkeypatch.setattr(
            slurm_instance.skypilot_config, 'get_nested',
            lambda keys, default: v1_enabled
            if keys == ('slurm', 'use_v1') else default)


class TestWillUseV1Template:

    def test_happy_path(self, monkeypatch):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task()
        assert slurm_instance.will_use_v1_template(
            task, is_launched_by_jobs_controller=True) is True

    def test_not_launched_by_controller_disables(self, monkeypatch):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task()
        assert slurm_instance.will_use_v1_template(
            task, is_launched_by_jobs_controller=False) is False

    def test_non_slurm_resource_alternative_disables(self, monkeypatch):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        non_slurm = _make_resource(cloud=sky_clouds.AWS())
        task = _make_task(resources=[_make_resource(), non_slurm])
        assert slurm_instance.will_use_v1_template(
            task, is_launched_by_jobs_controller=True) is False

    def test_empty_resources_disables(self, monkeypatch):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(resources=[])
        assert slurm_instance.will_use_v1_template(
            task, is_launched_by_jobs_controller=True) is False

    def test_unsupported_input_disables(self, monkeypatch):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(workdir='/local/path')
        assert slurm_instance.will_use_v1_template(
            task, is_launched_by_jobs_controller=True) is False

    def test_v1_gate_off_disables(self, monkeypatch):
        _V1GateFixture(monkeypatch, v1_enabled=False)
        task = _make_task()
        assert slurm_instance.will_use_v1_template(
            task, is_launched_by_jobs_controller=True) is False


# --------------------------- template_override ------------------------ #


class TestTemplateOverride:

    @pytest.fixture
    def patched_helpers(self, monkeypatch):
        """Stub out the controller-only helpers ``template_override``
        reaches into: env/secret extraction and num-gpus."""
        from sky import task as task_lib
        from sky.backends import backend_utils
        monkeypatch.setattr(task_lib, 'get_plaintext_envs_and_secrets',
                            lambda envs: dict(envs) if envs else {})
        monkeypatch.setattr(backend_utils, 'get_num_gpus_per_node',
                            lambda task: task.num_nodes and 0 or 0)

    def test_happy_path_returns_spec(self, monkeypatch, patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(
            setup='setup.sh',
            run='run.sh',
            envs_and_secrets={'FOO': 'bar'},
            num_nodes=2,
        )

        spec = slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )

        assert isinstance(spec, TemplateSpec)
        assert spec.template_path == 'slurm-managed-job-v1.yml.j2'
        expected_keys = {
            'setup',
            'run',
            'envs',
            'num_nodes',
            'num_gpus_per_node',
            'workdir',
            'file_mounts',
            'sbatch_options',
            'container_image',
        }
        assert expected_keys == set(spec.variables.keys())
        assert spec.variables['setup'] == 'setup.sh'
        assert spec.variables['run'] == 'run.sh'
        assert spec.variables['num_nodes'] == 2
        assert spec.variables['envs'] == {'FOO': 'bar'}

    def test_git_workdir_threaded_into_variables(self, monkeypatch,
                                                 patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        git_dict = {'url': 'https://github.com/foo/bar.git', 'ref': 'main'}
        task = _make_task(workdir=git_dict)

        spec = slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )

        assert spec is not None
        assert spec.variables['workdir'] == {'git': git_dict}

    def test_container_image_threaded_into_variables(self, monkeypatch,
                                                     patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        resource = _make_resource(docker_image='nvcr.io/foo:bar')
        task = _make_task(resources=[resource])

        spec = slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )

        assert spec is not None
        assert spec.variables['container_image'] == 'nvcr.io/foo:bar'

    def test_task_level_sbatch_options_threaded(self, monkeypatch,
                                                patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        overrides = {'slurm': {'sbatch_options': {'qos': 'high'}}}
        resource = _make_resource(cluster_overrides=overrides)
        task = _make_task(resources=[resource])

        spec = slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )

        assert spec is not None
        assert spec.variables['sbatch_options'] == {'qos': 'high'}

    def test_no_sbatch_options_yields_none(self, monkeypatch, patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task()

        spec = slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )

        assert spec is not None
        # Empty sbatch_options collapses to None in the template variables.
        assert spec.variables['sbatch_options'] is None

    # --- Fallback paths --- #

    def test_returns_none_when_not_controller_launched(self, monkeypatch,
                                                       patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task()
        assert slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=False) is None

    def test_returns_none_when_non_slurm_alt(self, monkeypatch,
                                             patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(resources=[
            _make_resource(),
            _make_resource(cloud=sky_clouds.AWS()),
        ])
        assert slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True) is None

    def test_returns_none_when_v1_disabled(self, monkeypatch, patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=False)
        task = _make_task()
        assert slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True) is None

    def test_returns_none_on_unsupported_input(self, monkeypatch,
                                               patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(workdir='/local/path')
        assert slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True) is None

    # --- Warning gate: "almost v1" only --- #
    #
    # The `sky` logger has propagate=False, so `caplog` cannot observe
    # it (same gotcha as test_sbatch_config.py::test_no_maxtime_..._warns).
    # Mock the module logger's `warning` directly instead.

    def test_fallback_warning_only_when_almost_v1(self, monkeypatch,
                                                  patched_helpers):
        """The "Falling back to legacy" warning fires only when the task
        is Slurm + controller-launched + v1-enabled + has unsupported
        inputs. Every other fallback path stays silent."""
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(workdir='/local/path')

        warning_mock = mock.MagicMock()
        monkeypatch.setattr(slurm_instance.logger, 'warning', warning_mock)
        result = slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )
        assert result is None
        warning_mock.assert_called_once()
        msg = warning_mock.call_args.args[0]
        assert 'Falling back to legacy' in msg

    def test_no_warning_when_not_controller_launched(self, monkeypatch,
                                                     patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(workdir='/local/path')
        warning_mock = mock.MagicMock()
        monkeypatch.setattr(slurm_instance.logger, 'warning', warning_mock)
        slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=False,
        )
        # No call, or no "Falling back to legacy" in any call.
        for call in warning_mock.call_args_list:
            assert 'Falling back to legacy' not in call.args[0]

    def test_no_warning_when_non_slurm_alt(self, monkeypatch, patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=True)
        task = _make_task(
            resources=[_make_resource(cloud=sky_clouds.AWS())],
            workdir='/local/path',
        )
        warning_mock = mock.MagicMock()
        monkeypatch.setattr(slurm_instance.logger, 'warning', warning_mock)
        slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )
        for call in warning_mock.call_args_list:
            assert 'Falling back to legacy' not in call.args[0]

    def test_no_warning_when_v1_disabled(self, monkeypatch, patched_helpers):
        _V1GateFixture(monkeypatch, v1_enabled=False)
        task = _make_task(workdir='/local/path')
        warning_mock = mock.MagicMock()
        monkeypatch.setattr(slurm_instance.logger, 'warning', warning_mock)
        slurm_instance.template_override(
            task,
            _extra_launch_context={},
            _is_launched_by_jobs_controller=True,
        )
        for call in warning_mock.call_args_list:
            assert 'Falling back to legacy' not in call.args[0]

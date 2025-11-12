"""Unit tests for pool validation errors."""

from unittest import mock

import pytest

import sky
from sky import backends
from sky.serve import serve_utils
from sky.serve.server import impl as serve_impl
from sky.serve.service_spec import SkyServiceSpec
from sky.utils import admin_policy_utils
from sky.utils import dag_utils


def test_pool_creation_with_run_section():
    """Test that pool creation errors out when using a run section."""
    # Create a task with a run section
    task = sky.Task(
        name='test-pool',
        setup='pip install numpy',
        run='python script.py',  # This should cause an error
    )
    task.set_service(
        SkyServiceSpec(
            readiness_path='/health',  # Required but not used for pools
            initial_delay_seconds=0,  # Required but not used for pools
            readiness_timeout_seconds=30,  # Required but not used for pools
            min_replicas=1,
            pool=True,
        ))
    task.validate()
    serve_utils.validate_service_task(task, pool=True)

    # Mock everything needed to get to the validation check in up()
    # The validation happens after DAG conversion and admin policy application
    with mock.patch('sky.serve.server.impl._get_service_record', return_value=None), \
         mock.patch('sky.serve.serve_state.add_service', return_value=None), \
         mock.patch('sky.execution.launch', return_value=(None, None)), \
         mock.patch('sky.backends.backend_utils.is_controller_accessible', return_value=None), \
         mock.patch('sky.data.storage.get_cached_enabled_storage_cloud_names_or_refresh', return_value=[]), \
         mock.patch('sky.utils.controller_utils.maybe_translate_local_file_mounts_and_sync_up'), \
         mock.patch('sky.utils.controller_utils.translate_local_file_mounts_to_two_hop', return_value={}), \
         mock.patch('sky.serve.server.impl._rewrite_tls_credential_paths_and_get_tls_env_vars', return_value={}), \
         mock.patch.object(admin_policy_utils, 'apply', side_effect=lambda x, **kwargs: x):
        # This should raise ValueError during the validation check in up()
        # at line 167 of sky/serve/server/impl.py
        with pytest.raises(
                ValueError,
                match='Pool creation does not support the `run` section'):
            serve_impl.up(task, service_name='test-pool', pool=True)


def test_pool_update_with_run_section():
    """Test that pool update errors out when using a run section."""
    # Create a task with a run section
    task = sky.Task(
        name='test-pool',
        setup='pip install numpy',
        run='python script.py',  # This should cause an error
    )
    task.set_service(
        SkyServiceSpec(
            readiness_path='/health',  # Required but not used for pools
            initial_delay_seconds=0,  # Required but not used for pools
            readiness_timeout_seconds=30,  # Required but not used for pools
            min_replicas=1,
            pool=True,
        ))
    task.validate()
    serve_utils.validate_service_task(task, pool=True)

    # Mock everything needed to get to the validation check in update()
    # The validation happens after getting the service record and applying admin policy
    mock_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-controller',
        cluster_name_on_cloud='test-controller',
        cluster_yaml='/tmp/test.yaml',
        launched_nodes=1,
        launched_resources=sky.Resources(),
    )
    # Create a mock backend that is an instance of CloudVmRayBackend
    from sky.backends import cloud_vm_ray_backend
    mock_backend = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayBackend)
    mock_backend.download_file = mock.MagicMock()
    with mock.patch('sky.backends.backend_utils.is_controller_accessible', return_value=mock_handle), \
         mock.patch('sky.backends.backend_utils.get_backend_from_handle', return_value=mock_backend), \
         mock.patch.object(serve_impl, '_get_service_record', return_value={'version': 1}), \
         mock.patch('sky.serve.serve_state.add_service', return_value=None), \
         mock.patch('sky.execution.launch', return_value=(None, None)), \
         mock.patch('sky.utils.yaml_utils.read_yaml', return_value={'pool': {'workers': 1}}), \
         mock.patch.object(admin_policy_utils, 'apply', side_effect=lambda x, **kwargs: x):
        # This should raise ValueError during the validation check in update()
        # at line 561 of sky/serve/server/impl.py
        with pytest.raises(
                ValueError,
                match='Pool update does not support the `run` section'):
            serve_impl.update(task, service_name='test-pool', pool=True)


def test_pool_job_launch_with_setup_section():
    """Test that launching a job to a pool errors out when using a setup section."""
    import click

    from sky.jobs import utils as jobs_utils

    # Create a task with a setup section
    task = sky.Task(
        name='test-job',
        setup='pip install numpy',  # This should cause an error
        run='python script.py',
    )

    # Convert to DAG
    dag = dag_utils.convert_entrypoint_to_dag(task)

    # Call the actual validation function
    pool = 'test-pool'
    with mock.patch.object(admin_policy_utils,
                           'apply',
                           side_effect=lambda x, **kwargs: x):
        with pytest.raises(click.UsageError,
                           match='Pool jobs are not allowed to modify'):
            jobs_utils.validate_pool_job(dag, pool)


def test_pool_job_launch_with_file_mounts_section():
    """Test that launching a job to a pool errors out when using a file_mounts section."""
    import click

    from sky.jobs import utils as jobs_utils

    # Create a task with file_mounts
    task = sky.Task(
        name='test-job',
        run='python script.py',
        file_mounts={'/remote/data': './local_data'
                    },  # This should cause an error
    )

    # Convert to DAG
    dag = dag_utils.convert_entrypoint_to_dag(task)

    # Call the actual validation function
    pool = 'test-pool'
    with mock.patch.object(admin_policy_utils,
                           'apply',
                           side_effect=lambda x, **kwargs: x):
        with pytest.raises(click.UsageError,
                           match='Pool jobs are not allowed to modify'):
            jobs_utils.validate_pool_job(dag, pool)


def test_sdk_launch_pool_job_with_setup_section():
    """Test that SDK launch rejects pool jobs with setup section."""
    import click

    from sky.jobs.client import sdk as jobs_sdk

    # Create a task with a setup section
    task = sky.Task(
        name='test-job',
        setup='pip install numpy',  # This should cause an error
        run='python script.py',
    )

    # Try to launch to a pool - should error before making any API calls
    pool = 'test-pool'
    with mock.patch.object(admin_policy_utils, 'apply', side_effect=lambda x, **kwargs: x), \
         mock.patch('sky.jobs.client.sdk.check_server_healthy_or_start_fn', return_value=None):
        with pytest.raises(click.UsageError,
                           match='Pool jobs are not allowed to modify'):
            # The validation happens early in launch() before API calls
            jobs_sdk.launch(task, pool=pool)

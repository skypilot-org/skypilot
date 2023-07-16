import tempfile
import textwrap
from typing import Callable, List, Optional

import pytest

import sky
from sky import clouds
from sky import exceptions


def _test_parse_task_yaml(spec: str, test_fn: Optional[Callable] = None):
    """Tests parsing a task from a YAML spec and running a test_fn."""
    with tempfile.NamedTemporaryFile('w') as f:
        f.write(spec)
        f.flush()
        with sky.Dag():
            task = sky.Task.from_yaml(f.name)
            if test_fn is not None:
                test_fn(task)


def _test_parse_cpus(spec, expected_cpus):

    def test_fn(task):
        assert list(task.resources)[0].cpus == expected_cpus

    _test_parse_task_yaml(spec, test_fn)


def _test_parse_memory(spec, expected_memory):

    def test_fn(task):
        assert list(task.resources)[0].memory == expected_memory

    _test_parse_task_yaml(spec, test_fn)


def _test_parse_accelerators(spec, expected_accelerators):

    def test_fn(task):
        assert list(task.resources)[0].accelerators == expected_accelerators

    _test_parse_task_yaml(spec, test_fn)


# Monkey-patching is required because in the test environment, no cloud is
# enabled. The optimizer checks the environment to find enabled clouds, and
# only generates plans within these clouds. The tests assume that all three
# clouds are enabled, so we monkeypatch the `sky.global_user_state` module
# to return all three clouds. We also monkeypatch `sky.check.check` so that
# when the optimizer tries calling it to update enabled_clouds, it does not
# TODO: Keep the cloud enabling in sync with the fixture enable_all_clouds
# in tests/conftest.py
# raise exceptions.
def _make_resources(
    monkeypatch,
    *resources_args,
    enabled_clouds: List[str] = None,
    **resources_kwargs,
):
    if enabled_clouds is None:
        enabled_clouds = list(clouds.CLOUD_REGISTRY.values())
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: enabled_clouds,
    )
    monkeypatch.setattr('sky.check.check', lambda *_args, **_kwargs: None)

    config_file_backup = tempfile.NamedTemporaryFile(
        prefix='tmp_backup_config_default', delete=False)
    monkeypatch.setattr('sky.clouds.gcp.GCP_CONFIG_SKY_BACKUP_PATH',
                        config_file_backup.name)
    monkeypatch.setattr(
        'sky.clouds.gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH',
        config_file_backup.name)
    monkeypatch.setenv('OCI_CONFIG', config_file_backup.name)

    # Should create Resources here, since it uses the enabled clouds.
    return sky.Resources(*resources_args, **resources_kwargs)


def _test_resources(monkeypatch,
                    *resources_args,
                    enabled_clouds: List[str] = None,
                    expected_cloud: clouds.Cloud = None,
                    **resources_kwargs):
    resources = _make_resources(monkeypatch,
                                *resources_args,
                                **resources_kwargs,
                                enabled_clouds=enabled_clouds)
    if expected_cloud is not None:
        assert expected_cloud.is_same_cloud(resources.cloud)


def _test_resources_launch(monkeypatch,
                           *resources_args,
                           enabled_clouds: List[str] = None,
                           cluster_name: str = None,
                           **resources_kwargs):
    resources = _make_resources(monkeypatch,
                                *resources_args,
                                **resources_kwargs,
                                enabled_clouds=enabled_clouds)

    with sky.Dag() as dag:
        task = sky.Task('test_task')
        task.set_resources({resources})
    sky.launch(dag, dryrun=True, cluster_name=cluster_name)
    assert True


def test_resources_aws(monkeypatch):
    _test_resources_launch(monkeypatch, sky.AWS(), 'p3.2xlarge')


def test_resources_azure(monkeypatch):
    _test_resources_launch(monkeypatch, sky.Azure(), 'Standard_NC24s_v3')


def test_resources_gcp(monkeypatch):
    _test_resources_launch(monkeypatch, sky.GCP(), 'n1-standard-16')


def test_partial_cpus(monkeypatch):
    _test_resources_launch(monkeypatch, cpus=4)
    _test_resources_launch(monkeypatch, cpus='4')
    _test_resources_launch(monkeypatch, cpus='7+')


def test_partial_memory(monkeypatch):
    _test_resources_launch(monkeypatch, memory=32)
    _test_resources_launch(monkeypatch, memory='32')
    _test_resources_launch(monkeypatch, memory='32+')


def test_partial_k80(monkeypatch):
    _test_resources_launch(monkeypatch, accelerators='K80')


def test_partial_m60(monkeypatch):
    _test_resources_launch(monkeypatch, accelerators='M60')


def test_partial_p100(monkeypatch):
    _test_resources_launch(monkeypatch, accelerators='P100')


def test_partial_t4(monkeypatch):
    _test_resources_launch(monkeypatch, accelerators='T4')
    _test_resources_launch(monkeypatch, accelerators={'T4': 8}, use_spot=True)


def test_partial_tpu(monkeypatch):
    _test_resources_launch(monkeypatch, accelerators='tpu-v3-8')


def test_partial_v100(monkeypatch):
    _test_resources_launch(monkeypatch, sky.AWS(), accelerators='V100')
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           accelerators='V100',
                           use_spot=True)
    _test_resources_launch(monkeypatch, sky.AWS(), accelerators={'V100': 8})


def test_invalid_cloud_tpu(monkeypatch):
    with pytest.raises(AssertionError) as e:
        _test_resources_launch(monkeypatch,
                               cloud=sky.AWS(),
                               accelerators='tpu-v3-8')
    assert 'Cloud must be GCP' in str(e.value)


def test_clouds_not_enabled(monkeypatch):
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(monkeypatch,
                               sky.AWS(),
                               enabled_clouds=[
                                   sky.Azure(),
                                   sky.GCP(),
                               ])

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(monkeypatch,
                               sky.Azure(),
                               enabled_clouds=[sky.AWS()])

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(monkeypatch,
                               sky.GCP(),
                               enabled_clouds=[sky.AWS()])


def test_instance_type_mismatches_cpus(monkeypatch):
    bad_instance_and_cpus = [
        # Actual: 8
        ('m6i.2xlarge', 4),
        # Actual: 2
        ('c6i.large', 4),
    ]
    for instance, cpus in bad_instance_and_cpus:
        with pytest.raises(ValueError) as e:
            _test_resources_launch(monkeypatch,
                                   sky.AWS(),
                                   instance_type=instance,
                                   cpus=cpus)
        assert 'does not have the requested number of vCPUs' in str(e.value)


def test_instance_type_mismatches_memory(monkeypatch):
    bad_instance_and_memory = [
        # Actual: 32
        ('m6i.2xlarge', 4),
        # Actual: 4
        ('c6i.large', 2),
    ]
    for instance, memory in bad_instance_and_memory:
        with pytest.raises(ValueError) as e:
            _test_resources_launch(monkeypatch,
                                   sky.AWS(),
                                   instance_type=instance,
                                   memory=memory)
        assert 'does not have the requested memory' in str(e.value)


def test_instance_type_matches_cpus(monkeypatch):
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='c6i.8xlarge',
                           cpus=32)
    _test_resources_launch(monkeypatch,
                           sky.Azure(),
                           instance_type='Standard_E8s_v5',
                           cpus='8')
    _test_resources_launch(monkeypatch,
                           sky.GCP(),
                           instance_type='n1-standard-8',
                           cpus='7+')
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='g4dn.2xlarge',
                           cpus=8.0)


def test_instance_type_matches_memory(monkeypatch):
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='c6i.8xlarge',
                           memory=64)
    _test_resources_launch(monkeypatch,
                           sky.Azure(),
                           instance_type='Standard_E8s_v5',
                           memory='64')
    _test_resources_launch(monkeypatch,
                           sky.GCP(),
                           instance_type='n1-standard-8',
                           memory='30+')
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='g4dn.2xlarge',
                           memory=32)


def test_instance_type_from_cpu_memory(monkeypatch, capfd):
    _test_resources_launch(monkeypatch, cpus=8)
    stdout, _ = capfd.readouterr()
    # Choose General Purpose instance types
    assert 'm6i.2xlarge' in stdout  # AWS, 8 vCPUs, 32 GB memory
    assert 'Standard_D8s_v5' in stdout  # Azure, 8 vCPUs, 32 GB memory
    assert 'n2-standard-8' in stdout  # GCP, 8 vCPUs, 32 GB memory

    _test_resources_launch(monkeypatch, memory=32)
    stdout, _ = capfd.readouterr()
    # Choose memory-optimized instance types, when the memory
    # is specified
    assert 'r6i.xlarge' in stdout  # AWS, 4 vCPUs, 32 GB memory
    assert 'Standard_E4s_v5' in stdout  # Azure, 4 vCPUs, 32 GB memory
    assert 'n2-highmem-4' in stdout  # GCP, 4 vCPUs, 32 GB memory

    _test_resources_launch(monkeypatch, memory='64+')
    stdout, _ = capfd.readouterr()
    # Choose memory-optimized instance types
    assert 'r6i.2xlarge' in stdout  # AWS, 8 vCPUs, 64 GB memory
    assert 'Standard_E8s_v5' in stdout  # Azure, 8 vCPUs, 64 GB memory
    assert 'n2-highmem-8' in stdout  # GCP, 8 vCPUs, 64 GB memory
    assert 'gpu_1x_a10' in stdout  # Lambda, 30 vCPUs, 200 GB memory

    _test_resources_launch(monkeypatch, cpus='4+', memory='4+')
    stdout, _ = capfd.readouterr()
    # Choose compute-optimized instance types, when the memory
    # requirement is less than the memory of General Purpose
    # instance types.
    assert 'n2-highcpu-4' in stdout  # GCP, 4 vCPUs, 4 GB memory
    assert 'c6i.xlarge' in stdout  # AWS, 4 vCPUs, 8 GB memory
    assert 'Standard_F4s_v2' in stdout  # Azure, 4 vCPUs, 8 GB memory
    assert 'gpu_1x_rtx6000' in stdout  # Lambda, 14 vCPUs, 46 GB memory

    _test_resources_launch(monkeypatch, accelerators='T4')
    stdout, _ = capfd.readouterr()
    # Choose cheapest T4 instance type
    assert 'g4dn.xlarge' in stdout  # AWS, 4 vCPUs, 16 GB memory, 1 T4 GPU
    assert 'Standard_NC4as_T4_v3' in stdout  # Azure, 4 vCPUs, 28 GB memory, 1 T4 GPU
    assert 'n1-highmem-4' in stdout  # GCP, 4 vCPUs, 26 GB memory, 1 T4 GPU

    _test_resources_launch(monkeypatch,
                           cpus='16+',
                           memory='32+',
                           accelerators='T4')
    stdout, _ = capfd.readouterr()
    # Choose cheapest T4 instance type that satisfies the requirement
    assert 'n1-standard-16' in stdout  # GCP, 16 vCPUs, 60 GB memory, 1 T4 GPU
    assert 'g4dn.4xlarge' in stdout  # AWS, 16 vCPUs, 64 GB memory, 1 T4 GPU
    assert 'Standard_NC16as_T4_v3' in stdout  # Azure, 16 vCPUs, 110 GB memory, 1 T4 GPU

    _test_resources_launch(monkeypatch, memory='200+', accelerators='T4')
    stdout, _ = capfd.readouterr()
    # Choose cheapest T4 instance type that satisfies the requirement
    assert 'n1-highmem-32' in stdout  # GCP, 32 vCPUs, 208 GB memory, 1 T4 GPU
    assert 'g4dn.16xlarge' in stdout  # AWS, 64 vCPUs, 256 GB memory, 1 T4 GPU
    assert 'Azure' not in stdout  # Azure does not have a 1 T4 GPU instance type with 200+ GB memory


def test_instance_type_mistmatches_accelerators(monkeypatch):
    bad_instance_and_accs = [
        # Actual: V100
        ('p3.2xlarge', 'K80'),
        # Actual: None
        ('m4.2xlarge', 'V100'),
    ]
    for instance, acc in bad_instance_and_accs:
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            _test_resources_launch(monkeypatch,
                                   sky.AWS(),
                                   instance_type=instance,
                                   accelerators=acc)
        assert 'Infeasible resource demands found' in str(e.value)

    with pytest.raises(exceptions.ResourcesMismatchError) as e:
        _test_resources_launch(monkeypatch,
                               sky.GCP(),
                               instance_type='n2-standard-8',
                               accelerators={'V100': 1})
        assert 'can only be attached to N1 VMs,' in str(e.value), str(e.value)

    with pytest.raises(exceptions.ResourcesMismatchError) as e:
        _test_resources_launch(monkeypatch,
                               sky.GCP(),
                               instance_type='a2-highgpu-1g',
                               accelerators={'A100': 2})
        assert 'cannot be attached to' in str(e.value), str(e.value)

    with pytest.raises(exceptions.ResourcesMismatchError) as e:
        _test_resources_launch(monkeypatch,
                               sky.AWS(),
                               instance_type='p3.16xlarge',
                               accelerators={'V100': 1})
        assert 'Infeasible resource demands found' in str(e.value)


def test_instance_type_matches_accelerators(monkeypatch):
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='p3.2xlarge',
                           accelerators='V100')
    _test_resources_launch(monkeypatch,
                           sky.GCP(),
                           instance_type='n1-standard-2',
                           accelerators='V100')

    _test_resources_launch(monkeypatch,
                           sky.GCP(),
                           instance_type='n1-standard-8',
                           accelerators='tpu-v3-8')
    _test_resources_launch(monkeypatch,
                           sky.GCP(),
                           instance_type='a2-highgpu-1g',
                           accelerators='a100')

    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='p3.16xlarge',
                           accelerators={'V100': 8})


def test_invalid_instance_type(monkeypatch):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(monkeypatch, cloud, instance_type='invalid')
        assert 'Invalid instance type' in str(e.value)


def test_infer_cloud_from_instance_type(monkeypatch):
    # AWS instances
    _test_resources(monkeypatch,
                    cloud=sky.AWS(),
                    instance_type='m5.12xlarge',
                    expected_cloud=sky.AWS())
    _test_resources(monkeypatch,
                    instance_type='p3.8xlarge',
                    expected_cloud=sky.AWS())
    _test_resources(monkeypatch,
                    instance_type='g4dn.2xlarge',
                    expected_cloud=sky.AWS())
    # GCP instances
    _test_resources(monkeypatch,
                    instance_type='n1-standard-96',
                    expected_cloud=sky.GCP())
    #Azure instances
    _test_resources(monkeypatch,
                    instance_type='Standard_NC12s_v3',
                    expected_cloud=sky.Azure())


def test_invalid_cpus(monkeypatch):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(monkeypatch, cloud, cpus='invalid')
        assert '"cpus" field should be' in str(e.value)


def test_invalid_memory(monkeypatch):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(monkeypatch, cloud, memory='invalid')
        assert '"memory" field should be' in str(e.value)


def test_invalid_region(monkeypatch):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP()]:
        with pytest.raises(ValueError) as e:
            _test_resources(monkeypatch, cloud, region='invalid')
        assert 'Invalid region' in str(e.value)

    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _test_resources_launch(monkeypatch,
                               sky.GCP(),
                               region='us-west1',
                               accelerators='tpu-v3-8')
        assert 'No launchable resource found' in str(e.value)


def test_invalid_zone(monkeypatch):
    for cloud in [sky.AWS(), sky.GCP()]:
        with pytest.raises(ValueError) as e:
            _test_resources(monkeypatch, cloud, zone='invalid')
        assert 'Invalid zone' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch, sky.Azure(), zone='invalid')
    assert 'Azure does not support zones.' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch,
                        sky.AWS(),
                        region='us-east-1',
                        zone='us-east-2a')
    assert 'Invalid zone' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch,
                        sky.GCP(),
                        region='us-west2',
                        zone='us-west1-a')
    assert 'Invalid zone' in str(e.value)

    input_zone = 'us-central1'
    expected_candidates = [
        'us-central1-a', 'us-central1-b', 'us-central1-c', 'us-central1-f'
    ]
    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch, sky.GCP(), zone=input_zone)
    assert 'Invalid zone' in str(e.value)
    for cand in expected_candidates:
        assert cand in str(e.value)


def test_invalid_image(monkeypatch):
    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch,
                        cloud=sky.AWS(),
                        image_id='ami-0868a20f5a3bf9702')
    assert 'in a specific region' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch, image_id='ami-0868a20f5a3bf9702')
    assert 'Cloud must be specified' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(monkeypatch, cloud=sky.Azure(), image_id='some-image')
    assert 'only supported for AWS/GCP/IBM/OCI' in str(e.value)


def test_valid_image(monkeypatch):
    _test_resources(monkeypatch,
                    cloud=sky.AWS(),
                    region='us-east-1',
                    image_id='ami-0868a20f5a3bf9702')
    _test_resources(
        monkeypatch,
        cloud=sky.GCP(),
        region='us-central1',
        image_id=
        'projects/deeplearning-platform-release/global/images/family/common-cpu-v20230126'
    )
    _test_resources(
        monkeypatch,
        cloud=sky.GCP(),
        image_id=
        'projects/deeplearning-platform-release/global/images/family/common-cpu-v20230126'
    )


def test_parse_cpus_from_yaml():
    spec = textwrap.dedent("""\
        resources:
            cpus: 1""")
    _test_parse_cpus(spec, '1')

    spec = textwrap.dedent("""\
        resources:
            cpus: 1.5""")
    _test_parse_cpus(spec, '1.5')

    spec = textwrap.dedent("""\
        resources:
            cpus: '3+' """)
    _test_parse_cpus(spec, '3+')

    spec = textwrap.dedent("""\
        resources:
            cpus: 3+ """)
    _test_parse_cpus(spec, '3+')


def test_parse_memory_from_yaml():
    spec = textwrap.dedent("""\
        resources:
            memory: 32""")
    _test_parse_memory(spec, '32')

    spec = textwrap.dedent("""\
        resources:
            memory: 1.5""")
    _test_parse_memory(spec, '1.5')

    spec = textwrap.dedent("""\
        resources:
            memory: '3+' """)
    _test_parse_memory(spec, '3+')

    spec = textwrap.dedent("""\
        resources:
            memory: 3+ """)
    _test_parse_memory(spec, '3+')


def test_parse_accelerators_from_yaml():
    spec = textwrap.dedent("""\
      resources:
        accelerators: V100""")
    _test_parse_accelerators(spec, {'V100': 1})

    spec = textwrap.dedent("""\
      resources:
        accelerators: V100:4""")
    _test_parse_accelerators(spec, {'V100': 4})

    spec = textwrap.dedent("""\
      resources:
        accelerators: V100:0.5""")
    _test_parse_accelerators(spec, {'V100': 0.5})

    spec = textwrap.dedent("""\
      resources:
        accelerators: \"V100: 0.5\"""")
    _test_parse_accelerators(spec, {'V100': 0.5})

    # Invalid.
    spec = textwrap.dedent("""\
      resources:
        accelerators: \"V100: expected_a_float_here\"""")
    with pytest.raises(ValueError) as e:
        _test_parse_accelerators(spec, None)
        assert 'The "accelerators" field as a str ' in str(e.value)


def test_invalid_num_nodes():
    for invalid_value in (-1, 2.2, 1.0):
        with pytest.raises(ValueError) as e:
            with sky.Dag():
                task = sky.Task()
                task.num_nodes = invalid_value
            assert 'num_nodes should be a positive int' in str(e.value)


def test_parse_empty_yaml():
    spec = textwrap.dedent("""\
        """)

    def test_fn(task):
        assert task.num_nodes == 1

    _test_parse_task_yaml(spec, test_fn)


def test_parse_name_only_yaml():
    spec = textwrap.dedent("""\
        name: test_task
        """)

    def test_fn(task):
        assert task.name == 'test_task'

    _test_parse_task_yaml(spec, test_fn)


def test_parse_invalid_envs_yaml(monkeypatch):
    spec = textwrap.dedent("""\
        envs:
          hello world: 1  # invalid key
          123: val  # invalid key
          good_key: val
        """)
    with pytest.raises(ValueError) as e:
        _test_parse_task_yaml(spec)
    assert '\'123\', \'hello world\' do not match any of the regexes' in str(
        e.value)


def test_parse_valid_envs_yaml(monkeypatch):
    spec = textwrap.dedent("""\
        envs:
          hello_world: 1
          HELLO: val
          GOOD123: 123
        """)
    _test_parse_task_yaml(spec)


def test_invalid_accelerators_regions(enable_all_clouds, monkeypatch):
    task = sky.Task(run='echo hi')
    task.set_resources(
        sky.Resources(
            sky.AWS(),
            accelerators='A100:8',
            region='us-west-1',
        ))
    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        sky.launch(task, cluster_name='should-fail', dryrun=True)
        assert 'No launchable resource found for' in str(e.value), str(e.value)

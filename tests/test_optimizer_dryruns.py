import io
import sys
import tempfile
import textwrap
import time
from typing import Callable, Optional, Set

from click import testing as cli_testing
import pytest

import sky
from sky import clouds
from sky import exceptions
from sky import optimizer
from sky import skypilot_config
from sky.client.cli import command
from sky.utils import registry
from sky.utils import resources_utils


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


def _make_resources(
    *resources_args,
    **resources_kwargs,
):
    # Should create Resources here, since it uses the enabled clouds.
    return sky.Resources(*resources_args, **resources_kwargs)


def _test_resources(
    *resources_args,
    expected_cloud: clouds.Cloud = None,
    **resources_kwargs,
):
    """This function is testing for the core functions on server side."""
    resources = _make_resources(*resources_args, **resources_kwargs)
    resources.validate()
    if expected_cloud is not None:
        assert expected_cloud.is_same_cloud(resources.cloud)


def _test_resources_from_yaml(spec: str, cluster_name: str = None):
    resources = sky.Resources.from_yaml_config(spec)
    with sky.Dag() as dag:
        task = sky.Task('test_task')
        task.set_resources(resources)
    sky.stream_and_get(sky.launch(dag, dryrun=True, cluster_name=cluster_name))
    return resources


def _test_resources_launch(*resources_args,
                           cluster_name: str = None,
                           **resources_kwargs):
    """This function is testing for the core functions on client side."""
    resources = _make_resources(*resources_args, **resources_kwargs)
    resources.validate()
    with sky.Dag() as dag:
        task = sky.Task('test_task')
        task.set_resources({resources})
    sky.stream_and_get(sky.launch(dag, dryrun=True, cluster_name=cluster_name))
    assert True


def test_resources_aws(enable_all_clouds):
    _test_resources_launch(infra='aws', instance_type='p3.2xlarge')


def test_resources_azure(enable_all_clouds):
    _test_resources_launch(infra='azure', instance_type='Standard_NC24s_v3')


def test_resources_gcp(enable_all_clouds):
    _test_resources_launch(infra='gcp', instance_type='n1-standard-16')
    _test_resources_launch(infra='gcp', instance_type='a3-highgpu-8g')


def test_partial_cpus(enable_all_clouds):
    _test_resources_launch(cpus=4)
    _test_resources_launch(cpus='4')
    _test_resources_launch(cpus='7+')


def test_partial_memory(enable_all_clouds):
    _test_resources_launch(memory=32)
    _test_resources_launch(memory='32')
    _test_resources_launch(memory='32+')


def test_partial_k80(enable_all_clouds):
    _test_resources_launch(accelerators='K80')


def test_partial_m60(enable_all_clouds):
    _test_resources_launch(accelerators='M60')


def test_partial_p100(enable_all_clouds):
    _test_resources_launch(accelerators='P100')


def test_partial_t4(enable_all_clouds):
    _test_resources_launch(accelerators='T4')
    _test_resources_launch(accelerators={'T4': 8}, use_spot=True)


def test_partial_tpu(enable_all_clouds):
    _test_resources_launch(accelerators='tpu-v3-8')


def test_partial_v100(enable_all_clouds):
    _test_resources_launch(sky.AWS(), accelerators='V100')
    _test_resources_launch(sky.AWS(), accelerators='V100', use_spot=True)
    _test_resources_launch(sky.AWS(), accelerators={'V100': 8})


def test_invalid_cloud_tpu(enable_all_clouds):
    with pytest.raises(AssertionError) as e:
        _test_resources_launch(cloud=sky.AWS(), accelerators='tpu-v3-8')
    assert 'Cloud must be GCP' in str(e.value)


@pytest.mark.parametrize('enable_all_clouds',
                         [[sky.Azure(), sky.GCP()]],
                         indirect=True)
def test_clouds_not_enabled_aws(enable_all_clouds):
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(sky.AWS())


@pytest.mark.parametrize('enable_all_clouds', [[sky.AWS()]], indirect=True)
def test_clouds_not_enabled_azure(enable_all_clouds):
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(sky.Azure())


@pytest.mark.parametrize('enable_all_clouds', [[sky.AWS()]], indirect=True)
def test_clouds_not_enabled_gcp(enable_all_clouds):
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(sky.GCP())


def test_instance_type_mismatches_cpus(enable_all_clouds):
    bad_instance_and_cpus = [
        # Actual: 8
        ('m6i.2xlarge', 4),
        # Actual: 2
        ('c6i.large', 4),
    ]
    for instance, cpus in bad_instance_and_cpus:
        with pytest.raises(ValueError) as e:
            _test_resources_launch(sky.AWS(), instance_type=instance, cpus=cpus)
        assert 'does not have the requested number of vCPUs' in str(e.value)


def test_instance_type_mismatches_memory(enable_all_clouds):
    bad_instance_and_memory = [
        # Actual: 32
        ('m6i.2xlarge', 4),
        # Actual: 4
        ('c6i.large', 2),
    ]
    for instance, memory in bad_instance_and_memory:
        with pytest.raises(ValueError) as e:
            _test_resources_launch(sky.AWS(),
                                   instance_type=instance,
                                   memory=memory)
        assert 'does not have the requested memory' in str(e.value)


def test_instance_type_matches_cpus(enable_all_clouds):
    _test_resources_launch(sky.AWS(), instance_type='c6i.8xlarge', cpus=32)
    _test_resources_launch(sky.Azure(),
                           instance_type='Standard_E8s_v5',
                           cpus='8')
    _test_resources_launch(sky.GCP(), instance_type='n1-standard-8', cpus='7+')
    _test_resources_launch(sky.AWS(), instance_type='g4dn.2xlarge', cpus=8.0)


def test_instance_type_matches_memory(enable_all_clouds):
    _test_resources_launch(sky.AWS(), instance_type='c6i.8xlarge', memory=64)
    _test_resources_launch(sky.Azure(),
                           instance_type='Standard_E8s_v5',
                           memory='64')
    _test_resources_launch(sky.GCP(),
                           instance_type='n1-standard-8',
                           memory='30+')
    _test_resources_launch(sky.AWS(), instance_type='g4dn.2xlarge', memory=32)


def test_instance_type_from_cpu_memory(enable_all_clouds, capfd):
    _test_resources_launch(cpus=8)
    stdout, _ = capfd.readouterr()
    # Choose General Purpose instance types
    assert 'm6i.2xlarge' in stdout  # AWS, 8 vCPUs, 32 GB memory
    assert 'Standard_D8s_v5' in stdout  # Azure, 8 vCPUs, 32 GB memory
    assert 'n4-standard-8' in stdout  # GCP, 8 vCPUs, 32 GB memory

    _test_resources_launch(memory=32)
    stdout, _ = capfd.readouterr()
    # Choose memory-optimized instance types, when the memory
    # is specified
    assert 'r6i.xlarge' in stdout  # AWS, 4 vCPUs, 32 GB memory
    assert 'Standard_E4s_v5' in stdout  # Azure, 4 vCPUs, 32 GB memory
    assert 'n4-highmem-4' in stdout  # GCP, 4 vCPUs, 32 GB memory

    _test_resources_launch(memory='64+')
    stdout, _ = capfd.readouterr()
    # Choose memory-optimized instance types
    assert 'r6i.2xlarge' in stdout  # AWS, 8 vCPUs, 64 GB memory
    assert 'Standard_E8s_v5' in stdout  # Azure, 8 vCPUs, 64 GB memory
    assert 'n4-highmem-8' in stdout  # GCP, 8 vCPUs, 64 GB memory
    assert 'gpu_1x_a10' in stdout  # Lambda, 30 vCPUs, 200 GB memory

    _test_resources_launch(cpus='4+', memory='4+')
    stdout, _ = capfd.readouterr()
    # Choose compute-optimized instance types, when the memory
    # requirement is less than the memory of General Purpose
    # instance types.
    assert 'n2-highcpu-4' in stdout  # GCP, 4 vCPUs, 4 GB memory
    assert 'c6i.xlarge' in stdout  # AWS, 4 vCPUs, 8 GB memory
    assert 'Standard_F4s_v2' in stdout  # Azure, 4 vCPUs, 8 GB memory
    assert 'cpu_4x_general' in stdout  # Lambda, 4 vCPUs, 16 GB memory

    _test_resources_launch(accelerators='T4')
    stdout, _ = capfd.readouterr()
    # Choose cheapest T4 instance type
    assert 'g4dn.xlarge' in stdout  # AWS, 4 vCPUs, 16 GB memory, 1 T4 GPU
    assert 'Standard_NC4as_T4_v3' in stdout  # Azure, 4 vCPUs, 28 GB memory, 1 T4 GPU
    assert 'n1-highmem-4' in stdout  # GCP, 4 vCPUs, 26 GB memory, 1 T4 GPU

    _test_resources_launch(cpus='16+', memory='32+', accelerators='T4')
    stdout, _ = capfd.readouterr()
    # Choose cheapest T4 instance type that satisfies the requirement
    assert 'n1-standard-16' in stdout  # GCP, 16 vCPUs, 60 GB memory, 1 T4 GPU
    assert 'g4dn.4xlarge' in stdout  # AWS, 16 vCPUs, 64 GB memory, 1 T4 GPU
    assert 'Standard_NC16as_T4_v3' in stdout  # Azure, 16 vCPUs, 110 GB memory, 1 T4 GPU

    _test_resources_launch(memory='200+', accelerators='T4')
    stdout, _ = capfd.readouterr()
    # Choose cheapest T4 instance type that satisfies the requirement
    assert 'n1-highmem-32' in stdout  # GCP, 32 vCPUs, 208 GB memory, 1 T4 GPU
    assert 'g4dn.16xlarge' in stdout  # AWS, 64 vCPUs, 256 GB memory, 1 T4 GPU
    assert 'Azure' not in stdout  # Azure does not have a 1 T4 GPU instance type with 200+ GB memory


def test_instance_type_mistmatches_accelerators(enable_all_clouds):
    bad_instance_and_accs = [
        # Actual: V100
        ('p3.2xlarge', 'K80'),
        # Actual: None
        ('m4.2xlarge', 'V100'),
    ]
    for instance, acc in bad_instance_and_accs:
        with pytest.raises(exceptions.ResourcesMismatchError) as e:
            _test_resources_launch(sky.AWS(),
                                   instance_type=instance,
                                   accelerators=acc)
        assert 'Infeasible resource demands found' in str(e.value)

    with pytest.raises(exceptions.ResourcesMismatchError) as e:
        _test_resources_launch(sky.GCP(),
                               instance_type='n2-standard-8',
                               accelerators={'V100': 1})
        assert 'can only be attached to N1 VMs,' in str(e.value), str(e.value)

    with pytest.raises(exceptions.ResourcesMismatchError) as e:
        _test_resources_launch(sky.GCP(),
                               instance_type='a2-highgpu-1g',
                               accelerators={'A100': 2})
        assert 'cannot be attached to' in str(e.value), str(e.value)

    with pytest.raises(exceptions.ResourcesMismatchError) as e:
        _test_resources_launch(sky.AWS(),
                               instance_type='p3.16xlarge',
                               accelerators={'V100': 1})
        assert 'Infeasible resource demands found' in str(e.value)


def test_instance_type_matches_accelerators(enable_all_clouds):
    _test_resources_launch(sky.AWS(),
                           instance_type='p3.2xlarge',
                           accelerators='V100')
    _test_resources_launch(sky.GCP(),
                           instance_type='n1-standard-2',
                           accelerators='V100')

    _test_resources_launch(sky.GCP(),
                           instance_type='n1-standard-8',
                           accelerators='tpu-v3-8',
                           accelerator_args={'tpu_vm': False})
    _test_resources_launch(sky.GCP(),
                           instance_type='a2-highgpu-1g',
                           accelerators='a100')
    _test_resources_launch(sky.GCP(),
                           instance_type='a3-highgpu-8g',
                           accelerators={'H100': 8})

    _test_resources_launch(sky.AWS(),
                           instance_type='p3.16xlarge',
                           accelerators={'V100': 8})


def test_invalid_instance_type(enable_all_clouds):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(cloud, instance_type='invalid')
        assert 'Invalid instance type' in str(e.value)


def test_infer_cloud_from_instance_type(enable_all_clouds):
    # AWS instances
    _test_resources(instance_type='m5.12xlarge', expected_cloud=sky.AWS())
    _test_resources_launch(instance_type='m5.12xlarge')
    _test_resources(instance_type='p3.8xlarge', expected_cloud=sky.AWS())
    _test_resources_launch(instance_type='p3.8xlarge')
    _test_resources(instance_type='g4dn.2xlarge', expected_cloud=sky.AWS())
    _test_resources_launch(instance_type='g4dn.2xlarge')
    # GCP instances
    _test_resources(instance_type='n1-standard-96', expected_cloud=sky.GCP())
    _test_resources_launch(instance_type='n1-standard-96')
    #Azure instances
    _test_resources(instance_type='Standard_NC12s_v3',
                    expected_cloud=sky.Azure())
    _test_resources_launch(instance_type='Standard_NC12s_v3')


def test_invalid_cpus(enable_all_clouds):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(cloud, cpus='invalid')
        assert '"cpus" field should be' in str(e.value)


def test_invalid_memory(enable_all_clouds):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(cloud, memory='invalid')
        assert '"memory" field should be' in str(e.value)


def test_invalid_region(enable_all_clouds):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP()]:
        with pytest.raises(ValueError) as e:
            _test_resources(cloud, region='invalid')
        assert 'Invalid region' in str(e.value)

    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        _test_resources_launch(sky.GCP(),
                               region='us-west1',
                               accelerators='tpu-v3-8')
        assert 'No launchable resource found' in str(e.value)


def test_invalid_zone(enable_all_clouds):
    for cloud in [sky.AWS(), sky.GCP()]:
        with pytest.raises(ValueError) as e:
            _test_resources(cloud, zone='invalid')
        assert 'Invalid zone' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(sky.Azure(), zone='invalid')
    assert 'Azure does not support zones.' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(sky.AWS(), region='us-east-1', zone='us-east-2a')
    assert 'Invalid zone' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(sky.GCP(), region='us-west2', zone='us-west1-a')
    assert 'Invalid zone' in str(e.value)

    input_zone = 'us-central1'
    expected_candidates = [
        'us-central1-a', 'us-central1-b', 'us-central1-c', 'us-central1-f'
    ]
    with pytest.raises(ValueError) as e:
        _test_resources(sky.GCP(), zone=input_zone)
    assert 'Invalid zone' in str(e.value)
    for cand in expected_candidates:
        assert cand in str(e.value)


def test_invalid_image(enable_all_clouds):
    with pytest.raises(ValueError) as e:
        _test_resources(cloud=sky.AWS(), image_id='ami-0868a20f5a3bf9702')
    assert 'in a specific region' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(image_id='ami-0868a20f5a3bf9702')
    assert 'Cloud must be specified' in str(e.value)

    with pytest.raises(ValueError) as e:
        _test_resources(cloud=sky.Lambda(), image_id='some-image')
    assert 'Lambda cloud only supports Docker images' in str(e.value)


def test_valid_image(enable_all_clouds):
    _test_resources(infra='aws/us-east-1', image_id='ami-0868a20f5a3bf9702')
    _test_resources(
        infra='gcp/us-central1',
        image_id=
        'projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20240927')
    _test_resources(
        infra='gcp',
        image_id=
        'projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20240927')


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

    spec = textwrap.dedent("""\
      resources:
        accelerators: {V100: 0.5}""")
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


def test_parse_invalid_envs_yaml():
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


def test_parse_valid_envs_yaml():
    spec = textwrap.dedent("""\
        envs:
          hello_world: 1
          HELLO: val
          GOOD123: 123
        """)
    _test_parse_task_yaml(spec)


def test_invalid_accelerators_regions(enable_all_clouds):
    task = sky.Task(run='echo hi')
    task.set_resources(
        sky.Resources(
            infra='aws/us-west-1',
            accelerators='A100:8',
        ))
    with pytest.raises(exceptions.ResourcesUnavailableError) as e:
        sky.stream_and_get(
            sky.launch(task, cluster_name='should-fail', dryrun=True))
        assert 'No launchable resource found for' in str(e.value), str(e.value)


def test_infer_cloud_from_region_or_zone(enable_all_clouds):
    # Maps to GCP.
    _test_resources_launch(region='us-east1')
    _test_resources_launch(zone='us-west2-a')

    # Maps to AWS.
    # Not use us-east-2 or us-west-1 as it is also supported by Lambda.
    _test_resources_launch(region='eu-south-1')
    _test_resources_launch(zone='us-west-2a')

    # `sky launch`
    _test_resources_launch()

    # Same-named regions need `cloud`.
    _test_resources_launch(region='us-east-1', cloud=sky.AWS())
    _test_resources_launch(region='us-east-1', cloud=sky.Lambda())

    # Cases below: cannot infer cloud.

    # Same-named region: AWS and Lambda.
    with pytest.raises(ValueError) as e:
        _test_resources_launch(region='us-east-1')
    assert ('Multiple enabled clouds have region/zone of the same names'
            in str(e))

    # Typo, fuzzy hint.
    with pytest.raises(ValueError) as e:
        _test_resources_launch(zone='us-west-2-a', cloud=sky.AWS())
    assert ('Did you mean one of these: \'us-west-2a\'?' in str(e.value))

    # Detailed hints.
    # ValueError: Invalid (region None, zone 'us-west-2-a') for any cloud among
    # [AWS, Azure, GCP, IBM, Lambda, OCI, SCP]. Details:
    # Cloud   Hint
    # -----   ----
    # AWS     Invalid zone 'us-west-2-a' Did you mean one of these: 'us-west-2a'?
    # Azure   Azure does not support zones.
    # GCP     Invalid zone 'us-west-2-a' Did you mean one of these: 'us-west2-a'?
    # IBM     Invalid zone 'us-west-2-a'
    # Lambda  Lambda Cloud does not support zones.
    # OCI     Invalid zone 'us-west-2-a'
    # SCP     SCP Cloud does not support zones.
    with pytest.raises(ValueError) as e:
        _test_resources_launch(zone='us-west-2-a')
    assert ('Invalid (region None, zone \'us-west-2-a\') for any cloud among'
            in str(e.value))

    with pytest.raises(ValueError) as e:
        _test_resources_launch(zone='us-west-2z')
    assert ('Invalid (region None, zone \'us-west-2z\') for any cloud among'
            in str(e.value))

    with pytest.raises(ValueError) as e:
        _test_resources_launch(region='us-east1', zone='us-west2-a')
    assert (
        'Invalid (region \'us-east1\', zone \'us-west2-a\') for any cloud among'
        in str(e.value))


def test_ordered_resources(enable_all_clouds):
    captured_output = io.StringIO()
    # Add fileno() method to avoid issues with executor trying to duplicate fds
    captured_output.fileno = lambda: 1
    original_stdout = sys.stdout
    try:
        sys.stdout = captured_output  # Redirect stdout to the StringIO object

        with sky.Dag() as dag:
            task = sky.Task('test_task')
            task.set_resources([
                sky.Resources(accelerators={'V100': 1}),
                sky.Resources(accelerators={'T4': 1}),
                sky.Resources(accelerators={'K80': 1}),
                sky.Resources(accelerators={'T4': 4}),
            ])
        dag = sky.optimize(dag)
        cli_runner = cli_testing.CliRunner()
        request_id = sky.launch(task, dryrun=True)
        result = cli_runner.invoke(command.api_logs, [request_id])
        assert not result.exit_code

        # Access the captured output
        output = captured_output.getvalue()
        assert any('V100' in line and '✔' in line for line in output.splitlines()), \
            'Expected to find a line with V100 and ✔ indicating V100 was chosen'
    finally:
        sys.stdout = original_stdout  # Restore original stdout


def test_disk_tier_mismatch(enable_all_clouds):
    for cloud in registry.CLOUD_REGISTRY.values():
        for tier in cloud._SUPPORTED_DISK_TIERS:
            sky.Resources(cloud=cloud, disk_tier=tier)
        for unsupported_tier in (set(resources_utils.DiskTier) -
                                 cloud._SUPPORTED_DISK_TIERS):
            with pytest.raises(ValueError) as e:
                resource = sky.Resources(cloud=cloud,
                                         disk_tier=unsupported_tier)
                resource.validate()
            assert f'is not supported' in str(e.value), str(e.value)


def test_optimize_disk_tier(enable_all_clouds):

    def _get_all_candidate_cloud(r: sky.Resources) -> Set[clouds.Cloud]:
        task = sky.Task()
        task.set_resources(r)
        _, per_cloud_candidates, _, _ = optimizer._fill_in_launchable_resources(
            task, blocked_resources=None)
        return set(per_cloud_candidates.keys())

    # All cloud supports BEST disk tier.
    best_tier_resources = sky.Resources(disk_tier=resources_utils.DiskTier.BEST)
    best_tier_candidates = _get_all_candidate_cloud(best_tier_resources)
    assert best_tier_candidates == set(
        registry.CLOUD_REGISTRY.values()), best_tier_candidates

    # Only AWS, GCP, Azure, OCI supports LOW disk tier.
    low_tier_resources = sky.Resources(disk_tier=resources_utils.DiskTier.LOW)
    low_tier_candidates = _get_all_candidate_cloud(low_tier_resources)
    assert low_tier_candidates == set(
        map(registry.CLOUD_REGISTRY.get,
            ['aws', 'gcp', 'azure', 'oci'])), low_tier_candidates

    # Only AWS, GCP, Azure, OCI supports HIGH disk tier.
    high_tier_resources = sky.Resources(disk_tier=resources_utils.DiskTier.HIGH)
    high_tier_candidates = _get_all_candidate_cloud(high_tier_resources)
    assert high_tier_candidates == set(
        map(registry.CLOUD_REGISTRY.get,
            ['aws', 'gcp', 'azure', 'oci'])), high_tier_candidates

    # Only AWS, GCP supports ULTRA disk tier.
    ultra_tier_resources = sky.Resources(
        disk_tier=resources_utils.DiskTier.ULTRA)
    ultra_tier_candidates = _get_all_candidate_cloud(ultra_tier_resources)
    assert ultra_tier_candidates == set(
        map(registry.CLOUD_REGISTRY.get, ['aws', 'gcp'])), ultra_tier_candidates


def test_launch_dryrun_with_reservations_and_dummy_sink(enable_all_clouds):
    """
    Tests that 'sky launch --dryrun' with reservations configured
    does not raise an AssertionError when Optimizer processes DummySink.
    Simulates 'sky launch --gpus A100:8 --cloud aws --dryrun'
    """
    override_config_dict = {
        'aws': {
            'specific_reservations': ['cr-xxx', 'cr-yyy']
        }
    }
    runner = cli_testing.CliRunner()
    with skypilot_config.override_skypilot_config(override_config_dict):
        # Ensure AWS is treated as an enabled cloud. The enable_all_clouds
        # fixture should typically handle this.
        result = runner.invoke(
            command.launch, ['--cloud', 'aws', '--gpus', 'A100:8', '--dryrun'])

        # Check for a successful dryrun invocation
        if result.exit_code != 0:
            pytest.fail(f"'sky launch --dryrun' failed with exit code "
                        f"{result.exit_code}.\nOutput:\n{result.output}\n"
                        f"Exception Info: {result.exc_info}")

    # If no exception is raised and exit code is 0, the test passes.


def test_resource_hints_for_invalid_resources(capfd, enable_all_clouds):
    """Tests that helpful hints are shown when no matching resources are found."""
    # Test when there are no fuzzy candidates
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(cloud=sky.AWS(), cpus=111, memory=999)
    stdout, _ = capfd.readouterr()
    assert 'Try specifying a different CPU count' in stdout
    assert 'add "+" to the end of the CPU count' in stdout
    assert 'Try specifying a different memory size' in stdout
    assert 'add "+" to the end of the memory size' in stdout
    assert 'Did you mean:' not in stdout  # No fuzzy candidates


def test_accelerator_memory_filtering(capfd, enable_all_clouds):
    """Test filtering accelerators by memory requirements."""
    # Test exact memory match
    spec = {'accelerators': '16GB'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()
    assert 'T4' in stdout  # T4 has 16GB memory

    # Test memory with plus (greater than or equal)
    spec = {'accelerators': '32GB+'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()
    assert 'V100' in stdout  # V100 has 32GB memory
    assert 'A100' in stdout  # A100 has 40GB/80GB memory
    assert 'T4' not in stdout  # T4 has 16GB memory

    spec = {'accelerators': ['32GB+']}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()
    assert 'V100' in stdout  # V100 has 32GB memory
    assert 'A100' in stdout  # A100 has 40GB/80GB memory
    assert 'T4' not in stdout  # T4 has 16GB memory

    _test_resources_from_yaml({'accelerators': {'32GB+': 1, 'T4': 1}})
    stdout, _ = capfd.readouterr()
    assert 'V100' in stdout  # V100 has 32GB memory
    assert 'A100' in stdout  # A100 has 40GB/80GB memory
    assert 'T4' in stdout  # T4 has 16GB memory

    _test_resources_from_yaml({'accelerators': ['32GB+', 'T4']})
    stdout, _ = capfd.readouterr()
    assert 'V100' in stdout  # V100 has 32GB memory
    assert 'A100' in stdout  # A100 has 40GB/80GB memory
    assert 'T4' in stdout  # T4 has 16GB memory

    _test_resources_from_yaml({'accelerators': ['32GB+', '16gb']})
    stdout, _ = capfd.readouterr()
    assert 'V100' in stdout  # V100 has 32GB memory
    assert 'A100' in stdout  # A100 has 40GB/80GB memory
    assert 'T4' in stdout  # T4 has 16GB memory

    # Test memory with different units
    spec = {'accelerators': '16384MB'}  # 16GB in MB
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()
    assert 'T4' in stdout


def test_accelerator_manufacturer_filtering(capfd, enable_all_clouds):
    """Test filtering accelerators by manufacturer."""
    # Test NVIDIA GPUs
    spec = {'accelerators': 'nvidia:16GB:1'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()
    assert 'T4' in stdout

    # Test with memory plus
    spec = {'accelerators': 'nvidia:32GB+'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()
    assert 'V100' in stdout
    assert 'A100' in stdout
    assert 'T4' not in stdout


def test_accelerator_cloud_filtering(capfd, enable_all_clouds):
    """Test filtering accelerators by cloud provider."""
    # Test AWS GPUs
    spec = {'accelerators': '16GB'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()

    # Test Azure GPUs
    spec = {'accelerators': '16GB'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()

    # Test with manufacturer and memory
    spec = {'accelerators': 'nvidia:32GB+'}
    _test_resources_from_yaml(spec)
    stdout, _ = capfd.readouterr()


def test_candidate_logging(enable_all_clouds, capfd):
    """
    Verifies that the optimizer candidate log outputs the correct chosen/cheapest resource.
    """
    with sky.Dag() as dag:
        task = sky.Task('test_candidate_logging')
        task.set_resources([
            sky.Resources(accelerators={'L4': 1},
                          use_spot=True,
                          cloud=sky.AWS()),
            # Other more expensive GPUs
            sky.Resources(accelerators={'A100': 1}, use_spot=True),
            sky.Resources(accelerators={'H100': 1}, use_spot=True),
            sky.Resources(accelerators={'H200': 1}, use_spot=True),
        ])
    sky.optimize(dag)
    sky.stream_and_get(sky.launch(dag, dryrun=True))
    stdout, _ = capfd.readouterr()
    l4_section = any(
        'L4:1' in line and '✔' in line for line in stdout.splitlines())
    assert l4_section, 'Expected L4:1 to be chosen.'
    assert f'Multiple {sky.AWS()} instances satisfy L4:1. The cheapest [spot](gpus=L4:1' in stdout, 'Expected L4:1 to be marked as cheapest.'

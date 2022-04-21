import tempfile
import textwrap
from typing import List

import pytest

import sky
from sky import clouds
from sky import exceptions


def _test_parse_accelerators(spec, expected_accelerators):
    with tempfile.NamedTemporaryFile('w') as f:
        f.write(spec)
        f.flush()
        with sky.Dag():
            task = sky.Task.from_yaml(f.name)
            assert list(task.resources)[0].accelerators == expected_accelerators


# Monkey-patching is required because in the test environment, no cloud is
# enabled. The optimizer checks the environment to find enabled clouds, and
# only generates plans within these clouds. The tests assume that all three
# clouds are enabled, so we monkeypatch the `sky.global_user_state` module
# to return all three clouds. We also monkeypatch `sky.check.check` so that
# when the optimizer tries calling it to update enabled_clouds, it does not
# raise SystemExit.
def _make_resources(
    monkeypatch,
    *resources_args,
    enabled_clouds: List[str] = None,
    **resources_kwargs,
):
    if enabled_clouds is None:
        enabled_clouds = list(clouds.Cloud.CLOUD_REGISTRY.values())
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: enabled_clouds,
    )
    monkeypatch.setattr('sky.check.check', lambda *_args, **_kwargs: None)
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
    _test_resources_launch(monkeypatch, clouds.AWS(), 'p3.2xlarge')


def test_resources_azure(monkeypatch):
    _test_resources_launch(monkeypatch, clouds.Azure(), 'Standard_NC24s_v3')


def test_resources_gcp(monkeypatch):
    _test_resources_launch(monkeypatch, clouds.GCP(), 'n1-standard-16')


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
                               clouds.AWS(),
                               enabled_clouds=[
                                   clouds.Azure(),
                                   clouds.GCP(),
                               ])

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(monkeypatch,
                               clouds.Azure(),
                               enabled_clouds=[clouds.AWS()])

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources_launch(monkeypatch,
                               clouds.GCP(),
                               enabled_clouds=[clouds.AWS()])


def test_instance_type_mistmatches_accelerators(monkeypatch):
    bad_instance_and_accs = [
        # Actual: V100
        ('p3.2xlarge', 'K80'),
        # Actual: None
        ('m4.2xlarge', 'V100'),
    ]
    for instance, acc in bad_instance_and_accs:
        with pytest.raises(ValueError) as e:
            _test_resources_launch(monkeypatch,
                                   sky.AWS(),
                                   instance_type=instance,
                                   accelerators=acc)
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
    # Partial use: Instance has 8 V100s, while the task needs 1 of them.
    _test_resources_launch(monkeypatch,
                           sky.AWS(),
                           instance_type='p3.16xlarge',
                           accelerators={'V100': 1})


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


def test_invalid_region(monkeypatch):
    for cloud in [sky.AWS(), sky.Azure(), sky.GCP(), None]:
        with pytest.raises(ValueError) as e:
            _test_resources(monkeypatch, cloud, region='invalid')
        assert 'Invalid region' in str(e.value)


def test_infer_cloud_from_region(monkeypatch):
    # AWS regions
    _test_resources(monkeypatch, region='us-east-1', expected_cloud=sky.AWS())
    _test_resources(monkeypatch, region='us-west-2', expected_cloud=sky.AWS())
    _test_resources(monkeypatch, region='us-west-1', expected_cloud=sky.AWS())
    # GCP regions
    _test_resources(monkeypatch, region='us-east1', expected_cloud=sky.GCP())
    _test_resources(monkeypatch, region='us-west1', expected_cloud=sky.GCP())
    #Azure regions
    _test_resources(monkeypatch, region='westus', expected_cloud=sky.Azure())
    _test_resources(monkeypatch,
                    cloud=sky.Azure(),
                    region='northcentralus',
                    expected_cloud=sky.Azure())


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

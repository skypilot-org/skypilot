import pytest
import tempfile
import textwrap

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
# to return all three clouds. We also monkeypatch `sky.init.init` so that
# when the optimizer tries calling it to update enabled_clouds, it does not
# raise SystemExit.
def _test_resources(monkeypatch, resources, enabled_clouds=None):
    if enabled_clouds is None:
        enabled_clouds = list(clouds.CLOUD_REGISTRY.values())
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: enabled_clouds,
    )
    monkeypatch.setattr('sky.check.check', lambda *_args, **_kwargs: None)
    with sky.Dag() as dag:
        task = sky.Task('test_task')
        task.set_resources({resources})
    sky.launch(dag, dryrun=True)
    assert True


def test_resources_aws(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(clouds.AWS(), 'p3.2xlarge'))


def test_resources_azure(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(clouds.Azure(), 'Standard_NC24s_v3'))


def test_resources_gcp(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(clouds.GCP(), 'n1-standard-16'))


def test_partial_k80(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='K80'))


def test_partial_m60(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='M60'))


def test_partial_p100(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='P100'))


def test_partial_t4(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='T4'))
    _test_resources(monkeypatch, sky.Resources(accelerators={'T4': 8}, use_spot=True))


def test_partial_tpu(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(accelerators='tpu-v3-8'))


def test_partial_v100(monkeypatch):
    _test_resources(monkeypatch, sky.Resources(sky.AWS(), accelerators='V100'))
    _test_resources(
        monkeypatch, sky.Resources(sky.AWS(), accelerators='V100', use_spot=True)
    )
    _test_resources(monkeypatch, sky.Resources(sky.AWS(), accelerators={'V100': 8}))


def test_invalid_cloud_tpu(monkeypatch):
    with pytest.raises(AssertionError) as e:
        _test_resources(
            monkeypatch, sky.Resources(cloud=sky.AWS(), accelerators='tpu-v3-8')
        )
    assert 'Cloud must be GCP' in str(e.value)


def test_clouds_not_enabled(monkeypatch):
    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources(
            monkeypatch,
            sky.Resources(clouds.AWS()),
            enabled_clouds=[
                clouds.Azure(),
                clouds.GCP(),
            ],
        )

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources(
            monkeypatch, sky.Resources(clouds.Azure()), enabled_clouds=[clouds.AWS()]
        )

    with pytest.raises(exceptions.ResourcesUnavailableError):
        _test_resources(
            monkeypatch, sky.Resources(clouds.GCP()), enabled_clouds=[clouds.AWS()]
        )


def test_instance_type_mistmatches_accelerators(monkeypatch):
    bad_instance_and_accs = [
        # Actual: V100
        ('p3.2xlarge', 'K80'),
        # Actual: None
        ('m4.2xlarge', 'V100'),
    ]
    for instance, acc in bad_instance_and_accs:
        with pytest.raises(ValueError) as e:
            _test_resources(
                monkeypatch,
                sky.Resources(sky.AWS(), instance_type=instance, accelerators=acc),
            )
        assert 'Infeasible resource demands found' in str(e.value)


def test_instance_type_matches_accelerators(monkeypatch):
    _test_resources(
        monkeypatch,
        sky.Resources(sky.AWS(), instance_type='p3.2xlarge', accelerators='V100'),
    )
    _test_resources(
        monkeypatch,
        sky.Resources(sky.GCP(), instance_type='n1-standard-2', accelerators='V100'),
    )
    # Partial use: Instance has 8 V100s, while the task needs 1 of them.
    _test_resources(
        monkeypatch,
        sky.Resources(sky.AWS(), instance_type='p3.16xlarge', accelerators={'V100': 1}),
    )


def test_parse_accelerators_from_yaml():
    spec = textwrap.dedent(
        """\
      resources:
        accelerators: V100"""
    )
    _test_parse_accelerators(spec, {'V100': 1})

    spec = textwrap.dedent(
        """\
      resources:
        accelerators: V100:4"""
    )
    _test_parse_accelerators(spec, {'V100': 4})

    spec = textwrap.dedent(
        """\
      resources:
        accelerators: V100:0.5"""
    )
    _test_parse_accelerators(spec, {'V100': 0.5})

    spec = textwrap.dedent(
        """\
      resources:
        accelerators: \"V100: 0.5\""""
    )
    _test_parse_accelerators(spec, {'V100': 0.5})

    # Invalid.
    spec = textwrap.dedent(
        """\
      resources:
        accelerators: \"V100: expected_a_float_here\""""
    )
    with pytest.raises(ValueError) as e:
        _test_parse_accelerators(spec, None)
        assert 'The "accelerators" field as a str ' in str(e.value)

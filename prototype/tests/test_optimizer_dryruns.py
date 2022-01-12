import pytest

import sky


def _test_resources(resources):
    with sky.Dag() as dag:
        train = sky.Task('train')
        train.set_resources({resources})
    sky.launch(dag, dryrun=True)


def test_resources_aws():
    _test_resources(sky.Resources(sky.AWS(), 'p3.2xlarge'))


def test_resources_azure():
    _test_resources(sky.Resources(sky.Azure(), 'Standard_NC24s_v3'))


def test_resources_gcp():
    _test_resources(sky.Resources(sky.GCP(), 'n1-standard-16'))


def test_partial_k80():
    _test_resources(sky.Resources(accelerators='K80'))


def test_partial_m60():
    _test_resources(sky.Resources(accelerators='M60'))


def test_partial_p100():
    _test_resources(sky.Resources(accelerators='P100'))


def test_partial_t4():
    _test_resources(sky.Resources(accelerators='T4'))
    _test_resources(sky.Resources(accelerators={'T4': 8}, use_spot=True))


def test_partial_tpu():
    _test_resources(sky.Resources(accelerators='tpu-v3-8'))


def test_partial_v100():
    _test_resources(sky.Resources(sky.AWS(), accelerators='V100'))
    _test_resources(sky.Resources(sky.AWS(), accelerators='V100',
                                  use_spot=True))
    _test_resources(sky.Resources(sky.AWS(), accelerators={'V100': 8}))


def test_instance_type_mistmatches_accelerators():
    bad_instance_and_accs = [
        # Actual: V100
        ('p3.2xlarge', 'K80'),
        # Actual: None
        ('m4.2xlarge', 'V100'),
    ]
    for instance, acc in bad_instance_and_accs:
        with pytest.raises(ValueError) as e:
            _test_resources(
                sky.Resources(sky.AWS(),
                              instance_type=instance,
                              accelerators=acc))
        assert 'Infeasible resource demands found' in str(e.value)


def test_instance_type_matches_accelerators():
    _test_resources(
        sky.Resources(sky.AWS(),
                      instance_type='p3.2xlarge',
                      accelerators='V100'))
    _test_resources(
        sky.Resources(sky.GCP(),
                      instance_type='n1-standard-2',
                      accelerators='V100'))
    # Partial use: Instance has 8 V100s, while the task needs 1 of them.
    _test_resources(
        sky.Resources(sky.AWS(),
                      instance_type='p3.16xlarge',
                      accelerators={'V100': 1}))

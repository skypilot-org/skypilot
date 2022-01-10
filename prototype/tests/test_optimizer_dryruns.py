import pytest

import sky
from sky import clouds


def _test_resources(resources):
    with sky.Dag() as dag:
        train = sky.Task('train')
        train.set_resources({resources})
    sky.run(dag, dryrun=True)
    assert True


def test_resources_aws():
    _test_resources(sky.Resources(clouds.AWS(), 'p3.2xlarge'))


def test_resources_azure():
    _test_resources(sky.Resources(clouds.Azure(), 'Standard_NC24s_v3'))


def test_resources_gcp():
    _test_resources(sky.Resources(clouds.GCP(), 'n1-standard-16'))


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
    _test_resources(sky.Resources(clouds.AWS(), accelerators='V100'))
    _test_resources(
        sky.Resources(clouds.AWS(), accelerators='V100', use_spot=True))
    _test_resources(sky.Resources(clouds.AWS(), accelerators={'V100': 8}))

import pytest

import sky
from sky import clouds


def _test_resources(resources):
    with sky.Dag() as dag:
        train = sky.Task('train')
        train.set_resources({resources})
    dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
    sky.execute(dag, dryrun=True)
    assert True


def test_resources_aws():
    _test_resources(sky.Resources(clouds.AWS(), 'p3.2xlarge'))


def test_resources_gcp():
    _test_resources(sky.Resources(clouds.GCP(), 'n1-standard-16'))


@pytest.mark.skip(reason='TODO: need to fix service catalog')
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

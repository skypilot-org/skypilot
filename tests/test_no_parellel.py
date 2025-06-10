import time

import sky
from sky.utils import registry


def _test_optimize_speed(resources: sky.Resources):
    with sky.Dag() as dag:
        task = sky.Task(run='echo hi')
        task.set_resources(resources)
    start = time.time()
    sky.optimize(dag)
    end = time.time()
    # 5.0 seconds = somewhat flaky.
    assert end - start < 6.0, (f'optimize took too long for {resources}, '
                               f'{end - start} seconds')


def test_optimize_speed(enable_all_clouds):
    _test_optimize_speed(sky.Resources(cpus=4))
    for cloud in registry.CLOUD_REGISTRY.values():
        _test_optimize_speed(sky.Resources(infra=str(cloud), cpus='4+'))
    _test_optimize_speed(sky.Resources(cpus='4+', memory='4+'))
    _test_optimize_speed(
        sky.Resources(cpus='4+', memory='4+', accelerators='V100:1'))
    _test_optimize_speed(
        sky.Resources(cpus='4+', memory='4+', accelerators='A100-80GB:8'))
    _test_optimize_speed(
        sky.Resources(cpus='4+', memory='4+', accelerators='tpu-v3-32'))

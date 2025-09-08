import pytest
from smoke_tests.smoke_tests_utils import get_cluster_name, BenchmarkTest, run_one_benchmark, LOW_RESOURCE_ARG
from pytest_benchmark.fixture import BenchmarkFixture

import sky
# from sky import skypilot_config
# from sky.skylet import constants
# from sky.skylet import events
# from sky.utils import common_utils
# from sky.utils import yaml_utils
from sky.client import sdk
import time

# def test_api_launch(benchmark: BenchmarkFixture, generic_cloud: str):
#     name = get_cluster_name()
#     test = BenchmarkTest(
#         'api_launch',
#         setup_command=None,
#         benchmarked_command=[f'sky launch -y -c {name} {LOW_RESOURCE_ARG} examples/minimal.yaml'],
#         teardown=f'sky down -y {name}',
#     )
#     run_one_benchmark(test, benchmark)

# def test_api_down(benchmark: BenchmarkFixture, generic_cloud: str):
#     name = get_cluster_name()
#     test = BenchmarkTest(
#         'api_down',
#         setup_command=f'sky launch -y -c {name} {LOW_RESOURCE_ARG} examples/minimal.yaml',
#         benchmarked_command=[f'sky down -y {name}'],
#         teardown=f'sky down -y {name}',
#     )
#     run_one_benchmark(test, benchmark)


# SIMPLE_TASK = sky.Task(
#     run='echo hello SkyPilot',
#     resources=sky.Resources(accelerators='V100:4'))

ROUNDS_PER_TEST = 1


def test_sdk_api_launch(benchmark: BenchmarkFixture, generic_cloud: str):
    name = get_cluster_name() + "-lloyd"
    simple_task = sky.Task(
        run='echo hello SkyPilot',
        resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
    )

    def down_cluster():
        req_id = sdk.down(name)
        try:
            res = sdk.get(req_id)
        except sky.exceptions.ClusterDoesNotExist as e:
            pass


    def submit_and_get_request_id():
        id = sdk.launch(task=simple_task, cluster_name=name)
        return sdk.get(id)

    benchmark.pedantic(
        submit_and_get_request_id,
        setup=down_cluster,
        iterations=1,
        rounds=ROUNDS_PER_TEST)
    down_cluster()

def test_sdk_api_down(benchmark: BenchmarkFixture, generic_cloud: str):
    name = get_cluster_name() + "-lloyd"
    simple_task = sky.Task(
        run='echo hello SkyPilot',
        resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
    )

    def down_cluster_then_launch():
        req_id = sdk.down(name)
        try:
            sdk.get(req_id)
        except sky.exceptions.ClusterDoesNotExist as e:
            pass

        req_id = sdk.launch(task=simple_task, cluster_name=name)
        sdk.get(req_id)


    def submit_and_get_request_id():
        req_id = sdk.down(cluster_name=name)
        return sdk.get(req_id)

    benchmark.pedantic(
        submit_and_get_request_id,
        kwargs={},
        setup=down_cluster_then_launch,
        iterations=1,
        rounds=ROUNDS_PER_TEST)

def test_sdk_api_tail_logs(benchmark: BenchmarkFixture, generic_cloud: str):
    name = get_cluster_name() + "-lloyd"
    simple_task = sky.Task(
        run='echo hello SkyPilot',
        resources=sky.Resources(cpus='2', memory='4GB', infra=generic_cloud)
    )

    job_id = None

    def teardown():
        nonlocal job_id
        if job_id is not None:
            req_id = sdk.down(name)
            sdk.get(req_id)
            job_id = None

    def wait_for_cluster_to_be_up():
        finished = False
        while not finished:
            try:
                req_id = sdk.status(cluster_names=[name])
                res = sdk.get(req_id)
                if res[0]['status'] == sky.ClusterStatus.UP:
                    finished = True
                    time.sleep(3)
                else:
                    time.sleep(1)
            except Exception as e:
                print(e)
                time.sleep(1)

    def launch_cluster():
        nonlocal job_id
        teardown()
        req_id = sdk.launch(task=simple_task, cluster_name=name)
        job_id = sdk.get(req_id)[0]
        wait_for_cluster_to_be_up()
    
    def tail_logs():
        nonlocal job_id
        try:
            req_id = sdk.tail_logs(cluster_name=name, job_id=job_id, follow=False)
            return sdk.get(req_id)
        except Exception as e:
            return str(e)

    res = benchmark.pedantic(
        tail_logs,
        kwargs={},
        setup=launch_cluster,
        iterations=1,
        rounds=ROUNDS_PER_TEST)
    teardown()
    assert "Job finished (status: SUCCEEDED)." in res
    launch_cluster()
    # while True:
    #     try:
    #         tail_logs()
    #         break
    #     except Exception as e:
    #         print(e)
    #         time.sleep(1)
    # teardown()
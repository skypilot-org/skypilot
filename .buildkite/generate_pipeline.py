"""
This script generates a Buildkite pipeline from test files.

The script will generate two pipelines:

tests/smoke_tests
├── test_*.py -> release pipeline
├── test_quick_tests_core.py -> run quick tests on PR before merging

run `PYTHONPATH=$(pwd)/tests:$PYTHONPATH python .buildkite/generate_pipeline.py`
to generate the pipeline for testing. The CI will run this script as a pre-step,
and use the generated pipeline to run the tests.

1. release pipeline, which runs all smoke tests by default, generates all
   smoke tests for all clouds.
2. pre-merge pipeline, which generates all smoke tests for all clouds,
   author should specify which clouds to run by setting env in the step.

We only have credentials for aws/azure/gcp/kubernetes(CLOUD_QUEUE_MAP and
SERVE_CLOUD_QUEUE_MAP) now, smoke tests for those clouds are generated, other
clouds are not supported yet, smoke tests for those clouds are not generated.
"""

import collections
import os
import random
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import click
from conftest import cloud_to_pytest_keyword
from conftest import default_clouds_to_run
import yaml

DEFAULT_CLOUDS_TO_RUN = default_clouds_to_run
PYTEST_TO_CLOUD_KEYWORD = {v: k for k, v in cloud_to_pytest_keyword.items()}

QUEUE_GENERIC_CLOUD = 'generic_cloud'
QUEUE_GENERIC_CLOUD_SERVE = 'generic_cloud_serve'
QUEUE_KUBERNETES = 'kubernetes'
QUEUE_GKE = 'gke'
# Only aws, gcp, azure, and kubernetes are supported for now.
# Other clouds do not have credentials.
CLOUD_QUEUE_MAP = {
    'aws': QUEUE_GENERIC_CLOUD,
    'gcp': QUEUE_GENERIC_CLOUD,
    'azure': QUEUE_GENERIC_CLOUD,
    'kubernetes': QUEUE_KUBERNETES
}
# Serve tests runs long, and different test steps usually requires locks.
# Its highly likely to fail if multiple serve tests are running concurrently.
# So we use a different queue that runs only one concurrent test at a time.
SERVE_CLOUD_QUEUE_MAP = {
    'aws': QUEUE_GENERIC_CLOUD_SERVE,
    'gcp': QUEUE_GENERIC_CLOUD_SERVE,
    'azure': QUEUE_GENERIC_CLOUD_SERVE,
    # Now we run kubernetes on local cluster, so it should be find if we run
    # serve tests on same queue as kubernetes.
    'kubernetes': QUEUE_KUBERNETES
}

# Skip tests that we do not have resources for.
SKIP_TESTS = ['test_tpu_pod_slice_gke']

GENERATED_FILE_HEAD = ('# This is an auto-generated Buildkite pipeline by '
                       '.buildkite/generate_pipeline.py, Please do not '
                       'edit directly.\n')


def _extract_marked_tests(
    file_path: str, filter_marks: List[str]
) -> Dict[str, Tuple[List[str], List[str], List[Optional[str]]]]:
    """Extract test functions and filter clouds using pytest.mark
    from a Python test file.

    We separate each test_function_{cloud} into different pipeline steps
    to maximize the parallelism of the tests via the buildkite CI job queue.
    This allows us to visualize the test results and rerun failures at the
    granularity of each test_function_{cloud}.

    If we make pytest --serve a job, it could contain dozens of test_functions
    and run for hours. This makes it hard to visualize the test results and
    rerun failures. Additionally, the parallelism would be controlled by pytest
    instead of the buildkite job queue.
    """
    cmd = f'pytest {file_path} --collect-only'
    output = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    matches = re.findall('Collected .+?\.py::(.+?) with marks: \[(.*?)\]',
                         output.stdout)
    function_name_marks_map = collections.defaultdict(set)
    function_name_param_map = collections.defaultdict(list)
    for function_name, marks in matches:
        clean_function_name = re.sub(r'\[.*?\]', '', function_name)
        if clean_function_name in SKIP_TESTS:
            continue
        marks = marks.replace('\'', '').split(',')
        marks = [i.strip() for i in marks]
        function_name_marks_map[clean_function_name].update(marks)

        # extract parameter from function name
        # example: test_skyserve_new_autoscaler_update[rolling]
        # param: rolling
        # function_name: test_skyserve_new_autoscaler_update
        param = None
        if '[' in function_name and 'serve' in marks:
            # Only serve tests are slow and flaky, so we separate them
            # to different steps for parallel execution
            param = re.search('\[(.+?)\]', function_name).group(1)
        if param:
            function_name_param_map[clean_function_name].append(param)

    function_cloud_map = {}
    filter_marks = set(filter_marks)
    for function_name, marks in function_name_marks_map.items():
        if filter_marks and not filter_marks & marks:
            continue
        clouds_to_include = []
        clouds_to_exclude = []
        is_serve_test = 'serve' in marks
        run_on_gke = 'requires_gke' in marks
        for mark in marks:
            if mark.startswith('no_'):
                clouds_to_exclude.append(mark[3:])
            else:
                if mark not in PYTEST_TO_CLOUD_KEYWORD:
                    # This mark does not specify a cloud, so we skip it.
                    continue
                clouds_to_include.append(PYTEST_TO_CLOUD_KEYWORD[mark])

        clouds_to_include = (clouds_to_include
                             if clouds_to_include else DEFAULT_CLOUDS_TO_RUN)
        clouds_to_include = [
            cloud for cloud in clouds_to_include
            if cloud not in clouds_to_exclude
        ]
        cloud_queue_map = SERVE_CLOUD_QUEUE_MAP if is_serve_test else CLOUD_QUEUE_MAP
        final_clouds_to_include = [
            cloud for cloud in clouds_to_include if cloud in cloud_queue_map
        ]
        if clouds_to_include and not final_clouds_to_include:
            print(
                f'Warning: {function_name} is marked to run on {clouds_to_include}, '
                f'but we do not have credentials for those clouds. Skipped.')
            continue
        if clouds_to_include != final_clouds_to_include:
            excluded_clouds = set(clouds_to_include) - set(
                final_clouds_to_include)
            print(
                f'Warning: {function_name} is marked to run on {clouds_to_include}, '
                f'but we only have credentials for {final_clouds_to_include}. '
                f'clouds {excluded_clouds} are skipped.')

        param_list = function_name_param_map.get(function_name, [None])
        if len(param_list) < len(final_clouds_to_include):
            # align, so we can zip them together
            param_list += [None
                          ] * (len(final_clouds_to_include) - len(param_list))
        function_cloud_map[function_name] = (final_clouds_to_include, [
            QUEUE_GKE if run_on_gke else cloud_queue_map[cloud]
            for cloud in final_clouds_to_include
        ], param_list)


def _generate_pipeline(test_file: str,
                       filter_marks: List[str],
                       auto_retry: bool = False) -> Dict[str, Any]:
    """Generate a Buildkite pipeline from test files."""
    steps = []
    function_cloud_map = _extract_marked_tests(test_file, filter_marks)
    for test_function, clouds_queues_param in function_cloud_map.items():
        for cloud, queue, param in zip(*clouds_queues_param):
            label = f'{test_function} on {cloud}'
            command = f'pytest {test_file}::{test_function} --{cloud}'
            if param:
                label += f' with param {param}'
                command += f' -k {param}'
            step = {
                'label': label,
                'command': command,
                'agents': {
                    # Separate agent pool for each cloud.
                    # Since they require different amount of resources and
                    # concurrency control.
                    'queue': queue
                },
                'if': f'build.env("{cloud}") == "1"'
            }
            if auto_retry:
                step['retry'] = {
                    # Automatically retry 2 times on any failure by default.
                    'automatic': True
                }
            steps.append(step)
    return {'steps': steps}


def _dump_pipeline_to_file(yaml_file_path: str,
                           pipelines: List[Dict[str, Any]],
                           extra_env: Optional[Dict[str, str]] = None):
    default_env = {'LOG_TO_STDOUT': '1', 'PYTHONPATH': '${PYTHONPATH}:$(pwd)'}
    if extra_env:
        default_env.update(extra_env)
    with open(yaml_file_path, 'w', encoding='utf-8') as file:
        file.write(GENERATED_FILE_HEAD)
        all_steps = []
        for pipeline in pipelines:
            all_steps.extend(pipeline['steps'])
        # Shuffle the steps to avoid flakyness, consecutive runs of the same
        # kind of test may fail for requiring locks on the same resources.
        random.shuffle(all_steps)
        final_pipeline = {'steps': all_steps, 'env': default_env}
        yaml.dump(final_pipeline, file, default_flow_style=False)


def _convert_release(test_files: List[str], filter_marks: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_release.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        pipeline = _generate_pipeline(test_file, filter_marks, auto_retry=True)
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    # Enable all clouds by default for release pipeline.
    _dump_pipeline_to_file(yaml_file_path,
                           output_file_pipelines,
                           extra_env={cloud: '1' for cloud in CLOUD_QUEUE_MAP})


def _convert_quick_tests_core(test_files: List[str], filter_marks: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_quick_tests_core.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        # We want enable all clouds by default for each test function
        # for pre-merge. And let the author controls which clouds
        # to run by parameter.
        pipeline = _generate_pipeline(test_file, filter_marks)
        pipeline['steps'].append({
            'label': 'Backward compatibility test',
            'command': 'bash tests/backward_compatibility_tests.sh',
            'agents': {
                'queue': 'back_compat'
            }
        })
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    _dump_pipeline_to_file(yaml_file_path,
                           output_file_pipelines,
                           extra_env={'SKYPILOT_SUPPRESS_SENSITIVE_LOG': '1'})


@click.command()
@click.option(
    '--filter-marks',
    type=str,
    help='Filter to include only a subset of pytest marks, e.g., managed_jobs')
def main(filter_marks):
    test_files = os.listdir('tests/smoke_tests')
    release_files = []
    quick_tests_core_files = []
    for test_file in test_files:
        if not test_file.startswith('test_'):
            continue
        test_file_path = os.path.join('tests/smoke_tests', test_file)
        if "test_quick_tests_core" in test_file:
            quick_tests_core_files.append(test_file_path)
        else:
            release_files.append(test_file_path)

    filter_marks = filter_marks or os.getenv('FILTER_MARKS')
    if filter_marks:
        filter_marks = filter_marks.split(',')
        print(f'Filter marks: {filter_marks}')
    else:
        filter_marks = []

    _convert_release(release_files, filter_marks)
    _convert_quick_tests_core(quick_tests_core_files, filter_marks)


if __name__ == '__main__':
    main()

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

We only have credentials for aws/azure/gcp/kubernetes(CLOUD_QUEUE_MAP) now,
smoke tests for those clouds are generated, other clouds are not supported yet,
smoke tests for those clouds are not generated.
"""

import argparse
import collections
import os
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
QUEUE_KUBERNETES = 'kubernetes'
QUEUE_EKS = 'eks'
QUEUE_GKE = 'gke'
# We use KUBE_BACKEND to specify the queue for kubernetes tests mark as
# resource_heavy. It can be either EKS or GKE.
QUEUE_KUBE_BACKEND = os.getenv('KUBE_BACKEND', QUEUE_EKS).lower()
assert QUEUE_KUBE_BACKEND in [QUEUE_EKS, QUEUE_GKE]
# Only aws, gcp, azure, and kubernetes are supported for now.
# Other clouds do not have credentials.
CLOUD_QUEUE_MAP = {
    'aws': QUEUE_GENERIC_CLOUD,
    'gcp': QUEUE_GENERIC_CLOUD,
    'azure': QUEUE_GENERIC_CLOUD,
    'kubernetes': QUEUE_KUBERNETES
}

GENERATED_FILE_HEAD = ('# This is an auto-generated Buildkite pipeline by '
                       '.buildkite/generate_pipeline.py, Please do not '
                       'edit directly.\n')


def _parse_args(args: Optional[str] = None):
    """
    Parse command-line arguments to figure out which clouds to run
    and the -k pattern for tests.

    :return: (list_of_clouds, k_pattern)
    """
    if args:
        args_list = args.split()
    else:
        args_list = []
    parser = argparse.ArgumentParser(
        description="Process cloud arguments for tests")

    # Flags for recognized clouds
    for cloud in PYTEST_TO_CLOUD_KEYWORD.keys():
        parser.add_argument(f"--{cloud}", action="store_true")

    # Generic cloud argument, which takes a value (e.g., --generic-cloud aws)
    parser.add_argument("--generic-cloud")

    # -k argument for a test selection pattern
    parser.add_argument("-k")

    parser.add_argument("--remote-server", action="store_true")

    parsed_args, _ = parser.parse_known_args(args_list)

    # Collect chosen clouds from the flags
    # TODO(zpoint): get default clouds from the conftest.py
    default_clouds_to_run = []
    for cloud in PYTEST_TO_CLOUD_KEYWORD.keys():
        if getattr(parsed_args, cloud):
            default_clouds_to_run.append(cloud)
    if default_clouds_to_run:
        default_clouds_to_run = list(
            set(default_clouds_to_run) & set(CLOUD_QUEUE_MAP.keys()))
    # if user pass clouds we don't support, we should revert back to default
    if not default_clouds_to_run:
        default_clouds_to_run = DEFAULT_CLOUDS_TO_RUN

    # If a generic cloud is specified, it overrides any chosen clouds
    if (parsed_args.generic_cloud and
            parsed_args.generic_cloud in CLOUD_QUEUE_MAP):
        default_clouds_to_run = [parsed_args.generic_cloud]

    if not default_clouds_to_run:
        default_clouds_to_run = DEFAULT_CLOUDS_TO_RUN

    extra_args = []
    if parsed_args.remote_server:
        extra_args.append('--remote-server')

    return default_clouds_to_run, parsed_args.k, extra_args


def _extract_marked_tests(
        file_path: str, args: str
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
    cmd = f'pytest {file_path} --collect-only {args}'
    output = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    matches = re.findall('Collected .+?\.py::(.+?) with marks: \[(.*?)\]',
                         output.stdout)
    print(f'args: {args}')
    default_clouds_to_run, k_value, extra_args = _parse_args(args)

    print(f'default_clouds_to_run: {default_clouds_to_run}, k_value: {k_value}')
    function_name_marks_map = collections.defaultdict(set)
    function_name_param_map = collections.defaultdict(list)

    for function_name, marks in matches:
        clean_function_name = re.sub(r'\[.*?\]', '', function_name)
        clean_function_name = re.sub(r'@.*?$', '', clean_function_name)
        # The skip mark is generated by pytest naturally, and print in
        # conftest.py
        if 'skip' in marks:
            continue
        if k_value is not None and k_value not in function_name:
            # TODO(zpoint): support and/or in k_value
            continue

        marks = marks.replace('\'', '').split(',')
        marks = [i.strip() for i in marks]

        function_name_marks_map[clean_function_name].update(marks)

        # extract parameter from function name
        # example: test_skyserve_new_autoscaler_update[rolling]
        # param: rolling
        # function_name: test_skyserve_new_autoscaler_update
        param = None
        if '[' in function_name and 'test_mount_and_storage' not in file_path:
            # We separate different params to different steps for parallel execution,
            # and separate different param's log to different steps for better visualization.
            # Exclude the test_mount_and_storage, because these tests are fast and have fewer logs.
            param = re.search('\[(.+?)\]', function_name).group(1)
        if param:
            function_name_param_map[clean_function_name].append(param)

    function_cloud_map = {}
    for function_name, marks in function_name_marks_map.items():
        clouds_to_include = []
        run_on_cloud_kube_backend = ('resource_heavy' in marks and
                                     'kubernetes' in default_clouds_to_run)

        for mark in marks:
            if mark not in PYTEST_TO_CLOUD_KEYWORD:
                # This mark does not specify a cloud, so we skip it.
                continue
            clouds_to_include.append(PYTEST_TO_CLOUD_KEYWORD[mark])

        clouds_to_include = (clouds_to_include
                             if clouds_to_include else default_clouds_to_run)
        final_clouds_to_include = [
            cloud for cloud in clouds_to_include if cloud in CLOUD_QUEUE_MAP
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

        # pytest will only run the first cloud if there are multiple clouds
        # make it consistent with pytest behavior
        final_clouds_to_include = [final_clouds_to_include[0]]
        param_list = function_name_param_map.get(function_name, [None])
        if len(final_clouds_to_include) < len(param_list):
            # align, so we can zip them together
            final_clouds_to_include += [final_clouds_to_include[0]] * (
                len(param_list) - len(final_clouds_to_include))
        if len(param_list) < len(final_clouds_to_include):
            param_list += [None
                          ] * (len(final_clouds_to_include) - len(param_list))
        function_cloud_map[function_name] = (final_clouds_to_include, [
            QUEUE_KUBE_BACKEND
            if run_on_cloud_kube_backend else CLOUD_QUEUE_MAP[cloud]
            for cloud in final_clouds_to_include
        ], param_list, [
            extra_args for _ in range(len(final_clouds_to_include))
        ])

    return function_cloud_map


def _generate_pipeline(test_file: str,
                       args: str,
                       auto_retry: bool = False) -> Dict[str, Any]:
    """Generate a Buildkite pipeline from test files."""
    steps = []
    generated_steps_set = set()
    function_cloud_map = _extract_marked_tests(test_file, args)
    for test_function, clouds_queues_param in function_cloud_map.items():
        for cloud, queue, param, extra_args in zip(*clouds_queues_param):
            label = f'{test_function} on {cloud}'
            command = f'pytest {test_file}::{test_function} --{cloud}'
            if param:
                label += f' with param {param}'
                command += f' -k {param}'
            if extra_args:
                command += f' {" ".join(extra_args)}'
            if label in generated_steps_set:
                # Skip duplicate nested function tests under the same class
                continue
            python_version_cmd = f"""source {os.path.expanduser("~/buildkite-envs")}/python-{os.environ["PYTHON_VERSION"]}/bin/activate && python -c "import sys; from platform import python_version; print(f'Python version: {{python_version()}}') ; print(f'Path: {{sys.path}}')" """
            step = {
                'label': label,
                'command': f"{python_version_cmd} && {command}",
                'agents': {
                    # Separate agent pool for each cloud.
                    # Since they require different amount of resources and
                    # concurrency control.
                    'queue': queue
                }
            }
            if auto_retry:
                step['retry'] = {
                    # Automatically retry 2 times on any failure by default.
                    'automatic': True
                }
            generated_steps_set.add(label)
            steps.append(step)
    return {'steps': steps}


def _dump_pipeline_to_file(yaml_file_path: str,
                           pipelines: List[Dict[str, Any]],
                           extra_env: Optional[Dict[str, str]] = None):
    default_env = {
        'LOG_TO_STDOUT': '1',
        'SKYPILOT_DISABLE_USAGE_COLLECTION': '1'
    }
    if extra_env:
        default_env.update(extra_env)
    with open(yaml_file_path, 'w', encoding='utf-8') as file:
        file.write(GENERATED_FILE_HEAD)
        all_steps = []
        for pipeline in pipelines:
            all_steps.extend(pipeline['steps'])
        final_pipeline = {'steps': all_steps, 'env': default_env}
        yaml.dump(final_pipeline, file, default_flow_style=False)


def _convert_release(test_files: List[str], args: str):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_release.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        pipeline = _generate_pipeline(test_file, args, auto_retry=True)
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    # Enable all clouds by default for release pipeline.
    _dump_pipeline_to_file(yaml_file_path, output_file_pipelines)


def _convert_quick_tests_core(test_files: List[str], args: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_quick_tests_core.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        # We want enable all clouds by default for each test function
        # for pre-merge. And let the author controls which clouds
        # to run by parameter.
        pipeline = _generate_pipeline(test_file, args)
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    _dump_pipeline_to_file(yaml_file_path,
                           output_file_pipelines,
                           extra_env={'SKYPILOT_SUPPRESS_SENSITIVE_LOG': '1'})


@click.command()
@click.option('--args',
              type=str,
              help='Args to pass to pytest, e.g., --managed-jobs --aws')
def main(args):
    test_files = os.listdir('tests/smoke_tests')
    release_files = []
    quick_tests_core_files = []
    for test_file in test_files:
        if not test_file.startswith('test_'):
            continue
        test_file_path = os.path.join('tests/smoke_tests', test_file)
        if "test_quick_tests_core" in test_file or "test_backward_compat" in test_file:
            quick_tests_core_files.append(test_file_path)
        else:
            release_files.append(test_file_path)

    args = args or os.getenv('ARGS', '')
    print(f'args: {args}')

    _convert_release(release_files, args)
    _convert_quick_tests_core(quick_tests_core_files, args)


if __name__ == '__main__':
    main()

"""
This script generates a Buildkite pipeline from test files.

The script will generate two pipelines:

tests/smoke_tests
├── test_*.py -> release pipeline
├── test_required_before_merge.py -> pre-merge pipeline

run `python .buildkite/generate_pipeline.py` to generate the pipeline for
testing. The CI will run this script as a pre-step, and use the generated
pipeline to run the tests.

1. release pipeline, which runs all smoke tests by default, some function
   support tests by multiple clouds, but we only generate one cloud per test
   function to save cost.
2. pre-merge pipeline, which generates all clouds supported by the test
   function, author should specify which clouds to run by setting env in the
   step.

We only have credentials for aws/azure/gcp/kubernetes(CLOUD_QUEUE_MAP) now,
smoke tests for those clouds are generated, other clouds are not supported
yet, smoke tests for those clouds are not generated.
"""

import ast
from collections import defaultdict
import copy
import os
import random
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CLOUDS_TO_RUN = ['aws', 'azure']

ALL_CLOUDS_IN_SMOKE_TESTS = [
    'aws', 'gcp', 'azure', 'lambda', 'cloudflare', 'ibm', 'scp', 'oci',
    'kubernetes', 'vsphere', 'cudo', 'fluidstack', 'paperspace', 'runpod',
    'lambda_cloud'
]
QUEUE_GENERIC_CLOUD = 'generic_cloud'
QUEUE_GENERIC_CLOUD_SERVE = 'generic_cloud_serve'
QUEUE_KUBERNETES = 'kubernetes'
QUEUE_KUBERNETES_SERVE = 'kubernetes_serve'
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
    'kubernetes': QUEUE_KUBERNETES_SERVE
}

GENERATED_FILE_HEAD = ('# This is an auto-generated Buildkite pipeline by '
                       '.buildkite/generate_pipeline.py, Please do not '
                       'edit directly.\n')


def _get_full_decorator_path(decorator: ast.AST) -> str:
    """Recursively get the full path of a decorator."""
    if isinstance(decorator, ast.Attribute):
        return f'{_get_full_decorator_path(decorator.value)}.{decorator.attr}'
    elif isinstance(decorator, ast.Name):
        return decorator.id
    elif isinstance(decorator, ast.Call):
        return _get_full_decorator_path(decorator.func)
    raise ValueError(f'Unknown decorator type: {type(decorator)}')


def _extract_marked_tests(file_path: str) -> Dict[str, List[str]]:
    """Extract test functions and filter clouds with pytest.mark
    from a Python test file."""
    with open(file_path, 'r', encoding='utf-8') as file:
        tree = ast.parse(file.read(), filename=file_path)

    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            setattr(child, 'parent', node)

    function_cloud_map = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
            class_name = None
            if hasattr(node, 'parent') and isinstance(node.parent,
                                                      ast.ClassDef):
                class_name = node.parent.name

            clouds_to_include = []
            clouds_to_exclude = []
            is_serve_test = False
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    # We only need to consider the decorator with no arguments
                    # to extract clouds.
                    continue
                full_path = _get_full_decorator_path(decorator)
                if full_path.startswith('pytest.mark.'):
                    assert isinstance(decorator, ast.Attribute)
                    suffix = decorator.attr
                    if suffix.startswith('no_'):
                        clouds_to_exclude.append(suffix[3:])
                    else:
                        if suffix == 'serve':
                            is_serve_test = True
                            continue
                        if suffix not in ALL_CLOUDS_IN_SMOKE_TESTS:
                            # This mark does not specify a cloud, so we skip it.
                            continue
                        clouds_to_include.append(suffix)
            clouds_to_include = (clouds_to_include if clouds_to_include else
                                 copy.deepcopy(DEFAULT_CLOUDS_TO_RUN))
            clouds_to_include = [
                cloud for cloud in clouds_to_include
                if cloud not in clouds_to_exclude
            ]
            cloud_queue_map = SERVE_CLOUD_QUEUE_MAP if is_serve_test else CLOUD_QUEUE_MAP
            final_clouds_to_include = [
                cloud for cloud in clouds_to_include if cloud in cloud_queue_map
            ]
            if clouds_to_include and not final_clouds_to_include:
                print(f'Warning: {file_path}:{node.name} '
                      f'is marked to run on {clouds_to_include}, '
                      f'but we do not have credentials for those clouds. '
                      f'Skipped.')
                continue
            function_name = (f'{class_name}::{node.name}'
                             if class_name else node.name)
            function_cloud_map[function_name] = (final_clouds_to_include, [
                cloud_queue_map[cloud] for cloud in final_clouds_to_include
            ])
    return function_cloud_map


def _generate_pipeline(test_file: str,
                       one_cloud_per_test_function: bool) -> Dict[str, Any]:
    """Generate a Buildkite pipeline from test files."""
    steps = []
    function_cloud_map = _extract_marked_tests(test_file)
    for test_function, clouds_and_queues in function_cloud_map.items():
        for cloud, queue in zip(*clouds_and_queues):
            step = {
                'label': f'{test_function} on {cloud}',
                'command': f'pytest {test_file}::{test_function} --{cloud}',
                'agents': {
                    # Separate agent pool for each cloud.
                    # Since they require different amount of resources and
                    # concurrency control.
                    'queue': queue
                },
                'if': f'build.env("{cloud}") == "1"'
            }
            steps.append(step)
            if one_cloud_per_test_function:
                break
    return {'steps': steps}


def _dump_pipeline_to_file(output_file_pipelines_map: Dict[str,
                                                           List[Dict[str,
                                                                     Any]]],
                           extra_env: Optional[Dict[str, str]] = None):
    default_env = {'LOG_TO_STDOUT': '1', 'PYTHONPATH': '${PYTHONPATH}:$(pwd)'}
    if extra_env:
        default_env.update(extra_env)

    for yaml_file_path, pipelines in output_file_pipelines_map.items():
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


def _convert_release(test_files: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_release.yaml'
    output_file_pipelines_map = defaultdict(list)
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        # We only need to run one cloud per test function.
        pipeline = _generate_pipeline(test_file, True)
        output_file_pipelines_map[yaml_file_path].append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    # Enable all clouds by default for release pipeline.
    _dump_pipeline_to_file(output_file_pipelines_map,
                           extra_env={cloud: '1' for cloud in CLOUD_QUEUE_MAP})


def _convert_pre_merge(test_files: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_pre_merge.yaml'
    output_file_pipelines_map = defaultdict(list)
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        # We want enable all clouds by default for each test function
        # for pre-merge. And let the author controls which clouds
        # to run by parameter.
        pipeline = _generate_pipeline(test_file, False)
        output_file_pipelines_map[yaml_file_path].append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    _dump_pipeline_to_file(output_file_pipelines_map,
                           extra_env={'SKYPILOT_SUPPRESS_SENSITIVE_LOG': '1'})


def main():
    test_files = os.listdir('tests/smoke_tests')
    release_files = []
    pre_merge_files = []
    for test_file in test_files:
        if not test_file.startswith('test_'):
            continue
        test_file_path = os.path.join('tests/smoke_tests', test_file)
        if "required_before_merge" in test_file:
            pre_merge_files.append(test_file_path)
        else:
            release_files.append(test_file_path)

    _convert_release(release_files)
    _convert_pre_merge(pre_merge_files)


if __name__ == '__main__':
    main()

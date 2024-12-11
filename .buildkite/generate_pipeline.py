"""
This script generates a Buildkite pipeline from test files.

The script will generate two pipelines:

tests/smoke_tests
├── test_*.py -> release pipeline
├── test_pre_merge.py -> pre-merge pipeline

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

import ast
import os
import random
from typing import Any, Dict, List, Optional

from conftest import cloud_to_pytest_keyword
from conftest import default_clouds_to_run
import yaml

DEFAULT_CLOUDS_TO_RUN = default_clouds_to_run
PYTEST_TO_CLOUD_KEYWORD = {v: k for k, v in cloud_to_pytest_keyword.items()}

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
                        if suffix not in PYTEST_TO_CLOUD_KEYWORD:
                            # This mark does not specify a cloud, so we skip it.
                            continue
                        clouds_to_include.append(
                            PYTEST_TO_CLOUD_KEYWORD[suffix])
            clouds_to_include = (clouds_to_include if clouds_to_include else
                                 DEFAULT_CLOUDS_TO_RUN)
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
            if clouds_to_include != final_clouds_to_include:
                excluded_clouds = set(clouds_to_include) - set(
                    final_clouds_to_include)
                print(
                    f'Warning: {file_path}:{node.name} '
                    f'is marked to run on {clouds_to_include}, '
                    f'but we only have credentials for {final_clouds_to_include}. '
                    f'clouds {excluded_clouds} are skipped.')
            function_name = (f'{class_name}::{node.name}'
                             if class_name else node.name)
            function_cloud_map[function_name] = (final_clouds_to_include, [
                cloud_queue_map[cloud] for cloud in final_clouds_to_include
            ])
    return function_cloud_map


def _generate_pipeline(test_file: str) -> Dict[str, Any]:
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


def _convert_release(test_files: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_release.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        pipeline = _generate_pipeline(test_file)
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    # Enable all clouds by default for release pipeline.
    _dump_pipeline_to_file(yaml_file_path,
                           output_file_pipelines,
                           extra_env={cloud: '1' for cloud in CLOUD_QUEUE_MAP})


def _convert_pre_merge(test_files: List[str]):
    yaml_file_path = '.buildkite/pipeline_smoke_tests_pre_merge.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        # We want enable all clouds by default for each test function
        # for pre-merge. And let the author controls which clouds
        # to run by parameter.
        pipeline = _generate_pipeline(test_file)
        pipeline['steps'].append({
            'label': 'Backward compatibility test',
            'command': 'bash tests/backward_compatibility_tests.sh',
            'agents': {
                'queue': 'back_compat'
            },
            'if': 'build.env("aws") == "1"'
        })
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    _dump_pipeline_to_file(yaml_file_path,
                           output_file_pipelines,
                           extra_env={'SKYPILOT_SUPPRESS_SENSITIVE_LOG': '1'})


def main():
    test_files = os.listdir('tests/smoke_tests')
    release_files = []
    pre_merge_files = []
    for test_file in test_files:
        if not test_file.startswith('test_'):
            continue
        test_file_path = os.path.join('tests/smoke_tests', test_file)
        if "test_pre_merge" in test_file:
            pre_merge_files.append(test_file_path)
        else:
            release_files.append(test_file_path)

    _convert_release(release_files)
    _convert_pre_merge(pre_merge_files)


if __name__ == '__main__':
    main()

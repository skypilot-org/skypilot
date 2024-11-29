"""This script generates a Buildkite pipeline from test files."""
import ast
from collections import defaultdict
import copy
import os
import random
from typing import Any, Dict, List

import yaml

DEFAULT_CLOUDS_TO_RUN = ['aws', 'azure']
# We only have credentials for aws, azure, and gcp.
# For those test cases that run on other clouds,
# we currently ignore them.
ALL_CLOUDS_WITH_CREDENTIALS = ['aws', 'azure', 'gcp', 'kubernetes']

ALL_CLOUDS_IN_SMOKE_TESTS = [
    'aws', 'gcp', 'azure', 'lambda', 'cloudflare', 'ibm', 'scp', 'oci',
    'kubernetes', 'vsphere', 'cudo', 'fluidstack', 'paperspace', 'runpod'
]


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
            final_clouds_to_include = [
                cloud for cloud in clouds_to_include
                if cloud in ALL_CLOUDS_WITH_CREDENTIALS
            ]
            if clouds_to_include and not final_clouds_to_include:
                print(f'Warning: {file_path}:{node.name} '
                      f'is marked to run on {clouds_to_include}, '
                      f'but we do not have credentials for those clouds. '
                      f'Skipped.')
                continue
            function_name = (f'{class_name}::{node.name}'
                             if class_name else node.name)
            function_cloud_map[function_name] = (clouds_to_include)
    return function_cloud_map


def _generate_pipeline(test_file: str) -> Dict[str, Any]:
    """Generate a Buildkite pipeline from test files."""
    steps = []
    function_cloud_map = _extract_marked_tests(test_file)
    for test_function, clouds in function_cloud_map.items():
        for cloud in clouds:
            step = {
                'label': f'{test_function} on {cloud}',
                'command': f'pytest {test_file}::{test_function} --{cloud}',
                'env': {
                    'LOG_TO_STDOUT': '1',
                    'PYTHONPATH': '${PYTHONPATH}:$(pwd)'
                }
            }
            steps.append(step)
            # we only run one cloud per test function for now
            break
    return {'steps': steps}


def main():
    # List of test files to include in the pipeline
    test_files = os.listdir('tests/smoke_tests')
    output_file_pipelines_map = defaultdict(list)

    for test_file in test_files:
        if not test_file.startswith('test_'):
            continue
        test_file_path = os.path.join('tests/smoke_tests', test_file)
        if test_file == 'test_required_before_merge.py':
            yaml_file_path = '.buildkite/pipeline_smoke_tests_pre_merge.yaml'
        else:
            yaml_file_path = '.buildkite/pipeline_smoke_tests_release.yaml'
        print(f'Converting {test_file_path} to {yaml_file_path}')
        pipeline = _generate_pipeline(test_file_path)
        output_file_pipelines_map[yaml_file_path].append(pipeline)
        print(f'Converted {test_file_path} to {yaml_file_path}\n\n')

    for yaml_file_path, pipelines in output_file_pipelines_map.items():
        with open(yaml_file_path, 'w', encoding='utf-8') as file:
            file.write('# This is an auto-generated Buildkite pipeline by '
                       '.buildkite/generate_pipeline.py, Please do not '
                       'edit directly.\n')
            all_steps = [pipeline['steps'] for pipeline in pipelines]
            # Shuffle the steps to avoid flakyness, consecutive runs of the same
            # kind of test may fail for requiring locks on the same resources.
            random.shuffle(all_steps)
            final_pipeline = {
                'steps': all_steps
            }
            yaml.dump(final_pipeline, file, default_flow_style=False)


if __name__ == '__main__':
    main()

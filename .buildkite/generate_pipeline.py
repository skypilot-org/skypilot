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
import hashlib
import os
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import click
from conftest import all_clouds_in_smoke_tests
from conftest import cloud_to_pytest_keyword
from conftest import default_clouds_to_run
import requests
import yaml

DEFAULT_CLOUDS_TO_RUN = default_clouds_to_run
PYTEST_TO_CLOUD_KEYWORD = {v: k for k, v in cloud_to_pytest_keyword.items()}

QUEUE_GENERIC_CLOUD = 'generic_cloud'
QUEUE_EKS = 'eks'
QUEUE_GKE = 'gke'
QUEUE_KIND = 'kind'
QUEUE_BENCHMARK = 'single_container'
# We use a separate queue for generic cloud tests on remote servers because:
# - generic_cloud queue has high concurrency on a single VM
# - remote-server requires launching a docker container per test
# - Reusing generic_cloud queue to run remote-server tests would overload the VM
# Kubernetes has low concurrency on a single VM originally,
# so remote-server won't drain VM resources, we can reuse the same queue.
QUEUE_GENERIC_CLOUD_REMOTE_SERVER = 'generic_cloud_remote_server'
# Default concurrency limit when --env-file is used (shared remote API
# server). Override per-build with --concurrency N.
DEFAULT_ENV_FILE_CONCURRENCY_LIMIT = 10
# We use KUBE_BACKEND to specify the queue for kubernetes tests mark as
# resource_heavy. It can be either EKS or GKE.
QUEUE_KUBE_BACKEND = os.getenv('KUBE_BACKEND', QUEUE_EKS).lower()
assert QUEUE_KUBE_BACKEND in [QUEUE_EKS, QUEUE_GKE]
# Only aws, gcp, azure, nebius, and kubernetes are supported for now.
# Other clouds do not have credentials.
CLOUD_QUEUE_MAP = {
    'aws': QUEUE_GENERIC_CLOUD,
    'gcp': QUEUE_GENERIC_CLOUD,
    'azure': QUEUE_GENERIC_CLOUD,
    'nebius': QUEUE_GENERIC_CLOUD,
    'lambda': QUEUE_GENERIC_CLOUD,
    'runpod': QUEUE_GENERIC_CLOUD,
    'slurm': QUEUE_GENERIC_CLOUD,
    'kubernetes': QUEUE_KIND
}

GENERATED_FILE_HEAD = ('# This is an auto-generated Buildkite pipeline by '
                       '.buildkite/generate_pipeline.py, Please do not '
                       'edit directly.\n')


def _get_buildkite_queue(cloud: str,
                         remote_server: bool,
                         run_on_cloud_kube_backend: bool,
                         args: str,
                         benchmark_test: bool = False) -> str:
    """Get the Buildkite queue for a given cloud.

    We use a separate queue for generic cloud tests on remote servers because:
    - generic_cloud queue has high concurrency on a single VM
    - remote-server requires launching a docker container per test
    - Reusing generic_cloud queue to run remote-server tests would overload the VM

    Kubernetes has low concurrency on a single VM originally,
    so remote-server won't drain VM resources, we can reuse the same queue.

    For benchmark test, we use a dedicated benchmark queue that has guaranteed
    resources offering to get reliable performance results.
    """
    env_queue = os.environ.get('BUILDKITE_QUEUE', None)
    if env_queue:
        return env_queue

    if benchmark_test:
        return QUEUE_BENCHMARK

    if '--env-file' in args:
        # TODO(zeping): Remove this when test requirements become more varied.
        # Currently, tests specifying --env-file and a custom API server endpoint are assigned to
        # the generic_cloud queue to optimize resource usage. If tests require customization
        # beyond the API server, update this logic to ensure they run on the correct resources.
        return QUEUE_GENERIC_CLOUD
    if run_on_cloud_kube_backend:
        return QUEUE_KUBE_BACKEND

    queue = CLOUD_QUEUE_MAP[cloud]
    if queue == QUEUE_GENERIC_CLOUD and remote_server:
        return QUEUE_GENERIC_CLOUD_REMOTE_SERVER
    return queue


def _parse_args(args: Optional[str] = None):
    """
    Parse command-line arguments to figure out which clouds to run
    and the -k pattern for tests.

    :return: (list_of_clouds, k_pattern)
    """
    if args:
        args_list = shlex.split(args)
    else:
        args_list = []
    parser = argparse.ArgumentParser(
        description="Process cloud arguments for tests")

    # Flags for recognized clouds - use cloud names (e.g., --lambda) to match pytest
    for cloud in all_clouds_in_smoke_tests:
        parser.add_argument(f"--{cloud}", action="store_true")

    # Generic cloud argument, which takes a value (e.g., --generic-cloud aws)
    parser.add_argument("--generic-cloud")

    # -k argument for a test selection pattern
    parser.add_argument("-k")
    parser.add_argument("--remote-server", action="store_true")
    parser.add_argument('--base-branch')
    parser.add_argument('--controller-cloud')
    parser.add_argument('--postgres', action="store_true")
    parser.add_argument('--helm-version')
    parser.add_argument('--helm-package')
    parser.add_argument('--jobs-consolidation', action="store_true")
    parser.add_argument('--serve-consolidation', action="store_true")
    parser.add_argument('--grpc', action="store_true")
    parser.add_argument('--env-file')
    parser.add_argument('--plugin-yaml')
    parser.add_argument('--submodule-base-branch')
    parser.add_argument('--dependency', nargs='?', const='', default='all')
    parser.add_argument('--concurrency', type=int)
    # Select only tests marked `exclusive` and serialize them (one step at a
    # time). Exclusive tests mutate shared server state, so they must not run
    # concurrently with other tests or each other. Without this flag, exclusive
    # tests are excluded from the (parallel) pipeline entirely. Generator-only
    # flag: not forwarded to pytest.
    parser.add_argument('--exclusive', action='store_true')

    # pytest_native: args the generate_pipeline parser does not recognise
    # (e.g. --no-resource-heavy).  They are conftest-registered pytest flags
    # and must be forwarded to `pytest --collect-only` unchanged.
    parsed_args, pytest_native = parser.parse_known_args(args_list)

    # Collect chosen clouds from the flags
    # TODO(zpoint): get default clouds from the conftest.py
    default_clouds_to_run = []
    for cloud in all_clouds_in_smoke_tests:
        if getattr(parsed_args, cloud, False):
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

    # Each entry is a single shell token so that shlex.join() can safely
    # quote the list when it is passed to pytest --collect-only.
    extra_args: List[str] = []
    if parsed_args.remote_server:
        extra_args.append('--remote-server')
    if parsed_args.base_branch:
        extra_args.extend(['--base-branch', parsed_args.base_branch])
    if parsed_args.controller_cloud:
        extra_args.extend(['--controller-cloud', parsed_args.controller_cloud])
    if parsed_args.postgres:
        extra_args.append('--postgres')
    if parsed_args.helm_version:
        extra_args.extend(['--helm-version', parsed_args.helm_version])
    if parsed_args.helm_package:
        extra_args.extend(['--helm-package', parsed_args.helm_package])
    if parsed_args.jobs_consolidation:
        extra_args.append('--jobs-consolidation')
    if parsed_args.serve_consolidation:
        extra_args.append('--serve-consolidation')
    if parsed_args.grpc:
        extra_args.append('--grpc')
    if parsed_args.env_file:
        extra_args.extend(['--env-file', parsed_args.env_file])
    if parsed_args.plugin_yaml:
        extra_args.extend(['--plugin-yaml', parsed_args.plugin_yaml])
    if parsed_args.submodule_base_branch:
        extra_args.extend(
            ['--submodule-base-branch', parsed_args.submodule_base_branch])
    if parsed_args.dependency != 'all':
        if parsed_args.dependency:
            extra_args.extend(['--dependency', parsed_args.dependency])
        else:
            extra_args.append('--dependency')
    # Cloud flags are conftest-registered; include them in extra_args so that
    # they reach `pytest --collect-only` (some marks depend on which clouds
    # are active).  They are already captured in default_clouds_to_run for
    # Buildkite-step generation; adding them here is intentional duplication.
    for cloud in all_clouds_in_smoke_tests:
        if getattr(parsed_args, cloud, False):
            extra_args.append(f'--{cloud}')
    if parsed_args.generic_cloud:
        extra_args.append(f'--generic-cloud {parsed_args.generic_cloud}')

    return (default_clouds_to_run, parsed_args.k, extra_args,
            parsed_args.concurrency, parsed_args.env_file,
            parsed_args.exclusive, pytest_native)


def _extract_marked_tests(
    file_path: str,
    args: str,
    default_clouds_to_run: List[str],
    k_value: Optional[str],
    extra_args: List[str],
    exclusive_run: bool = False
) -> Dict[str, Tuple[List[str], List[str], List[Optional[str]], List[List[str]],
                     List[bool], List[Optional[str]]]]:
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

    Returns:
        Dict mapping function_name to tuple of:
        (clouds, queues, params, extra_args, no_auto_retry_flags)
    """
    # Args are already in the format pytest expects (cloud names like --lambda)
    cmd = f'pytest {file_path} --collect-only {args}'
    output = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    # Exit code 5 means "no tests collected" — normal when a file has no
    # matching tests for the requested clouds.  Any other non-zero code is a
    # real error (e.g. unrecognised arguments, import failure) that would
    # silently produce 0 matches and generate an empty pipeline.  Fail loudly
    # so the build is visibly broken rather than a noop.
    if output.returncode not in (0, 5):
        print(
            f'ERROR: pytest collection failed (exit {output.returncode}) '
            f'for {file_path}:\n'
            f'STDOUT:\n{output.stdout}\n'
            f'STDERR:\n{output.stderr}',
            file=sys.stderr)
        sys.exit(output.returncode)
    matches = re.findall('Collected .+?\.py::(.+?) with marks: \[(.*?)\]',
                         output.stdout)

    function_name_marks_map = collections.defaultdict(set)
    function_name_param_map = collections.defaultdict(list)
    remote_server = '--remote-server' in extra_args

    for function_name, marks in matches:
        clean_function_name = re.sub(r'\[.*?\]', '', function_name)
        clean_function_name = re.sub(r'@.*?$', '', clean_function_name)
        # The skip mark is generated by pytest naturally, and print in
        # conftest.py
        if 'skip' in marks:
            continue
        if k_value is not None and k_value not in function_name and k_value not in file_path:
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
        # Partition exclusive vs normal tests. Exclusive-marked tests run only
        # in an --exclusive run (serialized) and are excluded from normal
        # parallel runs; non-exclusive tests are excluded from --exclusive runs.
        # The two never share a pipeline, so server-mutating tests never run
        # alongside others.
        if ('exclusive' in marks) != exclusive_run:
            continue
        clouds_to_include = []
        run_on_cloud_kube_backend = ('resource_heavy' in marks and
                                     'kubernetes' in default_clouds_to_run)
        benchmark_test = 'benchmark' in marks
        no_auto_retry = 'no_auto_retry' in marks

        # A concurrency_group(name) marker serializes this test globally across
        # all builds and pipelines: the step gets that concurrency_group with a
        # limit of 1, so only one instance runs at a time org-wide while every
        # other step is unaffected. conftest.py renders the marker as
        # 'concurrency_group(<name>)' in the collect-only output.
        test_concurrency_group = None
        for mark in marks:
            group_match = re.match(r'concurrency_group\((.+)\)$', mark)
            if group_match:
                test_concurrency_group = group_match.group(1).strip()
                break

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
            _get_buildkite_queue(cloud, remote_server,
                                 run_on_cloud_kube_backend, args,
                                 benchmark_test)
            for cloud in final_clouds_to_include
        ], param_list, [
            extra_args for _ in range(len(final_clouds_to_include))
        ], [no_auto_retry for _ in range(len(final_clouds_to_include))], [
            test_concurrency_group for _ in range(len(final_clouds_to_include))
        ])

    return function_cloud_map


def _generate_pipeline(test_file: str, args: str) -> Dict[str, Any]:
    """Generate a Buildkite pipeline from test files."""
    steps = []
    generated_steps_set = set()
    (default_clouds_to_run, k_value, extra_args, concurrency, env_file,
     exclusive, pytest_native) = _parse_args(args)
    has_env_file = env_file is not None
    # Pass a clean arg string: extra_args (conftest-registered flags extracted
    # from the generate_pipeline parser) + pytest_native (conftest-registered
    # flags the generate_pipeline parser did not recognise).
    # This excludes generate_pipeline-only flags (--concurrency,
    # --submodule-base-branch, --dependency, --generic-cloud, --base-branch)
    # that are not in older pinned conftests and would cause
    # `pytest --collect-only` to exit with code 4, silently collecting 0 tests.
    pytest_collect_args = shlex.join(extra_args + list(pytest_native))
    function_cloud_map = _extract_marked_tests(test_file, pytest_collect_args,
                                               default_clouds_to_run, k_value,
                                               extra_args, exclusive)
    concurrency_limit = None
    build_id = None
    concurrency_group = None
    if exclusive:
        # Exclusive tests mutate shared server state, so the whole exclusive-only
        # run is serialized to one step at a time. Key the group on the target
        # (the --env-file) rather than the build id, so two exclusive builds
        # against the SAME server also serialize -- e.g. a re-triggered run, or a
        # deploy-and-test command racing a manual run. Fall back to the build id
        # when there is no --env-file (each build kept isolated).
        concurrency_limit = 1
        tag = (hashlib.sha256(env_file.encode()).hexdigest()[:12]
               if env_file else os.environ.get('BUILDKITE_BUILD_ID', 'local'))
        concurrency_group = f'exclusive-smoke-test-{tag}'
    elif has_env_file:
        concurrency_limit = (concurrency if concurrency is not None else
                             DEFAULT_ENV_FILE_CONCURRENCY_LIMIT)
        build_id = os.environ.get('BUILDKITE_BUILD_ID', 'local')
        concurrency_group = f'env-file-smoke-test-{build_id}'
    for test_function, clouds_queues_param in function_cloud_map.items():
        for (cloud, queue, param, extra_args, no_auto_retry,
             test_concurrency_group) in zip(*clouds_queues_param):
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
            if 'PYTHON_VERSION' in os.environ:
                command = f'PYTHONPATH="$PWD:$PYTHONPATH" {command}'

            step = {
                'label': label,
                'command': command,
                'agents': {
                    # Separate agent pool for each cloud.
                    # Since they require different amount of resources and
                    # concurrency control.
                    'queue': queue
                }
            }
            if test_concurrency_group is not None:
                # Per-test global serialization takes precedence over any
                # run-wide group: a Buildkite step has a single concurrency
                # group, and this one is a fixed name shared across all builds
                # and pipelines, so instances of this test serialize org-wide.
                step['concurrency'] = 1
                step['concurrency_group'] = test_concurrency_group
            elif concurrency_limit is not None:
                step['concurrency'] = concurrency_limit
                step['concurrency_group'] = concurrency_group
            if no_auto_retry:
                # Disable automatic retries but allow manual retries.
                step['retry'] = {
                    'automatic': False,
                    'manual': {
                        'allowed': True
                    }
                }
            else:
                step['retry'] = {
                    # Automatically retry 2 times on any failure by default.
                    'automatic': True
                }
            generated_steps_set.add(label)
            steps.append(step)
    return {'steps': steps}


def _dump_pipeline_to_file(yaml_file_path: str,
                           pipelines: List[Dict[str, Any]],
                           trigger_command: str,
                           extra_env: Optional[Dict[str, str]] = None) -> int:
    """Write the generated steps to a pipeline file; return the step count.

    main() always generates more than one pipeline file (e.g. release and
    quick-tests-core).  A `-k`/file filter often matches tests in only one of
    them, so an individual file legitimately ending up with 0 steps is not an
    error -- it is skipped here, and main() fails loudly only if *every* file
    is empty (the genuine "matched nothing anywhere" misconfiguration).
    """
    default_env = {
        'LOG_TO_STDOUT': '1',
        'SKYPILOT_DISABLE_USAGE_COLLECTION': '1'
    }
    if extra_env:
        default_env.update(extra_env)
    all_steps = []
    for pipeline in pipelines:
        all_steps.extend(pipeline['steps'])

    if not all_steps:
        # Buildkite rejects pipelines with empty step groups, so skip writing
        # this file. main() decides whether 0 steps overall is fatal.
        print(f'No matching tests for {yaml_file_path}, skipping.')
        return 0

    with open(yaml_file_path, 'w', encoding='utf-8') as file:
        file.write(GENERATED_FILE_HEAD)
        # Extract key from trigger command, keeping only valid characters
        key = re.sub(r'[^a-zA-Z0-9_\-:]', '',
                     re.match(r'^[^ ]*', trigger_command).group(0))
        # Generate formatted group name from key
        group_name = ' '.join(
            word.capitalize() for word in re.split(r'[-_]', key))

        grouped_steps = [{
            'group': group_name,
            'key': key,
            'notify': [{
                'github_commit_status': {
                    'context': f'{trigger_command}'
                }
            }],
            'steps': all_steps
        }]

        final_pipeline = {'steps': grouped_steps, 'env': default_env}
        yaml.dump(final_pipeline, file, default_flow_style=False)
    return len(all_steps)


def _convert_release(test_files: List[str], args: str,
                     trigger_command: str) -> int:
    yaml_file_path = '.buildkite/pipeline_smoke_tests_release.yaml'
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        pipeline = _generate_pipeline(test_file, args)
        output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    # Enable all clouds by default for release pipeline.
    return _dump_pipeline_to_file(yaml_file_path, output_file_pipelines,
                                  trigger_command)


def _rest_request(url: str,
                  method: str,
                  json: Optional[Dict[str, Any]] = None) -> Any:
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.request(method, url, json=json, timeout=10)
        except Exception as e:  # pylint: disable=broad-except
            # Retry on transient network errors
            if attempt >= 3:
                raise RuntimeError(f'network error: {e}') from e
            time.sleep(1)
            continue

        # Retry on 5xx and 429
        if resp.status_code >= 500 or resp.status_code == 429:
            if attempt >= 3:
                raise RuntimeError(f'error {resp.status_code}: {resp.text}')
            time.sleep(1)
            continue

        if resp.status_code >= 400:
            # Non-retryable client error
            raise RuntimeError(f'error {resp.status_code}: {resp.text}')

        if resp.text:
            try:
                return resp.json()
            except Exception:  # pylint: disable=broad-except
                return resp.text
        return None


def _get_latest_pypi_version():
    resp = _rest_request('https://pypi.org/pypi/skypilot/json', 'GET')
    if isinstance(resp, dict):
        return resp.get('info', {}).get('version')
    raise RuntimeError(f'Failed to get latest pypi version: {resp}')


def _convert_quick_tests_core(test_files: List[str], args: str,
                              trigger_command: str) -> int:
    yaml_file_path = '.buildkite/pipeline_smoke_tests_quick_tests_core.yaml'
    base_branch = '--base-branch' in args
    base_branches = []
    if not base_branch:
        latest_pypi_version = _get_latest_pypi_version()
        print(f'latest_pypi_version: {latest_pypi_version}')
        base_branches = ['master', f'v{latest_pypi_version}']
    print(f'base_branches: {base_branches}')
    output_file_pipelines = []
    for test_file in test_files:
        print(f'Converting {test_file} to {yaml_file_path}')
        # We want enable all clouds by default for each test function
        # for pre-merge. And let the author controls which clouds
        # to run by parameter.
        if base_branches:
            for branch in base_branches:
                if ('test_quick_tests_core.py' in test_file and
                        branch != 'master'):
                    continue
                pipeline = _generate_pipeline(test_file,
                                              args + f' --base-branch {branch}')
                output_file_pipelines.append(pipeline)
        else:
            pipeline = _generate_pipeline(test_file, args)
            output_file_pipelines.append(pipeline)
        print(f'Converted {test_file} to {yaml_file_path}\n\n')
    return _dump_pipeline_to_file(
        yaml_file_path,
        output_file_pipelines,
        trigger_command,
        extra_env={'SKYPILOT_SUPPRESS_SENSITIVE_LOG': '1'})


@click.command()
@click.option('--args',
              type=str,
              help='Args to pass to pytest, e.g., --managed-jobs --aws')
@click.option('--file_pattern',
              type=str,
              help='File pattern to run, e.g., test_cluster_job.py')
def main(args: str, file_pattern: str):
    # parse arguments from command line and environment variables
    args = args or os.getenv('ARGS', '')
    print(f'args: {args}')
    file_pattern = file_pattern or os.getenv('FILE_PATTERN', '')
    print(f'file_pattern: {file_pattern}')
    file_pattern_list = [
        file_pattern_str.strip() for file_pattern_str in file_pattern.split(',')
    ] if file_pattern else []
    # If trigger via buildkite, TRIGGER_COMMAND should be set.
    # Otherwise, use the args passed in for local testing.
    trigger_command = (os.getenv('TRIGGER_COMMAND', '') or args or
                       '/smoke-test')

    test_files = []
    for root, _, files in os.walk('tests/smoke_tests'):
        for file in files:
            if (file.endswith('.py') and file.startswith('test_')):
                excluded_by_file_pattern = (file_pattern_list and all(
                    file_pattern_str not in file
                    for file_pattern_str in file_pattern_list))
                if excluded_by_file_pattern:
                    continue
                test_files.append(os.path.join(root, file))

    release_files = []
    quick_tests_core_files = []
    for test_file in test_files:
        if "test_quick_tests_core" in test_file or "test_backward_compat" in test_file:
            quick_tests_core_files.append(test_file)
        else:
            release_files.append(test_file)

    print(f'trigger_command: {trigger_command}')
    total_steps = 0
    total_steps += _convert_release(release_files, args, trigger_command)
    total_steps += _convert_quick_tests_core(quick_tests_core_files, args,
                                             trigger_command)

    if total_steps == 0:
        # Every generated pipeline file was empty: pytest --collect-only matched
        # no tests anywhere.  This is almost always a misconfiguration (wrong
        # cloud filter, unrecognised ARGS flag, missing env file, a typo'd -k)
        # rather than a legitimate "nothing to run".  Fail loudly so the empty
        # pipeline is not uploaded as a vacuous success that posts a false
        # "passed" status while running zero tests.
        print(
            'ERROR: No pipeline steps generated for any pipeline file. '
            'pytest --collect-only matched 0 tests across all test files. '
            'Check that ARGS point to valid tests and that the env-file (if '
            'any) is reachable.',
            file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

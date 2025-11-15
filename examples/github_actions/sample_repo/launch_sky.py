"""Launch a SkyPilot task with custom resources."""

import argparse
import copy
import logging
import os
import sys
from typing import Optional

import sky
from sky.jobs.client import sdk as jobs_sdk

logger = logging.getLogger('launch.py')


def patch_task(
    task: sky.Task,
    cpus: Optional[int] = None,
    gpus: Optional[int] = None,
    nodes: Optional[int] = None,
) -> sky.Task:
    """Patch the task to add the requested resources overrides."""
    overrides = {}
    if cpus:
        overrides['cpus'] = cpus
    if overrides:
        task.set_resources_override(overrides)
    if nodes:
        task.num_nodes = nodes

    new_resources_list = list(task.resources)

    if gpus:
        new_resources_list = []
        for res in list(task.resources):
            if not res.accelerators:
                # No accelerators, so no need to patch
                continue
            if not isinstance(res.accelerators, dict):
                # shouldn't happen with our current config
                raise Exception('Unexpected accelerator type: '
                                f'{res.accelerators}, {type(res.accelerators)}')

            patched_accelerators = copy.deepcopy(res.accelerators)
            patched_accelerators = {
                gpu_type: gpus for gpu_type in patched_accelerators.keys()
            }
            new_resources = res.copy(accelerators=patched_accelerators)
            new_resources_list.append(new_resources)

    if gpus:
        task.set_resources(type(task.resources)(new_resources_list))

    return task


def main() -> int:
    """Main entry point for launch.py.

    Returns:
        Exit code (0 for success, 1 for error)
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    launch_sky.py --task-yaml-path sample_task.yml --gpus 1 --nodes 1 --cpus 1 --job-name test_job
        """,
    )

    # Launch-specific flags
    parser.add_argument('--task-yaml-path',
                        type=str,
                        default='sample_task.yml',
                        help='Path to the task YAML file')
    parser.add_argument(
        '--gpus',
        type=int,
        default=None,
        help='Number of GPUs to request. If empty, uses SkyPilot task default.')
    parser.add_argument(
        '--nodes',
        type=int,
        default=None,
        help='Number of nodes to request. If empty, uses SkyPilot task default.'
    )
    parser.add_argument(
        '--cpus',
        type=int,
        default=None,
        help='Number of CPUs to request. If empty, uses SkyPilot task default.')
    parser.add_argument('--job-name',
                        type=str,
                        required=True,
                        help='Name of the job (required)')

    args = parser.parse_args()

    task = sky.Task.from_yaml(os.path.expanduser(args.task_yaml_path))
    task.name = args.job_name

    task.validate_name()
    task = patch_task(
        task,
        cpus=args.cpus,
        gpus=args.gpus,
        nodes=args.nodes,
    )

    # Launch the task asynchronously
    request_id = jobs_sdk.launch(task)
    print(f'Submitted sky.jobs.launch request: {request_id}')
    return 0


if __name__ == '__main__':
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(exit_code)

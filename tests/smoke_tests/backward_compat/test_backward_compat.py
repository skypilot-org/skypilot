import os
import pathlib
import re
import subprocess
import tempfile
import textwrap
from typing import Sequence

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky.backends import backend_utils
from sky.server import constants
from sky.skylet import constants as skylet_constants

# Add a pytest mark to limit concurrency to 1
pytestmark = pytest.mark.xdist_group(name="backward_compat")


class TestBackwardCompatibility:
    # Constants
    MANAGED_JOB_PREFIX = 'test-back-compat'
    SERVE_PREFIX = 'test-back-compat'
    TEST_TIMEOUT = 1800  # 30 minutes
    BASE_API_VERSION = constants.API_VERSION
    BASE_MIN_COMPATIBLE_API_VERSION = constants.MIN_COMPATIBLE_API_VERSION
    CURRENT_API_VERSION = constants.API_VERSION
    CURRENT_MIN_COMPATIBLE_API_VERSION = constants.MIN_COMPATIBLE_API_VERSION

    # Environment paths
    BASE_ENV_DIR = pathlib.Path(
        '~/sky-back-compat-base').expanduser().absolute()
    CURRENT_ENV_DIR = pathlib.Path(
        '~/sky-back-compat-current').expanduser().absolute()
    BASE_SKY_DIR = pathlib.Path('~/sky-base').expanduser().absolute()
    CURRENT_SKY_DIR = pathlib.Path('./').expanduser().absolute()
    SKY_WHEEL_DIR = pathlib.Path(
        backend_utils.SKY_REMOTE_PATH).expanduser().absolute()

    # Command templates
    ACTIVATE_BASE = f'rm -r {SKY_WHEEL_DIR} || true && source {BASE_ENV_DIR}/bin/activate && cd {BASE_SKY_DIR}'
    ACTIVATE_CURRENT = f'rm -r {SKY_WHEEL_DIR} || true && source {CURRENT_ENV_DIR}/bin/activate && cd {CURRENT_SKY_DIR}'

    # Shorthand of switching to base environment and running a list of commands in environment.
    def _switch_to_base(self, *cmds: str) -> list[str]:
        all_cmds = [smoke_tests_utils.SKY_API_RESTART] + list(cmds)
        return [f'{self.ACTIVATE_BASE} && {c}' for c in all_cmds]

    # Shorthand of switching to current environment and running a list of commands in environment.
    def _switch_to_current(self, *cmds: str) -> list[str]:
        all_cmds = [smoke_tests_utils.SKY_API_RESTART] + list(cmds)
        return [f'{self.ACTIVATE_CURRENT} && {c}' for c in all_cmds]

    def _run_cmd(self, cmd: str):
        subprocess.run(cmd, shell=True, check=True, executable='/bin/bash')

    def _is_git_sha(self, ref: str) -> bool:
        """Check if the reference looks like a git SHA (commit hash)."""
        # Git SHAs are hexadecimal strings, typically 7-40 characters long
        return bool(re.match(r'^[0-9a-f]{7,40}$', ref.lower()))

    def _wait_for_managed_job_status(self, job_name: str,
                                     status: Sequence[sky.ManagedJobStatus]):
        return smoke_tests_utils.get_cmd_wait_until_managed_job_status_contains_matching_job_name(
            job_name=job_name, job_status=status, timeout=300)

    def _get_base_skylet_version(self) -> str:
        """Get SKYLET_VERSION from the base environment by running Python code."""
        base_skylet_version = subprocess.run(
            f'{self.ACTIVATE_BASE} && python -c "'
            'from sky.skylet import constants; '
            'print(constants.SKYLET_VERSION);'
            '"',
            shell=True,
            check=False,
            executable='/bin/bash',
            text=True,
            capture_output=True)

        if base_skylet_version.returncode != 0:
            raise ValueError(f'Failed to get base SKYLET_VERSION. '
                             f'Return code: {base_skylet_version.returncode}, '
                             f'stderr: {base_skylet_version.stderr}, '
                             f'stdout: {base_skylet_version.stdout}')

        version = base_skylet_version.stdout.strip().split('\n')[-1]
        if not version:
            raise ValueError('SKYLET_VERSION is empty')

        return version

    @pytest.fixture(scope="session", autouse=True)
    def session_setup(self, request):
        """Session-wide setup that runs exactly once (concurrency limited to 1)."""
        # No locking mechanism needed since concurrency is limited to 1
        base_branch = request.config.getoption("--base-branch")

        # Check if gcloud is installed
        if subprocess.run('gcloud --version', shell=True).returncode != 0:
            raise Exception('gcloud not found')

        # Check if uv is installed
        if subprocess.run('uv --version', shell=True,
                          check=False).returncode != 0:
            raise Exception('uv not found')

        # Clone base SkyPilot version
        if self.BASE_SKY_DIR.exists():
            self._run_cmd(f'rm -rf {self.BASE_SKY_DIR}')

        install_from_pypi = base_branch.startswith('pypi/')
        if install_from_pypi:
            pypi_version = base_branch.split('/')[1]
            pip_install_cmd = f'uv pip install "{pypi_version}[all]"'
            self._run_cmd(f'mkdir -p {self.BASE_SKY_DIR}')
            self._run_cmd(f'cp -r tests {self.BASE_SKY_DIR}/ && '
                          f'cp -r examples {self.BASE_SKY_DIR}/')
        else:
            pip_install_cmd = 'uv pip install -e .[all]'

            if self._is_git_sha(base_branch):
                # For git SHA, clone first, fetch the specific commit, then checkout
                self._run_cmd(
                    f'git clone https://github.com/skypilot-org/skypilot.git {self.BASE_SKY_DIR}'
                )
                self._run_cmd(
                    f'cd {self.BASE_SKY_DIR} && '
                    f'git fetch -v --prune -- origin {base_branch} && '
                    f'git checkout -f {base_branch}')
            else:
                # For branch names, use -b flag
                self._run_cmd(
                    f'git clone -b {base_branch} '
                    f'https://github.com/skypilot-org/skypilot.git {self.BASE_SKY_DIR}',
                )

        # Create and set up virtual environments using uv
        for env_dir in [self.BASE_ENV_DIR, self.CURRENT_ENV_DIR]:
            if env_dir.exists():
                self._run_cmd(f'rm -rf {env_dir}')
            self._run_cmd(f'uv venv --seed --python=3.10 {env_dir}',)

        # Install dependencies in base environment
        self._run_cmd(
            f'{self.ACTIVATE_BASE} && '
            'uv pip uninstall skypilot && '
            'uv pip install --prerelease=allow "azure-cli>=2.65.0" && '
            f'{pip_install_cmd}')

        # Install current version in current environment
        self._run_cmd(
            f'{self.ACTIVATE_CURRENT} && '
            'uv pip uninstall skypilot && '
            'uv pip install --prerelease=allow "azure-cli>=2.65.0" && '
            'uv pip install -e .[all]',)

        base_sky_api_version = subprocess.run(
            f'{self.ACTIVATE_BASE} && python -c "'
            'from sky.server import constants; '
            'print(f\'API_VERSION: {constants.API_VERSION}\'); '
            # Handle the base that does not have MIN_COMPATIBLE_API_VERSION defined
            'min_compatible_version = getattr(constants, \'MIN_COMPATIBLE_API_VERSION\', constants.API_VERSION); '
            'print(f\'MIN_COMPATIBLE_API_VERSION: {min_compatible_version}\');'
            '"',
            shell=True,
            check=False,
            executable='/bin/bash',
            text=True,
            capture_output=True)
        if base_sky_api_version.returncode != 0:
            raise RuntimeError(
                f'Failed to get base API version information. '
                f'Return code: {base_sky_api_version.returncode}, '
                f'stderr: {base_sky_api_version.stderr}, '
                f'stdout: {base_sky_api_version.stdout}')

        # Use regex to extract version numbers from the output
        output = base_sky_api_version.stdout.strip()

        # Extract API_VERSION
        api_version_match = re.search(r'API_VERSION:\s*(\d+)', output)
        if not api_version_match:
            raise RuntimeError(f'Could not find API_VERSION in output. '
                               f'Output: {output}, '
                               f'stderr: {base_sky_api_version.stderr}')

        # Extract MIN_COMPATIBLE_API_VERSION
        min_compatible_version_match = re.search(
            r'MIN_COMPATIBLE_API_VERSION:\s*(\d+)', output)
        if not min_compatible_version_match:
            raise RuntimeError(
                f'Could not find MIN_COMPATIBLE_API_VERSION in output. '
                f'Output: {output}, '
                f'stderr: {base_sky_api_version.stderr}')

        TestBackwardCompatibility.BASE_API_VERSION = int(
            api_version_match.group(1))
        TestBackwardCompatibility.BASE_MIN_COMPATIBLE_API_VERSION = int(
            min_compatible_version_match.group(1))

        yield  # Optional teardown logic
        self._run_cmd(f'{self.ACTIVATE_CURRENT} && sky api stop',)

    def run_compatibility_test(self, test_name: str, commands: list,
                               teardown: str):
        """Helper method to create and run tests with proper cleanup"""
        test = smoke_tests_utils.Test(
            test_name,
            commands,
            teardown=teardown,
            timeout=self.TEST_TIMEOUT,
            env=smoke_tests_utils.LOW_CONTROLLER_RESOURCE_ENV,
        )
        smoke_tests_utils.run_one_test(test)

    def test_cluster_launch_and_exec(self, request, generic_cloud: str):
        """Test basic cluster launch and execution across versions"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        need_launch = request.config.getoption("--need-launch")
        need_launch_cmd = 'echo "skipping launch"'
        if need_launch:
            need_launch_cmd = f'{self.ACTIVATE_CURRENT} && sky launch --infra {generic_cloud} -y -c {cluster_name}'
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            # Use --cloud since old version may not have --infra
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky autostop -i 10 -y {cluster_name}',
            f'{self.ACTIVATE_BASE} && sky exec -d --infra {generic_cloud} --num-nodes 2 {cluster_name} sleep 120',
            # Must restart API server after switch the code base to ensure the client and server run in same version.
            # Test coverage for client and server run in differnet verions should be done in test_client_server_compatibility.
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && result="$(sky status {cluster_name})"; echo "$result"; echo "$result" | grep UP',
            f'{self.ACTIVATE_CURRENT} && result="$(sky status -r {cluster_name})"; echo "$result"; echo "$result" | grep UP',
            need_launch_cmd,
            f'{self.ACTIVATE_CURRENT} && sky exec -d --infra {generic_cloud} {cluster_name} sleep 50',
            f'{self.ACTIVATE_CURRENT} && result="$(sky queue -u {cluster_name})"; echo "$result"; echo "$result" | grep RUNNING | wc -l | grep 2',
            f'{self.ACTIVATE_CURRENT} && s=$(sky launch --infra {generic_cloud} -d -c {cluster_name} examples/minimal.yaml) && '
            'echo "$s" | sed -r "s/\\x1B\\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job ID: 4"',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status | grep -E "RUNNING|SUCCEEDED"',
            f"""
            {self.ACTIVATE_CURRENT} && {smoke_tests_utils.get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=f"{cluster_name}",
                job_id="2",
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=120,
                all_users=True)} && {smoke_tests_utils.get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=f"{cluster_name}",
                job_id="3",
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=120,
                all_users=True)}
            """
            f'{self.ACTIVATE_CURRENT} && result="$(sky queue -u {cluster_name})"; echo "$result"; echo "$result" | grep SUCCEEDED | wc -l | grep 4'
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y || true'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_cluster_stop_start(self, generic_cloud: str):
        """Test cluster stop/start functionality across versions"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && s=$(sky exec --cloud {generic_cloud} -d {cluster_name} examples/minimal.yaml) && '
            'echo "$s" | sed -r "s/\\x1B\\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job submitted, ID: 2"',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    @pytest.mark.no_kubernetes
    def test_autostop_functionality(self, generic_cloud: str):
        """Test autostop functionality across versions"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        task_yaml = textwrap.dedent("""\
            resources:
              autostop:
                idle_minutes: 5
            """)

        with tempfile.NamedTemporaryFile(prefix='autostop_',
                                         delete=False,
                                         mode='w') as f:
            f.write(task_yaml)
            yaml_path = f.name
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            # Set intiial autostop in base
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} {yaml_path}',
            f'{self.ACTIVATE_BASE} && sky status {cluster_name} | grep "5m"',
            # Change the autostop time in current
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && sky autostop -y -i0 {cluster_name}',
            f"""
            {self.ACTIVATE_CURRENT} && {smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                    cluster_name=f"{cluster_name}",
                    cluster_status=[sky.ClusterStatus.STOPPED],
                    timeout=150)}
            """
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_single_node_operations(self, generic_cloud: str):
        """Test single node operations (launch, stop, restart, logs)"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && sky launch --cloud {generic_cloud} -y --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_restarted_cluster_operations(self, generic_cloud: str):
        """Test operations on restarted clusters"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1',
            f'{self.ACTIVATE_CURRENT} && sky launch --cloud {generic_cloud} -y -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_multi_node_operations(self, generic_cloud: str):
        """Test multi-node cluster operations"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/multi_hostname.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1',
            f'{self.ACTIVATE_CURRENT} && sky exec --cloud {generic_cloud} {cluster_name} examples/multi_hostname.yaml',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_managed_jobs(self, generic_cloud: str):
        """Test managed jobs functionality across versions"""
        managed_job_name = smoke_tests_utils.get_cluster_name()

        def launch_job(job_name: str, command: str):
            return (
                f'sky jobs launch -d --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} '
                f'--num-nodes 2 -n {job_name} "{command}"')

        def wait_for_status(job_name: str,
                            status: Sequence[sky.ManagedJobStatus]):
            return smoke_tests_utils.get_cmd_wait_until_managed_job_status_contains_matching_job_name(
                job_name=job_name,
                job_status=status,
                timeout=600 if generic_cloud == 'kubernetes' else 300)

        blocking_seconds_for_cancel_job = 2000 if generic_cloud == 'kubernetes' else 1000

        # Dynamically inspect versions from both environments
        base_version = self._get_base_skylet_version()
        current_version = skylet_constants.SKYLET_VERSION
        expect_version_mismatch = base_version != current_version

        # Build the current environment commands with version mismatch handling
        # Common initial commands
        common_initial_commands = [
            f'result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {managed_job_name} | grep RUNNING | wc -l | grep 2',
            f'result="$(sky jobs logs --no-follow -n {managed_job_name}-1)"; echo "$result"; echo "$result" | grep hi',
        ]

        # Version-specific job-2 and job-3 handling
        if expect_version_mismatch:
            # Try to launch job-2, expect it to fail with version mismatch
            version_specific_commands = [
                (f'output=$({launch_job(f"{managed_job_name}-2", "echo hi; sleep 400")} 2>&1); '
                 f'exit_code=$?; '
                 f'echo "Launch exit code: $exit_code"; '
                 f'echo "$output"; '
                 f'if [ "$exit_code" -ne 0 ]; then '
                 f'  echo "Launch failed as expected due to version mismatch"; '
                 f'  echo "$output" | grep -q "non-terminal jobs on the controller" || {{ echo "ERROR: Expected version mismatch error message not found"; exit 1; }}; '
                 f'  echo "$output" | grep -q "{managed_job_name}-0" || {{ echo "ERROR: Job name not found in error"; exit 1; }}; '
                 f'  echo "$output" | grep -q "RUNNING" || {{ echo "ERROR: RUNNING status not found in error"; exit 1; }}; '
                 f'  echo "Version mismatch confirmed for job-2"; '
                 f'else '
                 f'  echo "ERROR: Launch should have failed due to version mismatch"; exit 1; '
                 f'fi'),
                # Cancel both job-0 and job-1 to clear all running jobs
                f'sky jobs cancel -y -n {managed_job_name}-0',
                # Job-1 could already be succeeded, so we don't want to fail the test
                f'sky jobs cancel -y -n {managed_job_name}-1 || true',
                # Wait for job-0 to be cancelled
                wait_for_status(f'{managed_job_name}-0', [
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.CANCELLING
                ]),
                # Wait for job-1 to reach terminal state (succeeded/cancelled)
                wait_for_status(f'{managed_job_name}-1', [
                    sky.ManagedJobStatus.SUCCEEDED,
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.CANCELLING
                ]),
                # Now launch job-2 should succeed
                launch_job(f'{managed_job_name}-2', 'echo hi; sleep 150'),
                f'result="$(sky jobs logs --no-follow -n {managed_job_name}-2)"; echo "$result"; echo "$result" | grep hi',
                # Now launch job-3 should succeed (simple launch without error handling)
                launch_job(f'{managed_job_name}-3',
                           f'echo hi; sleep {blocking_seconds_for_cancel_job}'),
                # Cancel job-3 and wait for final states
                f'sky jobs cancel -y -n {managed_job_name}-3',
                wait_for_status(f'{managed_job_name}-2',
                                [sky.ManagedJobStatus.SUCCEEDED]),
                wait_for_status(f'{managed_job_name}-3', [
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.CANCELLING
                ]),
            ]
        else:
            # No version mismatch expected - direct launch
            version_specific_commands = [
                launch_job(f'{managed_job_name}-2', 'echo hi; sleep 150'),
                f'result="$(sky jobs logs --no-follow -n {managed_job_name}-2)"; echo "$result"; echo "$result" | grep hi',
                launch_job(f'{managed_job_name}-3',
                           f'echo hi; sleep {blocking_seconds_for_cancel_job}'),
                f'sky jobs cancel -y -n {managed_job_name}-0',
                # Cancel job-3 and wait for final states
                f'sky jobs cancel -y -n {managed_job_name}-3',
                wait_for_status(f'{managed_job_name}-1',
                                [sky.ManagedJobStatus.SUCCEEDED]),
                wait_for_status(f'{managed_job_name}-2',
                                [sky.ManagedJobStatus.SUCCEEDED]),
                wait_for_status(f'{managed_job_name}-0', [
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.CANCELLING
                ]),
                wait_for_status(f'{managed_job_name}-3', [
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.CANCELLING
                ]),
                # Final count checks - job-0,3 cancelled, job-1,2 succeeded, old jobs have their own states
                f'result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {managed_job_name} | grep SUCCEEDED | wc -l | grep 3',
                f'result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {managed_job_name} | grep \'CANCELLING\\|CANCELLED\' | wc -l | grep 3',
            ]

        # Combine all commands
        current_commands = common_initial_commands + version_specific_commands

        commands = [
            *self._switch_to_base(
                # Cover jobs launched in the old version and ran to terminal states
                launch_job(f'{managed_job_name}-old-0',
                           f'echo hi; sleep {blocking_seconds_for_cancel_job}'),
                launch_job(f'{managed_job_name}-old-1', 'echo hi'),
                wait_for_status(f'{managed_job_name}-old-1',
                                [sky.ManagedJobStatus.SUCCEEDED]),
                wait_for_status(f'{managed_job_name}-old-0',
                                [sky.ManagedJobStatus.RUNNING]),
                f'sky jobs cancel -n {managed_job_name}-old-0 -y',
                wait_for_status(f'{managed_job_name}-old-0', [
                    sky.ManagedJobStatus.CANCELLED,
                    sky.ManagedJobStatus.CANCELLING
                ]),
                # Cover jobs launched in the new version and still running after upgrade
                launch_job(f'{managed_job_name}-0',
                           f'echo hi; sleep {blocking_seconds_for_cancel_job}'),
                launch_job(f'{managed_job_name}-1', 'echo hi; sleep 400'),
                wait_for_status(f'{managed_job_name}-0',
                                [sky.ManagedJobStatus.RUNNING]),
                wait_for_status(f'{managed_job_name}-1',
                                [sky.ManagedJobStatus.RUNNING]),
            ),
            *self._switch_to_current(*current_commands),
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky jobs cancel -n {managed_job_name}* -y'
        self.run_compatibility_test(managed_job_name, commands, teardown)

    def test_serve_deployment(self, generic_cloud: str):
        """Test serve deployment functionality across versions"""
        serve_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && sky check && '
            f'sky serve up --cloud {generic_cloud} -y -n {serve_name}-0 examples/serve/http_server/task.yaml',
            f'{self.ACTIVATE_BASE} && sky serve status {serve_name}-0',
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && sky serve status {serve_name}-0',
            f'{self.ACTIVATE_CURRENT} && sky serve logs {serve_name}-0 2 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --controller {serve_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --load-balancer {serve_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve update {serve_name}-0 -y --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 4 examples/serve/http_server/task.yaml',
            f'{self.ACTIVATE_CURRENT} && sky serve up --cloud {generic_cloud} -y -n {serve_name}-1 examples/serve/http_server/task.yaml',
            f'{self.ACTIVATE_CURRENT} && sky serve status {serve_name}-1',
            f'{self.ACTIVATE_CURRENT} && sky serve down {serve_name}-0 -y',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --controller {serve_name}-1 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --load-balancer {serve_name}-1 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve down {serve_name}-1 -y',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky serve down {serve_name}* -y'
        self.run_compatibility_test(serve_name, commands, teardown)

    def test_client_server_compatibility(self, generic_cloud: str):
        """Test client server compatibility across versions"""
        if self.BASE_API_VERSION < self.CURRENT_MIN_COMPATIBLE_API_VERSION or \
                self.CURRENT_API_VERSION < self.BASE_MIN_COMPATIBLE_API_VERSION:
            if self.BASE_API_VERSION < 11:
                pytest.skip(
                    f'Base API version: {self.BASE_API_VERSION} is too old, the enforce compatibility is supported after 11(release 0.10.0)'
                )
            if self.CURRENT_API_VERSION < 11:
                pytest.skip(
                    f'Current API version: {self.CURRENT_API_VERSION} is too old, the enforce compatibility is supported after 11(release 0.10.0)'
                )
            # This test runs against the master branch or the latest release
            # version, which must enforce compatibility in this test based on
            # our new version strategy that adjacent minor versions must be
            # compatible.
            pytest.fail(
                f'Base API version: {self.BASE_API_VERSION} and current API '
                f'version: {self.CURRENT_API_VERSION} are not compatible:\n'
                f'- Base minimal compatible API version: {self.BASE_MIN_COMPATIBLE_API_VERSION}\n'
                f'- Current minimal compatible API version: {self.CURRENT_MIN_COMPATIBLE_API_VERSION}\n'
                'Change is rejected since it breaks the compatibility between adjacent versions'
            )
        cluster_name = smoke_tests_utils.get_cluster_name()
        job_name = f"{cluster_name}-job"
        commands = [
            # Check API version compatibility
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'{self.ACTIVATE_CURRENT} && result="$(sky status 2>&1)" || true; '
            'if echo "$result" | grep -q "SkyPilot API server is too old"; then '
            '  echo "$result" && exit 1; '
            'fi',
            # managed job test
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'sky jobs launch -d --infra {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} -n {job_name} "echo hello world; sleep 60"',
            # No restart on switch to current, cli in current, server in base, verify cli works with different version of sky server
            f'{self.ACTIVATE_CURRENT} && sky api status',
            f'{self.ACTIVATE_CURRENT} && sky api info',
            f'{self.ACTIVATE_CURRENT} && {self._wait_for_managed_job_status(job_name, [sky.ManagedJobStatus.RUNNING])}',
            f'{self.ACTIVATE_CURRENT} && result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {job_name} | grep RUNNING',
            f'{self.ACTIVATE_CURRENT} && result="$(sky jobs logs --no-follow -n {job_name})"; echo "$result"; echo "$result" | grep "hello world"',
            f'{self.ACTIVATE_CURRENT} && {self._wait_for_managed_job_status(job_name, [sky.ManagedJobStatus.SUCCEEDED])}',
            f'{self.ACTIVATE_CURRENT} && result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {job_name} | grep SUCCEEDED',
            # cluster launch/exec test
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} &&'
            f'sky launch --infra {generic_cloud} -y -c {cluster_name} "echo hello world; sleep 60"',
            # No restart on switch to current, cli in current, server in base
            f'{self.ACTIVATE_CURRENT} && result="$(sky queue {cluster_name})"; echo "$result"',
            f'{self.ACTIVATE_CURRENT} && result="$(sky logs {cluster_name} 1 --status)"; echo "$result"',
            f'{self.ACTIVATE_CURRENT} && result="$(sky logs {cluster_name} 1)"; echo "$result"; echo "$result" | grep "hello world"',
            f'{self.ACTIVATE_BASE} && sky exec {cluster_name} "echo from base"',
            f'{self.ACTIVATE_CURRENT} && result="$(sky logs {cluster_name} 2)"; echo "$result"; echo "$result" | grep "from base"',
            f'{self.ACTIVATE_BASE} && sky autostop -i 1 -y {cluster_name}',
            # serve test
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'sky serve up --infra {generic_cloud} -y -n {cluster_name}-0 examples/serve/http_server/task.yaml',
            # No restart on switch to current, cli in current, server in base
            f'{self.ACTIVATE_CURRENT} && sky serve status {cluster_name}-0',
            f'{self.ACTIVATE_CURRENT} && sky serve logs {cluster_name}-0 2 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --controller {cluster_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --load-balancer {cluster_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve down {cluster_name}-0 -y',
        ]

        teardown = f'{self.ACTIVATE_BASE} && sky down {cluster_name} -y && sky serve down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_sdk_compatibility(self, generic_cloud: str):
        """Test SDK compatibility across versions"""
        if self.BASE_API_VERSION != self.CURRENT_API_VERSION:
            pytest.skip(
                f'Base API version: {self.BASE_API_VERSION} and current API '
                f'version: {self.CURRENT_API_VERSION} are different, '
                'skipping test')
        cluster_name = smoke_tests_utils.get_cluster_name()
        job_name = f"{cluster_name}-job"
        sdk_utils_file = os.path.join(os.path.dirname(__file__),
                                      'sdk_backward_compat_utils.py')
        cmd_to_sdk_file = f'python {sdk_utils_file}'
        commands = [
            # cluster test
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'{cmd_to_sdk_file} launch-cluster --cloud {generic_cloud} --cluster-name {cluster_name} --command "echo hello world; sleep 60"',
            f'{self.ACTIVATE_CURRENT} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'{cmd_to_sdk_file} get-cluster-status --cluster-name {cluster_name} | grep "\'name\': \'{cluster_name}\'"',
            f'{self.ACTIVATE_CURRENT} && {cmd_to_sdk_file} queue --cluster-name {cluster_name} | grep "\'job_id\': 1"',
            f'{self.ACTIVATE_CURRENT} && {cmd_to_sdk_file} cluster-logs --cluster-name {cluster_name} --job-id 1 | grep "hello world"',
            # # managed job test
            f'{self.ACTIVATE_BASE} && {smoke_tests_utils.SKY_API_RESTART} && '
            f'{cmd_to_sdk_file} launch-managed-job --job-name {job_name} --cloud {generic_cloud} --command "echo hello world; sleep 60"',
            f'{self.ACTIVATE_CURRENT} && {cmd_to_sdk_file} managed-job-logs --job-name {job_name} | grep "hello world"',
            f'{self.ACTIVATE_CURRENT} && {cmd_to_sdk_file} managed-job-queue | grep "{job_name}"',
            # ensure job success, through cli not through sdk
            f'{self.ACTIVATE_CURRENT} && {self._wait_for_managed_job_status(job_name, [sky.ManagedJobStatus.SUCCEEDED])}',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && {cmd_to_sdk_file} down --cluster-name {cluster_name}'
        self.run_compatibility_test(cluster_name, commands, teardown)

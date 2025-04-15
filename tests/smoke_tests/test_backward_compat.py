import pathlib
import subprocess
from typing import Sequence

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky.backends import backend_utils

# Add a pytest mark to limit concurrency to 1
pytestmark = pytest.mark.xdist_group(name="backward_compat")
ENDPOINT = 'http://127.0.0.1:46580/api/health'


class TestBackwardCompatibility:
    # Constants
    MANAGED_JOB_PREFIX = 'test-back-compat'
    SERVE_PREFIX = 'test-back-compat'
    TEST_TIMEOUT = 1800  # 30 minutes

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

    # Fix the flakyness of the test, server may not ready when we run the command after restart.
    WAIT_FOR_API = (
        'for i in $(seq 1 30); do '
        f'if curl -s {ENDPOINT} > /dev/null; then '
        'echo "API is up and running"; break; fi; '
        'echo "Waiting for API to be ready... ($i/30)"; '
        '[ $i -eq 30 ] && echo "Timed out waiting for API to be ready" && exit 1; '
        'sleep 1; done')

    SKY_API_RESTART = f'sky api stop || true && sky api start && {WAIT_FOR_API}'

    # Shorthand of switching to base environment and running a list of commands in environment.
    def _switch_to_base(self, *cmds: str) -> list[str]:
        cmds = [self.SKY_API_RESTART] + list(cmds)
        return [f'{self.ACTIVATE_BASE} && {c}' for c in cmds]

    # Shorthand of switching to current environment and running a list of commands in environment.
    def _switch_to_current(self, *cmds: str) -> list[str]:
        cmds = [self.SKY_API_RESTART] + list(cmds)
        return [f'{self.ACTIVATE_CURRENT} && {c}' for c in cmds]

    def _run_cmd(self, cmd: str):
        subprocess.run(cmd, shell=True, check=True, executable='/bin/bash')

    def _wait_for_managed_job_status(self, job_name: str,
                                     status: Sequence[sky.ManagedJobStatus]):
        return smoke_tests_utils.get_cmd_wait_until_managed_job_status_contains_matching_job_name(
            job_name=job_name, job_status=status, timeout=300)

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
            'uv pip install -e .[all]',)

        # Install current version in current environment
        self._run_cmd(
            f'{self.ACTIVATE_CURRENT} && '
            'uv pip uninstall skypilot && '
            'uv pip install --prerelease=allow "azure-cli>=2.65.0" && '
            'uv pip install -e .[all]',)

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
            need_launch_cmd = f'{self.ACTIVATE_CURRENT} && sky launch --cloud {generic_cloud} -y -c {cluster_name}'
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky autostop -i 10 -y {cluster_name}',
            f'{self.ACTIVATE_BASE} && sky exec -d --cloud {generic_cloud} --num-nodes 2 {cluster_name} sleep 100',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && result="$(sky status {cluster_name})"; echo "$result"; echo "$result" | grep UP',
            f'{self.ACTIVATE_CURRENT} && result="$(sky status -r {cluster_name})"; echo "$result"; echo "$result" | grep UP',
            need_launch_cmd,
            f'{self.ACTIVATE_CURRENT} && sky exec -d --cloud {generic_cloud} {cluster_name} sleep 50',
            f'{self.ACTIVATE_CURRENT} && result="$(sky queue -u {cluster_name})"; echo "$result"; echo "$result" | grep RUNNING | wc -l | grep 2',
            f'{self.ACTIVATE_CURRENT} && s=$(sky launch --cloud {generic_cloud} -d -c {cluster_name} examples/minimal.yaml) && '
            'echo "$s" | sed -r "s/\\x1B\\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job ID: 4"',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status | grep -E "RUNNING|SUCCEEDED"',
            f"""
            {self.ACTIVATE_CURRENT} && {smoke_tests_utils.get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=f"{cluster_name}",
                job_id=2,
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=120)} && {smoke_tests_utils.get_cmd_wait_until_job_status_contains_matching_job_id(
                cluster_name=f"{cluster_name}",
                job_id=3,
                job_status=[sky.JobStatus.SUCCEEDED],
                timeout=120)}
            """
            f'{self.ACTIVATE_CURRENT} && result="$(sky queue -u {cluster_name})"; echo "$result"; echo "$result" | grep SUCCEEDED | wc -l | grep 4'
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y || true'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_cluster_stop_start(self, generic_cloud: str):
        """Test cluster stop/start functionality across versions"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && s=$(sky exec --cloud {generic_cloud} -d {cluster_name} examples/minimal.yaml) && '
            'echo "$s" | sed -r "s/\\x1B\\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | grep "Job submitted, ID: 2"',
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_autostop_functionality(self, generic_cloud: str):
        """Test autostop functionality across versions"""
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky autostop -y -i0 {cluster_name}',
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
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky launch --cloud {generic_cloud} -y --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
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
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky start -y {cluster_name}',
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
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --num-nodes 2 -c {cluster_name} examples/multi_hostname.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky start -y {cluster_name}',
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
                job_name=job_name, job_status=status, timeout=300)

        commands = [
            *self._switch_to_base(
                # Cover jobs launched in the old version and ran to terminal states
                launch_job(f'{managed_job_name}-old-0', 'echo hi; sleep 1000'),
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
                launch_job(f'{managed_job_name}-0', 'echo hi; sleep 1000'),
                launch_job(f'{managed_job_name}-1', 'echo hi; sleep 400'),
                wait_for_status(f'{managed_job_name}-0',
                                [sky.ManagedJobStatus.RUNNING]),
                wait_for_status(f'{managed_job_name}-1',
                                [sky.ManagedJobStatus.RUNNING]),
            ),
            *self._switch_to_current(
                f'result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {managed_job_name} | grep RUNNING | wc -l | grep 2',
                f'result="$(sky jobs logs --no-follow -n {managed_job_name}-1)"; echo "$result"; echo "$result" | grep hi',
                launch_job(f'{managed_job_name}-2', 'echo hi; sleep 400'),
                # Cover cancelling jobs launched in the new version
                launch_job(f'{managed_job_name}-3', 'echo hi; sleep 1000'),
                f'result="$(sky jobs logs --no-follow -n {managed_job_name}-2)"; echo "$result"; echo "$result" | grep hi',
                f'sky jobs cancel -y -n {managed_job_name}-0',
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
                f'result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {managed_job_name} | grep SUCCEEDED | wc -l | grep 3',
                f'result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {managed_job_name} | grep \'CANCELLING\\|CANCELLED\' | wc -l | grep 3',
            ),
        ]
        teardown = f'{self.ACTIVATE_CURRENT} && sky jobs cancel -n {managed_job_name}* -y'
        self.run_compatibility_test(managed_job_name, commands, teardown)

    def test_serve_deployment(self, generic_cloud: str):
        """Test serve deployment functionality across versions"""
        serve_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky serve up --cloud {generic_cloud} -y -n {serve_name}-0 examples/serve/http_server/task.yaml',
            f'{self.ACTIVATE_BASE} && sky serve status {serve_name}-0',
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky serve status {serve_name}-0',
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
        cluster_name = smoke_tests_utils.get_cluster_name()
        job_name = f"{cluster_name}-job"
        commands = [
            # Check API version compatibility
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'{self.ACTIVATE_CURRENT} && result="$(sky status 2>&1)" || true; '
            'if echo "$result" | grep -q "SkyPilot API server is too old"; then '
            '  echo "$result" && exit 1; '
            'fi',
            # managed job test
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky jobs launch -d --cloud {generic_cloud} -y {smoke_tests_utils.LOW_RESOURCE_ARG} -n {job_name} "echo hello world; sleep 60"',
            # No restart on switch to current, cli in current, server in base, verify cli works with different version of sky server
            f'{self.ACTIVATE_CURRENT} && sky api status',
            f'{self.ACTIVATE_CURRENT} && sky api info',
            f'{self.ACTIVATE_CURRENT} && {self._wait_for_managed_job_status(job_name, [sky.ManagedJobStatus.RUNNING])}',
            f'{self.ACTIVATE_CURRENT} && result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {job_name} | grep RUNNING',
            f'{self.ACTIVATE_CURRENT} && result="$(sky jobs logs --no-follow -n {job_name})"; echo "$result"; echo "$result" | grep "hello world"',
            f'{self.ACTIVATE_CURRENT} && {self._wait_for_managed_job_status(job_name, [sky.ManagedJobStatus.SUCCEEDED])}',
            f'{self.ACTIVATE_CURRENT} && result="$(sky jobs queue)"; echo "$result"; echo "$result" | grep {job_name} | grep SUCCEEDED',
            # cluster launch/exec test
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} &&'
            f'sky launch --cloud {generic_cloud} -y -c {cluster_name} "echo hello world; sleep 60"',
            # No restart on switch to current, cli in current, server in base
            f'{self.ACTIVATE_CURRENT} && result="$(sky queue {cluster_name})"; echo "$result"',
            f'{self.ACTIVATE_CURRENT} && result="$(sky logs {cluster_name} 1 --status)"; echo "$result"',
            f'{self.ACTIVATE_CURRENT} && result="$(sky logs {cluster_name} 1)"; echo "$result"; echo "$result" | grep "hello world"',
            f'{self.ACTIVATE_BASE} && sky exec {cluster_name} "echo from base"',
            f'{self.ACTIVATE_CURRENT} && result="$(sky logs {cluster_name} 2)"; echo "$result"; echo "$result" | grep "from base"',
            f'{self.ACTIVATE_BASE} && sky autostop -i 1 -y {cluster_name}',
            # serve test
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky serve up --cloud {generic_cloud} -y -n {cluster_name}-0 examples/serve/http_server/task.yaml',
            # No restart on switch to current, cli in current, server in base
            f'{self.ACTIVATE_CURRENT} && sky serve status {cluster_name}-0',
            f'{self.ACTIVATE_CURRENT} && sky serve logs {cluster_name}-0 2 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --controller {cluster_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --load-balancer {cluster_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve down {cluster_name}-0 -y',
        ]

        teardown = f'{self.ACTIVATE_BASE} && sky down {cluster_name} -y && sky serve down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

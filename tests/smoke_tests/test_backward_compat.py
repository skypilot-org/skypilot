from pathlib import Path
import subprocess

import pytest
from smoke_tests import smoke_tests_utils


class TestBackwardCompatibility:
    # Constants
    BASE_BRANCH = 'master'
    MANAGED_JOB_PREFIX = 'test-back-compat'
    SERVE_PREFIX = 'test-back-compat'
    TEST_TIMEOUT = 1800  # 30 minutes
    GCLOUD_INSTALL_CMD = '''
    wget --quiet https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-sdk-424.0.0-linux-x86_64.tar.gz &&
    tar xzf google-cloud-sdk-424.0.0-linux-x86_64.tar.gz &&
    rm -rf ~/google-cloud-sdk &&
    mv google-cloud-sdk ~/ &&
    ~/google-cloud-sdk/install.sh -q &&
    echo "source ~/google-cloud-sdk/path.bash.inc" >> ~/.bashrc
    '''

    # Environment paths
    BASE_ENV_DIR = Path('~/sky-back-compat-base').expanduser()
    CURRENT_ENV_DIR = Path('~/sky-back-compat-current').expanduser()
    BASE_SKY_DIR = Path('~/sky-base').expanduser()

    # Command templates
    ACTIVATE_BASE = f'source {BASE_ENV_DIR}/bin/activate && cd {BASE_SKY_DIR}'
    ACTIVATE_CURRENT = f'source {CURRENT_ENV_DIR}/bin/activate && cd {smoke_tests_utils.CURRENT_DIR}'
    DEACTIVATE = 'deactivate'
    SKY_API_RESTART = 'sky api stop || true && sky api start'

    @pytest.fixture(scope='session', autouse=True)
    def class_setup(self, generic_cloud):
        '''Class-wide setup fixture for environment preparation'''
        self.generic_cloud = generic_cloud

        # Install gcloud if missing
        if subprocess.run('gcloud --version', shell=True).returncode != 0:
            subprocess.run(self.GCLOUD_INSTALL_CMD, shell=True, check=True)

        # Clone base SkyPilot version
        if not self.BASE_SKY_DIR.exists():
            self.BASE_SKY_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                f'git clone -b {self.BASE_BRANCH} '
                f'https://github.com/skypilot-org/skypilot.git {self.BASE_SKY_DIR}',
                shell=True,
                check=True,
            )

        # Create and set up virtual environments
        for env_dir in [self.BASE_ENV_DIR, self.CURRENT_ENV_DIR]:
            if not env_dir.exists():
                subprocess.run(f'python -m venv {env_dir}',
                               shell=True,
                               check=True)

        # Install dependencies in base environment
        subprocess.run(
            f'{self.ACTIVATE_BASE} && '
            'pip install -e .[all] && '
            'pip install --prerelease=allow azure-cli',
            shell=True,
            check=True,
        )

        # Install current version in current environment
        subprocess.run(
            f'{self.ACTIVATE_CURRENT} && '
            'pip install -e .[all] && '
            'pip install --prerelease=allow azure-cli',
            shell=True,
            check=True,
        )

    def run_compatibility_test(self, test_name: str, commands: list,
                               teardown: str):
        '''Helper method to create and run tests with proper cleanup'''
        test = smoke_tests_utils.Test(
            test_name,
            commands,
            teardown=teardown,
            timeout=self.TEST_TIMEOUT,
        )
        smoke_tests_utils.run_one_test(test)

    def test_cluster_launch_and_exec(self, generic_cloud: str):
        '''Test basic cluster launch and execution across versions'''
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky autostop -i 10 -y {cluster_name}',
            f'{self.ACTIVATE_BASE} && sky exec -d --cloud {generic_cloud} --num-nodes 2 {cluster_name} sleep 100',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky status {cluster_name} | grep UP',
            f'{self.ACTIVATE_CURRENT} && sky exec -d --cloud {generic_cloud} {cluster_name} sleep 50',
            f'{self.ACTIVATE_CURRENT} && sky queue -u {cluster_name} | grep RUNNING | wc -l | grep 2',
            f'{self.ACTIVATE_CURRENT} && sky launch --cloud {generic_cloud} -d -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
        ]
        teardown = f'sky down {cluster_name}* -y || true'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_cluster_stop_start(self, generic_cloud: str):
        '''Test cluster stop/start functionality across versions'''
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky stop -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky exec --cloud {generic_cloud} -d {cluster_name} examples/minimal.yaml',
        ]
        teardown = f'sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_autostop_functionality(self, generic_cloud: str):
        '''Test autostop functionality across versions'''
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky autostop -y -i0 {cluster_name}',
            'sleep 120',
            f'{self.ACTIVATE_CURRENT} && sky status -r {cluster_name} | grep STOPPED',
        ]
        teardown = f'sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_single_node_operations(self, generic_cloud: str):
        '''Test single node operations (launch, stop, restart, logs)'''
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky launch --cloud {generic_cloud} -y --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1 --status',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
        ]
        teardown = f'sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_restarted_cluster_operations(self, generic_cloud: str):
        '''Test operations on restarted clusters'''
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1 --status',
            f'{self.ACTIVATE_CURRENT} && sky launch --cloud {generic_cloud} -y -c {cluster_name} examples/minimal.yaml',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
        ]
        teardown = f'sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_multi_node_operations(self, generic_cloud: str):
        '''Test multi-node cluster operations'''
        cluster_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky launch --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -c {cluster_name} examples/multi_hostname.yaml',
            f'{self.ACTIVATE_BASE} && sky stop -y {cluster_name}',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky start -y {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 1 --status',
            f'{self.ACTIVATE_CURRENT} && sky exec --cloud {generic_cloud} {cluster_name} examples/multi_hostname.yaml',
            f'{self.ACTIVATE_CURRENT} && sky queue {cluster_name}',
            f'{self.ACTIVATE_CURRENT} && sky logs {cluster_name} 2 --status',
        ]
        teardown = f'sky down {cluster_name}* -y'
        self.run_compatibility_test(cluster_name, commands, teardown)

    def test_managed_jobs(self, generic_cloud: str):
        '''Test managed jobs functionality across versions'''
        managed_job_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky jobs launch -d --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -n {managed_job_name}-0 \'echo hi; sleep 1000\'',
            f'{self.ACTIVATE_BASE} && sky jobs launch -d --cloud {generic_cloud} -y --cpus 2 --num-nodes 2 -n {managed_job_name}-1 \'echo hi; sleep 400\'',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && '
            f'sky jobs queue | grep {managed_job_name} | grep RUNNING | wc -l | grep 2',
            f'{self.ACTIVATE_CURRENT} && sky jobs logs --no-follow -n {managed_job_name}-1 | grep hi',
            f'{self.ACTIVATE_CURRENT} && sky jobs launch -d --cloud {generic_cloud} --num-nodes 2 -y -n {managed_job_name}-2 \'echo hi; sleep 40\'',
            f'{self.ACTIVATE_CURRENT} && sky jobs logs --no-follow -n {managed_job_name}-2 | grep hi',
            f'{self.ACTIVATE_CURRENT} && sky jobs cancel -y -n {managed_job_name}-0',
            f'{self.ACTIVATE_CURRENT} && sky jobs queue | grep {managed_job_name} | grep SUCCEEDED | wc -l | grep 2',
            f'{self.ACTIVATE_CURRENT} && sky jobs queue | grep {managed_job_name} | grep \'CANCELLING\\|CANCELLED\' | wc -l | grep 1',
        ]
        teardown = f'sky jobs cancel -n {managed_job_name}* -y'
        self.run_compatibility_test(managed_job_name, commands, teardown)

    def test_serve_deployment(self, generic_cloud: str):
        '''Test serve deployment functionality across versions'''
        serve_name = smoke_tests_utils.get_cluster_name()
        commands = [
            f'{self.ACTIVATE_BASE} && {self.SKY_API_RESTART} && '
            f'sky serve up --cloud {generic_cloud} -y -n {serve_name}-0 examples/serve/http_server/task.yaml',
            self.DEACTIVATE,
            f'{self.ACTIVATE_CURRENT} && {self.SKY_API_RESTART} && sky serve status {serve_name}-0',
            f'{self.ACTIVATE_CURRENT} && sky serve logs {serve_name}-0 2 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --controller {serve_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --load-balancer {serve_name}-0 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve update {serve_name}-0 -y --cloud {generic_cloud} --cpus 2 --num-nodes 4 examples/serve/http_server/task.yaml',
            f'{self.ACTIVATE_CURRENT} && sky serve up --cloud {generic_cloud} -y -n {serve_name}-1 examples/serve/http_server/task.yaml',
            f'{self.ACTIVATE_CURRENT} && sky serve status {serve_name}-1',
            f'{self.ACTIVATE_CURRENT} && sky serve down {serve_name}-0 -y',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --controller {serve_name}-1 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve logs --load-balancer {serve_name}-1 --no-follow',
            f'{self.ACTIVATE_CURRENT} && sky serve down {serve_name}-1 -y',
        ]
        teardown = f'sky serve down {serve_name}* -y'
        self.run_compatibility_test(serve_name, commands, teardown)

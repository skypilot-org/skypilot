import os
import subprocess
import sys
import tempfile
import textwrap
from typing import List, Optional, Tuple, NamedTuple

import colorama
from click import testing as cli_testing
import pytest
import yaml

from sky import cli
from sky.utils import command_runner
from sky.utils import subprocess_utils


# TODO(mluo): Refactor smoke test methods
class Test(NamedTuple):
    name: str
    # Each command is executed serially.  If any failed, the remaining commands
    # are not run and the test is treated as failed.
    commands: List[str]
    teardown: Optional[str] = None
    # Timeout for each command in seconds.
    timeout: int = 15 * 60

    def echo(self, message: str):
        # pytest's xdist plugin captures stdout; print to stderr so that the
        # logs are streaming while the tests are running.
        prefix = f'[{self.name}]'
        message = f'{prefix} {message}'
        message = message.replace('\n', f'\n{prefix} ')
        print(message, file=sys.stderr, flush=True)


def run_one_test(test: Test) -> Tuple[int, str, str]:
    log_file = tempfile.NamedTemporaryFile('a',
                                           prefix=f'{test.name}-',
                                           suffix='.log',
                                           delete=False)
    test.echo(f'Test started. Log: less {log_file.name}')
    for command in test.commands:
        log_file.write(f'+ {command}\n')
        log_file.flush()
        proc = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=True,
        )
        try:
            proc.wait(timeout=test.timeout)
        except subprocess.TimeoutExpired as e:
            log_file.flush()
            test.echo(e)
            proc.returncode = 1  # None if we don't set it.
            break

        if proc.returncode:
            break

    style = colorama.Style
    fore = colorama.Fore
    outcome = (f'{fore.RED}Failed{style.RESET_ALL}'
               if proc.returncode else f'{fore.GREEN}Passed{style.RESET_ALL}')
    reason = f'\nReason: {command}' if proc.returncode else ''
    msg = (f'{outcome}.'
           f'{reason}'
           f'\nLog: less {log_file.name}\n')
    test.echo(msg)
    log_file.write(msg)
    if proc.returncode == 0 and test.teardown is not None:
        subprocess_utils.run(
            test.teardown,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=10 * 60,  # 10 mins
            shell=True,
        )

    if proc.returncode:
        raise Exception(f'test failed: less {log_file.name}')


# ---------- Testing On-prem ----------
class TestOnprem:
    """On-prem tests.

    Running these tests requires one or more nodes manually setup to run onprem Sky.
    You can either use a pre-setup cluster the sky team maintains
    (message Michael Luo for ip and credentials) or:
    1. Provision a VM on any cloud provider
    2. Add IP and path to private key in fixtures `_server_ips` and `_private_key` below.
    3. Create a user ubuntu, if not already exists and add the ssh key to allowed keys.
    4. Install Ray and Sky (described in sky onprem readme)
    5. Run tests with `pytest -s -q --tb=short --disable-warnings
       tests/test_onprem.py::TestOnprem`
    """
    # TODO(mluo): Automate on-prem node provisioning using boto3/docker.
    @pytest.fixture
    def server_ips(self):
        yield ['3.142.96.58']

    @pytest.fixture
    def local_cluster_name(self):
        yield 'on-prem-smoke-test'

    @pytest.fixture
    def admin_ssh_user(self):
        yield 'ubuntu'

    @pytest.fixture
    def first_ssh_user(self):
        yield 'test'

    @pytest.fixture
    def second_ssh_user(self):
        yield 'test1'

    @pytest.fixture
    def ssh_private_key(self):
        yield '~/.ssh/on-prem-smoke-key'

    @pytest.fixture
    def admin_cluster_config(self, server_ips, admin_ssh_user, ssh_private_key,
                             local_cluster_name):
        # Generates a cluster config for the sys admin.
        yaml_dict = {
            'cluster': {
                'ips': server_ips,
                'name': local_cluster_name
            },
            'auth': {
                'ssh_user': admin_ssh_user,
                'ssh_private_key': ssh_private_key
            }
        }
        return yaml_dict

    @pytest.fixture
    def cluster_config_setup(self, admin_cluster_config, local_cluster_name):
        # Returns a function that setups up the cluster for the user
        # Set up user's cluster config to be stored in `~/.sky/local/...`.
        def _local_cluster_setup(ssh_user, cluster_name=local_cluster_name):
            local_sky_folder = os.path.expanduser('~/.sky/local/')
            os.makedirs(local_sky_folder, exist_ok=True)
            with open(f'{local_sky_folder}/{cluster_name}.yml', 'w') as f:
                # Change from normal admin to user (emulates admin sending users
                # the private key)
                admin_cluster_config['auth']['ssh_user'] = ssh_user
                admin_cluster_config['cluster']['name'] = cluster_name
                yaml.dump(admin_cluster_config, f)

        return _local_cluster_setup

    @pytest.fixture
    def jobs_reset(self, admin_cluster_config, first_ssh_user, second_ssh_user):
        # Deletes all prior jobs on the cluster.
        head_ip = admin_cluster_config['cluster']['ips'][0]
        ssh_user = admin_cluster_config['auth']['ssh_user']
        ssh_key = admin_cluster_config['auth']['ssh_private_key']

        ssh_credentials = (ssh_user, ssh_key, 'sky-admin-deploy')
        runner = command_runner.SSHCommandRunner(head_ip, *ssh_credentials)

        # Removing /tmp/ray and ~/.sky to reset jobs id to index 0.
        rc = runner.run(('sudo rm -rf /tmp/ray; '
                         f'sudo rm -rf /home/{first_ssh_user}/.sky; '
                         f'sudo rm -rf /home/{second_ssh_user}/.sky'),
                        stream_logs=False)

    @pytest.fixture
    def admin_setup(self, jobs_reset, admin_cluster_config):
        # Sets up Ray cluster on the onprem node.
        cluster_name = admin_cluster_config['cluster']['name']
        with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
            yaml.dump(admin_cluster_config, f)
            file_path = f.name
            # Must tear down local cluster in ~/.sky. Running `sky down`
            # removes the local cluster from `sky status` and terminates
            # runnng jobs.
            subprocess.check_output(
                f'sky down -y -p {cluster_name}; sky admin deploy {file_path}',
                shell=True)

    def test_onprem_admin_deploy(self, admin_cluster_config, jobs_reset):
        # Tests running admin setup commands on on-prem cluster.
        name = admin_cluster_config['cluster']['name']
        with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
            f.write(str(admin_cluster_config))
            f.flush()
            file_path = f.name
            test = Test(
                'test-admin-deploy',
                [
                    f'sky admin deploy {file_path}',
                ],
                # Cleaning up artifacts created from the test.
                f'rm -rf ~/.sky/local/{name}.yml')
            run_one_test(test)

    def test_onprem_inline(self, local_cluster_name, admin_setup,
                           cluster_config_setup, first_ssh_user):

        # Setups up local cluster config for user 'test'
        cluster_config_setup(first_ssh_user)

        # Tests running commands on on-prem cluster.
        name = local_cluster_name

        test = Test(
            'test_onprem_inline_commands',
            [
                f'sky launch -c {name} -y --env TEST_ENV="hello world" -- "([[ ! -z \\"\$TEST_ENV\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
                f'sky logs {name} 1 --status',
                f'sky exec {name} --env TEST_ENV2="success" "([[ ! -z \\"\$TEST_ENV2\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_IPS\\" ]] && [[ ! -z \\"\$SKYPILOT_NODE_RANK\\" ]]) || exit 1"',
                f'sky logs {name} 2 --status',
            ],
            # Cleaning up artifacts created from the test.
            f'sky down -y {name}; rm -f ~/.sky/local/{name}.yml',
        )
        run_one_test(test)

    def test_onprem_yaml(self, local_cluster_name, admin_setup,
                         cluster_config_setup, first_ssh_user):
        # Setups up local cluster config for user 'test'
        cluster_config_setup(first_ssh_user)

        # Tests running Sky task yaml on on-prem cluster.
        name = local_cluster_name
        yaml_dict = {
            'setup': 'echo "Running setup"',
            'run': textwrap.dedent("""\
                    set -e
                    echo $(whoami)
                    pkill -f ray
                    echo NODE ID: $SKYPILOT_NODE_RANK
                    echo NODE IPS: "$SKYPILOT_NODE_IPS"
                    exit 0""")
        }

        with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
            yaml.dump(yaml_dict, f)
            file_path = f.name
            test = Test(
                'test_onprem_yaml',
                [
                    f'sky launch -c {name} -y {file_path}',
                    f'sky logs {name} 1 --status',
                    f'sky exec {name} {file_path}',
                    f'sky logs {name} 2 --status',
                ],
                # Cleaning up artifacts created from the test.
                f'sky down -y {name}; rm -f ~/.sky/local/{name}.yml',
            )
            run_one_test(test)

    def test_onprem_job_queue(self, local_cluster_name, admin_setup,
                              cluster_config_setup, first_ssh_user):
        # Setups up local cluster config for user 'test'
        cluster_config_setup(first_ssh_user)

        # Tests running many jobs on the on-prem cluster.
        name = local_cluster_name
        test = Test(
            'test_onprem_job_queue',
            [
                f'sky launch -y -c {name} -- "echo hi"',
                f'sky exec {name} -d -- "echo hi"',
                f'sky exec {name} -d -- "echo hi"',
                f'sky exec {name} -d -- "echo hi"',
                # Call sleep on the 5th job for `sky cancel` to cancel
                # a running/pending job.
                f'sky exec {name} -d -- "sleep 300"',
                f'sky exec {name} -d -- "echo hi"',
                f'sky cancel {name} 5',
                f'sky logs {name} 1',
                f's=$(sky queue {name}); printf "$s"; echo; echo; printf "$s" | grep "^5\\b" | grep CANCELLED',
            ],
            # Cleaning up artifacts created from the test.
            f'sky down -y {name}; rm -f ~/.sky/local/{name}.yml',
        )
        run_one_test(test)

    def test_onprem_multi_tenancy(self, local_cluster_name, admin_setup,
                                  cluster_config_setup, first_ssh_user,
                                  second_ssh_user):
        # Setups up local cluster config for users `test` and `test1`
        first_cluster_name = local_cluster_name
        cluster_config_setup(first_ssh_user)
        second_cluster_name = 'on-prem-smoke-test-1'
        cluster_config_setup(second_ssh_user, second_cluster_name)

        # Tests running many jobs on the on-prem cluster.
        test = Test(
            'test_onprem_multi_tenancy',
            [
                f'sky launch -y -c {first_cluster_name} -- "echo hi"',
                f'sky launch -y -c {second_cluster_name} -- "echo hi"',
                f'sky exec {first_cluster_name} -d -- "sleep 300"',
                f'sky exec {second_cluster_name} -d -- "sleep 300"',
                f'sky cancel {first_cluster_name} 2',
                'sleep 5',
                f's=$(sky queue {first_cluster_name}); printf "$s"; echo; echo; printf "$s" | grep "^2\\b" | grep CANCELLED',
                # User 1 should not cancel user 2's jobs.
                f's=$(sky queue {second_cluster_name}); printf "$s"; echo; echo; printf "$s" | grep "^2\\b" | grep -v CANCELLED',
                f'sky cancel {second_cluster_name} 2',
                f'sleep 5',
                f's=$(sky queue {second_cluster_name}); printf "$s"; echo; echo; printf "$s" | grep "^2\\b" | grep CANCELLED',
                f'sky logs {first_cluster_name} 1',
                f'sky logs {second_cluster_name} 1'
            ],
            # Cleaning up artifacts created from the test.
            (f'sky down -y {first_cluster_name} {second_cluster_name}; '
             f'rm -f ~/.sky/local/{first_cluster_name}.yml; '
             f'rm -f ~/.sky/local/{second_cluster_name}.yml'))
        run_one_test(test)

    def test_onprem_resource_mismatch(self, local_cluster_name, admin_setup,
                                      cluster_config_setup, first_ssh_user):
        # Setups up local cluster config for user 'test'
        cluster_config_setup(first_ssh_user)
        # Tests on-prem cases when users specify resources that are not on
        # the cluster. This test should error out with
        # ResourcesMismatchError.
        name = local_cluster_name
        cli_runner = cli_testing.CliRunner()
        # Setup the cluster handle and get cluster resources for `sky status`
        cli_runner.invoke(cli.launch, ['-c', name, '--', ''])

        def _test_overspecify_gpus(cluster_name):
            result = cli_runner.invoke(
                cli.launch,
                ['-c', cluster_name, '--gpus', 'V100:256', '--', ''])
            assert 'sky.exceptions.ResourcesMismatchError' not in str(
                type(result.exception))

        with pytest.raises(AssertionError) as e:
            _test_overspecify_gpus(name)
        # Cleaning up artifacts created from the test.
        subprocess.check_output(
            f'sky down -p -y {name}; rm -f ~/.sky/local/{name}.yml', shell=True)

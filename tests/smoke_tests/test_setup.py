import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

from sky import skypilot_config


# ---------- Test launching a cluster that has pyproject.toml in the workdir ----------
@pytest.mark.parametrize('image_id', [
    'docker:us-docker.pkg.dev/sky-dev-465/buildkite-test-images/test-workdir-pyproject:latest',
    'docker:us-docker.pkg.dev/sky-dev-465/buildkite-test-images/test-root-pyproject:latest',
])
def test_workdir_with_pyproject(generic_cloud: str, image_id: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'workdir_with_pyproject',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} --image-id {image_id}',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_post_provision_runcmd():
    """Test that post_provision_runcmd works with Kubernetes.

    Specifically, test that the post_provision_runcmd is executed before the setup command.
    """

    config = textwrap.dedent(f"""
    kubernetes:
        post_provision_runcmd:
            - echo "post_provision_runcmd executed" > /tmp/test_post_provision_runcmd
    """)

    yaml = textwrap.dedent(f"""
    setup: |
      cat /tmp/test_post_provision_runcmd
    """)

    with tempfile.NamedTemporaryFile(
            delete=True) as config_file, tempfile.NamedTemporaryFile(
                delete=True) as yaml_file:
        config_file.write(config.encode('utf-8'))
        config_file.flush()
        yaml_file.write(yaml.encode('utf-8'))
        yaml_file.flush()

        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'kubernetes_post_provision_runcmd',
            [
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_file.name}) && echo "$s" | grep "post_provision_runcmd executed"',
            ],
            teardown=f'sky down -y {name}',
            timeout=smoke_tests_utils.get_timeout('kubernetes'),
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: config_file.name,
            },
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_provision_command_timeout():
    """A wedged provisioning command must fail, not hang forever.

    Regression test for #10167. On nodes where the `kubectl exec` stream never
    delivers EOF, every byte of the transfer lands but both ends sit idle
    forever: `sky launch` hangs with the cluster stuck in INIT, and nothing in
    the provisioning path has a timeout to break the deadlock. A `sky serve up`
    controller left in INIT cannot even be torn down.

    Reproducing that kernel-specific stream bug in CI is not practical, so this
    reproduces its *shape* deterministically on any Kubernetes cluster: a
    post-provision command that never returns. Without a bound on provisioning
    commands the launch never comes back and this test fails on its own
    timeout; with one, provisioning gives up and reports the timeout.
    """
    # Small enough that the retries finish well inside the test timeout.
    command_timeout = 30
    config = textwrap.dedent(f"""\
    provision:
        setup_command_timeout: {command_timeout}
    kubernetes:
        post_provision_runcmd:
            - sleep 100000
    """)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as config_file:
        config_file.write(config)
        config_file.flush()

        name = smoke_tests_utils.get_cluster_name()
        log = f'/tmp/{name}-launch.log'
        # The launch must FAIL -- a hang would blow the test timeout instead --
        # and it must say why.
        launch_and_expect_timeout = (
            f'! sky launch -y -c {name} --infra kubernetes '
            f'{smoke_tests_utils.LOW_RESOURCE_ARG} "echo unreachable" '
            f'> {log} 2>&1; '
            f'grep -q "timed out after {command_timeout} seconds" {log}')

        test = smoke_tests_utils.Test(
            'kubernetes_provision_command_timeout',
            [launch_and_expect_timeout],
            teardown=f'sky down -y {name}; rm -f {log}',
            # Generously above the worst case: _auto_retry retries the setup
            # stage _MAX_RETRY times, each bounded by command_timeout plus
            # backoff.
            timeout=20 * 60,
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: config_file.name,
            },
        )
        smoke_tests_utils.run_one_test(test)

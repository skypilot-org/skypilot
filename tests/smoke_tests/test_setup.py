import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

from sky import skypilot_config


# ---------- Test launching a cluster that has pyproject.toml in the workdir ----------
@pytest.mark.no_slurm  # Slurm does not support docker images and/or image_id
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

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
def test_kubernetes_runtime_ignores_user_uv_project():
    """Runtime venv setup must not inherit a uv project from the image.

    An image whose home directory is a uv project (pyproject.toml with
    [tool.uv] settings) must not leak into the SkyPilot runtime venv
    resolution: `override-dependencies` beats explicit requirement pins by
    design, so a leaked override can silently install dependency versions
    that break the runtime (e.g. an old protobuf next to a recent
    googleapis-common-protos crashes ray's dashboard agent on import, and
    the raylet fate-shares with the agent, taking the whole node down
    minutes after a successful launch).

    Uses post_provision_runcmd to plant the hostile pyproject.toml in
    $HOME before any SkyPilot setup runs (simulating an image that ships
    one), then asserts the runtime venv's protobuf still honors SkyPilot's
    own pin. FAILS without UV_NO_CONFIG in SKY_UV_CMD (protobuf resolves
    to 4.25.x) and PASSES with it.
    """
    config = textwrap.dedent("""
    kubernetes:
        post_provision_runcmd:
            - "echo '[project]' > $HOME/pyproject.toml"
            - "echo 'name = \\"customer-image\\"' >> $HOME/pyproject.toml"
            - "echo 'version = \\"0.1.0\\"' >> $HOME/pyproject.toml"
            - "echo 'requires-python = \\">=3.12\\"' >> $HOME/pyproject.toml"
            - "echo '[tool.uv]' >> $HOME/pyproject.toml"
            - "echo 'override-dependencies = [\\"protobuf>=4.25.9,<5\\"]' >> $HOME/pyproject.toml"
    """)

    yaml = textwrap.dedent("""
    run: |
      ~/skypilot-runtime/bin/python -c "import google.protobuf as p; assert int(p.__version__.split('.')[0]) >= 5, f'uv override leaked into runtime venv: protobuf {p.__version__}'; print('runtime protobuf OK:', p.__version__)"
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
            'kubernetes_runtime_ignores_user_uv_project',
            [
                f's=$(SKYPILOT_DEBUG=0 sky launch -y -c {name} --infra kubernetes {smoke_tests_utils.LOW_RESOURCE_ARG} {yaml_file.name}) && echo "$s" | grep "runtime protobuf OK"',
            ],
            teardown=f'sky down -y {name}',
            timeout=smoke_tests_utils.get_timeout('kubernetes'),
            env={
                skypilot_config.ENV_VAR_GLOBAL_CONFIG: config_file.name,
            },
        )
        smoke_tests_utils.run_one_test(test)

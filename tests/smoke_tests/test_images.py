# Smoke tests for SkyPilot for image functionality
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_images.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_images.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_images.py::test_aws_images
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_images.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_images.py --generic-cloud aws

import os
import pathlib
import subprocess
import tempfile
import textwrap

import jinja2
import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants


# ---------- Test the image ----------
@pytest.mark.aws
def test_aws_images():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_images',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} examples/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i aws\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_images():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_images',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id skypilot:gpu-debian-10 --infra gcp tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:cpu-debian-10 --infra gcp tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} --infra gcp tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i gcp\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.azure
def test_azure_images():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'azure_images',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id skypilot:gpu-ubuntu-2204 --infra azure tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id skypilot:v1-ubuntu-2004 --infra azure tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i azure\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict_region():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_image_id_dict_region',
        [
            # YAML has
            #   image_id:
            #       us-west-2: skypilot:gpu-ubuntu-1804
            #       us-east-2: skypilot:gpu-ubuntu-2004
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra aws/us-east-1 examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra aws/us-east-2 examples/per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status -v | grep {name} | grep us-east-2',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --infra aws/us-east-2 examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --infra aws/us-east-2 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict_region():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_image_id_dict_region',
        [
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} --infra gcp/us-east1 {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/gcp_per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --infra gcp/us-west3 {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/gcp_per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --infra gcp --image-id projects/ubuntu-os-cloud/global/images/ubuntu-1804-bionic-v20230112 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --infra gcp --image-id projects/ubuntu-os-cloud/global/images/ubuntu-1804-bionic-v20230112 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --infra gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status -v | grep {name} | grep us-west3',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --infra gcp/us-west3 tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} --infra gcp/us-west3 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict_zone():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'aws_image_id_dict_zone',
        [
            # YAML has
            #   image_id:
            #       us-west-2: skypilot:gpu-ubuntu-1804
            #       us-east-2: skypilot:gpu-ubuntu-2004
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --infra aws/*/us-east-1b {smoke_tests_utils.LOW_RESOURCE_ARG} examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --infra aws/*/us-east-2a {smoke_tests_utils.LOW_RESOURCE_ARG} examples/per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status -v | grep {name} | grep us-east-2a',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --infra aws/*/us-east-2a examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --infra aws/us-east-2 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict_zone():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'gcp_image_id_dict_zone',
        [
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --infra */*/us-east1-a {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/gcp_per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --infra */*/us-central1-a {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/gcp_per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --infra gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --infra gcp --image-id skypilot:gpu-debian-10 tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status -v | grep {name} | grep us-central1',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --infra gcp/*/us-central1-a tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} --infra gcp/us-central1 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.skip(reason='Skipping this test as clone-disk-from is not '
                  'supported yet with the new client-server architecture.')
@pytest.mark.aws
def test_clone_disk_aws():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'clone_disk_aws',
        [
            f'sky launch -y -c {name} --infra aws/us-east-2 --retry-until-up "echo hello > ~/user_file.txt"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone && exit 1 || true',
            f'sky stop {name} -y',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=60),
            # Wait for EC2 instance to be in stopped state.
            # TODO: event based wait.
            'sleep 60',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone --infra aws/us-east-2 -d "cat ~/user_file.txt | grep hello"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone-2 --infra aws/us-east-2 -d "cat ~/user_file.txt | grep hello"',
            f'sky logs {name}-clone 1 --status',
            f'sky logs {name}-clone-2 1 --status',
        ],
        f'sky down -y {name} {name}-clone {name}-clone-2',
        timeout=30 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.skip(reason='Skipping this test as clone-disk-from is not '
                  'supported yet with the new client-server architecture.')
@pytest.mark.gcp
def test_clone_disk_gcp():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'clone_disk_gcp',
        [
            f'sky launch -y -c {name} --infra gcp/*/us-east1-b --retry-until-up "echo hello > ~/user_file.txt"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone && exit 1 || true',
            f'sky stop {name} -y',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone --infra gcp/*/us-central1-a "cat ~/user_file.txt | grep hello"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone-2 --infra gcp/*/us-east1-b "cat ~/user_file.txt | grep hello"',
            f'sky logs {name}-clone 1 --status',
            f'sky logs {name}-clone-2 1 --status',
        ],
        f'sky down -y {name} {name}-clone {name}-clone-2',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_mig():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    zone = 'us-central1-a'
    test = smoke_tests_utils.Test(
        'gcp_mig',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            # Launch a CPU instance asynchronously.
            f'sky launch -y -c {name}-cpu {smoke_tests_utils.LOW_RESOURCE_ARG} --infra gcp/*/us-central1-a --async tests/test_yamls/minimal.yaml',
            # Launch a GPU instance.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --gpus l4 --num-nodes 2 --image-id skypilot:gpu-debian-10 --infra gcp/{region} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check MIG exists.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'gcloud compute instance-groups managed list --format="value(name)" | grep "^sky-mig-{name}"'
                )),
            f'sky autostop -i 0 --down -y {name}',
            smoke_tests_utils.get_cmd_wait_until_cluster_is_not_found(
                cluster_name=name, timeout=150),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'gcloud compute instance-templates list | grep "sky-it-{name}"'
                )),
            # Launch again with the same region. The original instance template
            # should be removed.
            f'sky launch -y -c {name} --gpus L4 --num-nodes 2 --infra gcp/{region} nvidia-smi',
            f'sky logs {name} 1 | grep "L4"',
            f'sky down -y {name}',
            f'sky status | grep {name}-cpu | grep UP',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'gcloud compute instance-templates list | grep "sky-it-{name}" && exit 1 || true'
                )),
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=(
                    f'gcloud compute instances list --filter='
                    f'"(labels.ray-cluster-name:{name}-cpu)" '
                    f'--zones={zone} --format="value(name)" | wc -l | grep 1')),
            f'sky down -y {name}-cpu',
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=(f'gcloud compute instances list --filter='
                     f'"(labels.ray-cluster-name:{name}-cpu)" '
                     f'--zones={zone} --format="value(name)" | wc -l | grep 0'))
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
        env={
            skypilot_config.ENV_VAR_PROJECT_CONFIG: 'tests/test_yamls/use_mig_config.yaml',
        })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_gcp_force_enable_external_ips():
    name = smoke_tests_utils.get_cluster_name()

    # Command to check if the instance is on GCP
    is_on_gcp_command = (
        'curl -s -H "Metadata-Flavor: Google" '
        '"http://metadata.google.internal/computeMetadata/v1/instance/name"')

    is_on_k8s = os.getenv('KUBERNETES_SERVICE_HOST') is not None

    # Run the GCP check
    result = subprocess.run(f'{is_on_gcp_command}',
                            shell=True,
                            check=False,
                            text=True,
                            capture_output=True)
    is_on_gcp = result.returncode == 0 and result.stdout.strip()
    if not is_on_gcp or is_on_k8s:
        pytest.skip('Not on GCP, skipping test')

    test_commands = [
        is_on_gcp_command,
        f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra gcp --cpus 2 tests/test_yamls/minimal.yaml',
        # Check network of vm is "default"
        (f'gcloud compute instances list --filter=name~"{name}" --format='
         '"value(networkInterfaces.network)" | grep "networks/default"'),
        # Check External NAT in network access configs, corresponds to external ip
        (f'gcloud compute instances list --filter=name~"{name}" --format='
         '"value(networkInterfaces.accessConfigs[0].name)" | grep "External NAT"'
        ),
        f'sky down -y {name}',
    ]
    skypilot_config_file = 'tests/test_yamls/force_enable_external_ips_config.yaml'
    test = smoke_tests_utils.Test(
        'gcp_force_enable_external_ips',
        test_commands,
        f'sky down -y {name}',
        env={
            skypilot_config.ENV_VAR_GLOBAL_CONFIG: skypilot_config_file,
            constants.SKY_API_SERVER_URL_ENV_VAR:
                sky.server.common.get_server_url()
        })
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_image_no_conda():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'image_no_conda',
        [
            # Use image id dict.
            f'sky launch -y -c {name} {smoke_tests_utils.LOW_RESOURCE_ARG} --infra aws/us-east-2 examples/per_region_images.yaml',
            f'sky logs {name} 1 --status',
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_fluidstack  # FluidStack does not support stopping instances in SkyPilot implementation
@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
@pytest.mark.no_nebius  # Nebius does not support autodown
@pytest.mark.no_hyperbolic  # Hyperbolic does not support autodown
@pytest.mark.no_shadeform  # Shadeform does not support stopping instances
@pytest.mark.no_seeweb  # Seeweb does not support autodown
@pytest.mark.no_slurm  # Slurm does not support stopping instances yet
def test_custom_default_conda_env(generic_cloud: str):
    timeout = 80
    if generic_cloud == 'azure':
        timeout *= 3
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test('custom_default_conda_env', [
        f'sky launch -c {name} -y {smoke_tests_utils.LOW_RESOURCE_ARG} --infra {generic_cloud} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky status -r {name} | grep "UP"',
        f'sky logs {name} 1 --status',
        f'sky logs {name} 1 --no-follow | grep -E "myenv\\s+\\*"',
        f'sky exec {name} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky logs {name} 2 --status',
        f'sky autostop -y -i 0 {name}',
        smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
            cluster_name=name,
            cluster_status=[sky.ClusterStatus.STOPPED],
            timeout=timeout),
        f'sky start -y {name}',
        f'sky logs {name} 2 --no-follow | grep -E "myenv\\s+\\*"',
        f'sky exec {name} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky logs {name} 3 --status',
    ], f'sky down -y {name}')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
def test_kubernetes_docker_image_and_ssh():
    """Test K8s docker image ID interchangeability with/without prefix."""
    # We use a real, simple image like docker for the test.
    image_name = 'continuumio/miniconda3:latest'
    docker_prefixed_image_id = f'docker:{image_name}'
    unprefixed_image_id = image_name
    run_command = 'echo hello world'
    # Create temporary YAML files for testing
    import os
    import tempfile

    def create_temp_yaml(content, suffix):
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(content.encode())
            return f.name

    # YAML with docker: prefix
    docker_yaml = textwrap.dedent(f"""\
        resources:
            image_id: {docker_prefixed_image_id}
            cpus: 2+
            memory: 2+
            infra: kubernetes
        run: {run_command}
        """)

    # YAML without docker: prefix
    unprefixed_yaml = textwrap.dedent(f"""\
        resources:
            image_id: {unprefixed_image_id}
            cpus: 2+
            memory: 2+
            infra: kubernetes
        run: {run_command}
        """)

    docker_yaml_path = create_temp_yaml(docker_yaml, '_docker.yaml')
    unprefixed_yaml_path = create_temp_yaml(unprefixed_yaml, '_unprefixed.yaml')

    try:
        # Scenario 1: launch with docker:alpine, exec with alpine
        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'test_kubernetes_docker_image_and_ssh',
            [
                f'sky launch -c {name}-1 --retry-until-up -y --async '
                f'--cpus 2+ --memory 2+ '
                f'--infra kubernetes '
                f'--image-id {docker_prefixed_image_id} "{run_command}"',
                f'sky launch -c {name}-2 --retry-until-up -y '
                f'--cpus 2+ --memory 2+ '
                f'--infra kubernetes '
                f'--image-id {unprefixed_image_id} "{run_command}"',
                smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                    cluster_name=f'{name}-1',
                    cluster_status=[sky.ClusterStatus.UP],
                    timeout=5 * 60),
                f'sky logs {name}-1 1 --status',
                f'sky launch -y --fast -c {name}-1 {unprefixed_yaml_path}',
                f'sky exec {name}-1 {unprefixed_yaml_path}',
                f'sky logs {name}-1 2 --status',
                f'sky logs {name}-1 3 --status',
                # Second cluster
                f'sky logs {name}-2 1 --status',
                f'sky launch -y --fast -c {name}-2 {docker_yaml_path}',
                f'sky exec {name}-2 {docker_yaml_path}',
                f'sky logs {name}-2 2 --status',
                f'sky logs {name}-2 3 --status',
                # Ensure SSH config is updated.
                'sky status',
                f'ssh {name}-1 -- "{run_command}" | grep "hello world"',
                f'ssh {name}-2 -- "{run_command}" | grep "hello world"',
            ],
            f'sky down -y {name}-1 {name}-2',
            timeout=30 * 60,
        )
        smoke_tests_utils.run_one_test(test)
    finally:
        # Clean up temporary files
        os.unlink(docker_yaml_path)
        os.unlink(unprefixed_yaml_path)


@pytest.fixture
def private_docker_registry_setup(request):
    """Fixture to setup private docker registry test environment.

    Args:
        request: pytest request object containing the parameters
    """
    # Get parameters from the test function
    docker_username = request.param['docker_username']
    docker_password = request.param['docker_password']
    docker_server = request.param['docker_server']
    full_image_name = request.param['full_image_name']

    # Dynamically get passwords for cloud providers
    if 'ecr' in docker_server:
        # Get ECR login password
        # Extract region from ECR server URL
        region = docker_server.split(
            '.'
        )[3]  # e.g., us-east-1 from 195275664570.dkr.ecr.us-east-1.amazonaws.com
        result = subprocess.run(
            ['aws', 'ecr', 'get-login-password', '--region', region],
            capture_output=True,
            text=True,
            check=True)
        docker_password = result.stdout.strip()

    template_str = pathlib.Path(
        'tests/test_yamls/test_private_docker_registry.j2').read_text()
    template = jinja2.Template(template_str)
    content = template.render(docker_username=docker_username,
                              docker_password=docker_password,
                              docker_server=docker_server,
                              full_image_name=full_image_name)

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(content)
        f.flush()
        file_path = f.name
        yield file_path


@pytest.mark.no_azure
@pytest.mark.no_kubernetes
@pytest.mark.no_shadeform
@pytest.mark.no_slurm  # Slurm does not support private docker registries yet
@pytest.mark.parametrize(
    'private_docker_registry_setup,cloud_provider',
    [
        # AWS with docker.io registry
        ({
            'docker_username':
                os.environ.get('PRIVATE_REGISTRY_TEST_DOCKER_USERNAME'),
            'docker_password':
                os.environ.get('PRIVATE_REGISTRY_TEST_DOCKER_PASSWORD'),
            'docker_server':
                os.environ.get('PRIVATE_REGISTRY_TEST_DOCKER_SERVER'),
            'full_image_name':
                os.environ.get('PRIVATE_REGISTRY_TEST_DOCKER_FULL_IMAGE_NAME')
        }, 'aws'),
        # GCP with Artifact Registry
        ({
            'docker_username':
                os.environ.get('PRIVATE_REGISTRY_TEST_GCP_DOCKER_USERNAME'),
            'docker_password':
                os.environ.get('PRIVATE_REGISTRY_TEST_GCP_DOCKER_PASSWORD'),
            'docker_server':
                os.environ.get('PRIVATE_REGISTRY_TEST_GCP_DOCKER_SERVER'),
            'full_image_name': os.environ.get(
                'PRIVATE_REGISTRY_TEST_GCP_DOCKER_FULL_IMAGE_NAME')
        }, 'gcp'),
        # AWS with ECR
        ({
            'docker_username':
                os.environ.get('PRIVATE_REGISTRY_TEST_AWS_ECR_USERNAME'),
            'docker_password':
                os.environ.get('PRIVATE_REGISTRY_TEST_AWS_ECR_PASSWORD'),
            'docker_server':
                os.environ.get('PRIVATE_REGISTRY_TEST_AWS_ECR_SERVER'),
            'full_image_name':
                os.environ.get('PRIVATE_REGISTRY_TEST_AWS_ECR_FULL_IMAGE_NAME')
        }, 'aws'),
    ],
    indirect=['private_docker_registry_setup'])
def test_private_docker_registry(generic_cloud,
                                 private_docker_registry_setup: str,
                                 cloud_provider: str):
    # Skip test if environment variables are not set
    if not os.environ.get('PRIVATE_REGISTRY_TEST_DOCKER_FULL_IMAGE_NAME'):
        pytest.skip(
            'Skipping test as docker registry environment variables are not set'
        )

    # Skip test if the required cloud provider is not available
    if cloud_provider != generic_cloud:
        pytest.skip(
            f'Skipping test for {cloud_provider} as it is not the generic cloud'
        )

    name = smoke_tests_utils.get_cluster_name()
    test_name = f'private_docker_registry_{cloud_provider}'

    test = smoke_tests_utils.Test(
        test_name,
        [
            f'sky launch -c {name} -y --infra {cloud_provider} {smoke_tests_utils.LOW_RESOURCE_ARG} {private_docker_registry_setup}',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


def test_docker_nonroot_user(generic_cloud: str):
    """Test Docker image with non-root default user and ENV HOME override.

    Tests that SkyPilot correctly handles Docker images where:
    1. The default USER is non-root
    2. ENV HOME is explicitly set to the non-root user's home directory

    SkyPilot should:
    - Detect the container's default user (not root)
    - Place SSH keys in the correct home directory
    - SSH as the default user successfully
    """
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'docker_nonroot_user',
        [
            f'sky launch -y -c {name} --infra {generic_cloud} --image-id docker:us-docker.pkg.dev/sky-dev-465/buildkite-test-images/test-nonroot-home:latest tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',
            # Verify we're running as the non-root user
            f'sky exec {name} "whoami | grep testuser"',
            f'sky logs {name} 2 --status',
            # Verify HOME is set correctly
            f'sky exec {name} "echo \\$HOME | grep /home/testuser"',
            f'sky logs {name} 3 --status',
            # Test SSH works
            f'ssh {name} -- "echo hello"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_helm_deploy_gke(request):
    if not request.config.getoption('--helm-package'):
        # Test pulls image from dockerhub, unrelated to codebase. Package name
        # indicates intentional testing - without it, test is meaningless.
        pytest.skip('Skipping test as helm package is not set')

    helm_version = request.config.getoption('--helm-version')
    package_name = request.config.getoption('--helm-package')
    test = smoke_tests_utils.Test(
        'helm_deploy_gke',
        [
            f'bash tests/kubernetes/scripts/helm_upgrade.sh {package_name} {helm_version} gcp',
        ],
        # GKE termination requires longer timeout.
        timeout=50 * 60)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.aws
def test_helm_deploy_eks(request):
    if not request.config.getoption('--helm-package'):
        # Test pulls image from dockerhub, unrelated to codebase. Package name
        # indicates intentional testing - without it, test is meaningless.
        pytest.skip('Skipping test as helm package is not set')

    helm_version = request.config.getoption('--helm-version')
    package_name = request.config.getoption('--helm-package')
    test = smoke_tests_utils.Test(
        'helm_deploy_eks',
        [
            f'bash tests/kubernetes/scripts/helm_upgrade.sh {package_name} {helm_version} aws',
        ],
        # EKS termination requires longer timeout.
        timeout=90 * 60)
    smoke_tests_utils.run_one_test(test)


@pytest.mark.kubernetes
@pytest.mark.no_remote_server
@pytest.mark.no_dependency  # This test is not related to dependency
def test_helm_deploy_okta():
    test = smoke_tests_utils.Test('helm_deploy_okta', [
        f'bash tests/kubernetes/scripts/helm_okta.sh',
    ],
                                  timeout=30 * 60)
    smoke_tests_utils.run_one_test(test)

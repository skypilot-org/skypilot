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

import pytest
from smoke_tests.util import get_cluster_name
from smoke_tests.util import get_cmd_wait_until_cluster_is_not_found
from smoke_tests.util import get_cmd_wait_until_cluster_status_contains
from smoke_tests.util import run_one_test
from smoke_tests.util import Test

from sky.status_lib import ClusterStatus


# ---------- Test the image ----------
@pytest.mark.aws
def test_aws_images():
    name = get_cluster_name()
    test = Test(
        'aws_images',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml',
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
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_images():
    name = get_cluster_name()
    test = Test(
        'gcp_images',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-debian-10 --cloud gcp tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:cpu-debian-10 --cloud gcp tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i gcp\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.azure
def test_azure_images():
    name = get_cluster_name()
    test = Test(
        'azure_images',
        [
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-2204 --cloud azure tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -c {name} --image-id skypilot:v1-ubuntu-2004 --cloud azure tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            f'sky exec {name} \'echo $SKYPILOT_CLUSTER_INFO | jq .cloud | grep -i azure\'',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict():
    name = get_cluster_name()
    test = Test(
        'aws_image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict():
    name = get_cluster_name()
    test = Test(
        'gcp_image_id_dict',
        [
            # Use image id dict.
            f'sky launch -y -c {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict_region():
    name = get_cluster_name()
    test = Test(
        'aws_image_id_dict_region',
        [
            # YAML has
            #   image_id:
            #       us-west-2: skypilot:gpu-ubuntu-1804
            #       us-east-2: skypilot:gpu-ubuntu-2004
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} --region us-east-1 examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --region us-east-2 examples/per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-east-2',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --region us-east-2 examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --cloud aws --region us-east-2 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict_region():
    name = get_cluster_name()
    test = Test(
        'gcp_image_id_dict_region',
        [
            # Use region to filter image_id dict.
            f'sky launch -y -c {name} --region us-east1 tests/test_yamls/gcp_per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --region us-west3 tests/test_yamls/gcp_per_region_images.yaml',
            # Should success because the image id match for the region.
            f'sky launch -c {name} --cloud gcp --image-id projects/ubuntu-os-cloud/global/images/ubuntu-1804-bionic-v20230112 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --cloud gcp --image-id projects/ubuntu-os-cloud/global/images/ubuntu-1804-bionic-v20230112 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --cloud gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-west3',  # Ensure the region is correct.
            # Ensure exec works.
            f'sky exec {name} --region us-west3 tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} --cloud gcp --region us-west3 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_aws_image_id_dict_zone():
    name = get_cluster_name()
    test = Test(
        'aws_image_id_dict_zone',
        [
            # YAML has
            #   image_id:
            #       us-west-2: skypilot:gpu-ubuntu-1804
            #       us-east-2: skypilot:gpu-ubuntu-2004
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --zone us-east-1b examples/per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --zone us-east-2a examples/per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-2004 examples/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --image-id skypilot:gpu-ubuntu-1804 examples/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-east-2a',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --zone us-east-2a examples/per_region_images.yaml',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky exec {name} --cloud aws --region us-east-2 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_image_id_dict_zone():
    name = get_cluster_name()
    test = Test(
        'gcp_image_id_dict_zone',
        [
            # Use zone to filter image_id dict.
            f'sky launch -y -c {name} --zone us-east1-a tests/test_yamls/gcp_per_region_images.yaml && exit 1 || true',
            f'sky status | grep {name} && exit 1 || true',  # Ensure the cluster is not created.
            f'sky launch -y -c {name} --zone us-central1-a tests/test_yamls/gcp_per_region_images.yaml',
            # Should success because the image id match for the zone.
            f'sky launch -y -c {name} --cloud gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml',
            f'sky exec {name} --cloud gcp --image-id skypilot:cpu-debian-10 tests/test_yamls/minimal.yaml',
            # Fail due to image id mismatch.
            f'sky exec {name} --cloud gcp --image-id skypilot:gpu-debian-10 tests/test_yamls/minimal.yaml && exit 1 || true',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 --status',
            f'sky status --all | grep {name} | grep us-central1',  # Ensure the zone is correct.
            # Ensure exec works.
            f'sky exec {name} --cloud gcp --zone us-central1-a tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} tests/test_yamls/gcp_per_region_images.yaml',
            f'sky exec {name} --cloud gcp --region us-central1 "ls ~"',
            f'sky exec {name} "ls ~"',
            f'sky logs {name} 4 --status',
            f'sky logs {name} 5 --status',
            f'sky logs {name} 6 --status',
            f'sky logs {name} 7 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.aws
def test_clone_disk_aws():
    name = get_cluster_name()
    test = Test(
        'clone_disk_aws',
        [
            f'sky launch -y -c {name} --cloud aws --region us-east-2 --retry-until-up "echo hello > ~/user_file.txt"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone && exit 1 || true',
            f'sky stop {name} -y',
            get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[ClusterStatus.STOPPED],
                timeout=60),
            # Wait for EC2 instance to be in stopped state.
            # TODO: event based wait.
            'sleep 60',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone --cloud aws -d --region us-east-2 "cat ~/user_file.txt | grep hello"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone-2 --cloud aws -d --region us-east-2 "cat ~/user_file.txt | grep hello"',
            f'sky logs {name}-clone 1 --status',
            f'sky logs {name}-clone-2 1 --status',
        ],
        f'sky down -y {name} {name}-clone {name}-clone-2',
        timeout=30 * 60,
    )
    run_one_test(test)


@pytest.mark.gcp
def test_clone_disk_gcp():
    name = get_cluster_name()
    test = Test(
        'clone_disk_gcp',
        [
            f'sky launch -y -c {name} --cloud gcp --zone us-east1-b --retry-until-up "echo hello > ~/user_file.txt"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone && exit 1 || true',
            f'sky stop {name} -y',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone --cloud gcp --zone us-central1-a "cat ~/user_file.txt | grep hello"',
            f'sky launch --clone-disk-from {name} -y -c {name}-clone-2 --cloud gcp --zone us-east1-b "cat ~/user_file.txt | grep hello"',
            f'sky logs {name}-clone 1 --status',
            f'sky logs {name}-clone-2 1 --status',
        ],
        f'sky down -y {name} {name}-clone {name}-clone-2',
    )
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_mig():
    name = get_cluster_name()
    region = 'us-central1'
    test = Test(
        'gcp_mig',
        [
            f'sky launch -y -c {name} --gpus t4 --num-nodes 2 --image-id skypilot:gpu-debian-10 --cloud gcp --region {region} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky launch -y -c {name} tests/test_yamls/minimal.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check MIG exists.
            f'gcloud compute instance-groups managed list --format="value(name)" | grep "^sky-mig-{name}"',
            f'sky autostop -i 0 --down -y {name}',
            get_cmd_wait_until_cluster_is_not_found(cluster_name=name,
                                                    timeout=120),
            f'gcloud compute instance-templates list | grep "sky-it-{name}"',
            # Launch again with the same region. The original instance template
            # should be removed.
            f'sky launch -y -c {name} --gpus L4 --num-nodes 2 --region {region} nvidia-smi',
            f'sky logs {name} 1 | grep "L4"',
            f'sky down -y {name}',
            f'gcloud compute instance-templates list | grep "sky-it-{name}" && exit 1 || true',
        ],
        f'sky down -y {name}',
        env={'SKYPILOT_CONFIG': 'tests/test_yamls/use_mig_config.yaml'})
    run_one_test(test)


@pytest.mark.gcp
def test_gcp_force_enable_external_ips():
    name = get_cluster_name()
    test_commands = [
        f'sky launch -y -c {name} --cloud gcp --cpus 2 tests/test_yamls/minimal.yaml',
        # Check network of vm is "default"
        (f'gcloud compute instances list --filter=name~"{name}" --format='
         '"value(networkInterfaces.network)" | grep "networks/default"'),
        # Check External NAT in network access configs, corresponds to external ip
        (f'gcloud compute instances list --filter=name~"{name}" --format='
         '"value(networkInterfaces.accessConfigs[0].name)" | grep "External NAT"'
        ),
        f'sky down -y {name}',
    ]
    skypilot_config = 'tests/test_yamls/force_enable_external_ips_config.yaml'
    test = Test('gcp_force_enable_external_ips',
                test_commands,
                f'sky down -y {name}',
                env={'SKYPILOT_CONFIG': skypilot_config})
    run_one_test(test)


@pytest.mark.aws
def test_image_no_conda():
    name = get_cluster_name()
    test = Test(
        'image_no_conda',
        [
            # Use image id dict.
            f'sky launch -y -c {name} --region us-east-2 examples/per_region_images.yaml',
            f'sky logs {name} 1 --status',
            f'sky stop {name} -y',
            f'sky start {name} -y',
            f'sky exec {name} examples/per_region_images.yaml',
            f'sky logs {name} 2 --status',
        ],
        f'sky down -y {name}',
    )
    run_one_test(test)


@pytest.mark.no_fluidstack  # FluidStack does not support stopping instances in SkyPilot implementation
@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
def test_custom_default_conda_env(generic_cloud: str):
    name = get_cluster_name()
    test = Test('custom_default_conda_env', [
        f'sky launch -c {name} -y --cloud {generic_cloud} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky status -r {name} | grep "UP"',
        f'sky logs {name} 1 --status',
        f'sky logs {name} 1 --no-follow | grep -E "myenv\\s+\\*"',
        f'sky exec {name} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky logs {name} 2 --status',
        f'sky autostop -y -i 0 {name}',
        get_cmd_wait_until_cluster_status_contains(
            cluster_name=name,
            cluster_status=[ClusterStatus.STOPPED],
            timeout=80),
        f'sky start -y {name}',
        f'sky logs {name} 2 --no-follow | grep -E "myenv\\s+\\*"',
        f'sky exec {name} tests/test_yamls/test_custom_default_conda_env.yaml',
        f'sky logs {name} 3 --status',
    ], f'sky down -y {name}')
    run_one_test(test)

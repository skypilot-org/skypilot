# Smoke tests for SkyPilot for mounting volumes
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_volume_mount.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_volume_mount.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_volume_mount.py::test_volume_mount_compute
#
# Only run test for GCP tests
# > pytest tests/smoke_tests/test_volume_mount.py --gcp
#
# Change cloud for generic tests to gcp
# > pytest tests/smoke_tests/test_volume_mount.py --generic-cloud gcp

from datetime import datetime
import pathlib
import tempfile
from typing import TextIO

import jinja2
import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.skip(reason='We are having trouble getting TPUs in GCP.')
@pytest.mark.gcp
def test_volume_mount_tpu():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    instance_type = 'n2-standard-2'
    now = datetime.now()
    formatted_time = now.strftime("%Y%m%d%H%M%S")
    new_disk_name = f'new-pd1-{formatted_time}'
    existing_disk_name = f'existing-pd1-{formatted_time}'
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_cmd = _volume_mounts_commands_generator(
            f, name, existing_disk_name, new_disk_name, region, instance_type,
            None, False, False, True)
        test = smoke_tests_utils.Test(
            'test_volume_mount_tpu',
            test_commands,
            clean_cmd,
            timeout=15 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.skip(reason='We are having trouble getting TPUs in GCP.')
@pytest.mark.gcp
def test_volume_mount_tpu_container():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    instance_type = 'n2-standard-2'
    now = datetime.now()
    formatted_time = now.strftime("%Y%m%d%H%M%S")
    new_disk_name = f'new-pd2-{formatted_time}'
    existing_disk_name = f'existing-pd2-{formatted_time}'
    image_id = 'docker:ubuntu:20.04'

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_cmd = _volume_mounts_commands_generator(
            f, name, existing_disk_name, new_disk_name, region, instance_type,
            image_id, False, False, True)
        test = smoke_tests_utils.Test(
            'test_volume_mount_tpu_container',
            test_commands,
            clean_cmd,
            timeout=15 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_volume_mount_compute():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    instance_type = 'n2-standard-2'
    now = datetime.now()
    formatted_time = now.strftime("%Y%m%d%H%M%S")
    new_disk_name = f'new-pd3-{formatted_time}'
    existing_disk_name = f'existing-pd3-{formatted_time}'

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_cmd = _volume_mounts_commands_generator(
            f, name, existing_disk_name, new_disk_name, region, instance_type,
            None, False, False, False)
        test = smoke_tests_utils.Test(
            'test_volume_mount_compute',
            test_commands,
            clean_cmd,
            timeout=6 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_volume_mount_compute_container():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    instance_type = 'n2-standard-2'
    now = datetime.now()
    formatted_time = now.strftime("%Y%m%d%H%M%S")
    new_disk_name = f'new-pd4-{formatted_time}'
    existing_disk_name = f'existing-pd4-{formatted_time}'
    image_id = 'docker:ubuntu:20.04'

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_cmd = _volume_mounts_commands_generator(
            f, name, existing_disk_name, new_disk_name, region, instance_type,
            image_id, False, False, False)
        test = smoke_tests_utils.Test(
            'test_volume_mount_compute_container',
            test_commands,
            clean_cmd,
            timeout=6 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_volume_mount_mig():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    instance_type = 'g2-standard-4'
    now = datetime.now()
    formatted_time = now.strftime("%Y%m%d%H%M%S")
    new_disk_name = f'new-pd5-{formatted_time}'
    existing_disk_name = f'existing-pd5-{formatted_time}'

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_cmd = _volume_mounts_commands_generator(
            f, name, existing_disk_name, new_disk_name, region, instance_type,
            None, True, True, False)
        test = smoke_tests_utils.Test(
            'test_volume_mount_mig',
            test_commands,
            clean_cmd,
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)


@pytest.mark.gcp
def test_volume_mount_mig_container():
    name = smoke_tests_utils.get_cluster_name()
    region = 'us-central1'
    instance_type = 'g2-standard-4'
    now = datetime.now()
    formatted_time = now.strftime("%Y%m%d%H%M%S")
    new_disk_name = f'new-pd6-{formatted_time}'
    existing_disk_name = f'existing-pd6-{formatted_time}'
    image_id = 'docker:ubuntu:20.04'

    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        test_commands, clean_cmd = _volume_mounts_commands_generator(
            f, name, existing_disk_name, new_disk_name, region, instance_type,
            image_id, True, True, False)
        test = smoke_tests_utils.Test(
            'test_volume_mount_mig_container',
            test_commands,
            clean_cmd,
            timeout=10 * 60,
        )
        smoke_tests_utils.run_one_test(test)


def _volume_mounts_commands_generator(f: TextIO, name: str,
                                      existing_disk_name: str,
                                      new_disk_name: str, region: str,
                                      instance_type: str, image_id: str,
                                      use_mig: bool, zonal_disk: bool,
                                      tpu: bool):
    template_str = pathlib.Path(
        'tests/test_yamls/test_volume_mount.yaml.j2').read_text()
    template = jinja2.Template(template_str)

    content = template.render(
        existing_disk_name=existing_disk_name,
        new_disk_name=new_disk_name,
        mig_config=use_mig,
        tpu_config=tpu,
    )
    f.write(content)
    f.flush()
    file_path = f.name

    if zonal_disk:
        pre_launch_disk_cmd = (
            f'(gcloud compute disks delete {existing_disk_name} --zone={region}-a --quiet || true) && gcloud compute disks create {existing_disk_name} --size=10 --type=pd-ssd --zone={region}-a'
        )
        clean_cmd = f'sky down -y {name} && while true; do users=$(gcloud compute disks describe {existing_disk_name} --zone={region}-a --format="value(users)"); if [ -z "$users" ]; then break; fi; echo "users: $users"; sleep 10; done && echo "no users use the disk" && gcloud compute disks delete {existing_disk_name} --zone={region}-a --quiet'
    else:
        pre_launch_disk_cmd = (
            f'(gcloud compute disks delete {existing_disk_name} --region={region} --quiet || true) && gcloud compute disks create {existing_disk_name} --size=10 --type=pd-ssd --region={region} --replica-zones={region}-a,{region}-b'
        )
        clean_cmd = f'sky down -y {name} && while true; do users=$(gcloud compute disks describe {existing_disk_name} --region={region} --format="value(users)"); if [ -z "$users" ]; then break; fi; echo "users: $users"; sleep 10; done && echo "no users use the disk" && gcloud compute disks delete {existing_disk_name} --region={region} --quiet'
    post_launch_disk_cmd = (
        f'gcloud compute disks describe {new_disk_name} --zone={region}-a || gcloud compute disks describe {new_disk_name} --zone={region}-b || gcloud compute disks describe {new_disk_name} --zone={region}-c || gcloud compute disks describe {new_disk_name} --zone={region}-f'
    )
    if image_id:
        if tpu:
            launch_cmd = f'sky launch -y -c {name} --infra gcp/{region} --gpus tpu-v2-8 --image-id {image_id} {file_path}'
        else:
            launch_cmd = f'sky launch -y -c {name} --infra gcp/{region} --image-id {image_id} --instance-type {instance_type} {file_path}'
    else:
        if tpu:
            launch_cmd = f'sky launch -y -c {name} --infra gcp/{region} --gpus tpu-v2-8 {file_path}'
        else:
            launch_cmd = f'sky launch -y -c {name} --infra gcp/{region} --instance-type {instance_type} {file_path}'

    test_commands = [
        pre_launch_disk_cmd,
        *smoke_tests_utils.STORAGE_SETUP_COMMANDS,
        launch_cmd,
        f'sky logs {name} 1 --status',  # Ensure the job succeeded.
    ]
    # Have not support creating new volumes for TPU node now
    # and MIGs do not support specifying volume name
    if not tpu and not use_mig:
        test_commands.append(post_launch_disk_cmd)

    return test_commands, clean_cmd

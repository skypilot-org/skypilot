import getpass
import os
import pathlib
import time
import uuid
from packaging import version

import jinja2
import yaml

import sky
from sky.backends import backend_utils
from sky.clouds import aws as aws_cloud
from sky.utils import common_utils
from sky.provision import utils as provision_utils
from sky.provision import aws

TEMPLATE_FILE = pathlib.Path(__file__).parent.parent.resolve() / 'aws.yml.j2'


def test_provision():
    # Alternative AMIs:
    # ami-0b5eea76982371e91 (Amazon Linux 2 Kernel 5.10 AMI 2.0.20221210.1 x86_64 HVM gp2)
    # ami-06878d265978313ca (Canonical, Ubuntu, 22.04 LTS, amd64 jammy image build on 2022-12-06)
    # ami-0b7e0d9b36f4e8f14 (Deep Learning AMI GPU PyTorch 1.13.1 (Ubuntu 20.04) 20230103)

    template = jinja2.Template(TEMPLATE_FILE.read_text())
    cluster_name = f'sky-test-provision-{uuid.uuid4().hex}'
    region = 'us-east-1'
    availability_zone = 'us-east-1f'
    instance_type = aws_cloud.AWS.get_default_instance_type()
    num_nodes = 8
    content = template.render(
        cluster_name=cluster_name,
        region=region,
        instance_type=instance_type,
        image_id=aws_cloud.AWS.get_default_ami(region, instance_type),
        zones=availability_zone,
        num_nodes=num_nodes,
        disk_size=256,
        # If the current code is run by controller, propagate the real
        # calling user which should've been passed in as the
        # SKYPILOT_USER env var (see spot-controller.yaml.j2).
        user=os.environ.get('SKYPILOT_USER', getpass.getuser()),

        # AWS only:
        # Temporary measure, as deleting per-cluster SGs is too slow.
        # See https://github.com/skypilot-org/skypilot/pull/742.
        # Generate the name of the security group we're looking for.
        # (username, last 4 chars of hash of hostname): for uniquefying
        # users on shared-account cloud providers. Using uuid.getnode()
        # is incorrect; observed to collide on Macs.
        security_group=f'sky-sg-{common_utils.user_and_hostname_hash()}',
        # Cloud credentials for cloud storage.
        credentials={},
        # Sky remote utils.
        sky_remote_path=backend_utils.SKY_REMOTE_PATH,
        sky_local_path='',
        # Add yaml file path to the template variables.
        sky_ray_yaml_remote_path=backend_utils.SKY_RAY_YAML_REMOTE_PATH,
        sky_ray_yaml_local_path='',
        sky_version=str(version.parse(sky.__version__)),
        sky_wheel_hash='',
    )

    config = yaml.safe_load(content)

    import json
    print(json.dumps(config, indent=2))

    start = time.time()
    bootstrapped_config = aws.bootstrap(config)
    print(f'Bootstrapping duration = {time.time() - start:.3f}s')

    start = time.time()
    aws.create_or_resume_instances(
        region,
        cluster_name,
        node_config=bootstrapped_config["node_config"],
        tags={},
        count=num_nodes,
        resume_stopped_nodes=True)
    print(f'Start cluster (trigger) duration = {time.time() - start:.3f}s')

    start = time.time()
    aws.wait_instances(region, cluster_name, state='running')
    print(f'Start cluster duration = {time.time() - start:.3f}s')

    start = time.time()
    public_ips = aws.get_instance_ips(region, cluster_name, public_ips=True)
    print(f'Public IPs: {public_ips}')
    print(f'Get instance public IPs duration = {time.time() - start:.3f}s')

    start = time.time()
    provision_utils.wait_for_ssh(public_ips)
    print(f'Wait for SSH connection duration = {time.time() - start:.3f}s')

    start = time.time()
    aws.terminate_instances(region, cluster_name)
    print(f'Terminate cluster (trigger) duration = {time.time() - start:.3f}s')

    start = time.time()
    aws.wait_instances(region, cluster_name, state='terminated')
    print(f'Terminate cluster duration = {time.time() - start:.3f}s')

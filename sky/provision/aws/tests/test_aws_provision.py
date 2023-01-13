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
from sky.provision import aws

TEMPLATE_FILE = pathlib.Path(__file__).parent.parent.resolve() / 'aws.yml.j2'


def test_provision():
    template = jinja2.Template(TEMPLATE_FILE.read_text())
    cluster_name = f'sky-test-provision-{uuid.uuid4().hex}'
    region = 'us-east-1'
    availability_zone = 'us-east-1f'
    instance_type = aws_cloud.AWS.get_default_instance_type()
    content = template.render(
        cluster_name=cluster_name,
        region=region,
        instance_type=instance_type,
        image_id=aws_cloud.AWS.get_default_ami(region, instance_type),
        zones=availability_zone,
        num_nodes=1,
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
    aws.bootstrap(config)
    print(f'Bootstrapping duration = {time.time() - start:.3f}s')

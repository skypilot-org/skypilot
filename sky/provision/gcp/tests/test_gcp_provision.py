import getpass
import os
import pathlib
import time
import uuid
from packaging import version

import jinja2
import yaml

import sky
from sky import authentication
from sky.backends import backend_utils
from sky.clouds import gcp as gcp_cloud
from sky.utils import common_utils, command_runner
from sky.provision import utils as provision_utils
from sky.provision import gcp
from sky.provision import ca

TEMPLATE_FILE = pathlib.Path(__file__).parent.parent.resolve() / 'gcp.yml.j2'


def test_provision():
    # See sky.backends.backend_utils.write_cluster_config

    # ubuntu 20.04 ImageId
    image_id = 'projects/ubuntu-os-pro-cloud/global/images/ubuntu-pro-2004-focal-v20230113'
    gcp_project_id = gcp_cloud.GCP.get_project_id(dryrun=False)

    template = jinja2.Template(TEMPLATE_FILE.read_text())
    cluster_name = f'sky-test-provision-{uuid.uuid4().hex}'
    region = 'us-central1'
    availability_zone = 'us-central1-a'
    instance_type = gcp_cloud.GCP.get_default_instance_type()
    num_nodes = 2
    content = template.render(
        gcp_project_id=gcp_project_id,
        cluster_name=cluster_name,
        region=region,
        instance_type=instance_type,
        tpu_vm=False,
        image_id=image_id,
        zones=availability_zone,
        num_nodes=num_nodes,
        disk_size=256,
        ssh_private_key=os.path.expanduser(authentication.PRIVATE_SSH_KEY_PATH),
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
    bootstrapped_config = gcp.bootstrap(config)
    print(f'Bootstrapping duration = {time.time() - start:.3f}s')

    start = time.time()
    gcp.create_or_resume_instances(
        region,
        cluster_name,
        node_config=bootstrapped_config["node_config"],
        tags={},
        count=num_nodes,
        resume_stopped_nodes=True)
    print(f'Start cluster (trigger) duration = {time.time() - start:.3f}s')

    start = time.time()
    gcp.wait_instances(region, cluster_name, state='running')
    print(f'Start cluster duration = {time.time() - start:.3f}s')

    start = time.time()
    public_ips = gcp.get_instance_ips(region, cluster_name, public_ips=True)
    print(f'Public IPs: {public_ips}')
    print(f'Get instance public IPs duration = {time.time() - start:.3f}s')

    start = time.time()
    provision_utils.wait_for_ssh(public_ips)
    print(f'Wait for SSH connection duration = {time.time() - start:.3f}s')

    ca.generate_cert_file()
    print(f'Generated certification files')

    ssh_cmd_runners = command_runner.SSHCommandRunner.make_runner_list(
        public_ips,
        config['auth']['ssh_user'],
        config['auth']['ssh_private_key'],
    )
    for runner in ssh_cmd_runners:
        runner.run(
            f'mkdir -p {os.path.dirname(provision_utils.SKYLET_SERVER_REMOTE_PATH)}'
        )
        runner.rsync(provision_utils.SKYLET_SERVER_LOCAL_PATH,
                     provision_utils.SKYLET_SERVER_REMOTE_PATH,
                     up=True)
        runner.rsync(ca.CERTS_LOCAL_DIR, ca.CERTS_REMOTE_DIR, up=True)

    # start = time.time()
    # aws.terminate_instances(region, cluster_name)
    # print(f'Terminate cluster (trigger) duration = {time.time() - start:.3f}s')

    # start = time.time()
    # aws.wait_instances(region, cluster_name, state='terminated')
    # print(f'Terminate cluster duration = {time.time() - start:.3f}s')

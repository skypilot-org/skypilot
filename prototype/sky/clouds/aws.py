"""Amazon Web Services."""
import copy
import json
import os
import subprocess
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds.service_catalog import aws_catalog


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


class AWS(clouds.Cloud):
    """Amazon Web Services."""

    _REPR = 'AWS'
    _regions: List[clouds.Region] = []

    #### Regions/Zones ####

    @classmethod
    def regions(cls):
        if not cls._regions:
            # https://aws.amazon.com/premiumsupport/knowledge-center/vpc-find-availability-zone-options/
            cls._regions = [
                # TODO: troubles launching AMIs.
                # clouds.Region('us-west-1').set_zones([
                #     clouds.Zone('us-west-1a'),
                #     clouds.Zone('us-west-1b'),
                # ]),
                clouds.Region('us-west-2').set_zones([
                    clouds.Zone('us-west-2a'),
                    clouds.Zone('us-west-2b'),
                    clouds.Zone('us-west-2c'),
                    clouds.Zone('us-west-2d'),
                ]),
                clouds.Region('us-east-2').set_zones([
                    clouds.Zone('us-east-2a'),
                    clouds.Zone('us-east-2b'),
                    clouds.Zone('us-east-2c'),
                ]),
                clouds.Region('us-east-1').set_zones([
                    clouds.Zone('us-east-1a'),
                    clouds.Zone('us-east-1b'),
                    clouds.Zone('us-east-1c'),
                    clouds.Zone('us-east-1d'),
                    clouds.Zone('us-east-1e'),
                    clouds.Zone('us-east-1f'),
                ]),
            ]
        return cls._regions

    @classmethod
    def region_zones_provision_loop(
            cls,
            *,
            instance_type: Optional[str] = None,
            accelerators: Optional[Dict[str, int]] = None,
            use_spot: bool,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        # AWS provisioner can handle batched requests, so yield all zones under
        # each region.
        del accelerators  # unused

        if instance_type is None:
            # fallback to manually specified region/zones
            regions = cls.regions()
        else:
            regions = aws_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot)
        for region in regions:
            if region.name == 'us-west-1':
                # TODO: troubles launching AMIs.
                continue
            yield region, region.zones

    @classmethod
    def get_default_ami(cls, region_name: str) -> str:
        # AWS Deep Learning AMI (Ubuntu 18.04), version 50.0
        # https://aws.amazon.com/marketplace/pp/prodview-x5nivojpquy6y
        amis = {
            'us-east-1': 'ami-0e3c68b57d50caf64',
            'us-east-2': 'ami-0ae79682024fe31cd',
            # 'us-west-1': 'TODO: cannot launch',
            'us-west-2': 'ami-0050625d58fa27b6d',
        }
        assert region_name in amis, region_name
        return amis[region_name]

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        return aws_catalog.get_hourly_cost(instance_type, use_spot=use_spot)

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://aws.amazon.com/s3/pricing/
        # NOTE: egress from US East (Ohio).
        # NOTE: Not accurate as the pricing tier is based on cumulative monthly
        # usage.
        if num_gigabytes > 150 * 1024:
            return 0.05 * num_gigabytes
        cost = 0.0
        if num_gigabytes >= 50 * 1024:
            cost += (num_gigabytes - 50 * 1024) * 0.07
            num_gigabytes -= 50 * 1024

        if num_gigabytes >= 10 * 1024:
            cost += (num_gigabytes - 10 * 1024) * 0.085
            num_gigabytes -= 10 * 1024

        if num_gigabytes > 1:
            cost += (num_gigabytes - 1) * 0.09

        cost += 0.0
        return cost

    def __repr__(self):
        return AWS._REPR

    def is_same_cloud(self, other):
        return isinstance(other, AWS)

    @classmethod
    def get_default_instance_type(cls):
        # 8 vCpus, 32 GB RAM.  Prev-gen (as of 2021) general purpose.
        return 'm4.2xlarge'

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.

    def get_accelerators_from_instance_type(
            self,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return aws_catalog.get_accelerators_from_instance_type(instance_type)

    def make_deploy_resources_variables(self, resources):
        r = resources
        # r.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None
        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
        }

    def get_feasible_launchable_resources(self, resources):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources.accelerators = None
            return [resources]

        def _make(instance_type):
            r = copy.deepcopy(resources)
            r.cloud = AWS()
            r.instance_type = instance_type
            # Setting this to None as AWS doesn't separately bill / attach the
            # accelerators.  Billed as part of the VM type.
            r.accelerators = None
            return [r]

        # Currently, handle a filter on accelerators only.
        accelerators = resources.get_accelerators()
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return _make(AWS.get_default_instance_type())

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        instance_type = aws_catalog.get_instance_type_for_accelerator(
            acc, acc_count)
        if instance_type is None:
            return []
        return _make(instance_type)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        # This file is required because it will be synced to remote VMs for
        # `aws` to access private storage buckets.
        # `aws configure list` does not guarantee this file exists.
        if not os.path.isfile(os.path.expanduser('~/.aws/credentials')):
            return (False,
                    '~/.aws/credentials does not exist. Run `aws configure`.')
        try:
            output = _run_output('aws configure list')
        except subprocess.CalledProcessError:
            return False, 'AWS CLI not installed properly.'
        # Configured correctly, the AWS output should look like this:
        #   ...
        #   access_key     ******************** shared-credentials-file
        #   secret_key     ******************** shared-credentials-file
        #   ...
        # Otherwise, one or both keys will show as '<not set>'.
        lines = output.split('\n')
        if len(lines) < 2:
            return False, 'AWS CLI output invalid.'
        access_key_ok = False
        secret_key_ok = False
        for line in lines[2:]:
            line = line.lstrip()
            if line.startswith('access_key'):
                if '<not set>' not in line:
                    access_key_ok = True
            elif line.startswith('secret_key'):
                if '<not set>' not in line:
                    secret_key_ok = True
        if access_key_ok and secret_key_ok:
            return True, None
        return False, 'AWS credentials not set. Run `aws configure`.'

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {'~/.aws': '~/.aws'}

"""Amazon Web Services."""

# pylint: disable=import-outside-toplevel

import json
import os
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky.adaptors import aws
from sky.clouds import service_catalog
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    # renaming to avoid shadowing variables
    from sky import resources as resources_lib

# This local file (under ~/.aws/) will be uploaded to remote nodes (any
# cloud), if all of the following conditions hold:
#   - the current user identity is not using AWS SSO
#   - this file exists
# It has the following purposes:
#   - make all nodes (any cloud) able to access private S3 buckets
#   - make some remote nodes able to launch new nodes on AWS (i.e., makes
#     AWS head node able to launch AWS workers, or any-cloud spot controller
#     able to launch spot clusters on AWS).
#
# If we detect the current user identity is AWS SSO, we will not upload this
# file to any remote nodes (any cloud). Instead, a SkyPilot IAM role is
# assigned to both AWS head and workers.
# TODO(skypilot): This also means we leave open a bug for AWS SSO users that
# use multiple clouds. The non-AWS nodes will have neither the credential
# file nor the ability to understand AWS IAM.
_CREDENTIAL_FILES = [
    'credentials',
]

DEFAULT_AMI_GB = 45


@clouds.CLOUD_REGISTRY.register
class AWS(clouds.Cloud):
    """Amazon Web Services."""

    _REPR = 'AWS'

    # AWS has a limit of the tag value length to 256 characters.
    # By testing, the actual limit is 256 - 12 = 244 characters
    # (ray adds additional `ray-` and `-worker`), due to the
    # maximum length of DescribeInstances API filter value.
    # Reference: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Using_Tags.html # pylint: disable=line-too-long
    _MAX_CLUSTER_NAME_LEN_LIMIT = 244

    _regions: List[clouds.Region] = []

    _INDENT_PREFIX = '    '
    _STATIC_CREDENTIAL_HELP_STR = (
        'Run the following commands:'
        f'\n{_INDENT_PREFIX}  $ pip install boto3'
        f'\n{_INDENT_PREFIX}  $ aws configure'
        f'\n{_INDENT_PREFIX}For more info: '
        'https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html'  # pylint: disable=line-too-long
    )

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return dict()

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def _sso_credentials_help_str(cls, expired: bool = False) -> str:
        help_str = 'Run the following commands (must use aws v2 CLI):'
        if not expired:
            help_str += f'\n{cls._INDENT_PREFIX}  $ aws configure sso'
        help_str += (
            f'\n{cls._INDENT_PREFIX}  $ aws sso login --profile <profile_name>'
            f'\n{cls._INDENT_PREFIX}For more info: '
            'https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html'  # pylint: disable=line-too-long
        )
        return help_str

    _MAX_AWSCLI_MAJOR_VERSION = 1

    #### Regions/Zones ####

    @classmethod
    def regions(cls):
        if not cls._regions:
            # https://aws.amazon.com/premiumsupport/knowledge-center/vpc-find-availability-zone-options/
            cls._regions = [
                clouds.Region('us-west-1').set_zones([
                    clouds.Zone('us-west-1a'),
                    clouds.Zone('us-west-1b'),
                ]),
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
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'aws')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        if zone is not None:
            for r in regions:
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        # AWS provisioner can handle batched requests, so yield all zones under
        # each region.
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=None,
                                            zone=None)
        for region in regions:
            yield region, region.zones

    @classmethod
    def _get_default_ami(cls, region_name: str, instance_type: str) -> str:
        acc = cls.get_accelerators_from_instance_type(instance_type)
        image_id = service_catalog.get_image_id_from_tag(
            'skypilot:gpu-ubuntu-2004', region_name, clouds='aws')
        if acc is not None:
            assert len(acc) == 1, acc
            acc_name = list(acc.keys())[0]
            if acc_name == 'K80':
                image_id = service_catalog.get_image_id_from_tag(
                    'skypilot:k80-ubuntu-2004', region_name, clouds='aws')
        if image_id is not None:
            return image_id
        # Raise ResourcesUnavailableError to make sure the failover in
        # CloudVMRayBackend will be correctly triggered.
        # TODO(zhwu): This is a information leakage to the cloud implementor,
        # we need to find a better way to handle this.
        raise exceptions.ResourcesUnavailableError(
            'No image found in catalog for region '
            f'{region_name}. Try setting a valid image_id.')

    @classmethod
    def _get_image_id(
        cls,
        image_id: Optional[Dict[Optional[str], str]],
        region_name: str,
        instance_type: str,
    ) -> str:
        if image_id is None:
            return cls._get_default_ami(region_name, instance_type)
        if None in image_id:
            image_id_str = image_id[None]
        else:
            assert region_name in image_id, image_id
            image_id_str = image_id[region_name]
        if image_id_str.startswith('skypilot:'):
            image_id_str = service_catalog.get_image_id_from_tag(image_id_str,
                                                                 region_name,
                                                                 clouds='aws')
            if image_id_str is None:
                # Raise ResourcesUnavailableError to make sure the failover
                # in CloudVMRayBackend will be correctly triggered.
                # TODO(zhwu): This is a information leakage to the cloud
                # implementor, we need to find a better way to handle this.
                raise exceptions.ResourcesUnavailableError(
                    f'No image found for region {region_name}')
        return image_id_str

    def get_image_size(self, image_id: str, region: Optional[str]) -> float:
        if image_id.startswith('skypilot:'):
            return DEFAULT_AMI_GB
        assert region is not None, (image_id, region)
        client = aws.client('ec2', region_name=region)
        try:
            image_info = client.describe_images(ImageIds=[image_id])
            image_info = image_info['Images'][0]
            image_size = image_info['BlockDeviceMappings'][0]['Ebs'][
                'VolumeSize']
        except aws.botocore_exceptions().NoCredentialsError:
            # Fallback to default image size if no credentials are available.
            # The credentials issue will be caught when actually provisioning
            # the instance and appropriate errors will be raised there.
            return DEFAULT_AMI_GB
        except aws.botocore_exceptions().ClientError:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Image {image_id!r} not found in '
                                 f'AWS region {region}') from None
        return image_size

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        # The command for getting the current zone is from:
        # https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-identity-documents.html  # pylint: disable=line-too-long
        command_str = (
            'curl -s http://169.254.169.254/latest/dynamic/instance-identity/document'  # pylint: disable=line-too-long
            ' | python3 -u -c "import sys, json; '
            'print(json.load(sys.stdin)[\'availabilityZone\'])"')
        return command_str

    #### Normal methods ####

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='aws')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # AWS includes accelerators as part of the instance type.  Implementing
        # this is also necessary for e.g., the instance may have 4 GPUs, while
        # the task specifies to use 1 GPU.
        return 0

    def get_egress_cost(self, num_gigabytes: float):
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

    def is_same_cloud(self, other: clouds.Cloud):
        return isinstance(other, AWS)

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         clouds='aws')

    # TODO: factor the following three methods, as they are the same logic
    # between Azure and AWS.
    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='aws')

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='aws')

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        if region is None:
            assert zones is None, (
                'Set either both or neither for: region, zones.')
            region = self._get_default_region()
            zones = region.zones
        else:
            assert zones is not None, (
                'Set either both or neither for: region, zones.')

        region_name = region.name
        zone_names = [zone.name for zone in zones]

        r = resources
        # r.accelerators is cleared but .instance_type encodes the info.
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        image_id = self._get_image_id(r.image_id, region_name, r.instance_type)

        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            'region': region_name,
            'zones': ','.join(zone_names),
            'image_id': image_id,
        }

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=AWS(),
                    instance_type=instance_type,
                    # Setting this to None as AWS doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = AWS.get_default_instance_type(
                cpus=resources.cpus)
            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            use_spot=resources.use_spot,
            cpus=resources.cpus,
            region=resources.region,
            zone=resources.zone,
            clouds='aws')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        try:
            # pylint: disable=top-level-import-outside-toplevel,unused-import
            import boto3
            import botocore
        except ImportError:
            raise ImportError('Fail to import dependencies for AWS.'
                              'Try pip install "skypilot[aws]"') from None

        # Checks if the AWS CLI is installed properly
        proc = subprocess.run('aws --version',
                              shell=True,
                              check=False,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0:
            return False, (
                'AWS CLI is not installed properly. '
                'Run the following commands:'
                f'\n{self._INDENT_PREFIX}  $ pip install skypilot[aws]'
                f'{self._INDENT_PREFIX}Credentials may also need to be set. '
                f'{self._STATIC_CREDENTIAL_HELP_STR}')

        # Checks if AWS credentials 1) exist and 2) are valid.
        # https://stackoverflow.com/questions/53548737/verify-aws-credentials-with-boto3
        try:
            self.get_current_user_identity()
        except exceptions.CloudUserIdentityError as e:
            return False, str(e)

        static_credential_exists = os.path.isfile(
            os.path.expanduser('~/.aws/credentials'))
        hints = None
        if self._is_current_identity_sso():
            hints = 'AWS SSO is set. '
            if static_credential_exists:
                hints += (
                    ' To ensure multiple clouds work correctly, please use SkyPilot '
                    'with static credentials (e.g., ~/.aws/credentials) by unsetting '
                    'the AWS_PROFILE environment variable.')
            else:
                hints += (
                    ' It will work if you use AWS only, but will cause problems '
                    'if you want to use multiple clouds. To set up static credentials, '
                    'try: aws configure')

        else:
            # This file is required because it is required by the VMs launched on
            # other clouds to access private s3 buckets and resources like EC2.
            # `get_current_user_identity` does not guarantee this file exists.
            if not static_credential_exists:
                return (False, '~/.aws/credentials does not exist. ' +
                        self._STATIC_CREDENTIAL_HELP_STR)

        # Fetch the AWS availability zones mapping from ID to name.
        from sky.clouds.service_catalog import aws_catalog  # pylint: disable=import-outside-toplevel,unused-import
        return True, hints

    def _is_current_identity_sso(self) -> bool:
        proc = subprocess.run('aws configure list',
                              shell=True,
                              check=False,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0:
            return False
        return 'sso' in proc.stdout.decode().split()

    def get_current_user_identity(self) -> Optional[str]:
        """Returns the identity of the user on this cloud."""
        try:
            sts = aws.client('sts')
            # The caller identity contains 3 fields: UserId, AccountId, Arn.
            # 'UserId' is unique across all AWS entity, which looks like
            # "AROADBQP57FF2AEXAMPLE:role-session-name"
            # 'AccountId' can be shared by multiple users under the same
            # organization
            # 'Arn' is the full path to the user, which can be reused when
            # the user is deleted and recreated.
            # Refer to https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html # pylint: disable=line-too-long
            # and https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_variables.html#principaltable # pylint: disable=line-too-long
            user_id = sts.get_caller_identity()['UserId']
        except aws.botocore_exceptions().NoCredentialsError:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'AWS credentials are not set. {self._STATIC_CREDENTIAL_HELP_STR}'
                ) from None
        except aws.botocore_exceptions().ClientError:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'Failed to access AWS services with credentials. '
                    'Make sure that the access and secret keys are correct.'
                    f' {self._STATIC_CREDENTIAL_HELP_STR}') from None
        except aws.botocore_exceptions().InvalidConfigError as e:
            import awscli
            from packaging import version
            awscli_version = version.parse(awscli.__version__)
            if (awscli_version < version.parse('1.27.10') and
                    'configured to use SSO' in str(e)):
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.CloudUserIdentityError(
                        'awscli is too old to use SSO. Run the following command to upgrade:'
                        f'\n{self._INDENT_PREFIX}  $ pip install awscli>=1.27.10'
                        f'\n{self._INDENT_PREFIX}You may need to log into SSO again after '
                        f'upgrading. {self._sso_credentials_help_str()}'
                    ) from None
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Invalid AWS configuration.\n'
                    f'  Reason: {common_utils.format_exception(e, use_bracket=True)}.'
                ) from None
        except aws.botocore_exceptions().TokenRetrievalError:
            # This is raised when the access token is expired, which mainly
            # happens when the user is using temporary credentials or SSO
            # login.
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'AWS access token is expired.'
                    f' {self._sso_credentials_help_str(expired=True)}'
                ) from None
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Failed to get AWS user.\n'
                    f'  Reason: {common_utils.format_exception(e, use_bracket=True)}.'
                ) from None
        return user_id

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # TODO(skypilot): ~/.aws/credentials is required for users using multiple clouds.
        # If this file does not exist, users can launch on AWS via AWS SSO and assign
        # IAM role to the cluster.
        # However, if users launch clusters in a non-AWS cloud, those clusters do not
        # understand AWS IAM role so will not be able to access private AWS EC2 resources
        # and S3 buckets.

        # The file should not be uploaded if the user is using SSO, as the credential
        # file can be from a different account, and will make autopstop/autodown/spot
        # controller misbehave.

        # TODO(zhwu/zongheng): We can also avoid uploading the credential file for the
        # cluster launched on AWS even if the user is using static credentials. We need
        # to define a mechanism to find out the cloud provider of the cluster to be
        # launched in this function and make sure the cluster will not be used for
        # launching clusters in other clouds, e.g. spot controller.
        if self._is_current_identity_sso():
            return {}
        return {
            f'~/.aws/{filename}': f'~/.aws/{filename}'
            for filename in _CREDENTIAL_FILES
            if os.path.exists(os.path.expanduser(f'~/.aws/{filename}'))
        }

    def instance_type_exists(self, instance_type):
        return service_catalog.instance_type_exists(instance_type, clouds='aws')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'aws')

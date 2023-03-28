"""Amazon Web Services."""
import enum
import functools
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


class AWSIdentityType(enum.Enum):
    """AWS identity type.

    The account type is determined by the current user identity,
    based on `aws configure list`. We will check the existence of
    the value in the output of `aws configure list` to determine
    the account type.
    """
    SSO = 'sso'
    IAM_ROLE = 'iam-role'
    STATIC = 'static'


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
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'aws')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[List[clouds.Zone]]:
        # AWS provisioner can handle batched requests, so yield all zones under
        # each region.
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            assert r.zones is not None, r
            if num_nodes > 1:
                # When num_nodes > 1, we shouldn't pass a list of zones to the
                # AWS NodeProvider to try, because it may then place the nodes of
                # the same cluster in different zones. This is an artifact of the
                # current AWS NodeProvider implementation.
                for z in r.zones:
                    yield [z]
            else:
                yield r.zones

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
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
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
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='aws')

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources', region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        assert zones is not None, (region, zones)

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
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = AWS.get_default_instance_type(
                cpus=resources.cpus, memory=resources.memory)
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
            memory=resources.memory,
            region=resources.region,
            zone=resources.zone,
            clouds='aws')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    @functools.lru_cache(maxsize=1)  # Cache since getting identity is slow.
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        try:
            # pylint: disable=import-outside-toplevel,unused-import
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
                f'\n{cls._INDENT_PREFIX}  $ pip install skypilot[aws]'
                f'{cls._INDENT_PREFIX}Credentials may also need to be set. '
                f'{cls._STATIC_CREDENTIAL_HELP_STR}')

        # Checks if AWS credentials 1) exist and 2) are valid.
        # https://stackoverflow.com/questions/53548737/verify-aws-credentials-with-boto3
        try:
            cls.get_current_user_identity()
        except exceptions.CloudUserIdentityError as e:
            return False, str(e)

        static_credential_exists = os.path.isfile(
            os.path.expanduser('~/.aws/credentials'))
        hints = None
        identity_type = cls._current_identity_type()
        single_cloud_hint = (
            ' It will work if you use AWS only, but will cause problems '
            'if you want to use multiple clouds. To set up static credentials, '
            'try: aws configure')
        if identity_type == AWSIdentityType.SSO:
            hints = 'AWS SSO is set.'
            if static_credential_exists:
                hints += (
                    ' To ensure multiple clouds work correctly, please use SkyPilot '
                    'with static credentials (e.g., ~/.aws/credentials) by unsetting '
                    'the AWS_PROFILE environment variable.')
            else:
                hints += single_cloud_hint
        elif identity_type == AWSIdentityType.IAM_ROLE:
            # When using an IAM role, the credentials may not exist in the
            # ~/.aws/credentials file. So we don't check for the existence of the
            # file. This will happen when the user is on a VM (or spot-controller)
            # created by an SSO account, i.e. the VM will be assigned the IAM
            # role: skypilot-v1.
            hints = f'AWS IAM role is set.{single_cloud_hint}'
        else:
            # This file is required because it is required by the VMs launched on
            # other clouds to access private s3 buckets and resources like EC2.
            # `get_current_user_identity` does not guarantee this file exists.
            if not static_credential_exists:
                return (False, '~/.aws/credentials does not exist. ' +
                        cls._STATIC_CREDENTIAL_HELP_STR)

        # Fetch the AWS catalogs
        from sky.clouds.service_catalog import aws_catalog  # pylint: disable=import-outside-toplevel,unused-import
        # Trigger the fetch of the availability zones mapping.
        aws_catalog.get_default_instance_type()
        return True, hints

    @classmethod
    def _current_identity_type(cls) -> Optional[AWSIdentityType]:
        proc = subprocess.run('aws configure list',
                              shell=True,
                              check=False,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0:
            return None
        # We determine the identity type by looking at the output of
        # `aws configure list`. The output looks like:
        #   Name                   Value         Type    Location
        #   ----                   -----         ----    --------
        #   profile                <not set>     None    None
        #   access_key     *       <not set>     sso     None
        #   secret_key     *       <not set>     sso     None
        #   region                 <not set>     None    None
        # We try to determine the identity type by looking for the
        # string "sso"/"iam-role" in the output, i.e. the "Type" column.
        if AWSIdentityType.SSO.value in proc.stdout.decode():
            return AWSIdentityType.SSO
        elif AWSIdentityType.IAM_ROLE.value in proc.stdout.decode():
            return AWSIdentityType.IAM_ROLE
        else:
            return AWSIdentityType.STATIC

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        """Returns a [UserId, Account] list that uniquely identifies the user.

        These fields come from `aws sts get-caller-identity`. We permit the same
        actual user to:

          - switch between different root accounts (after which both elements
            of the list will be different) and have their clusters owned by
            each account be protected; or

          - within the same root account, switch between different IAM
            users, and treat [user_id=1234, account=A] and
            [user_id=4567, account=A] to be the *same*. Namely, switching
            between these IAM roles within the same root account will cause
            the first element of the returned list to differ, and will allow
            the same actual user to continue to interact with their clusters.
            Note: this is not 100% safe, since the IAM users can have very
            specific permissions, that disallow them to access the clusters
            but it is a reasonable compromise as that could be rare.

        Returns:
            A list of strings that uniquely identifies the user on this cloud.
            For identity check, we will fallback through the list of strings
            until we find a match, and print a warning if we fail for the
            first string.

        Raises:
            exceptions.CloudUserIdentityError: if the user identity cannot be
                retrieved.
        """
        try:
            sts = aws.client('sts')
            # The caller identity contains 3 fields: UserId, Account, Arn.
            # 1. 'UserId' is unique across all AWS entity, which looks like
            # "AROADBQP57FF2AEXAMPLE:role-session-name"
            # 2. 'Account' can be shared by multiple users under the same
            # organization
            # 3. 'Arn' is the full path to the user, which can be reused when
            # the user is deleted and recreated.
            # Refer to https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html # pylint: disable=line-too-long
            # and https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_variables.html#principaltable # pylint: disable=line-too-long
            user_info = sts.get_caller_identity()
            # Allow fallback to AccountId if UserId does not match, because:
            # 1. In the case where multiple IAM users belong a single root account,
            # those users normally share the visibility of the VMs, so we do not
            # need to identity them with each other. (There can be some cases,
            # when an IAM user is given a limited permission by the admin, we may
            # ignore that case for now, or print out a warning if the underlying
            # userid changed for a cluster).
            # 2. In the case where the multiple users belong to an organization,
            # those users will have different account id, so fallback works.
            user_ids = [user_info['UserId'], user_info['Account']]
        except aws.botocore_exceptions().NoCredentialsError:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'AWS credentials are not set. {cls._STATIC_CREDENTIAL_HELP_STR}'
                ) from None
        except aws.botocore_exceptions().ClientError:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'Failed to access AWS services with credentials. '
                    'Make sure that the access and secret keys are correct.'
                    f' {cls._STATIC_CREDENTIAL_HELP_STR}') from None
        except aws.botocore_exceptions().InvalidConfigError as e:
            # pylint: disable=import-outside-toplevel
            import awscli
            from packaging import version
            awscli_version = version.parse(awscli.__version__)
            if (awscli_version < version.parse('1.27.10') and
                    'configured to use SSO' in str(e)):
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.CloudUserIdentityError(
                        'awscli is too old to use SSO. Run the following command to upgrade:'
                        f'\n{cls._INDENT_PREFIX}  $ pip install awscli>=1.27.10'
                        f'\n{cls._INDENT_PREFIX}You may need to log into SSO again after '
                        f'upgrading. {cls._sso_credentials_help_str()}'
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
                    f' {cls._sso_credentials_help_str(expired=True)}') from None
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Failed to get AWS user.\n'
                    f'  Reason: {common_utils.format_exception(e, use_bracket=True)}.'
                ) from None
        return user_ids

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # TODO(skypilot): ~/.aws/credentials is required for users using multiple clouds.
        # If this file does not exist, users can launch on AWS via AWS SSO or assumed IAM
        # role (only when the user is on an AWS cluster) and assign IAM role to the cluster.
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
        if self._current_identity_type() != AWSIdentityType.STATIC:
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

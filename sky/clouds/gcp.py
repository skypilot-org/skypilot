"""Google Cloud Platform."""
import json
import os
import subprocess
import time
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import gcp
from sky.clouds import service_catalog
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources

logger = sky_logging.init_logger(__name__)

DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH = os.path.expanduser(
    '~/.config/gcloud/'
    'application_default_credentials.json')

# TODO(wei-lin): config_default may not be the config in use.
# See: https://github.com/skypilot-org/skypilot/pull/1539
GCP_CONFIG_PATH = '~/.config/gcloud/configurations/config_default'
# Do not place the backup under the gcloud config directory, as ray
# autoscaler can overwrite that directory on the remote nodes.
GCP_CONFIG_SKY_BACKUP_PATH = '~/.sky/.sky_gcp_config_default'

# Minimum set of files under ~/.config/gcloud that grant GCP access.
_CREDENTIAL_FILES = [
    'credentials.db',
    'application_default_credentials.json',
    'access_tokens.db',
    'configurations',
    'legacy_credentials',
    'active_config',
]

_GCLOUD_INSTALLATION_LOG = '~/.sky/logs/gcloud_installation.log'
# Need to be run with /bin/bash
# We factor out the installation logic to keep it align in both spot
# controller and cloud stores.
GCLOUD_INSTALLATION_COMMAND = f'pushd /tmp &>/dev/null && \
    gcloud --help > /dev/null 2>&1 || \
    {{ mkdir -p {os.path.dirname(_GCLOUD_INSTALLATION_LOG)} && \
    wget --quiet https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-sdk-382.0.0-linux-x86_64.tar.gz > {_GCLOUD_INSTALLATION_LOG} && \
    tar xzf google-cloud-sdk-382.0.0-linux-x86_64.tar.gz >> {_GCLOUD_INSTALLATION_LOG} && \
    rm -rf ~/google-cloud-sdk >> {_GCLOUD_INSTALLATION_LOG}  && \
    mv google-cloud-sdk ~/ && \
    ~/google-cloud-sdk/install.sh -q >> {_GCLOUD_INSTALLATION_LOG} 2>&1 && \
    echo "source ~/google-cloud-sdk/path.bash.inc > /dev/null 2>&1" >> ~/.bashrc && \
    source ~/google-cloud-sdk/path.bash.inc >> {_GCLOUD_INSTALLATION_LOG} 2>&1; }} && \
    {{ cp {GCP_CONFIG_SKY_BACKUP_PATH} {GCP_CONFIG_PATH} > /dev/null 2>&1 || true; }} && \
    popd &>/dev/null'

# TODO(zhwu): Move the default AMI size to the catalog instead.
DEFAULT_GCP_IMAGE_GB = 50


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


def is_api_disabled(endpoint: str, project_id: str) -> bool:
    proc = subprocess.run((f'gcloud services list --project {project_id} '
                           f' | grep {endpoint}.googleapis.com'),
                          check=False,
                          shell=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.returncode != 0


@clouds.CLOUD_REGISTRY.register
class GCP(clouds.Cloud):
    """Google Cloud Platform."""

    _REPR = 'GCP'

    # GCP has a 63 char limit; however, Ray autoscaler adds many
    # characters. Through testing, this is the maximum length for the Sky
    # cluster name on GCP.  Ref:
    # https://cloud.google.com/compute/docs/naming-resources#resource-name-format
    # NOTE: actually 37 is maximum for a single-node cluster which gets the
    # suffix '-head', but 35 for a multinode cluster because workers get the
    # suffix '-worker'. Here we do not distinguish these cases and take the
    # lower limit.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 35

    _regions: List[clouds.Region] = []
    _zones: List[clouds.Zone] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return dict()

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    #### Regions/Zones ####

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        if not cls._regions:
            # https://cloud.google.com/compute/docs/regions-zones
            cls._regions = [
                clouds.Region('us-west1').set_zones([
                    clouds.Zone('us-west1-a'),
                    clouds.Zone('us-west1-b'),
                    # clouds.Zone('us-west1-c'),  # No GPUs.
                ]),
                clouds.Region('us-central1').set_zones([
                    clouds.Zone('us-central1-a'),
                    clouds.Zone('us-central1-b'),
                    clouds.Zone('us-central1-c'),
                    clouds.Zone('us-central1-f'),
                ]),
                clouds.Region('us-east1').set_zones([
                    clouds.Zone('us-east1-b'),
                    clouds.Zone('us-east1-c'),
                    clouds.Zone('us-east1-d'),
                ]),
                clouds.Region('us-east4').set_zones([
                    clouds.Zone('us-east4-a'),
                    clouds.Zone('us-east4-b'),
                    clouds.Zone('us-east4-c'),
                ]),
                clouds.Region('us-west2').set_zones([
                    # clouds.Zone('us-west2-a'),  # No GPUs.
                    clouds.Zone('us-west2-b'),
                    clouds.Zone('us-west2-c'),
                ]),
                # Ignoring us-west3 as it doesn't have GPUs.
                clouds.Region('us-west4').set_zones([
                    clouds.Zone('us-west4-a'),
                    clouds.Zone('us-west4-b'),
                    # clouds.Zone('us-west4-c'),  # No GPUs.
                ]),
            ]
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        if accelerators is None:
            if instance_type is None:
                # Fall back to the default regions.
                # TODO: Get the regions from the service catalog.
                regions = cls.regions()
            else:
                regions = service_catalog.get_region_zones_for_instance_type(
                    instance_type, use_spot, clouds='gcp')
        else:
            assert len(accelerators) == 1, accelerators
            acc = list(accelerators.keys())[0]
            acc_count = list(accelerators.values())[0]
            acc_regions = service_catalog.get_region_zones_for_accelerators(
                acc, acc_count, use_spot, clouds='gcp')
            if instance_type is None:
                regions = acc_regions
            elif instance_type == 'TPU-VM':
                regions = acc_regions
            else:
                vm_regions = service_catalog.get_region_zones_for_instance_type(
                    instance_type, use_spot, clouds='gcp')
                # Find the intersection between `acc_regions` and `vm_regions`.
                regions = []
                for r1 in acc_regions:
                    for r2 in vm_regions:
                        if r1.name != r2.name:
                            continue
                        zones = []
                        for z1 in r1.zones:
                            for z2 in r2.zones:
                                if z1.name == z2.name:
                                    zones.append(z1)
                        if zones:
                            regions.append(r1.set_zones(zones))
                        break

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
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=None,
                                            zone=None)
        # GCP provisioner currently takes 1 zone per request.
        for region in regions:
            for zone in region.zones:
                yield (region, [zone])

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        # The command for getting the current zone is from:
        # https://cloud.google.com/compute/docs/metadata/querying-metadata
        command_str = (
            'curl -s http://metadata.google.internal/computeMetadata/v1/instance/zone'  # pylint: disable=line-too-long
            ' -H "Metadata-Flavor: Google" | awk -F/ \'{print $4}\'')
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
                                               clouds='gcp')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        assert len(accelerators) == 1, accelerators
        acc, acc_count = list(accelerators.items())[0]
        return service_catalog.get_accelerator_hourly_cost(acc,
                                                           acc_count,
                                                           use_spot=use_spot,
                                                           region=region,
                                                           zone=zone,
                                                           clouds='gcp')

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://cloud.google.com/storage/pricing#network-pricing
        # NOTE: egress to worldwide (excl. China, Australia).
        if num_gigabytes <= 1024:
            return 0.12 * num_gigabytes
        elif num_gigabytes <= 1024 * 10:
            return 0.11 * num_gigabytes
        else:
            return 0.08 * num_gigabytes

    def is_same_cloud(self, other):
        return isinstance(other, GCP)

    def get_image_size(self, image_id: str, region: Optional[str]) -> float:
        del region  # unused
        if image_id.startswith('skypilot:'):
            return DEFAULT_GCP_IMAGE_GB
        try:
            compute = gcp.build('compute',
                                'v1',
                                credentials=None,
                                cache_discovery=False)
        except gcp.credential_error_exception() as e:
            return DEFAULT_GCP_IMAGE_GB
        try:
            image_attrs = image_id.split('/')
            if len(image_attrs) == 1:
                raise ValueError(f'Image {image_id!r} not found in GCP.')
            project = image_attrs[1]
            image_name = image_attrs[-1]
            image_infos = compute.images().get(project=project,
                                               image=image_name).execute()
            return float(image_infos['diskSizeGb'])
        except gcp.http_error_exception() as e:
            if e.resp.status == 403:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Not able to access the image '
                                     f'{image_id!r}') from None
            if e.resp.status == 404:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(f'Image {image_id!r} not found in '
                                     'GCP.') from None
            raise

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         clouds='gcp')

    @classmethod
    def _get_default_region(cls) -> clouds.Region:
        return cls.regions()[-1]

    def make_deploy_resources_variables(
            self, resources: 'resources.Resources',
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
        zone_name = zones[0].name

        # gcloud compute images list \
        # --project deeplearning-platform-release \
        # --no-standard-images
        # We use the debian image, as the ubuntu image has some connectivity
        # issue when first booted.
        image_id = 'skypilot:cpu-debian-10'

        r = resources
        # Find GPU spec, if any.
        resources_vars = {
            'instance_type': r.instance_type,
            'region': region_name,
            'zones': zone_name,
            'gpu': None,
            'gpu_count': None,
            'tpu': None,
            'tpu_vm': False,
            'custom_resources': None,
            'use_spot': r.use_spot,
        }
        accelerators = r.accelerators
        if accelerators is not None:
            assert len(accelerators) == 1, r
            acc, acc_count = list(accelerators.items())[0]
            resources_vars['custom_resources'] = json.dumps(accelerators,
                                                            separators=(',',
                                                                        ':'))
            if 'tpu' in acc:
                resources_vars['tpu_type'] = acc.replace('tpu-', '')
                assert r.accelerator_args is not None, r

                resources_vars['tpu_vm'] = r.accelerator_args.get('tpu_vm')
                resources_vars['runtime_version'] = r.accelerator_args[
                    'runtime_version']
                resources_vars['tpu_name'] = r.accelerator_args.get('tpu_name')
            else:
                # Convert to GCP names:
                # https://cloud.google.com/compute/docs/gpus
                if acc == 'A100-80GB':
                    # A100-80GB has a different name pattern.
                    resources_vars['gpu'] = 'nvidia-{}'.format(acc.lower())
                else:
                    resources_vars['gpu'] = 'nvidia-tesla-{}'.format(
                        acc.lower())
                resources_vars['gpu_count'] = acc_count
                if acc == 'K80':
                    # Though the image is called cu113, it actually has later
                    # versions of CUDA as noted below.
                    # CUDA driver version 470.57.02, CUDA Library 11.4
                    image_id = 'skypilot:k80-debian-10'
                else:
                    # Though the image is called cu113, it actually has later
                    # versions of CUDA as noted below.
                    # CUDA driver version 510.47.03, CUDA Library 11.6
                    # Does not support torch==1.13.0 with cu117
                    image_id = 'skypilot:gpu-debian-10'

        if resources.image_id is not None:
            if None in resources.image_id:
                image_id = resources.image_id[None]
            else:
                assert region_name in resources.image_id, resources.image_id
                image_id = resources.image_id[region_name]
        if image_id.startswith('skypilot:'):
            image_id = service_catalog.get_image_id_from_tag(image_id,
                                                             clouds='gcp')

        assert image_id is not None, (image_id, r)
        resources_vars['image_id'] = image_id

        return resources_vars

    def get_feasible_launchable_resources(self, resources):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return ([resources], [])

        if resources.accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            host_vm_type = GCP.get_default_instance_type(cpus=resources.cpus)
            if host_vm_type is None:
                return ([], [])
            else:
                r = resources.copy(
                    cloud=GCP(),
                    instance_type=host_vm_type,
                    accelerators=None,
                    cpus=None,
                )
                return ([r], [])

        use_tpu_vm = False
        if resources.accelerator_args is not None:
            use_tpu_vm = resources.accelerator_args.get('tpu_vm', False)

        # Find instance candidates to meet user's requirements
        assert len(resources.accelerators.items()
                  ) == 1, 'cannot handle more than one accelerator candidates.'
        acc, acc_count = list(resources.accelerators.items())[0]

        # For TPU VMs, the instance type is fixed to 'TPU-VM'. However, we still
        # need to call the below function to get the fuzzy candidate list.
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            cpus=resources.cpus if not use_tpu_vm else None,
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone,
            clouds='gcp')

        if instance_list is None:
            return ([], fuzzy_candidate_list)
        assert len(
            instance_list
        ) == 1, f'More than one instance type matched, {instance_list}'

        if use_tpu_vm:
            host_vm_type = 'TPU-VM'
            # FIXME(woosuk): This leverages the fact that TPU VMs have 96 vCPUs.
            num_cpus_in_tpu_vm = 96
            if resources.cpus is not None:
                if resources.cpus.endswith('+'):
                    cpus = float(resources.cpus[:-1])
                    if cpus > num_cpus_in_tpu_vm:
                        return ([], fuzzy_candidate_list)
                else:
                    cpus = float(resources.cpus)
                    if cpus != num_cpus_in_tpu_vm:
                        return ([], fuzzy_candidate_list)
        else:
            host_vm_type = instance_list[0]

        acc_dict = {acc: acc_count}
        r = resources.copy(
            cloud=GCP(),
            instance_type=host_vm_type,
            accelerators=acc_dict,
            cpus=None,
        )
        return ([r], fuzzy_candidate_list)

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        # GCP handles accelerators separately from regular instance types,
        # hence return none here.
        return None

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='gcp')

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        try:
            # pylint: disable=import-outside-toplevel,unused-import
            from google import auth  # type: ignore
            # Check google-api-python-client installation.
            import googleapiclient

            # These files are required because they will be synced to remote
            # VMs for `gsutil` to access private storage buckets.
            # `auth.default()` does not guarantee these files exist.
            for file in [
                    '~/.config/gcloud/access_tokens.db',
                    '~/.config/gcloud/credentials.db'
            ]:
                if not os.path.isfile(os.path.expanduser(file)):
                    raise FileNotFoundError(file)
            # Check the installation of google-cloud-sdk.
            _run_output('gcloud --version')

            # Check if application default credentials are set.
            project_id = self.get_project_id()

            # Check if the user is activated.
            self.get_current_user_identity()
        except (auth.exceptions.DefaultCredentialsError,
                subprocess.CalledProcessError,
                exceptions.CloudUserIdentityError, FileNotFoundError,
                ImportError):
            # See also: https://stackoverflow.com/a/53307505/1165051
            return False, (
                'GCP tools are not installed or credentials are not set. '
                'Run the following commands:\n    '
                # Install the Google Cloud SDK:
                '  $ pip install google-api-python-client\n    '
                '  $ conda install -c conda-forge google-cloud-sdk -y\n    '
                # This authenticates the CLI to make `gsutil` work:
                '  $ gcloud init\n    '
                # This will generate
                # ~/.config/gcloud/application_default_credentials.json.
                '  $ gcloud auth application-default login\n    '
                'For more info: '
                'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html'  # pylint: disable=line-too-long
            )

        # Check APIs.
        apis = (
            ('compute', 'Compute Engine'),
            ('cloudresourcemanager', 'Cloud Resource Manager'),
            ('iam', 'Identity and Access Management (IAM)'),
            ('tpu', 'Cloud TPU'),  # Keep as final element.
        )
        enabled_api = False
        for endpoint, display_name in apis:
            if is_api_disabled(endpoint, project_id):
                # For 'compute': ~55-60 seconds for the first run. If already
                # enabled, ~1s. Other API endpoints take ~1-5s to enable.
                if endpoint == 'compute':
                    suffix = ' (free of charge; this may take a minute)'
                else:
                    suffix = ' (free of charge)'
                print(f'\nEnabling {display_name} API{suffix}...')
                t1 = time.time()
                proc = subprocess.run(
                    f'gcloud services enable {endpoint}.googleapis.com '
                    f'--project {project_id}',
                    check=False,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT)
                if proc.returncode == 0:
                    enabled_api = True
                    print(f'Done. Took {time.time() - t1:.1f} secs.')
                elif endpoint != 'tpu':
                    print('Failed. Detailed output:')
                    print(proc.stdout.decode())
                    return False, (
                        f'{display_name} API is disabled. Please retry '
                        '`sky check` in a few minutes, or manually enable it.')
                else:
                    # TPU API failed. Should still enable GCP.
                    print('Failed to enable Cloud TPU API. '
                          'This can be ignored if you do not use TPUs. '
                          'Otherwise, please enable it manually.\n'
                          'Detailed output:')
                    print(proc.stdout.decode())

        if enabled_api:
            print('\nHint: Enabled GCP API(s) may take a few minutes to take '
                  'effect. If any SkyPilot commands/calls failed, retry after '
                  'some time.')

        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # Create a backup of the config_default file, as the original file can
        # be modified on the remote cluster by ray causing authentication
        # problems. The backup file will be updated to the remote cluster
        # whenever the original file is not empty and will be applied
        # appropriately on the remote cluster when neccessary.
        if (os.path.exists(os.path.expanduser(GCP_CONFIG_PATH)) and
                os.path.getsize(os.path.expanduser(GCP_CONFIG_PATH)) > 0):
            subprocess.run(f'cp {GCP_CONFIG_PATH} {GCP_CONFIG_SKY_BACKUP_PATH}',
                           shell=True,
                           check=True)
        elif not os.path.exists(os.path.expanduser(GCP_CONFIG_SKY_BACKUP_PATH)):
            raise RuntimeError(
                'GCP credential file is empty. Please make sure you '
                'have run: gcloud init')

        # Excluding the symlink to the python executable created by the gcp
        # credential, which causes problem for ray up multiple nodes, tracked
        # in #494, #496, #483.
        credentials = {
            f'~/.config/gcloud/{filename}': f'~/.config/gcloud/{filename}'
            for filename in _CREDENTIAL_FILES
        }
        credentials[GCP_CONFIG_SKY_BACKUP_PATH] = GCP_CONFIG_SKY_BACKUP_PATH
        return credentials

    def get_current_user_identity(self) -> Optional[str]:
        """Returns the email address + project id of the active user."""
        try:
            account = _run_output('gcloud auth list --filter=status:ACTIVE '
                                  '--format="value(account)"')
        except subprocess.CalledProcessError as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Failed to get GCP user identity with unknown '
                    f'exception.\n'
                    '  Reason: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from e
        if not account:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    'No GCP account is activated. Try running `gcloud '
                    'auth list --filter=status:ACTIVE '
                    '--format="value(account)"` and ensure it correctly '
                    'returns the current user.')
        try:
            return f'{account} [project_id={self.get_project_id()}]'
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Failed to get GCP user identity with unknown '
                    f'exception.\n'
                    '  Reason: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from e

    def instance_type_exists(self, instance_type):
        return service_catalog.instance_type_exists(instance_type, 'gcp')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'gcp')

    def need_cleanup_after_preemption(self,
                                      resources: 'resources.Resources') -> bool:
        """Returns whether a spot resource needs cleanup after preeemption."""
        # Spot TPU VMs require manual cleanup after preemption.
        # "If your Cloud TPU is preempted,
        # you must delete it and create a new one ..."
        # See: https://cloud.google.com/tpu/docs/preemptible#tpu-vm

        # pylint: disable=import-outside-toplevel
        from sky.utils import tpu_utils
        return tpu_utils.is_tpu_vm(resources)

    @classmethod
    def get_project_id(cls, dryrun: bool = False) -> str:
        if dryrun:
            return 'dryrun-project-id'
        # pylint: disable=import-outside-toplevel
        from google import auth  # type: ignore
        _, project_id = auth.default()
        if project_id is None:
            raise exceptions.CloudUserIdentityError(
                'Failed to get GCP project id. Please make sure you have '
                'run: gcloud init')
        return project_id

    @staticmethod
    def check_host_accelerator_compatibility(
            instance_type: str, accelerators: Optional[Dict[str, int]]) -> None:
        service_catalog.check_host_accelerator_compatibility(
            instance_type, accelerators, 'gcp')

    @staticmethod
    def check_accelerator_attachable_to_host(
            instance_type: str,
            accelerators: Optional[Dict[str, int]],
            zone: Optional[str] = None) -> None:
        service_catalog.check_accelerator_attachable_to_host(
            instance_type, accelerators, zone, 'gcp')

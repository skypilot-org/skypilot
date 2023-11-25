"""Google Cloud Platform."""
import dataclasses
import datetime
import functools
import json
import os
import re
import subprocess
import time
import typing
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import cachetools
import colorama

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import gcp
from sky.clouds import service_catalog
from sky.skylet import log_lib
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources

logger = sky_logging.init_logger(__name__)

# Env var pointing to any service account key. If it exists, this path takes
# priority over the DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH below, and will be
# used instead for SkyPilot-launched instances. This is the same behavior as
# gcloud:
# https://cloud.google.com/docs/authentication/provide-credentials-adc#local-key
_GCP_APPLICATION_CREDENTIAL_ENV = 'GOOGLE_APPLICATION_CREDENTIALS'
# NOTE: do not expanduser() on this path. It's used as a destination path on the
# remote cluster.
DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH: str = (
    '~/.config/gcloud/'
    'application_default_credentials.json')

# TODO(wei-lin): config_default may not be the config in use.
# See: https://github.com/skypilot-org/skypilot/pull/1539
# NOTE: do not expanduser() on this path. It's used as a destination path on the
# remote cluster.
GCP_CONFIG_PATH = '~/.config/gcloud/configurations/config_default'
# Do not place the backup under the gcloud config directory, as ray
# autoscaler can overwrite that directory on the remote nodes.
# NOTE: do not expanduser() on this path. It's used as a destination path on the
# remote cluster.
GCP_CONFIG_SKY_BACKUP_PATH = '~/.sky/.sky_gcp_config_default'

# Minimum set of files under ~/.config/gcloud that grant GCP access.
_CREDENTIAL_FILES = [
    'credentials.db',
    'access_tokens.db',
    'configurations',
    'legacy_credentials',
    'active_config',
]

# NOTE: do not expanduser() on this path. It's used as a destination path on the
# remote cluster.
_GCLOUD_INSTALLATION_LOG = '~/.sky/logs/gcloud_installation.log'
_GCLOUD_VERSION = '424.0.0'
# Need to be run with /bin/bash
# We factor out the installation logic to keep it align in both spot
# controller and cloud stores.
GOOGLE_SDK_INSTALLATION_COMMAND: str = f'pushd /tmp &>/dev/null && \
    {{ gcloud --help > /dev/null 2>&1 || \
    {{ mkdir -p {os.path.dirname(_GCLOUD_INSTALLATION_LOG)} && \
    wget --quiet https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-sdk-{_GCLOUD_VERSION}-linux-x86_64.tar.gz > {_GCLOUD_INSTALLATION_LOG} && \
    tar xzf google-cloud-sdk-{_GCLOUD_VERSION}-linux-x86_64.tar.gz >> {_GCLOUD_INSTALLATION_LOG} && \
    rm -rf ~/google-cloud-sdk >> {_GCLOUD_INSTALLATION_LOG}  && \
    mv google-cloud-sdk ~/ && \
    ~/google-cloud-sdk/install.sh -q >> {_GCLOUD_INSTALLATION_LOG} 2>&1 && \
    echo "source ~/google-cloud-sdk/path.bash.inc > /dev/null 2>&1" >> ~/.bashrc && \
    source ~/google-cloud-sdk/path.bash.inc >> {_GCLOUD_INSTALLATION_LOG} 2>&1; }}; }} && \
    {{ cp {GCP_CONFIG_SKY_BACKUP_PATH} {GCP_CONFIG_PATH} > /dev/null 2>&1 || true; }} && \
    popd &>/dev/null'

# TODO(zhwu): Move the default AMI size to the catalog instead.
DEFAULT_GCP_IMAGE_GB = 50

# Firewall rule name for user opened ports.
USER_PORTS_FIREWALL_RULE_NAME = 'sky-ports-{}'

# UX message when image not found in GCP.
# pylint: disable=line-too-long
_IMAGE_NOT_FOUND_UX_MESSAGE = (
    'Image {image_id!r} not found in GCP.\n'
    '\nTo find GCP images: https://cloud.google.com/compute/docs/images\n'
    f'Format: {colorama.Style.BRIGHT}projects/<project-id>/global/images/<image-name>{colorama.Style.RESET_ALL}\n'
    'Example: projects/deeplearning-platform-release/global/images/common-cpu-v20230615-debian-11-py310\n'
    '\nTo find machine images: https://cloud.google.com/compute/docs/machine-images\n'
    f'Format: {colorama.Style.BRIGHT}projects/<project-id>/global/machineImages/<machine-image-name>{colorama.Style.RESET_ALL}\n'
    f'\nYou can query image id using: {colorama.Style.BRIGHT}gcloud compute images list --project <project-id> --no-standard-images{colorama.Style.RESET_ALL}'
    f'\nTo query common AI images: {colorama.Style.BRIGHT}gcloud compute images list --project deeplearning-platform-release | less{colorama.Style.RESET_ALL}'
)


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


@dataclasses.dataclass
class SpecificReservation:
    count: int
    in_use_count: int

    @classmethod
    def from_dict(cls, d: dict) -> 'SpecificReservation':
        return cls(count=int(d['count']), in_use_count=int(d['inUseCount']))


class GCPReservation:
    """GCP Reservation object that contains the reservation information."""

    def __init__(self, self_link: str, zone: str,
                 specific_reservation: SpecificReservation,
                 specific_reservation_required: bool) -> None:
        self.self_link = self_link
        self.zone = zone
        self.specific_reservation = specific_reservation
        self.specific_reservation_required = specific_reservation_required

    @classmethod
    def from_dict(cls, d: dict) -> 'GCPReservation':
        return cls(
            self_link=d['selfLink'],
            zone=d['zone'],
            specific_reservation=SpecificReservation.from_dict(
                d['specificReservation']),
            specific_reservation_required=d['specificReservationRequired'],
        )

    @property
    def available_resources(self) -> int:
        """Count resources available that can be used in this reservation."""
        return (self.specific_reservation.count -
                self.specific_reservation.in_use_count)

    def is_consumable(
        self,
        specific_reservations: Set[str],
    ) -> bool:
        """Check if the reservation is consumable.

        Check if the reservation is consumable with the provided specific
        reservation names. This is defined by the Consumption type.
        For more details:
        https://cloud.google.com/compute/docs/instances/reservations-overview#how-reservations-work
        """
        return (not self.specific_reservation_required or
                self.name in specific_reservations)

    @property
    def name(self) -> str:
        """Name derived from reservation self link.

        The naming convention can be found here:
        https://cloud.google.com/compute/docs/instances/reservations-consume#consuming_a_specific_shared_reservation
        """
        parts = self.self_link.split('/')
        return '/'.join(parts[-6:-4] + parts[-2:])


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

    _INDENT_PREFIX = '    '
    _DEPENDENCY_HINT = (
        'GCP tools are not installed. Run the following commands:\n'
        # Install the Google Cloud SDK:
        f'{_INDENT_PREFIX}  $ pip install google-api-python-client\n'
        f'{_INDENT_PREFIX}  $ conda install -c conda-forge '
        'google-cloud-sdk -y')

    _CREDENTIAL_HINT = (
        'Run the following commands:\n'
        # This authenticates the CLI to make `gsutil` work:
        f'{_INDENT_PREFIX}  $ gcloud init\n'
        # This will generate
        # ~/.config/gcloud/application_default_credentials.json.
        f'{_INDENT_PREFIX}  $ gcloud auth application-default login\n'
        f'{_INDENT_PREFIX}For more info: '
        'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#google-cloud-platform-gcp'  # pylint: disable=line-too-long
    )
    _APPLICATION_CREDENTIAL_HINT = (
        'Run the following commands:\n'
        f'{_INDENT_PREFIX}  $ gcloud auth application-default login\n'
        f'{_INDENT_PREFIX}Or set the environment variable GOOGLE_APPLICATION_CREDENTIALS '
        'to the path of your service account key file.\n'
        f'{_INDENT_PREFIX}For more info: '
        'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#google-cloud-platform-gcp'  # pylint: disable=line-too-long
    )

    def __init__(self):
        super().__init__()

        self._list_reservations_cache = None

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return {}

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    #### Regions/Zones ####
    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        if accelerators is None:
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
                        assert r1.zones is not None, r1
                        assert r2.zones is not None, r2
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
        del num_nodes  # Unused.
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        # GCP provisioner currently takes 1 zone per request.
        for r in regions:
            assert r.zones is not None, r
            for zone in r.zones:
                yield [zone]

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

    def get_egress_cost(self, num_gigabytes: float):
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

    @classmethod
    def _is_machine_image(cls, image_id: str) -> bool:
        find_machine = re.match(r'projects/.*/.*/machineImages/.*', image_id)
        return find_machine is not None

    @classmethod
    @functools.lru_cache(maxsize=1)
    def _get_image_size(cls, image_id: str) -> float:
        if image_id.startswith('skypilot:'):
            return DEFAULT_GCP_IMAGE_GB
        try:
            compute = gcp.build('compute',
                                'v1',
                                credentials=None,
                                cache_discovery=False)
        except gcp.credential_error_exception():
            return DEFAULT_GCP_IMAGE_GB
        try:
            image_attrs = image_id.split('/')
            if len(image_attrs) == 1:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        _IMAGE_NOT_FOUND_UX_MESSAGE.format(image_id=image_id))
            project = image_attrs[1]
            image_name = image_attrs[-1]
            # We support both GCP's Machine Images and Custom Images, both
            # of which are specified with the image_id field. We will
            # distinguish them by checking if the image_id contains
            # 'machineImages'.
            if cls._is_machine_image(image_id):
                image_infos = compute.machineImages().get(
                    project=project, machineImage=image_name).execute()
                # The VM launching in a different region than the machine
                # image is supported by GCP, so we do not need to check the
                # storageLocations.
                return float(
                    image_infos['instanceProperties']['disks'][0]['diskSizeGb'])
            else:
                start = time.time()
                image_infos = compute.images().get(project=project,
                                                   image=image_name).execute()
                logger.debug(f'GCP image get took {time.time() - start:.2f}s')
                return float(image_infos['diskSizeGb'])
        except gcp.http_error_exception() as e:
            if e.resp.status == 403:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Not able to access the image '
                                     f'{image_id!r}') from None
            if e.resp.status == 404:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        _IMAGE_NOT_FOUND_UX_MESSAGE.format(
                            image_id=image_id)) from None
            raise

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        del region  # Unused.
        return cls._get_image_size(image_id)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='gcp')

    def make_deploy_resources_variables(
            self, resources: 'resources.Resources', cluster_name_on_cloud: str,
            region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        assert zones is not None, (region, zones)

        region_name = region.name
        zone_name = zones[0].name

        # gcloud compute images list \
        # --project deeplearning-platform-release \
        # --no-standard-images
        # We use the debian image, as the ubuntu image has some connectivity
        # issue when first booted.
        image_id = 'skypilot:cpu-debian-11'

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
                if acc in ('A100-80GB', 'L4'):
                    # A100-80GB and L4 have a different name pattern.
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
                    # CUDA driver version 535.86.10, CUDA Library 12.2
                    image_id = 'skypilot:gpu-debian-11'

        if resources.image_id is not None and resources.extract_docker_image(
        ) is None:
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
        resources_vars['machine_image'] = None

        if self._is_machine_image(image_id):
            resources_vars['machine_image'] = image_id
            resources_vars['image_id'] = None

        resources_vars['disk_tier'] = GCP._get_disk_type(r.disk_tier)

        firewall_rule = None
        if resources.ports is not None:
            firewall_rule = (
                USER_PORTS_FIREWALL_RULE_NAME.format(cluster_name_on_cloud))
        resources_vars['firewall_rule'] = firewall_rule

        return resources_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources.Resources'
    ) -> Tuple[List['resources.Resources'], List[str]]:
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return ([resources], [])

        if resources.accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            host_vm_type = GCP.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
            if host_vm_type is None:
                return ([], [])
            else:
                r = resources.copy(
                    cloud=GCP(),
                    instance_type=host_vm_type,
                    accelerators=None,
                    cpus=None,
                    memory=None,
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
            memory=resources.memory if not use_tpu_vm else None,
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
            # FIXME(woosuk, wei-lin): This leverages the fact that TPU VMs
            # have 96 vCPUs, and 240 vCPUs for tpu-v4. We need to move
            # this to service catalog, instead.
            num_cpus_in_tpu_vm = 240 if 'v4' in acc else 96
            if resources.cpus is not None:
                if resources.cpus.endswith('+'):
                    cpus = float(resources.cpus[:-1])
                    if cpus > num_cpus_in_tpu_vm:
                        return ([], fuzzy_candidate_list)
                else:
                    cpus = float(resources.cpus)
                    if cpus != num_cpus_in_tpu_vm:
                        return ([], fuzzy_candidate_list)
            # FIXME(woosuk, wei-lin): This leverages the fact that TPU VMs
            # have 334 GB RAM, and 400 GB RAM for tpu-v4. We need to move
            # this to service catalog, instead.
            memory_in_tpu_vm = 400 if 'v4' in acc else 334
            if resources.memory is not None:
                if resources.memory.endswith('+'):
                    memory = float(resources.memory[:-1])
                    if memory > memory_in_tpu_vm:
                        return ([], fuzzy_candidate_list)
                else:
                    memory = float(resources.memory)
                    if memory != memory_in_tpu_vm:
                        return ([], fuzzy_candidate_list)
        else:
            host_vm_type = instance_list[0]

        acc_dict = {acc: acc_count}
        r = resources.copy(
            cloud=GCP(),
            instance_type=host_vm_type,
            accelerators=acc_dict,
            cpus=None,
            memory=None,
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
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='gcp')

    def get_reservations_available_resources(
        self,
        instance_type: str,
        region: str,
        zone: Optional[str],
        specific_reservations: Set[str],
    ) -> Dict[str, int]:
        del region  # Unused
        if zone is None:
            # For backward compatibility, the cluster in INIT state launched
            # before #2352 may not have zone information. In this case, we
            # return 0 for all reservations.
            return {reservation: 0 for reservation in specific_reservations}
        reservations = self._list_reservations_for_instance_type_in_zone(
            instance_type, zone)

        return {
            r.name: r.available_resources
            for r in reservations
            if r.is_consumable(specific_reservations)
        }

    def _list_reservations_for_instance_type_in_zone(
        self,
        instance_type: str,
        zone: str,
    ) -> List[GCPReservation]:
        reservations = self._list_reservations_for_instance_type(instance_type)
        return [r for r in reservations if r.zone.endswith(f'/{zone}')]

    def _get_or_create_ttl_cache(self):
        if getattr(self, '_list_reservations_cache', None) is None:
            # Backward compatibility: Clusters created before #2352 has GCP
            # objects serialized without the attribute. So we access it this
            # way.
            self._list_reservations_cache = cachetools.TTLCache(
                maxsize=1,
                ttl=datetime.timedelta(300),
                timer=datetime.datetime.now)
        return self._list_reservations_cache

    @cachetools.cachedmethod(cache=lambda self: self._get_or_create_ttl_cache())
    def _list_reservations_for_instance_type(
        self,
        instance_type: str,
    ) -> List[GCPReservation]:
        """List all reservations for the given instance type.

        TODO: We need to incorporate accelerators because the reserved instance
        can be consumed only when the instance_type + GPU type matches, and in
        GCP GPUs except for A100 and L4 do not have their own instance type.
        For example, if we have a specific reservation with n1-highmem-8
        in us-central1-c. `sky launch --gpus V100` will fail.
        """
        list_reservations_cmd = (
            'gcloud compute reservations list '
            f'--filter="specificReservation.instanceProperties.machineType={instance_type} AND status=READY" '
            '--format="json(specificReservation.count, specificReservation.inUseCount, specificReservationRequired, selfLink, zone)"'
        )
        returncode, stdout, stderr = subprocess_utils.run_with_retries(
            list_reservations_cmd,
            # 1: means connection aborted (although it shows 22 in the error,
            # but the actual error code is 1)
            # Example: ERROR: gcloud crashed (ConnectionError): ('Connection aborted.', OSError(22, 'Invalid argument')) # pylint: disable=line-too-long
            retry_returncode=[255, 1],
        )
        subprocess_utils.handle_returncode(
            returncode,
            list_reservations_cmd,
            error_msg=
            f'Failed to get list reservations for {instance_type!r}:\n{stderr}',
            stderr=stderr,
            stream_logs=True,
        )
        return [GCPReservation.from_dict(r) for r in json.loads(stdout)]

    @classmethod
    def _find_application_key_path(cls) -> str:
        # Check the application default credentials in the environment variable.
        # If the file does not exist, fallback to the default path.
        application_key_path = os.environ.get(_GCP_APPLICATION_CREDENTIAL_ENV,
                                              None)
        if application_key_path is not None:
            if not os.path.isfile(os.path.expanduser(application_key_path)):
                raise FileNotFoundError(
                    f'{_GCP_APPLICATION_CREDENTIAL_ENV}={application_key_path},'
                    ' but the file does not exist.')
            return application_key_path
        if (not os.path.isfile(
                os.path.expanduser(DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH))):
            # Fallback to the default application credential path.
            raise FileNotFoundError(DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH)
        return DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        try:
            # pylint: disable=import-outside-toplevel,unused-import
            # Check google-api-python-client installation.
            from google import auth  # type: ignore
            import googleapiclient

            # Check the installation of google-cloud-sdk.
            _run_output('gcloud --version')
        except (ImportError, subprocess.CalledProcessError) as e:
            return False, (
                f'{cls._DEPENDENCY_HINT}\n'
                f'{cls._INDENT_PREFIX}Credentials may also need to be set. '
                f'{cls._CREDENTIAL_HINT}\n'
                f'{cls._INDENT_PREFIX}Details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

        try:
            # These files are required because they will be synced to remote
            # VMs for `gsutil` to access private storage buckets.
            # `auth.default()` does not guarantee these files exist.
            for file in [
                    '~/.config/gcloud/access_tokens.db',
                    '~/.config/gcloud/credentials.db',
            ]:
                if not os.path.isfile(os.path.expanduser(file)):
                    raise FileNotFoundError(file)
        except FileNotFoundError as e:
            return False, (
                f'Credentails are not set. '
                f'{cls._CREDENTIAL_HINT}\n'
                f'{cls._INDENT_PREFIX}Details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

        try:
            cls._find_application_key_path()
        except FileNotFoundError as e:
            return False, (
                f'Application credentials are not set. '
                f'{cls._APPLICATION_CREDENTIAL_HINT}\n'
                f'{cls._INDENT_PREFIX}Details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

        try:
            # Check if application default credentials are set.
            project_id = cls.get_project_id()

            # Check if the user is activated.
            identity = cls.get_current_user_identity()
        except (auth.exceptions.DefaultCredentialsError,
                exceptions.CloudUserIdentityError) as e:
            # See also: https://stackoverflow.com/a/53307505/1165051
            return False, (
                'Getting project ID or user identity failed. You can debug '
                'with `gcloud auth list`. To fix this, '
                f'{cls._CREDENTIAL_HINT[0].lower()}'
                f'{cls._CREDENTIAL_HINT[1:]}\n'
                f'{cls._INDENT_PREFIX}Details: '
                f'{common_utils.format_exception(e, use_bracket=True)}')

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

        # pylint: disable=import-outside-toplevel,unused-import
        import google.auth
        import googleapiclient.discovery

        from sky.skylet.providers.gcp import constants

        # This takes user's credential info from "~/.config/gcloud/application_default_credentials.json".  # pylint: disable=line-too-long
        credentials, project = google.auth.default()
        service = googleapiclient.discovery.build('cloudresourcemanager',
                                                  'v1',
                                                  credentials=credentials)
        permissions = {'permissions': constants.VM_MINIMAL_PERMISSIONS}
        request = service.projects().testIamPermissions(resource=project,
                                                        body=permissions)
        ret_permissions = request.execute().get('permissions', [])

        diffs = set(constants.VM_MINIMAL_PERMISSIONS).difference(
            set(ret_permissions))
        if len(diffs) > 0:
            identity_str = identity[0] if identity else None
            return False, (
                'The following permissions are not enabled for the current '
                f'GCP identity ({identity_str}):\n    '
                f'{diffs}\n    '
                'For more details, visit: https://skypilot.readthedocs.io/en/latest/cloud-setup/cloud-permissions/gcp.html')  # pylint: disable=line-too-long
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # Create a backup of the config_default file, as the original file can
        # be modified on the remote cluster by ray causing authentication
        # problems. The backup file will be updated to the remote cluster
        # whenever the original file is not empty and will be applied
        # appropriately on the remote cluster when necessary.
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
        # Upload the application key path to the default path, so that
        # autostop and GCS can be accessed on the remote cluster.
        credentials[DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH] = (
            self._find_application_key_path())
        credentials[GCP_CONFIG_SKY_BACKUP_PATH] = GCP_CONFIG_SKY_BACKUP_PATH
        return credentials

    @classmethod
    @functools.lru_cache(maxsize=1)  # Cache since getting identity is slow.
    def get_current_user_identity(cls) -> Optional[List[str]]:
        """Returns the email address + project id of the active user."""
        try:
            account = _run_output('gcloud auth list --filter=status:ACTIVE '
                                  '--format="value(account)"')
            account = account.strip()
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
            project_id = cls.get_project_id()
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.CloudUserIdentityError(
                    f'Failed to get GCP user identity with unknown '
                    f'exception.\n'
                    '  Reason: '
                    f'{common_utils.format_exception(e, use_bracket=True)}'
                ) from e
        return [f'{account} [project_id={project_id}]']

    @classmethod
    def get_current_user_identity_str(cls) -> Optional[str]:
        user_identity = cls.get_current_user_identity()
        if user_identity is None:
            return None
        return user_identity[0].replace('\n', '')

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
                'run the following: gcloud init; '
                'gcloud auth application-default login')
        return project_id

    @staticmethod
    def _check_instance_type_accelerators_combination(
            resources: 'resources.Resources') -> None:
        assert resources.is_launchable(), resources
        service_catalog.check_accelerator_attachable_to_host(
            resources.instance_type, resources.accelerators, resources.zone,
            'gcp')

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: str,
                                disk_tier: str) -> None:
        del instance_type, disk_tier  # unused

    @classmethod
    def _get_disk_type(cls, disk_tier: Optional[str]) -> str:
        tier = disk_tier or cls._DEFAULT_DISK_TIER
        tier2name = {
            'high': 'pd-ssd',
            'medium': 'pd-balanced',
            'low': 'pd-standard',
        }
        return tier2name[tier]

    @classmethod
    def _label_filter_str(cls, tag_filters: Dict[str, str]) -> str:
        return ' '.join(f'labels.{k}={v}' for k, v in tag_filters.items())

    @classmethod
    def check_quota_available(cls, resources: 'resources.Resources') -> bool:
        """Check if GCP quota is available based on `resources`.

        GCP-specific implementation of check_quota_available. The function works by
        matching the `accelerator` to the a corresponding GCP keyword, and then using
        the GCP CLI commands to query for the specific quota (the `accelerator` as
        defined by `resources`).

        Returns:
            False if the quota is found to be zero, and True otherwise.
        Raises:
            CalledProcessError: error with the GCP CLI command.
        """

        if not resources.accelerators:
            # TODO(hriday): We currently only support checking quotas for GPUs.
            # For CPU-only instances, we need to try provisioning to check quotas.
            return True

        accelerator = list(resources.accelerators.keys())[0]
        use_spot = resources.use_spot
        region = resources.region

        # pylint: disable=import-outside-toplevel
        from sky.clouds.service_catalog import gcp_catalog

        quota_code = gcp_catalog.get_quota_code(accelerator, use_spot)

        if quota_code is None:
            # Quota code not found in the catalog for the chosen instance_type, try provisioning anyway
            return True

        command = f'gcloud compute regions describe {region} |grep -B 1 "{quota_code}" | awk \'/limit/ {{print; exit}}\''
        try:
            proc = subprocess_utils.run(cmd=command,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)

        except subprocess.CalledProcessError as e:
            logger.warning(f'Quota check command failed with error: '
                           f'{e.stderr.decode()}')
            return True

        # Extract quota from output
        # Example output:  "- limit: 16.0"
        out = proc.stdout.decode()
        try:
            quota = int(float(out.split('limit:')[-1].strip()))
        except (ValueError, IndexError, AttributeError) as e:
            logger.warning('Parsing the subprocess output failed '
                           f'with error: {e}')
            return True

        if quota == 0:
            return False
        # Quota found to be greater than zero, try provisioning
        return True

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List['status_lib.ClusterStatus']:
        """Query the status of a cluster."""
        del region  # unused

        # pylint: disable=import-outside-toplevel
        from sky.utils import tpu_utils
        use_tpu_vm = kwargs.pop('use_tpu_vm', False)

        label_filter_str = cls._label_filter_str(tag_filters)
        if use_tpu_vm:
            # TPU VM's state definition is different from compute VM
            # https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes#State # pylint: disable=line-too-long
            status_map = {
                'CREATING': status_lib.ClusterStatus.INIT,
                'STARTING': status_lib.ClusterStatus.INIT,
                'RESTARTING': status_lib.ClusterStatus.INIT,
                'READY': status_lib.ClusterStatus.UP,
                'REPAIRING': status_lib.ClusterStatus.INIT,
                # 'STOPPED' in GCP TPU VM means stopped, with disk preserved.
                'STOPPING': status_lib.ClusterStatus.STOPPED,
                'STOPPED': status_lib.ClusterStatus.STOPPED,
                'DELETING': None,
                'PREEMPTED': None,
            }
            tpu_utils.check_gcp_cli_include_tpu_vm()
            query_cmd = ('gcloud compute tpus tpu-vm list '
                         f'--zone {zone} '
                         f'--filter="({label_filter_str})" '
                         '--format="value(state)"')
        else:
            # Ref: https://cloud.google.com/compute/docs/instances/instance-life-cycle
            status_map = {
                'PROVISIONING': status_lib.ClusterStatus.INIT,
                'STAGING': status_lib.ClusterStatus.INIT,
                'RUNNING': status_lib.ClusterStatus.UP,
                'REPAIRING': status_lib.ClusterStatus.INIT,
                # 'TERMINATED' in GCP means stopped, with disk preserved.
                'STOPPING': status_lib.ClusterStatus.STOPPED,
                'TERMINATED': status_lib.ClusterStatus.STOPPED,
                # 'SUSPENDED' in GCP means stopped, with disk and OS memory
                # preserved.
                'SUSPENDING': status_lib.ClusterStatus.STOPPED,
                'SUSPENDED': status_lib.ClusterStatus.STOPPED,
            }
            # TODO(zhwu): The status of the TPU attached to the cluster should
            # also be checked, since TPUs are not part of the VMs.
            query_cmd = ('gcloud compute instances list '
                         f'--filter="({label_filter_str})" '
                         '--format="value(status)"')
        returncode, stdout, stderr = log_lib.run_with_log(query_cmd,
                                                          '/dev/null',
                                                          require_outputs=True,
                                                          shell=True)
        logger.debug(f'{query_cmd} returned {returncode}.\n'
                     '**** STDOUT ****\n'
                     f'{stdout}\n'
                     '**** STDERR ****\n'
                     f'{stderr}')

        if returncode != 0:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query GCP cluster {name!r} status: '
                    f'{stdout + stderr}')

        status_list = []
        for line in stdout.splitlines():
            status = status_map.get(line.strip())
            if status is None:
                continue
            status_list.append(status)

        return status_list

    @classmethod
    def create_image_from_cluster(cls, cluster_name: str,
                                  cluster_name_on_cloud: str,
                                  region: Optional[str],
                                  zone: Optional[str]) -> str:
        del region  # unused
        assert zone is not None
        # TODO(zhwu): This assumes the cluster is created with the
        # `ray-cluster-name` tag, which is guaranteed by the current `ray`
        # backend. Once the `provision.query_instances` is implemented for GCP,
        # we should be able to get rid of this assumption.
        tag_filters = {'ray-cluster-name': cluster_name_on_cloud}
        label_filter_str = cls._label_filter_str(tag_filters)
        instance_name_cmd = ('gcloud compute instances list '
                             f'--filter="({label_filter_str})" '
                             '--format="json(name)"')
        returncode, stdout, stderr = subprocess_utils.run_with_retries(
            instance_name_cmd,
            retry_returncode=[255],
        )
        subprocess_utils.handle_returncode(
            returncode,
            instance_name_cmd,
            error_msg=f'Failed to get instance name for {cluster_name!r}',
            stderr=stderr,
            stream_logs=True)
        instance_names = json.loads(stdout)
        if len(instance_names) != 1:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    'Only support creating image from single '
                    f'instance, but got: {instance_names}')
        instance_name = instance_names[0]['name']

        image_name = f'skypilot-{cluster_name}-{int(time.time())}'
        create_image_cmd = (f'gcloud compute images create {image_name} '
                            f'--source-disk  {instance_name} '
                            f'--source-disk-zone {zone}')
        logger.debug(create_image_cmd)
        subprocess_utils.run_with_retries(
            create_image_cmd,
            retry_returncode=[255],
        )
        subprocess_utils.handle_returncode(
            returncode,
            create_image_cmd,
            error_msg=f'Failed to create image for {cluster_name!r}',
            stderr=stderr,
            stream_logs=True)

        image_uri_cmd = (f'gcloud compute images describe {image_name} '
                         '--format="get(selfLink)"')
        returncode, stdout, stderr = subprocess_utils.run_with_retries(
            image_uri_cmd,
            retry_returncode=[255],
        )

        subprocess_utils.handle_returncode(
            returncode,
            image_uri_cmd,
            error_msg=f'Failed to get image uri for {cluster_name!r}',
            stderr=stderr,
            stream_logs=True)

        image_uri = stdout.strip()
        image_id = image_uri.partition('projects/')[2]
        image_id = 'projects/' + image_id
        return image_id

    @classmethod
    def maybe_move_image(cls, image_id: str, source_region: str,
                         target_region: str, source_zone: Optional[str],
                         target_zone: Optional[str]) -> str:
        del source_region, target_region, source_zone, target_zone  # Unused.
        # GCP images are global, so no need to move.
        return image_id

    @classmethod
    def delete_image(cls, image_id: str, region: Optional[str]) -> None:
        del region  # Unused.
        image_name = image_id.rpartition('/')[2]
        delete_image_cmd = f'gcloud compute images delete {image_name} --quiet'
        returncode, _, stderr = subprocess_utils.run_with_retries(
            delete_image_cmd,
            retry_returncode=[255],
        )
        subprocess_utils.handle_returncode(
            returncode,
            delete_image_cmd,
            error_msg=f'Failed to delete image {image_name!r}',
            stderr=stderr,
            stream_logs=True)

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()
        # We should avoid saving third-party object to the state, as it may
        # cause unpickling error when the third-party API is updated.
        state.pop('_list_reservations_cache', None)
        return state

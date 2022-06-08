"""Google Cloud Platform."""
import json
import os
import subprocess
from typing import Dict, Iterator, List, Optional, Tuple

from google import auth

from sky import clouds
from sky.clouds import service_catalog

DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH = os.path.expanduser(
    '~/.config/gcloud/'
    'application_default_credentials.json')

# Minimum set of files under ~/.config/gcloud that grant GCP access.
_CREDENTIAL_FILES = [
    'credentials.db',
    'application_default_credentials.json',
    'access_tokens.db',
    'configurations',
    'legacy_credentials',
]


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


@clouds.CLOUD_REGISTRY.register
class GCP(clouds.Cloud):
    """Google Cloud Platform."""

    _REPR = 'GCP'
    _regions: List[clouds.Region] = []

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
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: Optional[bool] = False,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        # GCP provisioner currently takes 1 zone per request.
        del instance_type  # unused
        if accelerators is None:
            # fallback to manually specified region/zones
            regions = cls.regions()
        else:
            assert len(accelerators) == 1, accelerators
            acc = list(accelerators.keys())[0]
            acc_count = list(accelerators.values())[0]
            regions = service_catalog.get_region_zones_for_accelerators(
                acc, acc_count, use_spot, clouds='gcp')

        for region in regions:
            for zone in region.zones:
                yield (region, [zone])

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        return service_catalog.get_hourly_cost(instance_type,
                                               region=None,
                                               use_spot=use_spot,
                                               clouds='gcp')

    def accelerators_to_hourly_cost(self, accelerators, use_spot: bool):
        assert len(accelerators) == 1, accelerators
        acc, acc_count = list(accelerators.items())[0]
        return service_catalog.get_accelerator_hourly_cost(acc,
                                                           acc_count,
                                                           use_spot,
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

    def __repr__(self):
        return GCP._REPR

    def is_same_cloud(self, other):
        return isinstance(other, GCP)

    @classmethod
    def get_default_instance_type(cls) -> str:
        # 8 vCpus, 52 GB RAM.  First-gen general purpose.
        return 'n1-highmem-8'

    @classmethod
    def get_default_region(cls) -> clouds.Region:
        return cls.regions()[-1]

    def make_deploy_resources_variables(self, resources):
        r = resources
        # Find GPU spec, if any.
        resources_vars = {
            'instance_type': r.instance_type,
            'gpu': None,
            'gpu_count': None,
            'tpu': None,
            'custom_resources': None,
            'use_spot': r.use_spot,
            'image_name': 'common-cpu',
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
                resources_vars['tf_version'] = r.accelerator_args['tf_version']
                resources_vars['tpu_name'] = r.accelerator_args.get('tpu_name')
            else:
                # Convert to GCP names:
                # https://cloud.google.com/compute/docs/gpus
                resources_vars['gpu'] = 'nvidia-tesla-{}'.format(acc.lower())
                resources_vars['gpu_count'] = acc_count
                # CUDA driver version 470.103.01, CUDA Library 11.3
                resources_vars['image_name'] = 'common-cu113'

        return resources_vars

    def get_feasible_launchable_resources(self, resources):
        fuzzy_candidate_list = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return ([resources], fuzzy_candidate_list)

        # No other resources (cpu/mem) to filter for now, so just return a
        # default VM type.
        host_vm_type = GCP.get_default_instance_type()
        acc_dict = None
        # Find instance candidates to meet user's requirements
        if resources.accelerators is not None:
            assert len(resources.accelerators.items(
            )) == 1, 'cannot handle more than one accelerator candidates.'
            acc, acc_count = list(resources.accelerators.items())[0]
            (instance_list, fuzzy_candidate_list
            ) = service_catalog.get_instance_type_for_accelerator(acc,
                                                                  acc_count,
                                                                  clouds='gcp')

            if instance_list is None:
                return ([], fuzzy_candidate_list)
            assert len(
                instance_list
            ) == 1, f'More than one instance type matched, {instance_list}'
            host_vm_type = instance_list[0]
            acc_dict = {acc: acc_count}

        r = resources.copy(
            cloud=GCP(),
            instance_type=host_vm_type,
            accelerators=acc_dict,
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

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        try:
            # These files are required because they will be synced to remote
            # VMs for `gsutil` to access private storage buckets.
            # `auth.default()` does not guarantee these files exist.
            for file in [
                    '~/.config/gcloud/access_tokens.db',
                    '~/.config/gcloud/credentials.db'
            ]:
                assert os.path.isfile(os.path.expanduser(file))
            # Check if application default credentials are set.
            self.get_project_id()
            # Calling `auth.default()` ensures the GCP client library works,
            # which is used by Ray Autoscaler to launch VMs.
            auth.default()
            # Check google-api-python-client installation.
            # pylint: disable=import-outside-toplevel,unused-import
            import googleapiclient

            # Check the installation of google-cloud-sdk.
            _run_output('gcloud --version')
        except (AssertionError, auth.exceptions.DefaultCredentialsError,
                subprocess.CalledProcessError, FileNotFoundError, KeyError,
                ImportError):
            # See also: https://stackoverflow.com/a/53307505/1165051
            return False, (
                'GCP tools are not installed or credentials are not set. '
                'Run the following commands:\n    '
                # Install the Google Cloud SDK:
                '  $ pip install google-api-python-client\n    '
                '  $ conda install -c conda-forge google-cloud-sdk\n    '
                # This authenticates the CLI to make `gsutil` work:
                '  $ gcloud init\n    '
                # This will generate
                # ~/.config/gcloud/application_default_credentials.json.
                '  $ gcloud auth application-default login\n    '
                'For more info: '
                'https://sky-proj-sky.readthedocs-hosted.com/en/latest/getting-started/installation.html'  # pylint: disable=line-too-long
            )
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        # Excluding the symlink to the python executable created by the gcp
        # credential, which causes problem for ray up multiple nodes, tracked
        # in #494, #496, #483.
        return {
            f'~/.config/gcloud/{filename}': f'~/.config/gcloud/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def instance_type_exists(self, instance_type):
        return service_catalog.instance_type_exists(instance_type, 'gcp')

    def region_exists(self, region: str) -> bool:
        return service_catalog.region_exists(region, 'gcp')

    @classmethod
    def get_project_id(cls, dryrun: bool = False) -> str:
        # TODO(zhwu): change the project id fetching with the following command
        # `gcloud info --format='value(config.project)'`
        if dryrun:
            return 'dryrun-project-id'
        if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
            gcp_credential_path = os.environ['GOOGLE_APPLICATION_CREDENTIALS']
        else:
            gcp_credential_path = DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH
        if not os.path.exists(gcp_credential_path):
            raise FileNotFoundError(f'No GCP credentials found at '
                                    f'{gcp_credential_path}. Please set the '
                                    f'GOOGLE_APPLICATION_CREDENTIALS '
                                    f'environment variable to point to '
                                    f'the path of your credentials file.')

        with open(gcp_credential_path, 'r') as fp:
            gcp_credentials = json.load(fp)
        project_id = gcp_credentials.get('quota_project_id',
                                         None) or gcp_credentials['project_id']
        return project_id

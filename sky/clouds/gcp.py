"""Google Cloud Platform."""
import copy
import json
import os
from typing import Dict, Iterator, List, Optional, Tuple

from google import auth

from sky import clouds
from sky.clouds import service_catalog


class GCP(clouds.Cloud):
    """Google Cloud Platform."""

    _REPR = 'GCP'
    _regions: List[clouds.Region] = []

    # Pricing.  All info assumes us-central1.
    # In general, query pricing from the cloud.
    _ON_DEMAND_PRICES = {
        # VMs: https://cloud.google.com/compute/all-pricing.
        # N1 standard
        'n1-standard-1': 0.04749975,
        'n1-standard-2': 0.0949995,
        'n1-standard-4': 0.189999,
        'n1-standard-8': 0.379998,
        'n1-standard-16': 0.759996,
        'n1-standard-32': 1.519992,
        'n1-standard-64': 3.039984,
        'n1-standard-96': 4.559976,
        # N1 highmem
        'n1-highmem-2': 0.118303,
        'n1-highmem-4': 0.236606,
        'n1-highmem-8': 0.473212,
        'n1-highmem-16': 0.946424,
        'n1-highmem-32': 1.892848,
        'n1-highmem-64': 3.785696,
        'n1-highmem-96': 5.678544,
    }

    _SPOT_PRICES = {
        # VMs: https://cloud.google.com/compute/all-pricing.
        # N1 standard
        'n1-standard-1': 0.01,
        'n1-standard-2': 0.02,
        'n1-standard-4': 0.04,
        'n1-standard-8': 0.08,
        'n1-standard-16': 0.16,
        'n1-standard-32': 0.32,
        'n1-standard-64': 0.64,
        'n1-standard-96': 0.96,
        # N1 highmem
        'n1-highmem-2': 0.024906,
        'n1-highmem-4': 0.049812,
        'n1-highmem-8': 0.099624,
        'n1-highmem-16': 0.199248,
        'n1-highmem-32': 0.398496,
        'n1-highmem-64': 0.796992,
        'n1-highmem-96': 1.195488,
    }

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
        if use_spot:
            return GCP._SPOT_PRICES[instance_type]
        return GCP._ON_DEMAND_PRICES[instance_type]

    def accelerators_to_hourly_cost(self, accelerators):
        assert len(accelerators) == 1, accelerators
        acc, acc_count = list(accelerators.items())[0]
        return service_catalog.get_accelerator_hourly_cost(acc,
                                                           acc_count,
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
    def get_default_instance_type(cls):
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

        return resources_vars

    def get_feasible_launchable_resources(self, resources):
        fuzzy_candidate_list = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return ([resources], fuzzy_candidate_list)
        accelerator_match = None
        if resources.accelerators is not None:
            available_accelerators = service_catalog.list_accelerators(
                gpus_only=False, clouds='gcp')
            for acc, acc_count in resources.accelerators.items():
                for acc_avail, infos in available_accelerators.items():
                    # case-insenstive matching
                    if acc.upper() == acc_avail.upper() and any(
                            acc_count == info.accelerator_count
                            for info in infos):
                        accelerator_match = {acc_avail: acc_count}
                        break
                if accelerator_match is None:
                    return (None, fuzzy_candidate_list)
        # No other resources (cpu/mem) to filter for now, so just return a
        # default VM type.
        r = copy.deepcopy(resources)
        r.cloud = GCP()
        r.instance_type = GCP.get_default_instance_type()
        r.accelerators = accelerator_match
        return ([r], fuzzy_candidate_list)

    def get_accelerators_from_instance_type(
        self,
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
            # Calling `auth.default()` ensures the GCP client library works,
            # which is used by Ray Autoscaler to launch VMs.
            auth.default()
        except (AssertionError, auth.exceptions.DefaultCredentialsError):
            # See also: https://stackoverflow.com/a/53307505/1165051
            return False, (
                'GCP credentials not set. Run the following commands:\n    '
                # Install the Google Cloud SDK:
                '$ pip install google-api-python-client\n    '
                '$ conda install -c conda-forge google-cloud-sdk\n    '
                # This authenticates the CLI to make `gsutil` work:
                '$ gcloud init\n    '
                # This will generate
                # ~/.config/gcloud/application_default_credentials.json.
                '$ gcloud auth application-default login\n    '
                'For more info: '
                'https://sky-proj-sky.readthedocs-hosted.com/en/latest/getting-started/installation.html'  # pylint: disable=line-too-long
            )
        return True, None

    def get_credential_file_mounts(self) -> Tuple[Dict[str, str], List[str]]:
        # Excluding the symlink to the python executable created by the gcp
        # credential, which causes problem for ray up multiple nodes, tracked
        # in #494, #496, #483.
        # rsync_exclude only supports relative paths.
        # TODO(zhwu): rsync_exclude here is unsafe as it may exclude the folder
        # from other file_mounts as well in ray yaml.
        return {'~/.config/gcloud': '~/.config/gcloud'}, ['virtenv']

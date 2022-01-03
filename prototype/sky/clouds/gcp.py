"""Google Cloud Platform."""
import copy
import json
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds


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
    # GPUs: https://cloud.google.com/compute/gpus-pricing.
    _ON_DEMAND_PRICES_GPUS = {
        # T4
        'T4': 0.35,
        '1x T4': 0.35,
        '2x T4': 0.35 * 2,
        '4x T4': 0.35 * 4,
        # P4
        'P4': 0.60,
        '1x P4': 0.60,
        '2x P4': 0.60 * 2,
        '4x P4': 0.60 * 4,
        # V100
        'V100': 2.48,
        '1x V100': 2.48,
        '2x V100': 2.48 * 2,
        '4x V100': 2.48 * 4,
        '8x V100': 2.48 * 8,
        # P100
        'P100': 1.46,
        '1x P100': 1.46,
        '2x P100': 1.46 * 2,
        '4x P100': 1.46 * 4,
        # K80
        'K80': 0.45,
        '1x K80': 0.45,
        '2x K80': 0.45 * 2,
        '4x K80': 0.45 * 4,
        '8x K80': 0.45 * 8,
    }
    # TPUs: https://cloud.google.com/tpu/pricing.
    _ON_DEMAND_PRICES_TPUS = {
        'tpu-v2-8': 4.5,
        'tpu-v3-8': 8.0,
    }
    _ON_DEMAND_PRICES.update(_ON_DEMAND_PRICES_GPUS)
    _ON_DEMAND_PRICES.update(_ON_DEMAND_PRICES_TPUS)

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
    # GPUs: https://cloud.google.com/compute/gpus-pricing.
    _SPOT_PRICES_GPUS = {
        # T4
        'T4': 0.11,
        '1x T4': 0.11,
        '2x T4': 0.11 * 2,
        '4x T4': 0.11 * 4,
        # P4
        'P4': 0.216,
        '1x P4': 0.216,
        '2x P4': 0.216 * 2,
        '4x P4': 0.216 * 4,
        # V100
        'V100': 0.74,
        '1x V100': 0.74,
        '2x V100': 0.74 * 2,
        '4x V100': 0.74 * 4,
        '8x V100': 0.74 * 8,
        # P100
        'P100': 0.43,
        '1x P100': 0.43,
        '2x P100': 0.43 * 2,
        '4x P100': 0.43 * 4,
        # K80
        'K80': 0.0375,
        '1x K80': 0.0375,
        '2x K80': 0.0375 * 2,
        '4x K80': 0.0375 * 4,
        '8x K80': 0.0375 * 8,
    }
    # TPUs: https://cloud.google.com/tpu/pricing.
    _SPOT_PRICES_TPUS = {
        'tpu-v2-8': 1.35,
        'tpu-v3-8': 2.40,
    }
    _SPOT_PRICES.update(_SPOT_PRICES_GPUS)
    _SPOT_PRICES.update(_SPOT_PRICES_TPUS)

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
        # TODO: enable this after GCP catalog completes.
        # regions = gcp_catalog.get_region_zones_for_accelerators(accelerators)
        del instance_type, accelerators, use_spot  # unused

        for region in cls.regions():
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
        if acc in GCP._ON_DEMAND_PRICES_GPUS:
            # Assuming linear pricing.
            return GCP._ON_DEMAND_PRICES_GPUS[acc] * acc_count
        if acc in GCP._ON_DEMAND_PRICES_TPUS:
            assert acc_count == 1, accelerators
            return GCP._ON_DEMAND_PRICES_TPUS[acc]
        assert False, accelerators

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

    def make_deploy_resources_variables(self, task):
        r = task.best_resources
        # Find GPU spec, if any.
        resources_vars = {
            'instance_type': r.instance_type,
            'gpu': None,
            'gpu_count': None,
            'tpu': None,
            'custom_resources': None,
            'use_spot': r.use_spot,
        }
        accelerators = r.get_accelerators()
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
                resources_vars['tpu_name'] = r.accelerator_args['tpu_name']
            else:
                # Convert to GCP names:
                # https://cloud.google.com/compute/docs/gpus
                resources_vars['gpu'] = 'nvidia-tesla-{}'.format(acc.lower())
                resources_vars['gpu_count'] = acc_count

        return resources_vars

    def get_feasible_launchable_resources(self, resources):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return [resources]
        if resources.accelerators is not None:
            for acc in resources.accelerators.keys():
                if acc not in self._ON_DEMAND_PRICES_GPUS \
                    and acc not in self._ON_DEMAND_PRICES_TPUS:
                    return []
        # No other resources (cpu/mem) to filter for now, so just return a
        # default VM type.
        r = copy.deepcopy(resources)
        r.cloud = GCP()
        r.instance_type = GCP.get_default_instance_type()
        return [r]

    def get_accelerators_from_instance_type(
            self,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return None

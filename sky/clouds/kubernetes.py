"""Kubernetes."""
import json
import math
import os
import re
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky import exceptions
from sky import status_lib
from sky.adaptors import kubernetes
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import kubernetes_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

_CREDENTIAL_PATH = '~/.kube/config'


class KubernetesInstanceType:
    """
    Class to represent the "Instance Type" in a Kubernetes.

    Since Kubernetes does not have a notion of instances, we generate
    virtual instance types that represent the resources requested by a
    pod ("node").

    This name captures the following resource requests:
        - CPU
        - Memory
        - Accelerators

    The name format is "{n}CPU--{k}GB" where n is the number of vCPUs and
    k is the amount of memory in GB. Accelerators can be specified by
    appending "--{a}{type}" where a is the number of accelerators and
    type is the accelerator type.

    CPU and memory can be specified as floats. Accelerator count must be int.

    Examples:
        - 4CPU--16GB
        - 0.5CPU--1.5GB
        - 4CPU--16GB--1V100
    """

    def __init__(self,
                 cpus: float,
                 memory: float,
                 accelerator_count: Optional[int] = None,
                 accelerator_type: Optional[str] = None):
        self.cpus = cpus
        self.memory = memory
        self.accelerator_count = accelerator_count
        self.accelerator_type = accelerator_type

    @property
    def name(self) -> str:
        """Returns the name of the instance."""
        assert self.cpus is not None
        assert self.memory is not None
        name = (f'{self._format_count(self.cpus)}CPU--'
                f'{self._format_count(self.memory)}GB')
        if self.accelerator_count:
            name += f'--{self.accelerator_count}{self.accelerator_type}'
        return name

    @staticmethod
    def is_valid_instance_type(name: str) -> bool:
        """Returns whether the given name is a valid instance type."""
        pattern = re.compile(r'^(\d+(\.\d+)?CPU--\d+(\.\d+)?GB)(--\d+\S+)?$')
        return bool(pattern.match(name))

    @classmethod
    def _parse_instance_type(
            cls,
            name: str) -> Tuple[float, float, Optional[int], Optional[str]]:
        """Returns the cpus, memory, accelerator_count, and accelerator_type
        from the given name."""
        pattern = re.compile(
            r'^(?P<cpus>\d+(\.\d+)?)CPU--(?P<memory>\d+(\.\d+)?)GB(?:--(?P<accelerator_count>\d+)(?P<accelerator_type>\S+))?$'  # pylint: disable=line-too-long
        )
        match = pattern.match(name)
        if match:
            cpus = float(match.group('cpus'))
            memory = float(match.group('memory'))
            accelerator_count = match.group('accelerator_count')
            accelerator_type = match.group('accelerator_type')
            if accelerator_count:
                accelerator_count = int(accelerator_count)
                accelerator_type = str(accelerator_type)
            else:
                accelerator_count = None
                accelerator_type = None
            return cpus, memory, accelerator_count, accelerator_type
        else:
            raise ValueError(f'Invalid instance name: {name}')

    @classmethod
    def from_instance_type(cls, name: str) -> 'KubernetesInstanceType':
        """Returns an instance name object from the given name."""
        if not cls.is_valid_instance_type(name):
            raise ValueError(f'Invalid instance name: {name}')
        cpus, memory, accelerator_count, accelerator_type = \
            cls._parse_instance_type(name)
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    @classmethod
    def from_resources(cls,
                       cpus: float,
                       memory: float,
                       accelerator_count: int = 0,
                       accelerator_type: str = '') -> 'KubernetesInstanceType':
        """Returns an instance name object from the given resources.

        If accelerator_count is not an int, it will be rounded up since GPU
        requests in Kubernetes must be int.
        """
        name = f'{cpus}CPU--{memory}GB'
        # Round up accelerator_count if it is not an int.
        accelerator_count = math.ceil(accelerator_count)
        if accelerator_count > 0:
            name += f'--{accelerator_count}{accelerator_type}'
        return cls(cpus=cpus,
                   memory=memory,
                   accelerator_count=accelerator_count,
                   accelerator_type=accelerator_type)

    def __str__(self):
        return self.name

    @classmethod
    def _format_count(cls, num: Union[float, int]) -> str:
        """Formats a float to not show decimal point if it is a whole number"""
        if isinstance(num, int):
            return str(num)
        return '{:.0f}'.format(num) if num.is_integer() else '{:.1f}'.format(
            num)


@clouds.CLOUD_REGISTRY.register
class Kubernetes(clouds.Cloud):
    """Kubernetes."""

    SKY_SSH_KEY_SECRET_NAME = f'sky-ssh-{common_utils.get_user_hash()}'

    # Timeout for resource provisioning. This timeout determines how long to
    # wait for pod to be in pending status before giving up.
    # Larger timeout may be required for autoscaling clusters, since autoscaler
    # may take some time to provision new nodes.
    # Note that this timeout includes time taken by the Kubernetes scheduler
    # itself, which can be upto 2-3 seconds.
    # For non-autoscaling clusters, we conservatively set this to 10s.
    # TODO(romilb): Make the timeout configurable.
    TIMEOUT = 10

    _DEFAULT_NUM_VCPUS = 2
    _DEFAULT_MEMORY_CPU_RATIO = 1
    _REPR = 'Kubernetes'
    _regions: List[clouds.Region] = [clouds.Region('kubernetes')]
    _CLOUD_UNSUPPORTED_FEATURES = {
        # TODO(romilb): Stopping might be possible to implement with
        #  container checkpointing introduced in Kubernetes v1.25. See:
        #  https://kubernetes.io/blog/2022/12/05/forensic-container-checkpointing-alpha/ # pylint: disable=line-too-long
        clouds.CloudImplementationFeatures.STOP: 'Kubernetes does not '
                                                 'support stopping VMs.',
        clouds.CloudImplementationFeatures.AUTOSTOP: 'Kubernetes does not '
                                                     'support stopping VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Multi-node is not '
                                                       'supported by the '
                                                       'Kubernetes '
                                                       'implementation yet.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Spot instances are '
                                                          'not supported in '
                                                          'Kubernetes.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'Custom disk '
                                                             'tiers are not '
                                                             'supported in '
                                                             'Kubernetes.',
    }

    IMAGE_CPU = ('us-central1-docker.pkg.dev/'
                 'skypilot-375900/skypilotk8s/skypilot:latest')
    IMAGE_GPU = ('us-central1-docker.pkg.dev/skypilot-375900/'
                 'skypilotk8s/skypilot-gpu:latest')

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        # No notion of regions in Kubernetes - return a single region.
        return cls.regions()

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        # TODO(romilb): Investigate how users can provide their own cost catalog
        #  for Kubernetes clusters.
        # For now, assume zero cost for Kubernetes clusters
        return 0.0

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        return isinstance(other, Kubernetes)

    @classmethod
    def get_port(cls, svc_name) -> int:
        ns = kubernetes_utils.get_current_kube_config_context_namespace()
        return kubernetes_utils.get_port(svc_name, ns)

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[str] = None) -> str:
        del disk_tier  # Unused.
        # TODO(romilb): We should check the maximum number of CPUs and memory
        #  that can be requested, and return None if the requested resources
        #  exceed the maximum. This may require thought about how to handle
        #  autoscaling clusters.
        # We strip '+' from resource requests since Kubernetes can provision
        # exactly the requested resources.
        instance_cpus = float(
            cpus.strip('+')) if cpus is not None else cls._DEFAULT_NUM_VCPUS
        instance_mem = float(memory.strip('+')) if memory is not None else \
            instance_cpus * cls._DEFAULT_MEMORY_CPU_RATIO
        virtual_instance_type = KubernetesInstanceType(instance_cpus,
                                                       instance_mem).name
        return virtual_instance_type

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        inst = KubernetesInstanceType.from_instance_type(instance_type)
        return {
            inst.accelerator_type: inst.accelerator_count
        } if (inst.accelerator_count and inst.accelerator_type) else None

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        k = KubernetesInstanceType.from_instance_type(instance_type)
        return k.cpus, k.memory

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List[clouds.Zone]]]:
        del num_nodes, region, instance_type, accelerators, use_spot  # Unused.
        for r in cls.regions():
            yield r.zones

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        del zones
        if region is None:
            region = self._regions[0]

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        # resources.memory and cpus are None if they are not explicitly set.
        # We fetch the default values for the instance type in that case.
        k = KubernetesInstanceType.from_instance_type(resources.instance_type)
        cpus = k.cpus
        mem = k.memory
        # Optionally populate accelerator information.
        acc_count = k.accelerator_count if k.accelerator_count else 0
        acc_type = k.accelerator_type if k.accelerator_type else None

        # Select image based on whether we are using GPUs or not.
        image = self.IMAGE_GPU if acc_count > 0 else self.IMAGE_CPU

        k8s_acc_label_key = None
        k8s_acc_label_value = None

        # If GPUs are requested, set node label to match the GPU type.
        if acc_count > 0 and acc_type is not None:
            label_formatter, node_labels = \
                kubernetes_utils.detect_gpu_label_formatter()
            if label_formatter is None:
                # TODO(romilb): This will fail early for autoscaling clusters.
                #  For AS clusters, we may need a way for users to specify the
                #  GPULabelFormatter to use since the cluster may be scaling up
                #  from zero nodes and may not have any GPU nodes yet.
                with ux_utils.print_exception_no_traceback():
                    supported_formats = ', '.join([
                        f.get_label_key()
                        for f in kubernetes_utils.LABEL_FORMATTER_REGISTRY
                    ])
                    suffix = ''
                    if env_options.Options.SHOW_DEBUG_INFO.get():
                        suffix = ' Found node labels: {}'.format(node_labels)
                    raise exceptions.ResourcesUnavailableError(
                        'Could not detect GPU labels in Kubernetes cluster. '
                        'Please ensure at least one node in the cluster has '
                        'node labels of either of these formats: '
                        f'{supported_formats}. Please refer to '
                        'the documentation on how to set up node labels.'
                        f'{suffix}')

            k8s_acc_label_key = label_formatter.get_label_key()
            k8s_acc_label_value = label_formatter.get_label_value(acc_type)
        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            'timeout': str(self.TIMEOUT),
            'k8s_ssh_key_secret_name': self.SKY_SSH_KEY_SECRET_NAME,
            'k8s_acc_label_key': k8s_acc_label_key,
            'k8s_acc_label_value': k8s_acc_label_value,
            # TODO(romilb): Allow user to specify custom images
            'image_id': image,
        }
        return deploy_vars

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
        fuzzy_candidate_list: List[str] = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], fuzzy_candidate_list)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Kubernetes(),
                    instance_type=instance_type,
                    accelerators=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        default_instance_type = Kubernetes.get_default_instance_type(
            cpus=resources.cpus,
            memory=resources.memory,
            disk_tier=resources.disk_tier)
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            if default_instance_type is None:
                return ([], [])
            else:
                return _make([default_instance_type]), []

        assert len(accelerators) == 1, resources
        # GPUs requested - build instance type.
        acc_type, acc_count = list(accelerators.items())[0]
        default_inst = KubernetesInstanceType.from_instance_type(
            default_instance_type)
        instance_type = KubernetesInstanceType.from_resources(
            default_inst.cpus, default_inst.memory, acc_count, acc_type).name
        # No fuzzy lists for Kubernetes
        return _make([instance_type]), []

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        if os.path.exists(os.path.expanduser(_CREDENTIAL_PATH)):
            # Test using python API
            return kubernetes_utils.check_credentials()
        else:
            return False, 'Credentials not found - ' \
                          f'check if {_CREDENTIAL_PATH} exists.'

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {_CREDENTIAL_PATH: _CREDENTIAL_PATH}

    def instance_type_exists(self, instance_type: str) -> bool:
        return KubernetesInstanceType.is_valid_instance_type(instance_type)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        # Kubernetes doesn't have regions or zones, so we don't need to validate
        return region, zone

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        # TODO(romilb): All accelerators are marked as not available for now.
        #  In the future, we should return false for accelerators that we know
        #  are not supported by the cluster.
        return True

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List['status_lib.ClusterStatus']:
        del tag_filters, region, zone, kwargs  # Unused.
        namespace = kubernetes_utils.get_current_kube_config_context_namespace()

        # Get all the pods with the label skypilot-cluster: <cluster_name>
        try:
            pods = kubernetes.core_api().list_namespaced_pod(
                namespace,
                label_selector=f'skypilot-cluster={name}',
                _request_timeout=kubernetes.API_TIMEOUT).items
        except kubernetes.max_retry_error():
            with ux_utils.print_exception_no_traceback():
                ctx = kubernetes_utils.get_current_kube_config_context_name()
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query cluster {name!r} status. '
                    'Network error - check if the Kubernetes cluster in '
                    f'context {ctx} is up and accessible.') from None
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to query Kubernetes cluster {name!r} status: '
                    f'{common_utils.format_exception(e)}')

        # Check if the pods are running or pending
        cluster_status = []
        for pod in pods:
            if pod.status.phase == 'Running':
                cluster_status.append(status_lib.ClusterStatus.UP)
            elif pod.status.phase == 'Pending':
                cluster_status.append(status_lib.ClusterStatus.INIT)
        # If pods are not found, we don't add them to the return list
        return cluster_status

    @classmethod
    def query_env_vars(cls, name: str) -> Dict[str, str]:
        namespace = kubernetes_utils.get_current_kube_config_context_namespace()
        pod = kubernetes.core_api().list_namespaced_pod(
            namespace,
            label_selector=f'skypilot-cluster={name},ray-node-type=head'
        ).items[0]
        response = kubernetes.stream()(
            kubernetes.core_api().connect_get_namespaced_pod_exec,
            pod.metadata.name,
            namespace,
            command=['env'],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _request_timeout=kubernetes.API_TIMEOUT)
        # Split response by newline and filter lines containing '='
        raw_lines = response.split('\n')
        filtered_lines = [line for line in raw_lines if '=' in line]

        # Split each line at the first '=' occurrence
        lines = [line.split('=', 1) for line in filtered_lines]

        # Construct the dictionary using only valid environment variable names
        env_vars = {}
        for line in lines:
            key = line[0]
            if common_utils.is_valid_env_var(key):
                env_vars[key] = line[1]

        return env_vars

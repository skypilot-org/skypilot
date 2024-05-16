"""Kubernetes."""
import json
import os
import re
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.clouds import service_catalog
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import schemas

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

# Check if KUBECONFIG is set, and use it if it is.
DEFAULT_KUBECONFIG_PATH = '~/.kube/config'
CREDENTIAL_PATH = os.environ.get('KUBECONFIG', DEFAULT_KUBECONFIG_PATH)

# Namespace for SkyPilot resources shared across multiple tenants on the
# same cluster (even if they might be running in different namespaces).
# E.g., FUSE device manager daemonset is run in this namespace.
_SKYPILOT_SYSTEM_NAMESPACE = 'skypilot-system'


@clouds.CLOUD_REGISTRY.register
class Kubernetes(clouds.Cloud):
    """Kubernetes."""

    SKY_SSH_KEY_SECRET_NAME = 'sky-ssh-keys'
    SKY_SSH_JUMP_NAME = 'sky-ssh-jump-pod'
    PORT_FORWARD_PROXY_CMD_TEMPLATE = \
        'kubernetes-port-forward-proxy-command.sh.j2'
    PORT_FORWARD_PROXY_CMD_PATH = '~/.sky/port-forward-proxy-cmd.sh'
    # Timeout for resource provisioning. This timeout determines how long to
    # wait for pod to be in pending status before giving up.
    # Larger timeout may be required for autoscaling clusters, since autoscaler
    # may take some time to provision new nodes.
    # Note that this timeout includes time taken by the Kubernetes scheduler
    # itself, which can be upto 2-3 seconds.
    # For non-autoscaling clusters, we conservatively set this to 10s.
    timeout = skypilot_config.get_nested(['kubernetes', 'provision_timeout'],
                                         10)

    _SUPPORTS_SERVICE_ACCOUNT_ON_REMOTE = True

    _DEFAULT_NUM_VCPUS = 2
    _DEFAULT_MEMORY_CPU_RATIO = 1
    _DEFAULT_MEMORY_CPU_RATIO_WITH_GPU = 4  # Allocate more memory for GPU tasks
    _REPR = 'Kubernetes'
    _SINGLETON_REGION = 'kubernetes'
    _regions: List[clouds.Region] = [clouds.Region(_SINGLETON_REGION)]
    _CLOUD_UNSUPPORTED_FEATURES = {
        # TODO(romilb): Stopping might be possible to implement with
        #  container checkpointing introduced in Kubernetes v1.25. See:
        #  https://kubernetes.io/blog/2022/12/05/forensic-container-checkpointing-alpha/ # pylint: disable=line-too-long
        clouds.CloudImplementationFeatures.STOP: 'Kubernetes does not '
                                                 'support stopping VMs.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'Spot instances are '
                                                          'not supported in '
                                                          'Kubernetes.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'Custom disk '
                                                             'tiers are not '
                                                             'supported in '
                                                             'Kubernetes.',
    }

    IMAGE_CPU = 'skypilot:cpu-ubuntu-2004'
    IMAGE_GPU = 'skypilot:gpu-ubuntu-2004'

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @property
    def ssh_key_secret_field_name(self):
        # Use a fresh user hash to avoid conflicts in the secret object naming.
        # This can happen when the controller is reusing the same user hash
        # through USER_ID_ENV_VAR but has a different SSH key.
        fresh_user_hash = common_utils.get_user_hash(force_fresh_hash=True)
        return f'ssh-publickey-{fresh_user_hash}'

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        unsupported_features = cls._CLOUD_UNSUPPORTED_FEATURES
        is_exec_auth, message = kubernetes_utils.is_kubeconfig_exec_auth()
        if is_exec_auth:
            assert isinstance(message, str), message
            # Controllers cannot spin up new pods with exec auth.
            unsupported_features[
                clouds.CloudImplementationFeatures.HOST_CONTROLLERS] = message
            # Pod does not have permissions to terminate itself with exec auth.
            unsupported_features[
                clouds.CloudImplementationFeatures.AUTO_TERMINATE] = message
        return unsupported_features

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
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[resources_utils.DiskTier] = None) -> str:
        # TODO(romilb): In the future, we may want to move the instance type
        #  selection + availability checking to a kubernetes_catalog module.
        del disk_tier  # Unused.
        # We strip '+' from resource requests since Kubernetes can provision
        # exactly the requested resources.
        instance_cpus = float(
            cpus.strip('+')) if cpus is not None else cls._DEFAULT_NUM_VCPUS
        if memory is not None:
            if memory.endswith('+'):
                instance_mem = float(memory[:-1])
            elif memory.endswith('x'):
                instance_mem = float(memory[:-1]) * instance_cpus
            else:
                instance_mem = float(memory)
        else:
            instance_mem = instance_cpus * cls._DEFAULT_MEMORY_CPU_RATIO
        virtual_instance_type = kubernetes_utils.KubernetesInstanceType(
            instance_cpus, instance_mem).name
        return virtual_instance_type

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        inst = kubernetes_utils.KubernetesInstanceType.from_instance_type(
            instance_type)
        return {
            inst.accelerator_type: inst.accelerator_count
        } if (inst.accelerator_count is not None and
              inst.accelerator_type is not None) else None

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        k = kubernetes_utils.KubernetesInstanceType.from_instance_type(
            instance_type)
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

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> int:
        del image_id, region  # Unused.
        # We don't limit the image by its size compared to the disk size, as
        # we don't have a notion of disk size in Kubernetes.
        return 0

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str,
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']],
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del cluster_name_on_cloud, zones, dryrun  # Unused.
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
        k = kubernetes_utils.KubernetesInstanceType.from_instance_type(
            resources.instance_type)
        cpus = k.cpus
        mem = k.memory
        # Optionally populate accelerator information.
        acc_count = k.accelerator_count if k.accelerator_count else 0
        acc_type = k.accelerator_type if k.accelerator_type else None

        if resources.image_id is not None:
            # Use custom image specified in resources
            image_id = resources.image_id['kubernetes']
            if image_id.startswith('docker:'):
                image_id = image_id[len('docker:'):]
        else:
            # Select image based on whether we are using GPUs or not.
            image_id = self.IMAGE_GPU if acc_count > 0 else self.IMAGE_CPU
            # Get the container image ID from the service catalog.
            image_id = service_catalog.get_image_id_from_tag(
                image_id, clouds='kubernetes')
        # TODO(romilb): Create a lightweight image for SSH jump host
        ssh_jump_image = service_catalog.get_image_id_from_tag(
            self.IMAGE_CPU, clouds='kubernetes')

        k8s_acc_label_key = None
        k8s_acc_label_value = None

        # If GPUs are requested, set node label to match the GPU type.
        if acc_count > 0 and acc_type is not None:
            k8s_acc_label_key, k8s_acc_label_value = \
                kubernetes_utils.get_gpu_label_key_value(acc_type)

        port_mode = network_utils.get_port_mode(None)

        remote_identity = skypilot_config.get_nested(
            ('kubernetes', 'remote_identity'),
            schemas.get_default_remote_identity('kubernetes'))
        if (remote_identity ==
                schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value):
            # SA name doesn't matter since automounting credentials is disabled
            k8s_service_account_name = 'default'
            k8s_automount_sa_token = 'false'
        elif (remote_identity ==
              schemas.RemoteIdentityOptions.SERVICE_ACCOUNT.value):
            # Use the default service account
            k8s_service_account_name = (
                kubernetes_utils.DEFAULT_SERVICE_ACCOUNT_NAME)
            k8s_automount_sa_token = 'true'
        else:
            # User specified a custom service account
            k8s_service_account_name = remote_identity
            k8s_automount_sa_token = 'true'

        fuse_device_required = bool(resources.requires_fuse)

        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            'timeout': str(self.timeout),
            'k8s_namespace':
                kubernetes_utils.get_current_kube_config_context_namespace(),
            'k8s_port_mode': port_mode.value,
            'k8s_ssh_key_secret_name': self.SKY_SSH_KEY_SECRET_NAME,
            'k8s_acc_label_key': k8s_acc_label_key,
            'k8s_acc_label_value': k8s_acc_label_value,
            'k8s_ssh_jump_name': self.SKY_SSH_JUMP_NAME,
            'k8s_ssh_jump_image': ssh_jump_image,
            'k8s_service_account_name': k8s_service_account_name,
            'k8s_automount_sa_token': k8s_automount_sa_token,
            'k8s_fuse_device_required': fuse_device_required,
            # Namespace to run the FUSE device manager in
            'k8s_skypilot_system_namespace': _SKYPILOT_SYSTEM_NAMESPACE,
            'image_id': image_id,
        }

        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> Tuple[List['resources_lib.Resources'], List[str]]:
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
            # For CPU only clusters, need no special handling
            chosen_instance_type = default_instance_type
        else:
            assert len(accelerators) == 1, resources
            # GPUs requested - build instance type.
            acc_type, acc_count = list(accelerators.items())[0]

            # Parse into KubernetesInstanceType
            k8s_instance_type = (kubernetes_utils.KubernetesInstanceType.
                                 from_instance_type(default_instance_type))

            gpu_task_cpus = k8s_instance_type.cpus
            # Special handling to bump up memory multiplier for GPU instances
            gpu_task_memory = (float(resources.memory.strip('+')) if
                               resources.memory is not None else gpu_task_cpus *
                               self._DEFAULT_MEMORY_CPU_RATIO_WITH_GPU)
            chosen_instance_type = (
                kubernetes_utils.KubernetesInstanceType.from_resources(
                    gpu_task_cpus, gpu_task_memory, acc_count, acc_type).name)

        # Check if requested instance type will fit in the cluster.
        autoscaler_type = kubernetes_utils.get_autoscaler_type()
        if autoscaler_type is None:
            # If autoscaler is not set, check if the instance type fits in the
            # cluster. Else, rely on the autoscaler to provision the right
            # instance type without running checks. Worst case, if autoscaling
            # fails, the pod will be stuck in pending state until
            # provision_timeout, after which failover will be triggered.
            fits, reason = kubernetes_utils.check_instance_fits(
                chosen_instance_type)
            if not fits:
                logger.debug(f'Instance type {chosen_instance_type} does '
                             'not fit in the Kubernetes cluster. '
                             f'Reason: {reason}')
                return [], []

        # No fuzzy lists for Kubernetes
        return _make([chosen_instance_type]), []

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        # Test using python API
        try:
            return kubernetes_utils.check_credentials()
        except Exception as e:  # pylint: disable=broad-except
            return (False, 'Credential check failed: '
                    f'{common_utils.format_exception(e)}')

    def get_credential_file_mounts(self) -> Dict[str, str]:
        if os.path.exists(os.path.expanduser(CREDENTIAL_PATH)):
            # Upload kubeconfig to the default path to avoid having to set
            # KUBECONFIG in the environment.
            return {DEFAULT_KUBECONFIG_PATH: CREDENTIAL_PATH}
        else:
            return {}

    def instance_type_exists(self, instance_type: str) -> bool:
        return kubernetes_utils.KubernetesInstanceType.is_valid_instance_type(
            instance_type)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        if region != self._SINGLETON_REGION:
            raise ValueError(
                'Kubernetes support does not support setting region.'
                ' Cluster used is determined by the kubeconfig.')
        if zone is not None:
            raise ValueError('Kubernetes support does not support setting zone.'
                             ' Cluster used is determined by the kubeconfig.')
        return region, zone

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        k8s = kubernetes.kubernetes
        try:
            _, current_context = k8s.config.list_kube_config_contexts()
            if 'namespace' in current_context['context']:
                namespace = current_context['context']['namespace']
            else:
                namespace = kubernetes_utils.DEFAULT_NAMESPACE

            user = current_context['context']['user']
            cluster = current_context['context']['cluster']
            return [f'{cluster}_{user}_{namespace}']
        except k8s.config.config_exception.ConfigException:
            return None

    @classmethod
    def is_label_valid(cls, label_key: str,
                       label_value: str) -> Tuple[bool, Optional[str]]:
        # Kubernetes labels can be of the format <domain>/<key>: <value>
        key_regex = re.compile(
            # Look-ahead to ensure proper domain formatting up to a slash
            r'^(?:(?=[a-z0-9]([-a-z0-9.]*[a-z0-9])?\/)'
            # Match domain: starts and ends with alphanum up to 253 chars
            # including a slash in the domain.
            r'[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?\/)?'
            # Match key: starts and ends with alphanum, upto to 63 chars.
            r'[a-z0-9]([-a-z0-9_.]{0,61}[a-z0-9])?$')
        value_regex = re.compile(
            r'^([a-zA-Z0-9]([-a-zA-Z0-9_.]{0,61}[a-zA-Z0-9])?)?$')
        key_valid = bool(key_regex.match(label_key))
        value_valid = bool(value_regex.match(label_value))
        error_msg = None
        condition_msg = ('Value must consist of alphanumeric characters or '
                         '\'-\', \'_\', \'.\', and must be no more than 63 '
                         'characters in length.')
        if not key_valid:
            error_msg = (f'Invalid label key {label_key} for Kubernetes. '
                         f'{condition_msg}')
        if not value_valid:
            error_msg = (f'Invalid label value {label_value} for Kubernetes. '
                         f'{condition_msg}')
        if not key_valid or not value_valid:
            return False, error_msg
        return True, None

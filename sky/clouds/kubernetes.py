"""Kubernetes."""
import functools
import os
import re
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.clouds import service_catalog
from sky.provision import instance_setup
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.skylet import constants
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


@clouds.CLOUD_REGISTRY.register(aliases=['k8s'])
class Kubernetes(clouds.Cloud):
    """Kubernetes."""

    SKY_SSH_KEY_SECRET_NAME = 'sky-ssh-keys'
    SKY_SSH_JUMP_NAME = 'sky-ssh-jump-pod'

    # Limit the length of the cluster name to avoid exceeding the limit of 63
    # characters for Kubernetes resources. We limit to 42 characters (63-21) to
    # allow additional characters for creating ingress services to expose ports.
    # These services are named as {cluster_name_on_cloud}--skypilot-svc--{port},
    # where the suffix is 21 characters long.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 42

    _SUPPORTS_SERVICE_ACCOUNT_ON_REMOTE = True

    _DEFAULT_NUM_VCPUS = 2
    _DEFAULT_MEMORY_CPU_RATIO = 1
    _DEFAULT_MEMORY_CPU_RATIO_WITH_GPU = 4  # Allocate more memory for GPU tasks
    _REPR = 'Kubernetes'
    _LEGACY_SINGLETON_REGION = 'kubernetes'
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

    IMAGE_CPU = 'skypilot:custom-cpu-ubuntu-2004'
    IMAGE_GPU = 'skypilot:custom-gpu-ubuntu-2004'

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
        unsupported_features = cls._CLOUD_UNSUPPORTED_FEATURES.copy()
        context = resources.region
        if context is None:
            context = kubernetes_utils.get_current_kube_config_context_name()
        # Features to be disabled for exec auth
        is_exec_auth, message = kubernetes_utils.is_kubeconfig_exec_auth(
            context)
        if is_exec_auth:
            assert isinstance(message, str), message
            # Controllers cannot spin up new pods with exec auth.
            unsupported_features[
                clouds.CloudImplementationFeatures.HOST_CONTROLLERS] = message
            # Pod does not have permissions to terminate itself with exec auth.
            unsupported_features[
                clouds.CloudImplementationFeatures.AUTO_TERMINATE] = message
        # Allow spot instances if supported by the cluster
        spot_label_key, _ = kubernetes_utils.get_spot_label(context)
        if spot_label_key is not None:
            unsupported_features.pop(
                clouds.CloudImplementationFeatures.SPOT_INSTANCE, None)
        return unsupported_features

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    @functools.lru_cache(maxsize=1)
    def _log_skipped_contexts_once(cls, skipped_contexts: Tuple[str,
                                                                ...]) -> None:
        """Log skipped contexts for only once.

        We don't directly cache the result of _filter_existing_allowed_contexts
        as the admin policy may update the allowed contexts.
        """
        if skipped_contexts:
            logger.warning(
                f'Kubernetes contexts {set(skipped_contexts)!r} specified in '
                '"allowed_contexts" not found in kubeconfig. '
                'Ignoring these contexts.')

    @classmethod
    def _existing_allowed_contexts(cls) -> List[Optional[str]]:
        """Get existing allowed contexts.

        If None is returned in the list, it means that we are running in a pod
        with in-cluster auth. In this case, we specify None context, which will
        use the service account mounted in the pod.
        """
        all_contexts = kubernetes_utils.get_all_kube_config_context_names()
        if len(all_contexts) == 0:
            return []
        if all_contexts == [None]:
            # If only one context is found and it is None, we are running in a
            # pod with in-cluster auth. In this case, we allow it to be used
            # without checking against allowed_contexts.
            # TODO(romilb): We may want check in-cluster auth against
            #  allowed_contexts in the future by adding a special context name
            #  for in-cluster auth.
            return [None]
        all_contexts = set(all_contexts)

        allowed_contexts = skypilot_config.get_nested(
            ('kubernetes', 'allowed_contexts'), None)

        if allowed_contexts is None:
            current_context = (
                kubernetes_utils.get_current_kube_config_context_name())
            allowed_contexts = []
            if current_context is not None:
                allowed_contexts = [current_context]

        existing_contexts = []
        skipped_contexts = []
        for context in allowed_contexts:
            if context in all_contexts:
                existing_contexts.append(context)
            else:
                skipped_contexts.append(context)
        cls._log_skipped_contexts_once(tuple(skipped_contexts))
        return existing_contexts

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators, zone, use_spot  # unused
        existing_contexts = cls._existing_allowed_contexts()

        regions = []
        for context in existing_contexts:
            if context is None:
                # If running in-cluster, we allow the region to be set to the
                # singleton region since there is no context name available.
                regions.append(clouds.Region(
                    kubernetes_utils.IN_CLUSTER_REGION))
            else:
                regions.append(clouds.Region(context))

        if region is not None:
            regions = [r for r in regions if r.name == region]

        # Check if requested instance type will fit in the cluster.
        # TODO(zhwu,romilb): autoscaler type needs to be regional (per
        # kubernetes cluster/context).
        regions_to_return = []
        autoscaler_type = kubernetes_utils.get_autoscaler_type()
        if autoscaler_type is None and instance_type is not None:
            # If autoscaler is not set, check if the instance type fits in the
            # cluster. Else, rely on the autoscaler to provision the right
            # instance type without running checks. Worst case, if autoscaling
            # fails, the pod will be stuck in pending state until
            # provision_timeout, after which failover will be triggered.
            for r in regions:
                context = r.name
                fits, reason = kubernetes_utils.check_instance_fits(
                    context, instance_type)
                if fits:
                    regions_to_return.append(r)
                else:
                    logger.debug(
                        f'Instance type {instance_type} does '
                        'not fit in the Kubernetes cluster with context: '
                        f'{context}. Reason: {reason}')
        else:
            regions_to_return = regions

        return regions_to_return

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
    ) -> Optional[Dict[str, Union[int, float]]]:
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
        # Always yield None for zones, since Kubernetes does not have zones, and
        # we should allow any region get to this point.
        yield None

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> int:
        del image_id, region  # Unused.
        # We don't limit the image by its size compared to the disk size, as
        # we don't have a notion of disk size in Kubernetes.
        return 0

    @staticmethod
    def _calculate_provision_timeout(num_nodes: int) -> int:
        """Calculate provision timeout based on number of nodes.

        The timeout scales linearly with the number of nodes to account for
        scheduling overhead, but is capped to avoid excessive waiting.

        Args:
            num_nodes: Number of nodes being provisioned

        Returns:
            Timeout in seconds
        """
        base_timeout = 10  # Base timeout for single node
        per_node_timeout = 0.2  # Additional seconds per node
        max_timeout = 60  # Cap at 1 minute

        return int(
            min(base_timeout + (per_node_timeout * (num_nodes - 1)),
                max_timeout))

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name: resources_utils.ClusterName,
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']],
            num_nodes: int,
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del cluster_name, zones, dryrun  # Unused.
        if region is None:
            context = kubernetes_utils.get_current_kube_config_context_name()
        else:
            context = region.name
        assert context is not None, 'No context found in kubeconfig'

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # resources.memory and cpus are None if they are not explicitly set.
        # We fetch the default values for the instance type in that case.
        k = kubernetes_utils.KubernetesInstanceType.from_instance_type(
            resources.instance_type)
        cpus = k.cpus
        mem = k.memory
        # Optionally populate accelerator information.
        acc_count = k.accelerator_count if k.accelerator_count else 0
        acc_type = k.accelerator_type if k.accelerator_type else None

        image_id_dict = resources.image_id
        if image_id_dict is not None:
            # Use custom image specified in resources
            if None in image_id_dict:
                image_id = image_id_dict[None]
            else:
                assert resources.region in image_id_dict, image_id_dict
                image_id = image_id_dict[resources.region]
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
        k8s_topology_label_key = None
        k8s_topology_label_value = None
        k8s_resource_key = None
        tpu_requested = False

        # If GPU/TPUs are requested, set node label to match the GPU/TPU type.
        if acc_count > 0 and acc_type is not None:
            (k8s_acc_label_key, k8s_acc_label_value, k8s_topology_label_key,
             k8s_topology_label_value) = (
                 kubernetes_utils.get_accelerator_label_key_value(
                     context, acc_type, acc_count))
            if (k8s_acc_label_key ==
                    kubernetes_utils.GKELabelFormatter.TPU_LABEL_KEY):
                tpu_requested = True
                k8s_resource_key = kubernetes_utils.TPU_RESOURCE_KEY
            else:
                k8s_resource_key = kubernetes_utils.GPU_RESOURCE_KEY

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

        # Configure spot labels, if requested and supported
        spot_label_key, spot_label_value = None, None
        if resources.use_spot:
            spot_label_key, spot_label_value = kubernetes_utils.get_spot_label()

        # Timeout for resource provisioning. This timeout determines how long to
        # wait for pod to be in pending status before giving up.
        # Larger timeout may be required for autoscaling clusters, since
        # autoscaler may take some time to provision new nodes.
        # Note that this timeout includes time taken by the Kubernetes scheduler
        # itself, which can be upto 2-3 seconds, and up to 10-15 seconds when
        # scheduling 100s of pods.
        # We use a linear scaling formula to determine the timeout based on the
        # number of nodes.

        timeout = self._calculate_provision_timeout(num_nodes)
        timeout = skypilot_config.get_nested(
            ('kubernetes', 'provision_timeout'),
            timeout,
            override_configs=resources.cluster_config_overrides)
        # We specify object-store-memory to be 500MB to avoid taking up too
        # much memory on the head node. 'num-cpus' should be set to limit
        # the CPU usage on the head pod, otherwise the ray cluster will use the
        # CPU resources on the node instead within the pod.
        custom_ray_options = {
            'object-store-memory': 500000000,
            'num-cpus': str(int(cpus)),
        }
        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            'timeout': str(timeout),
            'k8s_port_mode': port_mode.value,
            'k8s_networking_mode': network_utils.get_networking_mode().value,
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
            'k8s_spot_label_key': spot_label_key,
            'k8s_spot_label_value': spot_label_value,
            'tpu_requested': tpu_requested,
            'k8s_topology_label_key': k8s_topology_label_key,
            'k8s_topology_label_value': k8s_topology_label_value,
            'k8s_resource_key': k8s_resource_key,
            'image_id': image_id,
            'ray_installation_commands': constants.RAY_INSTALLATION_COMMANDS,
            'ray_head_start_command': instance_setup.ray_head_start_command(
                custom_resources, custom_ray_options),
            'skypilot_ray_port': constants.SKY_REMOTE_RAY_PORT,
            'ray_worker_start_command': instance_setup.ray_worker_start_command(
                custom_resources, custom_ray_options, no_restart=False),
        }

        # Add kubecontext if it is set. It may be None if SkyPilot is running
        # inside a pod with in-cluster auth.
        if context is not None:
            deploy_vars['k8s_context'] = context

        namespace = kubernetes_utils.get_kube_config_context_namespace(context)
        deploy_vars['k8s_namespace'] = namespace

        return deploy_vars

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        # TODO(zhwu): This needs to be updated to return the correct region
        # (context) that has enough resources.
        fuzzy_candidate_list: List[str] = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            regions = self.regions_with_offering(
                resources.instance_type,
                accelerators=resources.accelerators,
                use_spot=resources.use_spot,
                region=resources.region,
                zone=resources.zone)
            if not regions:
                return resources_utils.FeasibleResources([], [], None)
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources],
                                                     fuzzy_candidate_list, None)

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

        # Check the availability of the specified instance type in all contexts.
        available_regions = self.regions_with_offering(
            chosen_instance_type,
            accelerators=None,
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone)
        if not available_regions:
            return resources_utils.FeasibleResources([], [], None)
        # No fuzzy lists for Kubernetes
        # We don't set the resources returned with regions, because the
        # optimizer will further find the valid region (context) for the
        # resources.
        return resources_utils.FeasibleResources(_make([chosen_instance_type]),
                                                 [], None)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        # Test using python API
        try:
            existing_allowed_contexts = cls._existing_allowed_contexts()
        except ImportError as e:
            return (False,
                    f'{common_utils.format_exception(e, use_bracket=True)}')
        if not existing_allowed_contexts:
            if skypilot_config.loaded_config_path() is None:
                check_skypilot_config_msg = ''
            else:
                check_skypilot_config_msg = (
                    ' and check "allowed_contexts" in your '
                    f'{skypilot_config.loaded_config_path()} file.')
            return (False, 'No available context found in kubeconfig. '
                    'Check if you have a valid kubeconfig file' +
                    check_skypilot_config_msg)
        reasons = []
        for context in existing_allowed_contexts:
            try:
                check_result = kubernetes_utils.check_credentials(context)
                if check_result[0]:
                    return check_result
                reasons.append(f'{context}: {check_result[1]}')
            except Exception as e:  # pylint: disable=broad-except
                return (False, f'Credential check failed for {context}: '
                        f'{common_utils.format_exception(e)}')
        return (False, 'Failed to find available context with working '
                'credentials. Details:\n' + '\n'.join(reasons))

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
        if region == self._LEGACY_SINGLETON_REGION:
            # For backward compatibility, we allow the region to be set to the
            # legacy singleton region.
            # TODO: Remove this after 0.9.0.
            return region, zone

        if region == kubernetes_utils.IN_CLUSTER_REGION:
            # If running incluster, we set region to IN_CLUSTER_REGION
            # since there is no context name available.
            return region, zone

        all_contexts = kubernetes_utils.get_all_kube_config_context_names()
        if all_contexts == [None]:
            # If [None] context is returned, use the singleton region since we
            # are running in a pod with in-cluster auth.
            all_contexts = [kubernetes_utils.IN_CLUSTER_REGION]
        if region not in all_contexts:
            raise ValueError(
                f'Context {region} not found in kubeconfig. Kubernetes only '
                'supports context names as regions. Available '
                f'contexts: {all_contexts}')
        if zone is not None:
            raise ValueError('Kubernetes support does not support setting zone.'
                             ' Cluster used is determined by the kubeconfig.')
        return region, zone

    @staticmethod
    def get_identity_from_context(context):
        if 'namespace' in context['context']:
            namespace = context['context']['namespace']
        else:
            namespace = kubernetes_utils.DEFAULT_NAMESPACE
        user = context['context']['user']
        cluster = context['context']['cluster']
        identity_str = f'{cluster}_{user}_{namespace}'
        return identity_str

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        k8s = kubernetes.kubernetes
        identities = []
        try:
            all_contexts, current_context = (
                k8s.config.list_kube_config_contexts())
        except k8s.config.config_exception.ConfigException:
            return None
        # Add current context at the head of the list
        current_identity = [cls.get_identity_from_context(current_context)]
        identities.append(current_identity)
        for context in all_contexts:
            identity = [cls.get_identity_from_context(context)]
            identities.append(identity)
        return identities

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

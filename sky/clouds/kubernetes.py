"""Kubernetes."""
import os
import re
import subprocess
import tempfile
import typing
from typing import Dict, Iterator, List, Optional, Set, Tuple, Union

import colorama

from sky import catalog
from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.clouds.utils import gcp_utils
from sky.provision import instance_setup
from sky.provision.gcp import constants as gcp_constants
from sky.provision.kubernetes import network_utils
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.kubernetes.utils import is_tpu_on_gke
from sky.provision.kubernetes.utils import KubernetesHighPerformanceNetworkType
from sky.provision.kubernetes.utils import normalize_tpu_accelerator_name
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import kubernetes_enums
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas
from sky.volumes import volume as volume_lib

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

# Namespace for SkyPilot resources shared across multiple tenants on the
# same cluster (even if they might be running in different namespaces).
# E.g., FUSE device manager daemonset is run in this namespace.
_SKYPILOT_SYSTEM_NAMESPACE = 'skypilot-system'

# Shared directory to communicate with fusermount-server, refer to
# addons/fuse-proxy/README.md for more details.
_FUSERMOUNT_SHARED_DIR = '/var/run/fusermount'


@registry.CLOUD_REGISTRY.register(aliases=['k8s'])
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
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported in '
             'Kubernetes.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently not supported in '
             f'{_REPR}.'),
    }

    IMAGE_CPU = 'skypilot:custom-cpu-ubuntu-2004'
    IMAGE_GPU = 'skypilot:custom-gpu-ubuntu-2004'

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    _INDENT_PREFIX = ' ' * 4

    # Set of contexts that has logged as temporarily unreachable
    logged_unreachable_contexts: Set[str] = set()

    @property
    def ssh_key_secret_field_name(self):
        # Use a fresh user hash to avoid conflicts in the secret object naming.
        # This can happen when the controller is reusing the same user hash
        # through USER_ID_ENV_VAR but has a different SSH key.
        fresh_user_hash = common_utils.generate_user_hash()
        return f'ssh-publickey-{fresh_user_hash}'

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        # TODO(aylei): features need to be regional (per context) to make
        # multi-kubernetes selection/failover work.
        unsupported_features = cls._CLOUD_UNSUPPORTED_FEATURES.copy()
        context = resources.region
        if context is None:
            context = kubernetes_utils.get_current_kube_config_context_name()
        unsupported_features[clouds.CloudImplementationFeatures.STOP] = (
            'Stopping clusters is not supported on Kubernetes.')
        unsupported_features[clouds.CloudImplementationFeatures.AUTOSTOP] = (
            'Auto-stop is not supported on Kubernetes.')
        # Allow spot instances if supported by the cluster
        try:
            spot_label_key, _ = kubernetes_utils.get_spot_label(context)
            if spot_label_key is not None:
                unsupported_features.pop(
                    clouds.CloudImplementationFeatures.SPOT_INSTANCE, None)
            # Allow custom network tier if supported by the cluster
            # (e.g., Nebius clusters with high performance networking)
            network_type, _ = cls._detect_network_type(context,
                                                       resources.network_tier)
            if network_type.supports_high_performance_networking():
                unsupported_features.pop(
                    clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
                    None)
        except exceptions.KubeAPIUnreachableError as e:
            cls._log_unreachable_context(context, str(e))
        return unsupported_features

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    @annotations.lru_cache(scope='global', maxsize=1)
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
    def existing_allowed_contexts(cls, silent: bool = False) -> List[str]:
        """Get existing allowed contexts.

        If None is returned in the list, it means that we are running in a pod
        with in-cluster auth. In this case, we specify None context, which will
        use the service account mounted in the pod.
        """
        all_contexts = kubernetes_utils.get_all_kube_context_names()
        if not all_contexts:
            return []

        all_contexts = set(all_contexts)

        # Allowed_contexts specified for workspace should take precedence over
        # the global allowed_contexts.
        allowed_contexts = skypilot_config.get_workspace_cloud(
            'kubernetes').get('allowed_contexts', None)
        if allowed_contexts is None:
            allowed_contexts = skypilot_config.get_effective_region_config(
                cloud='kubernetes',
                region=None,
                keys=('allowed_contexts',),
                default_value=None)

        # Exclude contexts starting with `ssh-`
        # TODO(romilb): Remove when SSH Node Pools use a separate kubeconfig.
        all_contexts = [
            ctx for ctx in all_contexts if not ctx.startswith('ssh-')
        ]

        if allowed_contexts is None:
            # Try kubeconfig if present
            current_context = (
                kubernetes_utils.get_current_kube_config_context_name())
            if ((current_context is None or current_context.startswith('ssh-'))
                    and kubernetes_utils.is_incluster_config_available()):
                # If no kubeconfig contexts found, use in-cluster if available
                current_context = kubernetes.in_cluster_context_name()
            allowed_contexts = []
            if current_context is not None:
                allowed_contexts = [current_context]

        existing_contexts = []
        skipped_contexts = []
        for context in allowed_contexts:
            if context in all_contexts:
                existing_contexts.append(context)
            else:
                # Skip SSH Node Pool contexts
                if context.startswith('ssh-'):
                    continue
                skipped_contexts.append(context)
        if not silent:
            cls._log_skipped_contexts_once(tuple(skipped_contexts))
        return existing_contexts

    @classmethod
    def _log_unreachable_context(cls,
                                 context: str,
                                 reason: Optional[str] = None) -> None:
        """Logs a Kubernetes context as unreachable.

        Args:
            context: The Kubernetes context to mark as unreachable.
            reason: Optional reason for marking the context as unreachable.
            silent: Whether to suppress the log message.
        """
        # Skip if this context has already been logged as unreachable
        if context in cls.logged_unreachable_contexts:
            return

        cls.logged_unreachable_contexts.add(context)
        msg = f'Excluding Kubernetes context {context}'
        if reason is not None:
            msg += f': {reason}'
        logger.info(msg)

        # Check if all existing allowed contexts are now unreachable
        existing_contexts = cls.existing_allowed_contexts()
        if existing_contexts and all(ctx in cls.logged_unreachable_contexts
                                     for ctx in existing_contexts):
            logger.warning(
                'All Kubernetes contexts are unreachable. '
                'Retry if it is a transient error, or run sky check to '
                'refresh Kubernetes availability if permanent.')

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators, zone, use_spot  # unused
        existing_contexts = cls.existing_allowed_contexts()

        regions = []
        for context in existing_contexts:
            regions.append(clouds.Region(context))

        if region is not None:
            regions = [r for r in regions if r.name == region]

        # Check if requested instance type will fit in the cluster.
        # TODO(zhwu,romilb): autoscaler type needs to be regional (per
        # kubernetes cluster/context).
        if instance_type is None:
            return regions

        regions_to_return = []
        for r in regions:
            context = r.name
            try:
                fits, reason = kubernetes_utils.check_instance_fits(
                    context, instance_type)
            except exceptions.KubeAPIUnreachableError as e:
                cls._log_unreachable_context(context, str(e))
                continue
            if fits:
                regions_to_return.append(r)
                continue
            logger.debug(f'Instance type {instance_type} does '
                         'not fit in the existing Kubernetes cluster '
                         'with context: '
                         f'{context}. Reason: {reason}')

            autoscaler_type = skypilot_config.get_effective_region_config(
                cloud='kubernetes',
                region=context,
                keys=('autoscaler',),
                default_value=None)
            if (autoscaler_type is not None and
                    not kubernetes_utils.get_autoscaler(
                        kubernetes_enums.KubernetesAutoscalerType(
                            autoscaler_type)).can_query_backend):
                # Unsupported autoscaler type. Rely on the autoscaler to
                # provision the right instance type without running checks.
                # Worst case, if autoscaling fails, the pod will be stuck in
                # pending state until provision_timeout, after which failover
                # will be triggered.
                #
                # Removing this if statement produces the same behavior,
                # because can_create_new_instance_of_type() always returns True
                # for unsupported autoscaler types.
                # This check is here as a performance optimization to avoid
                # further code executions that is known to return this result.
                regions_to_return.append(r)
                continue

            if autoscaler_type is None:
                continue
            autoscaler = kubernetes_utils.get_autoscaler(
                kubernetes_enums.KubernetesAutoscalerType(autoscaler_type))
            logger.debug(f'{context} has autoscaler of type: {autoscaler_type}')
            if autoscaler.can_create_new_instance_of_type(
                    context, instance_type):
                logger.debug(f'Kubernetes cluster {context} can be '
                             'autoscaled to create instance type '
                             f'{instance_type}. Including {context} '
                             'in the list of regions to return.')
                regions_to_return.append(r)
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
            disk_tier: Optional['resources_utils.DiskTier'] = None) -> str:
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
    def _calculate_provision_timeout(
        num_nodes: int,
        volume_mounts: Optional[List['volume_lib.VolumeMount']],
        enable_flex_start: bool,
    ) -> int:
        """Calculate provision timeout based on number of nodes.

        The timeout scales linearly with the number of nodes to account for
        scheduling overhead, but is capped to avoid excessive waiting.

        Args:
            num_nodes: Number of nodes being provisioned
            volume_mounts: Volume mounts for the pod
            enable_flex_start: Whether flex start is enabled

        Returns:
            Timeout in seconds
        """
        base_timeout = 10  # Base timeout for single node
        per_node_timeout = 0.2  # Additional seconds per node
        max_timeout = 60  # Cap at 1 minute
        if enable_flex_start:
            # Flex start takes longer to provision.
            base_timeout = 1200
            per_node_timeout = 10
            max_timeout = 2400
        elif volume_mounts is not None:
            for volume_mount in volume_mounts:
                if (volume_mount.volume_config.type ==
                        volume_lib.VolumeType.PVC.value):
                    if (volume_mount.volume_config.config.get(
                            'access_mode', '') ==
                            volume_lib.VolumeAccessMode.READ_WRITE_MANY.value):
                        # GKE may take several minutes to provision a PV
                        # supporting READ_WRITE_MANY with filestore.
                        base_timeout = 180
                        max_timeout = 240
                        break

        return int(
            min(base_timeout + (per_node_timeout * (num_nodes - 1)),
                max_timeout))

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: 'resources_utils.ClusterName',
        region: Optional['clouds.Region'],
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Optional[str]]:
        del cluster_name, zones, dryrun  # Unused.
        if region is None:
            context = kubernetes_utils.get_current_kube_config_context_name()
        else:
            context = region.name
        assert context is not None, 'No context found in kubeconfig'

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        # resources.memory and cpus are None if they are not explicitly set.
        # We fetch the default values for the instance type in that case.
        k = kubernetes_utils.KubernetesInstanceType.from_instance_type(
            resources.instance_type)
        cpus = k.cpus
        mem = k.memory
        # Optionally populate accelerator information.
        acc_type = k.accelerator_type
        acc_count = k.accelerator_count
        if acc_type is not None and is_tpu_on_gke(acc_type):
            acc_type, acc_count = normalize_tpu_accelerator_name(acc_type)
        else:
            acc_count = acc_count or 0

        def _get_image_id(resources: 'resources_lib.Resources') -> str:
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
                image_id = catalog.get_image_id_from_tag(image_id,
                                                         clouds='kubernetes')
            return image_id

        image_id = _get_image_id(resources)
        # TODO(romilb): Create a lightweight image for SSH jump host
        ssh_jump_image = catalog.get_image_id_from_tag(self.IMAGE_CPU,
                                                       clouds='kubernetes')

        # Set environment variables for the pod. Note that SkyPilot env vars
        # are set separately when the task is run. These env vars are
        # independent of the SkyPilot task to be run.
        k8s_env_vars = {kubernetes.IN_CLUSTER_CONTEXT_NAME_ENV_VAR: context}

        # Setup GPU/TPU labels and resource keys.
        k8s_acc_label_key = None
        k8s_acc_label_values = None
        k8s_topology_label_key = None
        k8s_topology_label_value = None
        k8s_resource_key = None
        tpu_requested = False
        avoid_label_keys = None

        # If GPU/TPUs are requested, set node label to match the GPU/TPU type.
        if acc_count > 0 and acc_type is not None:
            (k8s_acc_label_key, k8s_acc_label_values, k8s_topology_label_key,
             k8s_topology_label_value) = (
                 kubernetes_utils.get_accelerator_label_key_values(
                     context, acc_type, acc_count))
            if (k8s_acc_label_key ==
                    kubernetes_utils.GKELabelFormatter.TPU_LABEL_KEY):
                tpu_requested = True
                k8s_resource_key = kubernetes_utils.TPU_RESOURCE_KEY
            else:
                k8s_resource_key = kubernetes_utils.get_gpu_resource_key()
        else:
            # If no GPUs are requested, we set NVIDIA_VISIBLE_DEVICES=none to
            # maintain GPU isolation. This is to override the default behavior
            # of Nvidia device plugin which would expose all GPUs to the pod
            # when no GPUs are requested.
            # Note that NVIDIA_VISIBLE_DEVICES is different from
            # CUDA_VISIBLE_DEVICES - the latter is used to control which GPUs
            # are visible to the application and is set inside the pod, while
            # the former is used to control which GPUs are visible to the pod
            # through the nvidia runtime.
            # See: https://github.com/NVIDIA/k8s-device-plugin/issues/61
            k8s_env_vars['NVIDIA_VISIBLE_DEVICES'] = 'none'
            avoid_label_keys = kubernetes_utils.get_accelerator_label_keys(
                context)
            if len(avoid_label_keys) == 0:
                avoid_label_keys = None
        port_mode = network_utils.get_port_mode(None, context)

        remote_identity = skypilot_config.get_effective_region_config(
            cloud='kubernetes',
            region=context,
            keys=('remote_identity',),
            default_value=schemas.get_default_remote_identity('kubernetes'))

        if isinstance(remote_identity, dict):
            # If remote_identity is a dict, use the service account for the
            # current context
            k8s_service_account_name = remote_identity.get(context, None)
            if k8s_service_account_name is None:
                err_msg = (f'Context {context!r} not found in '
                           'remote identities from config.yaml')
                raise ValueError(err_msg)
        else:
            # If remote_identity is not a dict, use
            k8s_service_account_name = remote_identity

        lc = schemas.RemoteIdentityOptions.LOCAL_CREDENTIALS.value
        sa = schemas.RemoteIdentityOptions.SERVICE_ACCOUNT.value

        if k8s_service_account_name == lc or k8s_service_account_name == sa:
            # Use the default service account if remote identity is not set.
            # For LOCAL_CREDENTIALS, this is for in-cluster authentication
            # which needs a serviceaccount (specifically for SSH node pools
            # which uses in-cluster authentication internally, and we would
            # like to support exec-auth when the user is also using SSH infra)
            k8s_service_account_name = (
                kubernetes_utils.DEFAULT_SERVICE_ACCOUNT_NAME)

        fuse_device_required = bool(resources.requires_fuse)

        # Configure spot labels, if requested and supported
        spot_label_key, spot_label_value = None, None
        if resources.use_spot:
            spot_label_key, spot_label_value = kubernetes_utils.get_spot_label()

        network_type, machine_type = self._detect_network_type(
            context, resources.network_tier)

        # Check if this cluster supports high performance networking and
        # configure appropriate settings for different cluster types
        if (resources.network_tier is not None and
                resources.network_tier == resources_utils.NetworkTier.BEST):
            # Only proceed if CUSTOM_NETWORK_TIER is supported by this cluster
            unsupported_features = self._unsupported_features_for_resources(
                resources)
            if clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER \
                    not in unsupported_features:
                # Add high-performance networking environment variables for
                # Nebius (GCP environment variables are handled directly in
                # the template)
                if (network_type == KubernetesHighPerformanceNetworkType.NEBIUS
                   ):
                    network_env_vars = network_type.get_network_env_vars()
                    k8s_env_vars.update(network_env_vars)

        # We specify object-store-memory to be 500MB to avoid taking up too
        # much memory on the head node. 'num-cpus' should be set to limit
        # the CPU usage on the head pod, otherwise the ray cluster will use the
        # CPU resources on the node instead within the pod.
        custom_ray_options = {
            'object-store-memory': 500000000,
            # 'num-cpus' must be an integer, but we should not set it to 0 if
            # cpus is <1.
            'num-cpus': str(max(int(cpus), 1)),
        }

        # Get the storage class name for high availability controller's PVC
        k8s_ha_storage_class_name = (
            skypilot_config.get_effective_region_config(
                cloud='kubernetes',
                region=context,
                keys=('high_availability', 'storage_class_name'),
                default_value=None))

        k8s_kueue_local_queue_name = (
            skypilot_config.get_effective_region_config(
                cloud='kubernetes',
                region=context,
                keys=('kueue', 'local_queue_name'),
                default_value=None,
                override_configs=resources.cluster_config_overrides))

        # Check DWS configuration for GKE.
        (enable_flex_start, enable_flex_start_queued_provisioning,
         max_run_duration_seconds) = gcp_utils.get_dws_config(
             context, k8s_kueue_local_queue_name,
             resources.cluster_config_overrides)
        if enable_flex_start_queued_provisioning or enable_flex_start:
            # DWS is only supported in GKE, check the autoscaler type.
            autoscaler_type = skypilot_config.get_effective_region_config(
                cloud='kubernetes',
                region=context,
                keys=('autoscaler',),
                default_value=None)
            if (autoscaler_type !=
                    kubernetes_enums.KubernetesAutoscalerType.GKE.value):
                raise ValueError(
                    f'DWS is only supported in GKE, but the autoscaler type '
                    f'for context {context} is {autoscaler_type}')

        # Timeout for resource provisioning. This timeout determines how long to
        # wait for pod to be in pending status before giving up.
        # Larger timeout may be required for autoscaling clusters, since
        # autoscaler may take some time to provision new nodes.
        # Note that this timeout includes time taken by the Kubernetes scheduler
        # itself, which can be upto 2-3 seconds, and up to 10-15 seconds when
        # scheduling 100s of pods.
        # We use a linear scaling formula to determine the timeout based on the
        # number of nodes.

        timeout = self._calculate_provision_timeout(
            num_nodes, volume_mounts, enable_flex_start or
            enable_flex_start_queued_provisioning)
        timeout = skypilot_config.get_effective_region_config(
            cloud='kubernetes',
            region=context,
            keys=('provision_timeout',),
            default_value=timeout,
            override_configs=resources.cluster_config_overrides)

        deploy_vars = {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'cpus': str(cpus),
            'memory': str(mem),
            'accelerator_count': str(acc_count),
            'timeout': str(timeout),
            'k8s_port_mode': port_mode.value,
            'k8s_networking_mode': network_utils.get_networking_mode(
                None, context=context).value,
            'k8s_ssh_key_secret_name': self.SKY_SSH_KEY_SECRET_NAME,
            'k8s_acc_label_key': k8s_acc_label_key,
            'k8s_acc_label_values': k8s_acc_label_values,
            'k8s_ssh_jump_name': self.SKY_SSH_JUMP_NAME,
            'k8s_ssh_jump_image': ssh_jump_image,
            'k8s_service_account_name': k8s_service_account_name,
            'k8s_automount_sa_token': 'true',
            'k8s_fuse_device_required': fuse_device_required,
            'k8s_kueue_local_queue_name': k8s_kueue_local_queue_name,
            # Namespace to run the fusermount-server daemonset in
            'k8s_skypilot_system_namespace': _SKYPILOT_SYSTEM_NAMESPACE,
            'k8s_fusermount_shared_dir': _FUSERMOUNT_SHARED_DIR,
            'k8s_spot_label_key': spot_label_key,
            'k8s_spot_label_value': spot_label_value,
            'tpu_requested': tpu_requested,
            'k8s_topology_label_key': k8s_topology_label_key,
            'k8s_topology_label_value': k8s_topology_label_value,
            'k8s_resource_key': k8s_resource_key,
            'k8s_env_vars': k8s_env_vars,
            'image_id': image_id,
            'ray_installation_commands': constants.RAY_INSTALLATION_COMMANDS,
            'ray_head_start_command': instance_setup.ray_head_start_command(
                custom_resources, custom_ray_options),
            'skypilot_ray_port': constants.SKY_REMOTE_RAY_PORT,
            'ray_worker_start_command': instance_setup.ray_worker_start_command(
                custom_resources, custom_ray_options, no_restart=False),
            'k8s_high_availability_deployment_volume_mount_name':
                (kubernetes_utils.HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_NAME
                ),
            'k8s_high_availability_deployment_volume_mount_path':
                (kubernetes_utils.HIGH_AVAILABILITY_DEPLOYMENT_VOLUME_MOUNT_PATH
                ),
            'k8s_high_availability_deployment_setup_script_path':
                (constants.PERSISTENT_SETUP_SCRIPT_PATH),
            'k8s_high_availability_deployment_run_script_dir':
                (constants.PERSISTENT_RUN_SCRIPT_DIR),
            'k8s_high_availability_restarting_signal_file':
                (constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE),
            'ha_recovery_log_path': constants.HA_PERSISTENT_RECOVERY_LOG_PATH,
            'sky_python_cmd': constants.SKY_PYTHON_CMD,
            'k8s_high_availability_storage_class_name':
                (k8s_ha_storage_class_name),
            'avoid_label_keys': avoid_label_keys,
            'k8s_enable_flex_start': enable_flex_start,
            'k8s_max_run_duration_seconds': max_run_duration_seconds,
        }

        # Add kubecontext if it is set. It may be None if SkyPilot is running
        # inside a pod with in-cluster auth.
        if context is not None:
            deploy_vars['k8s_context'] = context

        namespace = kubernetes_utils.get_kube_config_context_namespace(context)
        deploy_vars['k8s_namespace'] = namespace

        # Add backward compatibility template variables for GPUDirect variants
        deploy_vars['k8s_enable_gpudirect_tcpx'] = (
            network_type == KubernetesHighPerformanceNetworkType.GCP_TCPX)
        deploy_vars['k8s_enable_gpudirect_tcpxo'] = (
            network_type == KubernetesHighPerformanceNetworkType.GCP_TCPXO)
        rdma_enabled = (network_type ==
                        KubernetesHighPerformanceNetworkType.GCP_GPUDIRECT_RDMA)
        deploy_vars['k8s_enable_gpudirect_rdma'] = rdma_enabled
        if rdma_enabled and machine_type.startswith('a4'):
            deploy_vars['k8s_enable_gpudirect_rdma_a4'] = True
        else:
            deploy_vars['k8s_enable_gpudirect_rdma_a4'] = False

        deploy_vars['k8s_ipc_lock_capability'] = (
            network_type.requires_ipc_lock_capability())

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
                    cloud=self.__class__(),
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
    def _check_single_context(cls, context: str) -> Tuple[bool, str]:
        """Check if the user has access credentials to a single SSH context."""

        def _red_color(str_to_format: str) -> str:
            return (f'{colorama.Fore.LIGHTRED_EX}'
                    f'{str_to_format}'
                    f'{colorama.Style.RESET_ALL}')

        def _dim_color(str_to_format: str) -> str:
            return (f'{colorama.Style.DIM}'
                    f'{str_to_format}'
                    f'{colorama.Style.RESET_ALL}')

        def _bright_green_color(str_to_format: str) -> str:
            return (f'{colorama.Fore.GREEN}'
                    f'{str_to_format}'
                    f'{colorama.Style.RESET_ALL}')

        try:
            check_result = kubernetes_utils.check_credentials(
                context, run_optional_checks=True)
            if check_result[0]:
                if check_result[1] is not None:
                    return True, (_bright_green_color('enabled.') +
                                  _dim_color(f' Note: {check_result[1]}'))
                else:
                    return True, _bright_green_color('enabled.')
            else:
                assert check_result[1] is not None
                return False, (_red_color('disabled.') +
                               _dim_color(f' Reason: {check_result[1]}'))
        except Exception as e:  # pylint: disable=broad-except
            return False, _red_color(str(e))

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        Kubernetes."""
        # Check for port forward dependencies
        logger.debug(f'Checking compute credentials for {cls.canonical_name()}')
        reasons = kubernetes_utils.check_port_forward_mode_dependencies(False)
        if reasons is not None:
            formatted = '\n'.join(
                [reasons[0]] +
                [f'{cls._INDENT_PREFIX}' + r for r in reasons[1:]])
            return (False, formatted)

        # Test using python API
        try:
            existing_allowed_contexts = cls.existing_allowed_contexts()
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

        ctx2text = {}
        success = False
        for context in existing_allowed_contexts:
            suc, text = cls._check_single_context(context)
            success = success or suc
            ctx2text[context] = text

        return success, ctx2text

    @classmethod
    def _format_credential_check_results(cls, hints: List[str],
                                         reasons: List[str]) -> str:
        """Format credential check results with hints and reasons.

        Args:
            hints: List of successful context check messages.
            reasons: List of failed context check reasons.

        Returns:
            A formatted string containing hints and by failure reasons.
        """
        message_parts = []
        if len(hints) == 1 and not reasons:
            return hints[0]
        if hints:
            message_parts.append(f'\n{cls._INDENT_PREFIX}  ' +
                                 f'\n{cls._INDENT_PREFIX}  '.join(hints))
        if reasons:
            if hints:
                message_parts.append('\n')
            message_parts.append(
                f'\n{cls._INDENT_PREFIX}Unavailable contexts (remove from '
                '"allowed_contexts" config if permanently unavailable): '
                f'\n{cls._INDENT_PREFIX}  ' +
                f'\n{cls._INDENT_PREFIX}  '.join(reasons))
        return ''.join(message_parts)

    def get_credential_file_mounts(self) -> Dict[str, str]:
        credential_paths = kubernetes_utils.get_kubeconfig_paths()
        if credential_paths:
            # For single kubeconfig path, keep the original path.
            kubeconfig_file = credential_paths[0]
            if len(credential_paths) > 1:
                # For multiple kubeconfig paths, merge them into a single file.
                # TODO(aylei): GC merged kubeconfig files.
                kubeconfig_file = tempfile.NamedTemporaryFile(
                    prefix='merged-kubeconfig-', suffix='.yaml',
                    delete=False).name
                subprocess.run(
                    'kubectl config view --flatten '
                    f'> {kubeconfig_file}',
                    shell=True,
                    check=True)
            if os.path.exists(kubeconfig_file):
                # convert auth plugin paths (e.g.: gke-gcloud-auth-plugin)
                kubeconfig_file = kubernetes_utils.format_kubeconfig_exec_auth_with_cache(kubeconfig_file)  # pylint: disable=line-too-long

            # Upload kubeconfig to the default path to avoid having to set
            # KUBECONFIG in the environment.
            return {kubernetes.DEFAULT_KUBECONFIG_PATH: kubeconfig_file}
        else:
            return {}

    def instance_type_exists(self, instance_type: str) -> bool:
        return kubernetes_utils.KubernetesInstanceType.is_valid_instance_type(
            instance_type)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        if region == kubernetes.in_cluster_context_name():
            # If running incluster, we set region to IN_CLUSTER_REGION
            # since there is no context name available.
            return region, zone

        all_contexts = kubernetes_utils.get_all_kube_context_names()

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
        identities = []
        k8s = kubernetes.kubernetes
        try:
            all_contexts, current_context = (
                kubernetes.list_kube_config_contexts())
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

    @classmethod
    def expand_infras(cls) -> List[str]:
        return [
            f'{cls.canonical_name()}/{c}'
            for c in cls.existing_allowed_contexts(silent=True)
        ]

    @classmethod
    def _detect_network_type(
        cls,
        context: str,
        network_tier: Optional['resources_utils.NetworkTier'] = None
    ) -> Tuple[KubernetesHighPerformanceNetworkType, str]:
        """Detect the type of Kubernetes network based on node labels.

        Args:
            context: The Kubernetes context to check.
            network_tier: The network tier requested. If None or not BEST,
                         returns NONE (no high-performance networking).

        Returns:
            A tuple of the detected network type and the instance type.
        """
        # If network_tier is None or not BEST, return NONE
        if (network_tier is None or
                network_tier != resources_utils.NetworkTier.BEST):
            return KubernetesHighPerformanceNetworkType.NONE, ''

        try:
            nodes = kubernetes_utils.get_kubernetes_nodes(context=context)
            for node in nodes:
                if node.metadata.labels:
                    # Check for Nebius clusters
                    for label_key, _ in node.metadata.labels.items():
                        if label_key.startswith('nebius.com/'):
                            return (KubernetesHighPerformanceNetworkType.NEBIUS,
                                    '')

                    # Check for GKE clusters with specific GPUDirect variants
                    machine_family = node.metadata.labels.get(
                        'cloud.google.com/machine-family', '')
                    instance_type = node.metadata.labels.get(
                        'node.kubernetes.io/instance-type', '')
                    gke_accelerator = node.metadata.labels.get(
                        'cloud.google.com/gke-accelerator', '')

                    # Check if this is a GKE cluster with A3/A4 machine family
                    if machine_family in ['a3', 'a4']:
                        # Check instance type to determine specific GPUDirect
                        # variant
                        if 'a3-highgpu-8g' in instance_type:
                            return (
                                KubernetesHighPerformanceNetworkType.GCP_TCPX,
                                'a3-highgpu-8g')
                        elif 'a3-edgegpu-8g' in instance_type:
                            return (
                                KubernetesHighPerformanceNetworkType.GCP_TCPX,
                                'a3-edgegpu-8g')
                        elif 'a3-megagpu-8g' in instance_type:
                            return (
                                KubernetesHighPerformanceNetworkType.GCP_TCPXO,
                                'a3-megagpu-8g')
                        elif 'a4-highgpu-8g' in instance_type:
                            return (KubernetesHighPerformanceNetworkType.
                                    GCP_GPUDIRECT_RDMA, 'a4-highgpu-8g')
                        elif 'a3-ultragpu-8g' in instance_type:
                            return (KubernetesHighPerformanceNetworkType.
                                    GCP_GPUDIRECT_RDMA, 'a3-ultragpu-8g')
                        # Generic A3/A4 detection as fallback
                        elif machine_family == 'a4':
                            return (KubernetesHighPerformanceNetworkType.
                                    GCP_GPUDIRECT_RDMA, 'a4')

                    # Fallback: Check for GPU Direct TCPX capable instance
                    # types with high-perf GPUs
                    is_gpu_direct_tcpx_instance = (
                        instance_type
                        in gcp_constants.GPU_DIRECT_TCPX_INSTANCE_TYPES)
                    has_high_perf_gpu = ('nvidia-h100' in gke_accelerator or
                                         'nvidia-h200' in gke_accelerator or
                                         'nvidia-b200' in gke_accelerator)

                    if is_gpu_direct_tcpx_instance and has_high_perf_gpu:
                        # Default to TCPX if we can't determine the specific
                        # variant
                        return (KubernetesHighPerformanceNetworkType.GCP_TCPX,
                                instance_type)

        except exceptions.KubeAPIUnreachableError:
            # If we can't reach the cluster, assume no high perf networking
            pass

        # If we cannot determine the network type based on nodes
        # Check if the cluster has any node pools with autoscaling enabled
        # with machine types that support high perf networking for GKE.
        autoscaler_type = skypilot_config.get_effective_region_config(
            cloud='kubernetes',
            region=context,
            keys=('autoscaler',),
            default_value=None)
        if (autoscaler_type !=
                kubernetes_enums.KubernetesAutoscalerType.GKE.value):
            return KubernetesHighPerformanceNetworkType.NONE, ''
        autoscaler = kubernetes_utils.get_autoscaler(
            kubernetes_enums.KubernetesAutoscalerType(autoscaler_type))
        logger.debug(f'{context} has autoscaler of type: {autoscaler_type}')
        machine_types = autoscaler.get_available_machine_types(context)
        # Check if any machine type supports high perf networking for GKE.
        if 'a3-highgpu-8g' in machine_types:
            return (KubernetesHighPerformanceNetworkType.GCP_TCPX,
                    'a3-highgpu-8g')
        elif 'a3-edgegpu-8g' in machine_types:
            return (KubernetesHighPerformanceNetworkType.GCP_TCPX,
                    'a3-edgegpu-8g')
        elif 'a3-megagpu-8g' in machine_types:
            return (KubernetesHighPerformanceNetworkType.GCP_TCPXO,
                    'a3-megagpu-8g')
        elif 'a4-highgpu-8g' in machine_types:
            return (KubernetesHighPerformanceNetworkType.GCP_GPUDIRECT_RDMA,
                    'a4-highgpu-8g')
        elif 'a3-ultragpu-8g' in machine_types:
            return (KubernetesHighPerformanceNetworkType.GCP_GPUDIRECT_RDMA,
                    'a3-ultragpu-8g')

        return KubernetesHighPerformanceNetworkType.NONE, ''

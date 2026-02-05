"""Volume functions for provisioning and deleting ephemeral volumes."""

import copy
from typing import Any, Dict, Optional

from sky import clouds
from sky import global_user_state
from sky import models
from sky import sky_logging
from sky.provision import common as provision_common
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import volume as volume_utils
from sky.volumes import volume as volume_lib
from sky.volumes.server import core as volume_server_core

logger = sky_logging.init_logger(__name__)


def _resolve_volume_type(cloud: clouds.Cloud,
                         volume_type: Optional[str]) -> str:
    if not volume_type:
        volume_types = None
        for cloud_key, vol_types in volume_lib.CLOUD_TO_VOLUME_TYPE.items():
            if cloud.is_same_cloud(cloud_key):
                volume_types = vol_types
                break
        if volume_types is None:
            raise ValueError(f'No default volume type found for cloud {cloud}')
        if len(volume_types) != 1:
            raise ValueError(
                f'Found multiple volume types for cloud {cloud}: {volume_types}'
            )
        return volume_types[0].value
    supported_volume_types = [
        volume_type.value for volume_type in volume_utils.VolumeType
    ]
    volume_type = volume_type.lower()
    if volume_type not in supported_volume_types:
        raise ValueError(
            f'Invalid volume type: {volume_type} for cloud {cloud}')
    return volume_type


def _resolve_pvc_volume_config(cloud: clouds.Cloud,
                               config: provision_common.ProvisionConfig,
                               volume_config: Dict[str, Any]) -> Dict[str, Any]:
    provider_config = config.provider_config
    if not cloud.is_same_cloud(clouds.Kubernetes()):
        raise ValueError(
            f'PVC volume type is only supported on Kubernetes not on {cloud}')
    supported_access_modes = [
        access_mode.value for access_mode in volume_utils.VolumeAccessMode
    ]
    access_mode = volume_config.get('access_mode')
    if access_mode is None:
        access_mode = volume_utils.VolumeAccessMode.READ_WRITE_ONCE.value
        volume_config['access_mode'] = access_mode
    elif access_mode not in supported_access_modes:
        raise ValueError(f'Invalid access mode: {access_mode} for PVC')
    if (access_mode == volume_utils.VolumeAccessMode.READ_WRITE_ONCE.value and
            config.count > 1):
        raise ValueError(
            'Access mode ReadWriteOnce is not supported for multi-node'
            ' clusters.')
    namespace = kubernetes_utils.get_namespace_from_config(provider_config)
    volume_config['namespace'] = namespace
    return volume_config


def _create_ephemeral_volume(
    cloud: clouds.Cloud, region: str, cluster_name_on_cloud: str,
    config: provision_common.ProvisionConfig,
    volume_mount: volume_utils.VolumeMount
) -> Optional[volume_utils.VolumeInfo]:
    provider_name = repr(cloud)
    path = volume_mount.path
    volume_config = volume_mount.volume_config
    volume_type = _resolve_volume_type(cloud, volume_config.type)
    labels = volume_config.labels
    if volume_type == volume_utils.VolumeType.PVC.value:
        internal_volume_config = _resolve_pvc_volume_config(
            cloud, config, volume_config.config)
        if labels:
            for key, value in labels.items():
                valid, err_msg = cloud.is_label_valid(key, value)
                if not valid:
                    raise ValueError(f'{err_msg}')
        else:
            labels = {}
        labels.update({
            provision_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud
        })
    else:
        logger.warning(f'Skipping unsupported ephemeral volume type: '
                       f'{volume_type} for cloud {cloud}.')
        return None
    volume_name = volume_config.name
    volume_server_core.volume_apply(
        name=volume_name,
        volume_type=volume_type,
        cloud=provider_name,
        region=region,
        zone=None,
        size=volume_config.size,
        config=internal_volume_config,
        labels=labels,
        is_ephemeral=True,
    )
    volume = global_user_state.get_volume_by_name(volume_name)
    if volume is None:
        raise ValueError(f'Failed to get record for volume: {volume_name}')
    assert 'handle' in volume, 'Volume handle is None.'
    volume_config: models.VolumeConfig = volume['handle']
    volume_info = volume_utils.VolumeInfo(
        name=volume_name,
        path=path,
        volume_name_on_cloud=volume_config.name_on_cloud,
        volume_id_on_cloud=volume_config.id_on_cloud,
    )
    return volume_info


def provision_ephemeral_volumes(
    cloud: clouds.Cloud,
    region: str,
    cluster_name_on_cloud: str,
    config: provision_common.ProvisionConfig,
) -> None:
    """Provision ephemeral volumes for a cluster."""
    provider_config = config.provider_config
    ephemeral_volume_mounts = provider_config.get('ephemeral_volume_specs')
    if not ephemeral_volume_mounts:
        return
    volume_infos = []
    try:
        for ephemeral_volume_mount in ephemeral_volume_mounts:
            mount_copy = copy.deepcopy(ephemeral_volume_mount)
            volume_mount = volume_utils.VolumeMount.from_yaml_config(mount_copy)
            volume_info = _create_ephemeral_volume(cloud, region,
                                                   cluster_name_on_cloud,
                                                   config, volume_mount)
            if volume_info is None:
                continue
            volume_infos.append(volume_info)
        provider_config['ephemeral_volume_infos'] = volume_infos
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(f'Failed to provision ephemeral volumes: {e}')
        raise e


def delete_ephemeral_volumes(provider_config: Dict[str, Any],) -> None:
    """Provision ephemeral volumes for a cluster."""
    ephemeral_volume_mounts = provider_config.get('ephemeral_volume_specs')
    if not ephemeral_volume_mounts:
        return
    ephemeral_volume_names = []
    for ephemeral_volume_mount in ephemeral_volume_mounts:
        mount_copy = copy.deepcopy(ephemeral_volume_mount)
        volume_mount = volume_utils.VolumeMount.from_yaml_config(mount_copy)
        volume_name = volume_mount.volume_config.name
        ephemeral_volume_names.append(volume_name)
    volume_server_core.volume_delete(names=ephemeral_volume_names,
                                     ignore_not_found=True)

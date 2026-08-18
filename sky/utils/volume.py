"""Volume utilities."""
from dataclasses import dataclass
import enum
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import exceptions
from sky import global_user_state
from sky import models
from sky import skypilot_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import status_lib

MIN_RUNPOD_NETWORK_VOLUME_SIZE_GB = 10


class VolumeAccessMode(enum.Enum):
    """Volume access mode."""
    READ_WRITE_ONCE = 'ReadWriteOnce'
    READ_WRITE_ONCE_POD = 'ReadWriteOncePod'
    READ_WRITE_MANY = 'ReadWriteMany'
    READ_ONLY_MANY = 'ReadOnlyMany'


class VolumeMountMode(enum.Enum):
    """Per-mount permission for host path bind mounts."""
    RO = 'ro'
    RW = 'rw'


class VolumeType(enum.Enum):
    """Volume type."""
    PVC = 'k8s-pvc'
    HOSTPATH = 'k8s-hostpath'
    RUNPOD_NETWORK_VOLUME = 'runpod-network-volume'

    @classmethod
    def supported_types(cls) -> list:
        """Return list of supported volume type values."""
        return [vt.value for vt in cls]


EPHEMERAL_VOLUME_TYPES = [VolumeType.PVC.value]


class AutoMountScope(enum.Enum):
    """Scope of an auto_mounts config entry.

    Controls whose launches an auto-mount volume is mounted onto,
    mirroring the personal/workspace/global scopes used by secrets.
    """
    # Mount only on launches by the user who owns the volume.
    PERSONAL = 'personal'
    # Mount only on launches in the volume's workspace.
    WORKSPACE = 'workspace'
    # Mount on every launch (default; the original auto_mounts behavior).
    GLOBAL = 'global'

    @classmethod
    def supported_scopes(cls) -> list:
        """Return list of supported scope values."""
        return [s.value for s in cls]


def auto_mount_in_scope(scope: str, volume_user_hash: Optional[str],
                        volume_workspace: Optional[str],
                        current_user_hash: Optional[str],
                        active_workspace: Optional[str]) -> bool:
    """Whether an auto-mount entry applies to the current launch.

    Args:
        scope: The entry's scope ('personal', 'workspace', or 'global').
        volume_user_hash: user_hash of the volume record (its owner).
        volume_workspace: workspace of the volume record.
        current_user_hash: user hash of the user performing the launch.
        active_workspace: workspace the launch is running in.

    Returns:
        True if the volume should be auto-mounted onto this launch.

    Raises:
        ValueError: if scope is not a supported scope value.
    """
    if scope == AutoMountScope.GLOBAL.value:
        return True
    if scope == AutoMountScope.PERSONAL.value:
        return (volume_user_hash is not None and
                volume_user_hash == current_user_hash)
    if scope == AutoMountScope.WORKSPACE.value:
        return (volume_workspace is not None and
                volume_workspace == active_workspace)
    raise ValueError(f'Invalid auto-mount scope {scope!r}. Supported '
                     f'scopes: {AutoMountScope.supported_scopes()}')


def is_read_write_many_pvc(volume_config: models.VolumeConfig) -> bool:
    """Whether a volume is a PVC that may take minutes to provision.

    ReadWriteMany PVCs are backed by a network filesystem (GKE Filestore, EFS,
    ...), which is the slow case the Kubernetes provision timeout has to
    accommodate.
    """
    return (volume_config.type == VolumeType.PVC.value and
            volume_config.config.get('access_mode')
            == VolumeAccessMode.READ_WRITE_MANY.value)


# The one reason a volume can be recorded not-ready and still become usable on
# its own: a StorageClass that binds Immediately starts provisioning when the
# claim is created, so the claim is Pending for as long as the backend takes,
# and a network filesystem takes minutes. `_get_pvc_error` builds its message
# from this, and `volume_error_may_resolve` reads it back.
PVC_PROVISIONING_MESSAGE = ('PVC is pending: the PersistentVolume is still '
                            'being provisioned.')


def volume_error_may_resolve(error_message: Optional[str]) -> bool:
    """Whether a not-ready volume may still become usable without a change.

    Refusing a launch over a volume is for volumes that will not work until
    someone fixes them. This tells that case from the one where waiting is all
    that is needed, so a launch is not refused over the minutes a network
    filesystem legitimately takes.

    A recorded message with no reason to expect it to resolve is read as needing
    a change -- including one carrying no gRPC code at all, e.g. an access mode
    the available PersistentVolumes do not support, which is permanent. Deciding
    the other way round (refuse only what is provably terminal) would let that
    class of misconfiguration through.
    """
    if not error_message:
        return False
    return PVC_PROVISIONING_MESSAGE in error_message


def mount_is_read_write_many_pvc(volume_mount: 'VolumeMount') -> bool:
    """`is_read_write_many_pvc` for a volume declared on a task.

    An ephemeral volume's type is only resolved when it is provisioned
    (`sky.provision.volume._resolve_volume_type`), which happens after the
    provision timeout has been computed. On Kubernetes it can only resolve to
    a PVC (`EPHEMERAL_VOLUME_TYPES`), so an unset type is read as one.
    """
    volume_config = volume_mount.volume_config
    if volume_mount.is_ephemeral and not volume_config.type:
        return (volume_config.config.get('access_mode') ==
                VolumeAccessMode.READ_WRITE_MANY.value)
    return is_read_write_many_pvc(volume_config)


@dataclass
class AutoMount:
    """An `auto_mounts` config entry that a launch will mount."""
    volume_name: str
    # The volume's row in the volume DB, as returned by
    # `global_user_state.get_volume_by_name`.
    record: Dict[str, Any]
    # The entry's mount_paths, unexpanded (`~` is resolved against the image's
    # home directory, which only the provisioning code knows).
    mount_paths: List[str]

    @property
    def volume_config(self) -> models.VolumeConfig:
        return self.record['handle']


@dataclass
class SkippedAutoMount:
    """An `auto_mounts` config entry that a launch will not mount."""
    volume_name: str
    message: str
    # A missing volume or an unusable access mode is a misconfiguration the
    # user has to see; an out-of-scope entry is normal operation.
    is_warning: bool


@dataclass
class AutoMountResolution:
    """Which `auto_mounts` entries apply to a launch, and which do not."""
    mounted: List[AutoMount]
    skipped: List[SkippedAutoMount]


def resolve_auto_mounts(region: Optional[str]) -> AutoMountResolution:
    """Resolves which `auto_mounts` volumes a launch will mount.

    Applies the three filters that decide whether an entry is mounted at all:
    the volume exists, its scope covers this launch, and its access mode
    permits the concurrent multi-pod access auto-mounting implies.

    Readiness is deliberately not checked here. Refusing a launch belongs to
    the injection path, and this also runs while computing the provision
    timeout, which must not raise. Nor does this log: it runs more than once
    per launch, so the caller on the launch path logs `skipped` and the others
    ignore it.

    `auto_mounts` is a Kubernetes config key; `region` is the context whose
    effective config to read.
    """
    auto_mounts_config = skypilot_config.get_effective_region_config(
        cloud='kubernetes',
        region=region,
        keys=('auto_mounts',),
        default_value=None)
    if not auto_mounts_config:
        return AutoMountResolution(mounted=[], skipped=[])

    mounted: List[AutoMount] = []
    skipped: List[SkippedAutoMount] = []
    current_user_hash = common_utils.get_current_user().id
    active_workspace = skypilot_config.get_active_workspace()
    for entry in auto_mounts_config:
        volume_name = entry['volume_name']
        record = global_user_state.get_volume_by_name(volume_name)
        if record is None:
            skipped.append(
                SkippedAutoMount(
                    volume_name,
                    f'Auto-mount volume {volume_name!r} not found in SkyPilot '
                    f'volume DB. Skipping. Create it with: sky volumes apply',
                    is_warning=True))
            continue
        scope = entry.get('scope', AutoMountScope.GLOBAL.value)
        if not auto_mount_in_scope(scope,
                                   volume_user_hash=record['user_hash'],
                                   volume_workspace=record['workspace'],
                                   current_user_hash=current_user_hash,
                                   active_workspace=active_workspace):
            skipped.append(
                SkippedAutoMount(
                    volume_name,
                    f'Auto-mount volume {volume_name!r} has scope {scope!r} '
                    f'and does not apply to this launch (user '
                    f'{current_user_hash!r}, workspace {active_workspace!r}). '
                    f'Skipping.',
                    is_warning=False))
            continue
        volume_config = record['handle']
        # Only hostPath and ReadWriteMany PVC volumes support the concurrent
        # multi-pod access auto_mounts requires.
        if (volume_config.type == VolumeType.PVC.value and
                not is_read_write_many_pvc(volume_config)):
            skipped.append(
                SkippedAutoMount(
                    volume_name,
                    f'Auto-mount volume {volume_name!r} has access mode '
                    f'{volume_config.config.get("access_mode")!r}, which does '
                    f'not support concurrent multi-pod access. Only hostPath '
                    f'volumes and ReadWriteMany PVC volumes are supported for '
                    f'auto_mounts. Skipping.',
                    is_warning=True))
            continue
        mounted.append(
            AutoMount(volume_name=volume_name,
                      record=record,
                      mount_paths=entry.get('mount_paths', [])))
    return AutoMountResolution(mounted=mounted, skipped=skipped)


@dataclass
class VolumeInfo:
    """Represents volume info."""
    name: str
    path: str
    volume_name_on_cloud: Optional[str] = None
    volume_id_on_cloud: Optional[str] = None
    sub_path: Optional[str] = None
    volume_type: Optional[str] = None
    host_path: Optional[str] = None


class VolumeMount:
    """Volume mount specification."""

    def __init__(self,
                 path: str,
                 volume_name: str,
                 volume_config: models.VolumeConfig,
                 is_ephemeral: bool = False,
                 sub_path: Optional[str] = None):
        self.path: str = path
        self.volume_name: str = volume_name
        self.volume_config: models.VolumeConfig = volume_config
        self.is_ephemeral: bool = is_ephemeral
        self.sub_path: Optional[str] = sub_path

    def pre_mount(self) -> None:
        """Update the volume status before actual mounting."""
        # Inline and ephemeral volumes have no global volume record.
        if not self.volume_name:
            return
        # TODO(aylei): for ReadWriteOnce volume, we also need to queue the
        # mount request if the target volume is already mounted to another
        # cluster. For now, we only support ReadWriteMany volume.
        global_user_state.update_volume(self.volume_name,
                                        last_attached_at=int(time.time()),
                                        status=status_lib.VolumeStatus.IN_USE)

    @classmethod
    def resolve(cls,
                path: str,
                volume_name: str,
                sub_path: Optional[str] = None) -> 'VolumeMount':
        """Resolve the volume mount by populating metadata of volume."""
        if sub_path is not None:
            if not re.match(constants.SUB_PATH_PATTERN, sub_path):
                raise ValueError(
                    f'sub_path contains invalid characters: {sub_path!r}. '
                    'Must be a relative path containing only '
                    'alphanumeric characters, dots, slashes, '
                    'underscores and hyphens, and must not start '
                    'with a slash.')
            if '..' in sub_path.split('/'):
                raise ValueError(
                    f'sub_path must not contain directory traversal '
                    f'(..): {sub_path!r}')
        record = global_user_state.get_volume_by_name(volume_name)
        if record is None:
            raise exceptions.VolumeNotFoundError(
                f'Volume {volume_name} not found.')
        if record.get('status') == status_lib.VolumeStatus.NOT_READY:
            error_message = record.get('error_message')
            # Same rule as the check that runs on every launch (see
            # `_reject_not_ready_volume`): a volume that is being provisioned is
            # not-ready and is not a reason to refuse.
            if not volume_error_may_resolve(error_message):
                msg = f'Volume {volume_name} is not ready.'
                if error_message:
                    msg += f' Error: {error_message}'
                raise exceptions.VolumeNotReadyError(msg)
        assert 'handle' in record, 'Volume handle is None.'
        volume_config: models.VolumeConfig = record['handle']
        return cls(path, volume_name, volume_config, sub_path=sub_path)

    @classmethod
    def resolve_host_path_config(cls, path: str,
                                 config: Dict[str, Any]) -> 'VolumeMount':
        """Create a non-provisioned host path mount from inline config."""
        host_path = config.get('host_path')
        if not isinstance(host_path, str) or not host_path.startswith('/'):
            raise ValueError(
                f'host_path must be an absolute path, got: {host_path!r}')
        if host_path == '/':
            raise ValueError('host_path must not be the root directory \'/\'')
        mode = config.get('mode', VolumeMountMode.RO.value)
        if mode not in (VolumeMountMode.RO.value, VolumeMountMode.RW.value):
            raise ValueError(f'Invalid host_path volume mode {mode!r}. '
                             'Supported modes are "ro" and "rw".')
        unexpected_fields = set(config) - {'host_path', 'mode'}
        if unexpected_fields:
            raise ValueError(f'Invalid host_path volume config fields: '
                             f'{sorted(unexpected_fields)}')
        volume_config = models.VolumeConfig(name='',
                                            type='',
                                            cloud='Slurm',
                                            region=None,
                                            zone=None,
                                            name_on_cloud=host_path,
                                            size=None,
                                            config={
                                                'host_path': host_path,
                                                'mode': mode,
                                            })
        return cls(path, '', volume_config)

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'VolumeMount':
        common_utils.validate_schema(config, schemas.get_volume_mount_schema(),
                                     'Invalid volume mount config: ')

        path = config.pop('path', None)
        volume_name = config.pop('volume_name', None)
        is_ephemeral = config.pop('is_ephemeral', False)
        sub_path = config.pop('sub_path', None)
        volume_config: models.VolumeConfig = models.VolumeConfig.model_validate(
            config.pop('volume_config', None))
        return cls(path,
                   volume_name,
                   volume_config,
                   is_ephemeral,
                   sub_path=sub_path)

    @classmethod
    def resolve_ephemeral_config(cls, path: str,
                                 config: Dict[str, Any]) -> 'VolumeMount':
        """Create an ephemeral volume mount from inline config.

        Args:
            path: The mount path for the volume.
            config: The volume configuration dict with size, and
                optional type, labels, and config fields, etc.

        Returns:
            A VolumeMount instance for the ephemeral volume.
        """
        volume_type = config.get('type')
        if volume_type and volume_type.lower() not in EPHEMERAL_VOLUME_TYPES:
            raise ValueError(f'Unsupported ephemeral volume type: '
                             f'{volume_type}. Supported types: '
                             f'{", ".join(EPHEMERAL_VOLUME_TYPES)}')
        size_config = config.get('size')
        if size_config is None:
            raise ValueError('Volume size must be specified for ephemeral '
                             'volumes.')
        try:
            size = resources_utils.parse_memory_resource(size_config,
                                                         'size',
                                                         allow_rounding=True)
            if size == '0':
                raise ValueError('Size must be no less than 1Gi')
        except ValueError as e:
            raise ValueError(
                f'Invalid size {size_config} for ephemeral volume: {e}') from e

        # Create VolumeConfig for ephemeral volume
        # Note: the empty fields will be populated during provisioning
        volume_config = models.VolumeConfig(
            name='',
            type=config.get('type', ''),
            # Default to kubernetes cloud here for backward compatibility,
            # but this will be reset to the correct cloud during provisioning.
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='',
            size=size,
            config=config.get('config', {}),
            labels=config.get('labels'),
        )

        return cls(path, '', volume_config, is_ephemeral=True)

    def to_yaml_config(self) -> Dict[str, Any]:
        config = {
            'path': self.path,
            'volume_name': self.volume_name,
            'volume_config': self.volume_config.model_dump(),
            'is_ephemeral': self.is_ephemeral,
        }
        if self.sub_path is not None:
            config['sub_path'] = self.sub_path
        return config

    @property
    def name(self) -> str:
        """Return the volume name for use in provisioning."""
        return self.volume_name

    def __repr__(self):
        return (f'VolumeMount('
                f'\n\tpath={self.path},'
                f'\n\tvolume_name={self.volume_name},'
                f'\n\tis_ephemeral={self.is_ephemeral},'
                f'\n\tsub_path={self.sub_path},'
                f'\n\tvolume_config={self.volume_config})')


class VolumeMountConflictChecker:
    """Detects conflicts between volume mounts from different sources.

    Three checks are performed as each volume mount is registered:
    1. Mount path uniqueness — no two mounts can share the same path.
    2. Volume name consistency — if two mounts share a volume name,
       they must reference the same underlying volume (same PVC claim
       or host directory).  Otherwise the template's name-based dedup
       silently drops one volume definition.
    3. Same PVC from different volume entries — different volume names
       pointing to the same PVC is almost certainly a misconfiguration.
       Refer to https://github.com/kubernetes/kubernetes/issues/127004.
    """

    def __init__(self):
        # mount_path -> (source, volume_desc)
        self._seen_mount_paths: Dict[str, Tuple[str, str]] = {}
        # volume_name -> (source, volume_desc, vol_source_id)
        self._seen_volume_names: Dict[str, Tuple[str, str, Optional[str]]] = {}
        # volume_name_on_cloud -> (volume_name, source, volume_desc)
        self._seen_pvcs: Dict[str, Tuple[str, str, str]] = {}

    @staticmethod
    def _get_vol_source_identity(
            volume_type: Optional[str],
            vol_name_on_cloud: Optional[str] = None,
            vol_host_path: Optional[str] = None) -> Optional[str]:
        """Return a type-aware volume source identity string.

        Each volume type defines its own identity key. Returns None if
        the type is unknown or the identity cannot be determined (e.g.,
        ephemeral volumes before provisioning).
        """
        if volume_type == VolumeType.PVC.value and vol_name_on_cloud:
            return f'pvc:{vol_name_on_cloud}'
        if volume_type == VolumeType.HOSTPATH.value and vol_host_path:
            return f'hostpath:{vol_host_path}'
        return None

    def check(self, vol: VolumeInfo, source: str, volume_desc: str) -> None:
        """Check for volume mount conflicts and register the entry.

        Args:
            vol: Volume info to check.
            source: Where the volume came from (e.g. 'task YAML volumes',
                'auto_mounts config').
            volume_desc: Human-readable description for error messages.

        Raises ValueError on conflict with a message identifying both
        conflicting volumes and their sources.
        """
        # Check 1: Mount path uniqueness
        if vol.path in self._seen_mount_paths:
            prev_source, prev_desc = self._seen_mount_paths[vol.path]
            raise ValueError(
                f'Volume mount path conflict: {vol.path!r} is '
                f'mounted by {volume_desc} (from {source}) and also '
                f'by {prev_desc} (from {prev_source}). '
                f'Please remove the duplicate from your task YAML '
                f'volumes config or auto_mounts config.')
        self._seen_mount_paths[vol.path] = (source, volume_desc)

        if not vol.name:
            return

        # Check 2: Volume name consistency.
        # Volume definitions are deduplicated by name but volume mounts
        # are not. If two entries share the same name but reference
        # different volume sources, one volume definition is silently
        # dropped.
        vol_source_id = self._get_vol_source_identity(vol.volume_type,
                                                      vol.volume_name_on_cloud,
                                                      vol.host_path)
        if vol.name in self._seen_volume_names:
            prev_src, prev_desc, prev_vol_source_id = (
                self._seen_volume_names[vol.name])
            # If identity is None (unknown type or ephemeral), we
            # cannot confirm they are the same volume, so treat same
            # name as a conflict.
            if (vol_source_id is None or prev_vol_source_id is None or
                    vol_source_id != prev_vol_source_id):
                raise ValueError(
                    f'Volume name conflict: volume name '
                    f'{vol.name!r} is used by {volume_desc} '
                    f'(from {source}) and by {prev_desc} '
                    f'(from {prev_src}), but they reference different '
                    f'volumes. Please remove the duplicate from your task '
                    f'YAML volumes config or auto_mounts config.')
            # Same name, same volume source: OK (e.g. auto_mount with
            # multiple mount_paths).
            return
        self._seen_volume_names[vol.name] = (source, volume_desc, vol_source_id)

        # Check 3: Same PVC from different volume entries.
        if (vol.volume_type == VolumeType.PVC.value and
                vol.volume_name_on_cloud):
            if vol.volume_name_on_cloud in self._seen_pvcs:
                prev_vol_name, prev_src, prev_desc = (
                    self._seen_pvcs[vol.volume_name_on_cloud])
                if prev_vol_name != vol.name:
                    raise ValueError(
                        f'Volume PVC conflict: PVC '
                        f'{vol.volume_name_on_cloud!r} is referenced '
                        f'by {volume_desc} (from {source}) and also '
                        f'by {prev_desc} (from {prev_src}). If you '
                        f'need to mount different sub-paths of the '
                        f'same PVC, use a single volume entry with '
                        f'sub_path. Please remove the '
                        f'duplicate from your task YAML volumes '
                        f'config or auto_mounts config.')
            self._seen_pvcs[vol.volume_name_on_cloud] = (vol.name, source,
                                                         volume_desc)

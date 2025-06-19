"""Volume types and access modes."""
import enum


class VolumeType(enum.Enum):
    """Volume type."""
    PVC = 'pvc'
    BLOCK_STORAGE = 'block_storage'
    OBJECT_STORAGE = 'object_storage'
    FILE_SYSTEM = 'file_system'


class VolumeAccessMode(enum.Enum):
    """Volume access mode."""
    READ_WRITE_ONCE = 'ReadWriteOnce'
    READ_WRITE_MANY = 'ReadWriteMany'
    READ_ONLY_MANY = 'ReadOnlyMany'

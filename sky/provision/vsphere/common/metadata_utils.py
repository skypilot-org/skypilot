"""vsphere metadata utils
"""
import json
import os
from typing import List, Optional

METADATA_PATH = '~/.vsphere/metadata'


class Metadata:
    """Metadata file for each cluster."""

    def __init__(self) -> None:
        """Initialize Metadata instance, loading existing metadata if present."""
        self.path: str = os.path.expanduser(METADATA_PATH)
        self.metadata: dict = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    self.metadata = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                raise RuntimeError(
                    f'Failed to load metadata from {self.path}: {e}'
                ) from e

    def get(self, cluster_name: str) -> Optional[List[str]]:
        """Get instance IDs for a cluster.

        Args:
            cluster_name: The name of the cluster.

        Returns:
            List of instance IDs or None if not found.
        """
        return self.metadata.get(cluster_name)

    def set(self, cluster_name: str, instance_ids: List[str]) -> None:
        """Set instance IDs for a cluster.

        Args:
            cluster_name: The name of the cluster.
            instance_ids: List of instance IDs to associate.
        """
        self.metadata[cluster_name] = instance_ids

    def pop(self, cluster_name: str) -> None:
        """Remove metadata for a cluster.

        Args:
            cluster_name: The name of the cluster to remove.
        """
        self.metadata.pop(cluster_name, None)

    def save(self) -> None:
        """Save metadata to file.

        Raises:
            IOError: If unable to write to the metadata file.
        """
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f)
        except IOError as e:
            raise RuntimeError(
                f'Failed to save metadata to {self.path}: {e}'
            ) from e

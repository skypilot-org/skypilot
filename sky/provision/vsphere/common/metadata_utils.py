"""vsphere metadata utils
"""
import json
import os
from typing import List, Optional

METADATA_PATH = '~/.vsphere/metadata'


class Metadata:
    """Metadata file for each cluster."""

    def __init__(self) -> None:
        self.path = os.path.expanduser(METADATA_PATH)
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {}

    def get(self, cluster_name: str) -> Optional[List[str]]:
        return self.metadata.get(cluster_name)

    def set(self, cluster_name: str, instance_ids: List[str]) -> None:
        self.metadata[cluster_name] = instance_ids

    def pop(self, cluster_name: str) -> None:
        self.metadata.pop(cluster_name, None)

    def save(self) -> None:
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f)

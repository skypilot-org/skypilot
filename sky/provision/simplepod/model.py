"""SimplePod models."""
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class Region:
    """SimplePod region."""
    id: str
    name: str
    country: str

@dataclass
class Instance:
    """SimplePod instance."""
    id: str
    name: str
    status: str
    gpu_type: str
    gpu_count: int
    private_ip: str
    public_ip: str
    region: str
    ssh_key: str

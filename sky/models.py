"""Data Models for SkyPilot."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    # User hash
    id: str
    # Display name of the user
    name: Optional[str] = None

"""Utility functions for resources."""
import enum
from typing import List


class DiskTier(enum.Enum):
    """All disk tiers supported by SkyPilot."""
    LOW = 'LOW'
    MEDIUM = 'MEDIUM'
    HIGH = 'HIGH'
    BEST = 'BEST'

    @classmethod
    def supported_tiers(cls) -> List[str]:
        return [tier.value for tier in cls]

    @classmethod
    def cli_help_message(cls) -> str:
        return (
            f'OS disk tier. Could be one of {", ".join(cls.supported_tiers())}'
            f'. if {cls.BEST.value} is specified, use the best possible disk '
            f'tier. Default: {cls.MEDIUM.value}'
        )

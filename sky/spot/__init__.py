"""Modules for managed spot clusters."""
from sky.spot.controller import SpotController
from sky.spot.recovery_strategy import SPOT_STRATEGIES

__all__ = [
    'SpotController',
    'SPOT_STRATEGIES',
]

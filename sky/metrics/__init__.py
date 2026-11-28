"""Prometheus metrics of the SkyPilot API server."""
from sky.metrics import value_lock

value_lock.install()

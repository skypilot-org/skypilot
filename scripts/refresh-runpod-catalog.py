#!/usr/bin/env python3
"""Refresh SkyPilot's RunPod catalog without exposing a partial cache."""

from sky.catalog.runpod_refresh import refresh_catalog

if __name__ == '__main__':
    refresh_catalog()

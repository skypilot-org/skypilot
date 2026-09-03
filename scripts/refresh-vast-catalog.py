#!/usr/bin/env python3
"""Refresh the Vast catalog using SkyPilot's catalog refresh worker."""

from sky.catalog import vast_refresh

if __name__ == '__main__':
    vast_refresh.refresh_catalog()

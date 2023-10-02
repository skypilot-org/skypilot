# Utility for Clouds

This folder contains the utility functions of the clouds which are required by both
the `sky.skylet.node_providers` and other modules in SkyPilot.

This is to avoid importing other unecessary modules in `sky.skylet.node_providers`, especially
`ray`, which will cause failure for clouds that has adopted the new provisioner #1702.

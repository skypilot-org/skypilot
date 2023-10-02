# Utility for Clouds

This folder contains the utility functions for clouds which are required by both
the `sky.skylet.providers` and other modules in SkyPilot.

This is to avoid importing other unnecessary modules in `sky.skylet.providers`.
When a utility file is placed under, e.g., `sky.skylet.providers.<cloud>`, and is
imported by other modules in SkyPilot, Python will import the `__init__.py` file in
the folder, which will then import
`sky.skylet.provider.<cloud>.node_provider`, causing the import of `ray`.
Importing `ray` will cause failure for clouds that have adopted the new provisioner
#1702 and removed the dependency of ray #2625.

"""Minimal version constants for managed jobs.

This module contains ONLY the version constant, with no other imports.
It exists to allow lightweight modules (like controller_api) to access
the version without triggering heavy imports from the full constants module.
"""
# The version of the lib files that jobs/utils use. Whenever there is an API
# change for the jobs/utils, we need to bump this version and update
# job.utils.ManagedJobCodeGen to handle the version update.
# WARNING: If you update this due to a codegen change, make sure to make the
# corresponding change in the ManagedJobsService AND bump the SKYLET_VERSION.
MANAGED_JOBS_VERSION = 12

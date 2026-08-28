"""Patch Ray modules.

The applier itself lives in apply_patches.py, which imports no ``sky`` so it
can also run from an unpacked copy of this directory -- see the module
docstring there for why the Kubernetes bootstrap needs that.

Each patched file has two representations that must stay in sync: a *.patch
(normal diff, for the system `patch` binary) and a *.diff (unified diff, for
the pure-python fallback). Regenerate both together, or images with and
without `patch` installed end up running different Ray code.

To get original versions, go to the Ray branch with version:

  sky.constants.SKY_REMOTE_RAY_VERSION

Example workflow:

  >> RAY_RAW=https://raw.githubusercontent.com/ray-project/ray/releases/2.4.0
  >> wget $RAY_RAW/python/ray/autoscaler/_private/command_runner.py
  >> cp command_runner.py command_runner.py.1

  >> # Make some edits to command_runner.py.1...

  >> diff command_runner.py command_runner.py.1 >command_runner.py.patch
  >> diff -u command_runner.py command_runner.py.1 >command_runner.py.diff
  >> # Rewrite the two header lines of the .diff to a/<name> and b/<name>.

  >> # Inspect command_runner.py.patch, then add it to apply_patches._PATCHES.
"""
from sky.skylet import constants
from sky.skylet.ray_patches import apply_patches


def patch() -> None:
    # Patch the buggy ray files. This should only be called from an isolated
    # python process, because once imported the python module would persist in
    # the memory.
    apply_patches.apply_patches(constants.SKY_REMOTE_RAY_VERSION)

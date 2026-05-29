"""Hot-patch for backward compat: pin click<8.3.0 in the old SkyPilot install.

Old SkyPilot versions installed from PyPI lack PR #9459's click<8.3.0 pin.
Without it, when the old sky launches a managed-jobs / serve controller, the
controller's skypilot-runtime venv resolves click to 8.3.x (typer 0.25.x
transitively requires click>=8.2.1 with no upper bound, so uv picks the
latest = 8.3.3). ray 2.9.3's `add_command_alias` then crashes on import:

    File ".../site-packages/ray/scripts/scripts.py", line 2440, in <module>
      add_command_alias(up, name="create_or_update", hidden=True)
    File ".../scripts.py", line 2430, in add_command_alias
      new_command = copy.deepcopy(command)
    ValueError: <object object at 0x...> is not a valid Sentinel

which surfaces in the test as
'RuntimeError: Failed to start ray on the head node' and the controller is
stuck in INIT.

This script patches two files in the old env's installed sky package:
1. sky/skylet/constants.py — adds an unconditional `uv pip install
   "click<8.3.0"` to RAY_INSTALLATION_COMMANDS, and appends "click<8.3.0"
   to the `ray[default]==... pydantic-core==...` install line. The
   unconditional pre-install protects the case where ray is already baked
   into the SkyPilot AMI and the version-guard skips the ray install.
2. sky/utils/controller_utils.py — adds 'click<8.3.0' to the cloud-deps
   install set, so typer/fastapi-cli don't bump click after the ray install.

The patch is idempotent: if the old version already has the fix, it skips.

See https://github.com/ray-project/ray/issues/56747 for the upstream issue.

TODO: Remove this file once the base version tested against in backward
compat tests is newer than 2026-04-28 (which includes commit a1a1f0bef from
PR #9459).
"""
import pathlib
import re
import sys

import sky


def _patch_constants(constants_path: pathlib.Path) -> bool:
    """Patch constants.py to pin click<8.3.0 in RAY_INSTALLATION_COMMANDS."""
    src = constants_path.read_text()

    if 'click<8.3.0' in src:
        print(f'[hotpatch] {constants_path} already pins click<8.3.0, '
              'skipping')
        return False

    # Patch 1: insert an unconditional click pin right after the setuptools
    # pin. This protects controllers booting from a SkyPilot AMI where ray is
    # already installed and the version-guard would skip the ray reinstall.
    insert = (
        '    # [hotpatch] Pin click<8.3.0: click 8.3.0+ breaks Ray CLI via\n'
        '    # copy.deepcopy on Sentinel values. See ray#56747 / sky#9459.\n'
        '    f\'{SKY_UV_PIP_CMD} install "click<8.3.0"; \'\n')
    src, n1 = re.subn(
        r'(\s+f\'\{SKY_UV_PIP_CMD\} install "setuptools<70"; \'\n)',
        lambda m: m.group(1) + insert,
        src,
    )

    # Patch 2: also append "click<8.3.0" to the `ray[default]==... pydantic-
    # core==...` install line so a fresh ray install doesn't pull click
    # 8.3.x as a transitive dependency.
    src, n2 = re.subn(
        r'("pydantic-core==2\.41\.1")',
        r'\1 "click<8.3.0"',
        src,
    )

    if n1 == 0 or n2 == 0:
        print(f'[hotpatch] WARNING: Could not fully patch {constants_path} '
              f'(setuptools-anchor: {n1}, pydantic-anchor: {n2} '
              'replacements). The old version structure may differ.')
        return False

    constants_path.write_text(src)
    print(f'[hotpatch] Patched {constants_path} with click<8.3.0 pin in '
          'RAY_INSTALLATION_COMMANDS')
    return True


def _patch_controller_utils(controller_utils_path: pathlib.Path) -> bool:
    """Patch controller_utils.py to add click<8.3.0 to cloud-deps install."""
    src = controller_utils_path.read_text()

    if "python_packages.add('click<8.3.0')" in src or 'click<8.3.0' in src:
        print(f'[hotpatch] {controller_utils_path} already pins click<8.3.0, '
              'skipping')
        return False

    # Insert the click pin right before `packages_string = ' '.join(`. Using
    # the literal opening of the join() call as anchor — this string is
    # stable across recent SkyPilot releases.
    insert = (
        '    # [hotpatch] Pin click<8.3.0: typer 0.25 transitively pulls\n'
        '    # click>=8.2.1 with no upper bound, which lets uv resolve to\n'
        '    # 8.3.x and breaks Ray on the controller. See sky#9459.\n'
        "    python_packages.add('click<8.3.0')\n")
    src, n = re.subn(
        r"(\n)(    packages_string = ' '\.join\(\n)",
        lambda m: m.group(1) + insert + m.group(2),
        src,
    )

    if n == 0:
        print(f'[hotpatch] WARNING: Could not find packages_string anchor in '
              f'{controller_utils_path}. The old version structure may differ.')
        return False

    controller_utils_path.write_text(src)
    print(f'[hotpatch] Patched {controller_utils_path} with click<8.3.0 pin '
          'in cloud-deps install')
    return True


def main():
    sky_dir = pathlib.Path(sky.__file__).parent

    constants_path = sky_dir / 'skylet' / 'constants.py'
    controller_utils_path = sky_dir / 'utils' / 'controller_utils.py'

    if not constants_path.exists():
        print(f'[hotpatch] ERROR: {constants_path} not found', file=sys.stderr)
        sys.exit(1)

    if not controller_utils_path.exists():
        print(f'[hotpatch] ERROR: {controller_utils_path} not found',
              file=sys.stderr)
        sys.exit(1)

    print(f'[hotpatch] Sky package at: {sky_dir}')

    patched = False
    patched |= _patch_constants(constants_path)
    patched |= _patch_controller_utils(controller_utils_path)

    if patched:
        print('[hotpatch] Successfully applied click<8.3.0 pin (PR #9459)')
    else:
        print('[hotpatch] No patches needed (already fixed or unsupported '
              'version)')


if __name__ == '__main__':
    main()

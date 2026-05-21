"""Hot-patch for backward compat: accept dict[K, V] openapi_types format.

The kubernetes Python client v36.0.0 (released 2026-05-20) changed the
representation of dict fields in `openapi_types` from `'dict(K, V)'`
(parens) to `'dict[K, V]'` (square brackets). Old SkyPilot versions'
PodValidator only handles the parens form, so on a fresh install where
pip auto-resolves kubernetes==36.0.0 (the package pin is open:
`kubernetes>=20.0.0,!=32.0.0`), every `sky launch --cloud kubernetes`
fails with:

    InvalidCloudConfigs: Invalid pod_config. Details:
    Validation error in metadata.labels:
    No module named 'kubernetes.client.models.dict[str, str]'

because getattr(kubernetes_models, 'dict[str, str]') triggers the lib's
lazy importlib loader, which fails. See Buildkite quicktest-core
build #3239.

This script rewrites the validator in the old env's installed sky
package to also accept `dict[K, V]`, matching the fix in master.

The patch is idempotent: if the old version already has the fix, it
skips.

TODO: Remove this file once the base version tested against in backward
compat tests includes the fix.
"""
import pathlib
import sys

import sky

OLD = ("            if klass.startswith('dict('):\n"
       "                match = re.match(r'dict\\(([^,]*), (.*)\\)', klass)\n")

NEW = ("            if klass.startswith('dict(') or "
       "klass.startswith('dict['):\n"
       "                match = re.match(r'dict[(\\[]([^,]*), (.*)[)\\]]', "
       "klass)\n")


def _patch(utils_path: pathlib.Path) -> bool:
    src = utils_path.read_text()

    if "klass.startswith('dict[')" in src:
        print(f'[hotpatch] {utils_path} already accepts dict[K, V], skipping')
        return False

    if OLD not in src:
        print(f'[hotpatch] WARNING: Could not find dict( validator pattern in '
              f'{utils_path}. The old version structure may differ.')
        return False

    utils_path.write_text(src.replace(OLD, NEW, 1))
    print(f'[hotpatch] Patched {utils_path} to accept dict[K, V] '
          'openapi_types format (kubernetes>=36.0.0 compatibility)')
    return True


def main():
    sky_dir = pathlib.Path(sky.__file__).parent
    utils_path = sky_dir / 'provision' / 'kubernetes' / 'utils.py'

    if not utils_path.exists():
        print(f'[hotpatch] ERROR: {utils_path} not found', file=sys.stderr)
        sys.exit(1)

    print(f'[hotpatch] Sky package at: {sky_dir}')
    if _patch(utils_path):
        print('[hotpatch] Successfully applied dict[K, V] openapi_types fix')
    else:
        print('[hotpatch] No patches needed (already fixed or unsupported '
              'version)')


if __name__ == '__main__':
    main()

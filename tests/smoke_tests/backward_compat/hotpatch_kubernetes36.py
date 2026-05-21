"""Hot-patch for backward compat: make old SkyPilot wheels work with
kubernetes Python client v36.0.0+.

kubernetes==36.0.0 (released to PyPI 2026-05-20 20:44 UTC) shipped two
backward-incompatible changes that break every published SkyPilot release
v0.8.0 -> 0.12.2 + every nightly on a fresh install, because the
dependency pin `kubernetes>=20.0.0,!=32.0.0` has no upper bound:

1. openapi_types changed `'dict(K, V)'` -> `'dict[K, V]'`. SkyPilot's
   PodValidator regex only handled the parens form, so every
   `sky launch --cloud kubernetes` fails on metadata.labels with:

       Validation error in metadata.labels:
       No module named 'kubernetes.client.models.dict[str, str]'

   (getattr on kubernetes.client.models triggers its lazy importlib
   loader, which fails on the unparsed `dict[str, str]` string.)

2. Attribute names for fields whose JSON keys end in `IPs`/`IDs`/`URLs`
   /`WWNs`/`CIDRs` were normalized. The change relevant to SkyPilot:

       V1ServiceSpec.external_i_ps  ->  V1ServiceSpec.external_ips

   `sky/provision/kubernetes/network_utils.py` reads this attribute on
   the ingress service during `sky serve`, so every `sky serve up`
   against k8s fails with:

       AttributeError: 'V1ServiceSpec' object has no attribute
       'external_i_ps'. Did you mean: 'external_ips'?

This script rewrites both call sites in the old env's installed sky
package so the test harness can exercise the back-compat path against
the new client. The patch is idempotent.

See Buildkite quicktest-core build #3239 (dict format) and build #3246
(external_i_ps).

TODO: Remove this file once the base version tested against in
backward-compat tests includes both fixes.
"""
import pathlib
import sys

import sky

# Patch 1: PodValidator dict format
DICT_OLD = (
    "            if klass.startswith('dict('):\n"
    "                match = re.match(r'dict\\(([^,]*), (.*)\\)', klass)\n")
DICT_NEW = ("            if klass.startswith('dict(') or "
            "klass.startswith('dict['):\n"
            "                match = re.match(r'dict[(\\[]([^,]*), "
            "(.*)[)\\]]', klass)\n")

# Patch 2: V1ServiceSpec.external_i_ps -> external_ips with fallback
IPS_OLD = ("        ip = None\n"
           "        if ingress_service.spec.external_i_ps is not None:\n"
           "            ip = ingress_service.spec.external_i_ps[0]\n")
IPS_NEW = (
    "        ip = None\n"
    "        # [hotpatch] kubernetes>=36.0.0 renamed external_i_ps -> "
    "external_ips.\n"
    "        # Try both so the patch works against either client version.\n"
    "        _external_ips = (getattr(ingress_service.spec, "
    "'external_ips', None) or\n"
    "                         getattr(ingress_service.spec, "
    "'external_i_ps', None))\n"
    "        if _external_ips:\n"
    "            ip = _external_ips[0]\n")


def _patch_pod_validator(utils_path: pathlib.Path) -> bool:
    src = utils_path.read_text()

    if "klass.startswith('dict[')" in src:
        print(f'[hotpatch] {utils_path} already accepts dict[K, V], skipping')
        return False

    if DICT_OLD not in src:
        print(f'[hotpatch] WARNING: Could not find dict( validator pattern in '
              f'{utils_path}. The old version structure may differ.')
        return False

    utils_path.write_text(src.replace(DICT_OLD, DICT_NEW, 1))
    print(f'[hotpatch] Patched {utils_path} to accept dict[K, V] '
          'openapi_types format')
    return True


def _patch_external_ips(network_utils_path: pathlib.Path) -> bool:
    src = network_utils_path.read_text()

    if "getattr(ingress_service.spec, 'external_ips'" in src:
        print(f'[hotpatch] {network_utils_path} already handles external_ips, '
              'skipping')
        return False

    if IPS_OLD not in src:
        print(f'[hotpatch] WARNING: Could not find external_i_ps pattern in '
              f'{network_utils_path}. The old version structure may differ.')
        return False

    network_utils_path.write_text(src.replace(IPS_OLD, IPS_NEW, 1))
    print(f'[hotpatch] Patched {network_utils_path} to handle '
          'external_ips (kubernetes>=36) with external_i_ps fallback')
    return True


def main():
    sky_dir = pathlib.Path(sky.__file__).parent
    utils_path = sky_dir / 'provision' / 'kubernetes' / 'utils.py'
    network_utils_path = (sky_dir / 'provision' / 'kubernetes' /
                          'network_utils.py')

    if not utils_path.exists():
        print(f'[hotpatch] ERROR: {utils_path} not found', file=sys.stderr)
        sys.exit(1)
    if not network_utils_path.exists():
        print(f'[hotpatch] ERROR: {network_utils_path} not found',
              file=sys.stderr)
        sys.exit(1)

    print(f'[hotpatch] Sky package at: {sky_dir}')

    patched = False
    patched |= _patch_pod_validator(utils_path)
    patched |= _patch_external_ips(network_utils_path)

    if patched:
        print('[hotpatch] Successfully applied kubernetes>=36 compat fixes')
    else:
        print('[hotpatch] No patches needed (already fixed or unsupported '
              'version)')


if __name__ == '__main__':
    main()

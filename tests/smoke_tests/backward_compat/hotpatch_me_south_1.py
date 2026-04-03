"""Hot-patch for backward compat: inject me-south-1 AWS region fix.

The old SkyPilot version installed from PyPI lacks:
- PR #9240 (commit 168ca6793): catch botocore ConnectionError in
  _get_availability_zones() + add ENDPOINT_CONNECTION_ERROR to exceptions
- PR #9244 (commit 6e5d73633): catch ReadTimeoutError + add explicit
  timeouts to EC2 client

When the AWS me-south-1 (Bahrain) region is unreachable from the CI
environment, the old code's ThreadPool crashes, causing `sky launch`
to fail in backward compatibility tests with:
  FAILED_PRECHECKS: Task requires aws which is not enabled

This script patches two files in the old env's installed sky package:
1. exceptions.py — adds ENDPOINT_CONNECTION_ERROR enum value + message
2. catalog/data_fetchers/fetch_aws.py — adds EC2 client timeouts and
   catches both ConnectionError and ReadTimeoutError

The patch is idempotent: if the old version already has the fix,
it skips patching.

This should be removed once the minimum compatible version (used as
base_branch in backward compat tests) includes commit 6e5d73633.
"""
import pathlib
import re
import sys


def _patch_exceptions(exc_path: pathlib.Path) -> bool:
    """Patch exceptions.py to add ENDPOINT_CONNECTION_ERROR."""
    src = exc_path.read_text()

    if 'ENDPOINT_CONNECTION_ERROR' in src:
        print(f'[hotpatch] {exc_path} already has ENDPOINT_CONNECTION_ERROR, '
              'skipping')
        return False

    # Add enum value after AZ_PERMISSION_DENIED
    src = src.replace(
        "AZ_PERMISSION_DENIED = 'AZ_PERMISSION_DENIED'",
        "AZ_PERMISSION_DENIED = 'AZ_PERMISSION_DENIED'\n"
        "        ENDPOINT_CONNECTION_ERROR = 'ENDPOINT_CONNECTION_ERROR'",
    )

    # Add message handler before the else:raise ValueError fallback
    src = src.replace(
        "            else:\n"
        "                raise ValueError(f'Unknown reason {self}')",
        "            elif self == self.ENDPOINT_CONNECTION_ERROR:\n"
        "                return ('Failed to connect to the AWS EC2 endpoint. '\n"
        "                        'This may be due to network issues or the "
        "region being '\n"
        "                        'unreachable from the current network "
        "environment.')\n"
        "            else:\n"
        "                raise ValueError(f'Unknown reason {self}')",
    )

    exc_path.write_text(src)
    print(f'[hotpatch] Patched {exc_path} with ENDPOINT_CONNECTION_ERROR')
    return True


def _patch_fetch_aws(fetch_aws_path: pathlib.Path) -> bool:
    """Patch fetch_aws.py with timeouts and ConnectionError/ReadTimeoutError."""
    src = fetch_aws_path.read_text()

    # Check if already patched
    if ('botocore_exceptions().ConnectionError' in src and
            'ENDPOINT_CONNECTION_ERROR' in src):
        print(f'[hotpatch] {fetch_aws_path} already has ConnectionError '
              'handler, skipping')
        return False

    # Patch 1: Add timeouts to the EC2 client constructor.
    # Old code:  client = aws.client('ec2', region_name=region)
    # New code:  client = aws.client('ec2', region_name=region,
    #                                 connect_timeout=10, read_timeout=10,
    #                                 total_max_attempts=3)
    src, n = re.subn(
        r"client = aws\.client\('ec2', region_name=region\)",
        "client = aws.client('ec2',\n"
        "                    region_name=region,\n"
        "                    connect_timeout=10,\n"
        "                    read_timeout=10,\n"
        "                    total_max_attempts=3)",
        src,
    )
    if n == 0:
        print('[hotpatch] WARNING: Could not find aws.client(ec2) call. '
              'Skipping client timeout patch.')
    else:
        print(f'[hotpatch] Added EC2 client timeouts to {fetch_aws_path}')

    # Patch 2: Add ConnectionError + ReadTimeoutError handler.
    # Old code ends with:
    #         else:
    #             raise
    #     for resp in response['AvailabilityZones']:
    #
    # We insert the new except block between "raise" and "for resp".
    pattern = re.compile(r"(        else:\n"
                         r"            raise\n)"
                         r"(    for resp in response\['AvailabilityZones'\]:)")
    replacement = (
        r"\1"
        r"    except (aws.botocore_exceptions().ConnectionError,\n"
        r"            aws.botocore_exceptions().ReadTimeoutError):\n"
        r"        with ux_utils.print_exception_no_traceback():\n"
        r"            raise exceptions.AWSAzFetchingError(\n"
        r"                region,\n"
        r"                reason=exceptions.AWSAzFetchingError.Reason.\n"
        r"                ENDPOINT_CONNECTION_ERROR) from None\n"
        r"\2")

    new_src, count = pattern.subn(replacement, src)
    if count == 0:
        print(f'[hotpatch] WARNING: Could not find except block target in '
              f'{fetch_aws_path}. The old code structure may differ.')
        return False

    fetch_aws_path.write_text(new_src)
    print(f'[hotpatch] Patched {fetch_aws_path} with '
          'ConnectionError + ReadTimeoutError handler')
    return True


def main():
    try:
        import sky
        sky_dir = pathlib.Path(sky.__file__).parent
    except ImportError:
        print('[hotpatch] ERROR: Could not import sky from current environment',
              file=sys.stderr)
        sys.exit(1)

    exc_path = sky_dir / 'exceptions.py'
    fetch_aws_path = sky_dir / 'catalog' / 'data_fetchers' / 'fetch_aws.py'

    if not exc_path.exists():
        print(f'[hotpatch] ERROR: {exc_path} not found', file=sys.stderr)
        sys.exit(1)

    if not fetch_aws_path.exists():
        print(f'[hotpatch] ERROR: {fetch_aws_path} not found', file=sys.stderr)
        sys.exit(1)

    print(f'[hotpatch] Sky package at: {sky_dir}')

    patched = False
    patched |= _patch_exceptions(exc_path)
    patched |= _patch_fetch_aws(fetch_aws_path)

    if patched:
        print('[hotpatch] Successfully applied me-south-1 fix '
              '(PR #9240 + #9244)')
    else:
        print('[hotpatch] No patches needed (already fixed or unsupported '
              'version)')


if __name__ == '__main__':
    main()

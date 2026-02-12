"""Import SkyPilot API server state from a tarball.

Usage::

    python -m sky.utils.db.state_migration_import \
        --input /path/to/state.tar.gz [--force]

The ``--force`` flag truncates existing data in the target tables before
importing.  Without it the import is aborted if any target table already
contains data.
"""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Import SkyPilot API server state from a tarball.')
    parser.add_argument('--input',
                        '-i',
                        required=True,
                        help='Path to the tarball to import.')
    parser.add_argument(
        '--force',
        '-f',
        action='store_true',
        default=False,
        help='Truncate existing data in target tables before import.')
    args = parser.parse_args()

    # Import here so that argparse --help works without pulling in heavy deps.
    # pylint: disable=import-outside-toplevel
    from sky.utils.db import state_migration

    # pylint: enable=import-outside-toplevel

    print(f'Importing SkyPilot state from {args.input} ...')
    state_migration.import_state(args.input, force=args.force)


if __name__ == '__main__':
    main()

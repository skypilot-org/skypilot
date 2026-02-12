"""Export SkyPilot API server state to a tarball.

Usage::

    python -m sky.utils.db.state_migration_export \
        --output /path/to/output.tar.gz

If ``--output`` is omitted the tarball is written to the current directory
as ``skypilot-state-<timestamp>.tar.gz``.
"""
import argparse
import datetime


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Export SkyPilot API server state to a tarball.')
    parser.add_argument('--output',
                        '-o',
                        default=None,
                        help=('Output tarball path. Defaults to '
                              './skypilot-state-<timestamp>.tar.gz'))
    args = parser.parse_args()

    output_path = args.output
    if output_path is None:
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        output_path = f'skypilot-state-{ts}.tar.gz'

    # Import here so that argparse --help works without pulling in heavy deps.
    # pylint: disable=import-outside-toplevel
    from sky.utils.db import state_migration

    # pylint: enable=import-outside-toplevel

    print(f'Exporting SkyPilot state to {output_path} ...')
    result_path = state_migration.export_state(output_path)
    print(f'\nExport complete: {result_path}')


if __name__ == '__main__':
    main()

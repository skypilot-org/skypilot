#!/usr/bin/env python3
"""Wrapper script to run alembic migrations manually with database URL."""
import argparse
import os
import sys

from alembic import command
from alembic.config import Config

# Valid sections from alembic.ini
VALID_SECTIONS = ['state_db', 'spot_jobs_db', 'serve_db']

# Valid commands
VALID_COMMANDS = [
    'current', 'upgrade', 'history', 'downgrade', 'heads', 'branches', 'show'
]


def main():
    parser = argparse.ArgumentParser(
        description='Run alembic migrations manually with database URL',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check current version
  python run_alembic.py --url postgresql://user@localhost/sky --section state_db current

  # Upgrade to latest
  python run_alembic.py --url postgresql://user@localhost/sky --section state_db upgrade head

  # Upgrade to specific revision
  python run_alembic.py --url postgresql://user@localhost/sky --section state_db upgrade 009

  # Downgrade one revision
  python run_alembic.py --url postgresql://user@localhost/sky --section state_db downgrade -1

  # View history
  python run_alembic.py --url postgresql://user@localhost/sky --section state_db history
        """)

    parser.add_argument(
        '--url',
        required=True,
        help='Database URL (e.g., postgresql://user@localhost/sky '
        'or sqlite:////path/to/db.db)')
    parser.add_argument(
        '--section',
        choices=VALID_SECTIONS,
        default='state_db',
        help='Database section to operate on (default: state_db)')
    parser.add_argument(
        '--config',
        default=None,
        help='Path to alembic.ini (default: auto-detected from script location)'
    )
    parser.add_argument('command',
                        choices=VALID_COMMANDS,
                        help='Alembic command to run')
    parser.add_argument(
        'revision',
        nargs='?',
        default=None,
        help='Revision for upgrade/downgrade (e.g., head, 009, -1)')

    args = parser.parse_args()

    # Determine alembic.ini path
    if args.config:
        alembic_ini_path = args.config
    else:
        # From sky/schemas/db/scripts/run_alembic.py ->
        #      sky/setup_files/alembic.ini
        script_dir = os.path.dirname(os.path.abspath(__file__))
        alembic_ini_path = os.path.join(script_dir, '..', '..', '..',
                                        'setup_files', 'alembic.ini')

    # Validate config file exists
    if not os.path.exists(alembic_ini_path):
        print(f'Error: alembic.ini not found at {alembic_ini_path}',
              file=sys.stderr)
        sys.exit(1)

    # Create alembic config
    alembic_cfg = Config(alembic_ini_path, ini_section=args.section)

    # Set the database URL (escape % for configparser interpolation)
    url = args.url.replace('%', '%%')
    alembic_cfg.set_section_option(args.section, 'sqlalchemy.url', url)

    # Execute the command
    try:
        if args.command == 'current':
            command.current(alembic_cfg, verbose=True)
        elif args.command == 'upgrade':
            revision = args.revision or 'head'
            command.upgrade(alembic_cfg, revision)
        elif args.command == 'downgrade':
            if not args.revision:
                print('Error: revision required for downgrade (e.g., -1, base)',
                      file=sys.stderr)
                sys.exit(1)
            command.downgrade(alembic_cfg, args.revision)
        elif args.command == 'history':
            command.history(alembic_cfg)
        elif args.command == 'heads':
            command.heads(alembic_cfg)
        elif args.command == 'branches':
            command.branches(alembic_cfg)
        elif args.command == 'show':
            if not args.revision:
                print('Error: revision required for show (e.g., head, 009)',
                      file=sys.stderr)
                sys.exit(1)
            command.show(alembic_cfg, args.revision)
    # pylint: disable=broad-except
    except Exception as e:
        print(f'Error executing alembic command: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

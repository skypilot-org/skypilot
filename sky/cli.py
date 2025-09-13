"""The 'sky' command line tool."""

from sky.client.cli import command


# For backward compatibility.
# setup.py uses this file to install the CLI.
def cli():
    command.cli()

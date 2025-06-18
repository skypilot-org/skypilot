"""The 'sky' command line tool."""

from sky.client import cli as cli_client


# For backward compatibility.
# setup.py uses this file to install the CLI.
def cli():
    cli_client.cli()

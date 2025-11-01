"""Minimal template - replace this with your example description."""

import os
from dotenv import load_dotenv
from agent_sandbox import Sandbox

load_dotenv()


def main():
    """Your main code goes here."""
    # Connect to sandbox
    sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
    client = Sandbox(base_url=sandbox_url)

    # Get sandbox home directory
    home_dir = client.sandbox.get_context().home_dir
    print(f"Connected to sandbox: {home_dir}")

    # TODO: Add your code here
    # Reference:
    # https://sandbox.agent-infra.com/

if __name__ == "__main__":
    main()

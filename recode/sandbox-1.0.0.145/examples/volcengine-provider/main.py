#!/usr/bin/env python3
"""
Example usage of the Volcengine cloud provider for sandbox management.

This example demonstrates how to use the VolcengineProvider to create,
manage, and delete sandbox instances using the Volcengine VEFAAS API.
"""

from __future__ import print_function

import os
import time
from pathlib import Path
from dotenv import load_dotenv
from agent_sandbox import Sandbox
from agent_sandbox.providers import VolcengineProvider

# Load environment variables from .env file
load_dotenv()


def main():
    """
    Main function demonstrating Volcengine provider usage.
    """
    # Configuration - replace with your actual credentials
    access_key = os.getenv("VOLCENGINE_ACCESS_KEY")
    secret_key = os.getenv("VOLCENGINE_SECRET_KEY")
    region = os.getenv("VOLCENGINE_REGION", "cn-beijing")

    # Initialize the Volcengine provider
    provider = VolcengineProvider(
        access_key=access_key,
        secret_key=secret_key,
        region=region
    )

    print("=== Volcengine Sandbox Provider Example ===\n")

    print("1. Creating an application...(If created, skip this create step)")
    application_id = provider.create_application(
        name="aio-3", gateway_name="test2-ly")
    print(f"Sandbox application id: {application_id}")
    if not application_id:
        print("Application creation failed; skipping sandbox operations.")
        return
    print("\n")

    print("2. Checking if the application is ready...")
    ready, function_id = provider.get_application_readiness(id=application_id)
    while not ready:
        print("Application is not ready, waiting for 1 second...")
        time.sleep(1)
        ready, function_id = provider.get_application_readiness(
            id=application_id)
    print(f"Is ready: {ready}, function_id: {function_id}")
    print("\n")

    if not function_id:
        print("Function ID unavailable; skipping sandbox operations.")
        return

    print("3. Creating a sandbox...")
    sandbox_id = provider.create_sandbox(function_id=function_id)
    print(f"Create response: {sandbox_id}")
    print("\n")

    # Example 4: List all sandboxes for the function
    print("4. Listing all sandboxes for function...")
    list_response = provider.list_sandboxes(function_id=function_id)

    print(f"Number of sandboxes: {len(list_response)}")
    # print(f"list_response: {list_response}")
    print("\n")

    print(f"5. Get sandbox details {sandbox_id}")
    get_response = provider.get_sandbox(
        function_id=function_id, sandbox_id=sandbox_id)
    print(f"Get response: {get_response}")

    domains = get_response["domains"]
    for domain in domains:
        if domain["type"] == "public":
            print(f'public domains: {domain["domain"]}')
            break
    print("\n")

    # test the sandbox
    print("5. Testing the sandbox...")
    client = Sandbox(base_url=domain["domain"])
    list_result = client.file.list_path(path=".")
    print(f"\nFiles in sandbox: {list_result}")
    print("\n")

    # Example 6: Delete the sandbox
    print(f"6. Deleting sandbox '{sandbox_id}'...")
    delete_response = provider.delete_sandbox(
        function_id=function_id, sandbox_id=sandbox_id)
    print(f"Delete response: {delete_response}")
    print("\n")

    print("=== Example completed ===")


if __name__ == "__main__":
    main()

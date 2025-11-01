"""Basic example demonstrating sandbox file operations."""

import os
import base64
from dotenv import load_dotenv
from agent_sandbox import Sandbox

# Load environment variables from .env file
load_dotenv()


def main():
    """Example of basic file operations in sandbox."""
    sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
    client = Sandbox(base_url=sandbox_url)

    # Get the sandbox home directory
    home_dir = client.sandbox.get_context().home_dir
    print(f"Sandbox home directory: {home_dir}\n")

    # Example 1: Write a text file to sandbox
    test_content = "Hello from agent-sandbox!\nThis is a test file.\n"
    sandbox_path = f"{home_dir}/test.txt"

    write_result = client.file.write_file(
        file=sandbox_path,
        content=test_content,
        encoding="utf-8"
    )
    print(f"✓ File written to sandbox: {sandbox_path}")
    print(f"  Bytes written: {write_result.data.bytes_written}\n")

    # Example 2: List files in sandbox
    list_result = client.file.list_path(path=home_dir)
    print(f"✓ Files in sandbox home directory: {list_result.data.total_count} items")
    print(f"  - Text files found:")
    for file in list_result.data.files:
        if not file.is_directory and file.extension in ['', '.txt', '.md']:
            print(f"    • {file.name} ({file.size} bytes)")
    print()

    # Example 3: Read file back from sandbox
    read_result = client.file.read_file(file=sandbox_path)
    print(f"✓ Read file from sandbox:")
    print(f"  Content: {read_result.data.content}")

    # Example 4 (bonus): Write a binary file (PNG)
    print(f"\n✓ Binary file example:")
    with open("./foo.png", "rb") as f:
        png_content = base64.b64encode(f.read()).decode("utf-8")

    png_path = f"{home_dir}/test.png"
    png_write_result = client.file.write_file(
        file=png_path,
        content=png_content,
        encoding="base64"
    )
    print(f"  PNG written to: {png_path}")
    print(f"  Bytes written: {png_write_result.data.bytes_written}")


if __name__ == "__main__":
    main()

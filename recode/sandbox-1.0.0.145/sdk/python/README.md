# Agent Sandbox Python SDK

A Python SDK for the All-in-One Sandbox API, providing access to sandbox, shell, file, jupyter, nodejs, and mcp services.

## Installation

```bash
pip install agent-sandbox
```

## Usage

```python
from agent_sandbox import Sandbox

client = Sandbox(base_url="http://localhost:8091")

ctx = client.sandbox.get_context()
print(ctx)

result = client.shell.exec_command(command="ls -la")
print(result)
```

## Async Support

The SDK also provides async support through the `AsyncSandbox` class:

```python
import asyncio
from agent_sandbox import AsyncSandbox

async def main():
    client = AsyncSandbox(base_url="http://localhost:8091")
    
    # Get sandbox context
    ctx = await client.sandbox.get_context()
    print(ctx)

    result = await client.shell.exec_command(command="ls -la")
    print(result)

asyncio.run(main())
```

## Cloud Providers

### Volcengine

Create a sandbox instance using the Volcengine provider. For more details, please refer to [examples/provider_volcengine.py](examples/provider_volcengine.py).

```
from __future__ import print_function

import os
import sys

# Add the parent directory to Python path so we can import agent_sandbox
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_sandbox.providers import VolcengineProvider


def main():
    """
    Main function demonstrating Volcengine provider usage.
    """
    # Configuration - replace with your actual credentials
    access_key = os.getenv("VOLC_ACCESSKEY")
    secret_key = os.getenv("VOLC_SECRETKEY")
    region = os.getenv("VOLCENGINE_REGION", "cn-beijing")
    
    # Initialize the Volcengine provider
    provider = VolcengineProvider(
        access_key=access_key,
        secret_key=secret_key,
        region=region
    )
    
    print("=== Volcengine Sandbox Provider Example ===\n")
    
    function_id = "yatoczqh"
    
    print("1. Creating a sandbox...")
    sandbox_id = provider.create_sandbox(function_id=function_id)
    print(f"Create response: {sandbox_id}")


if __name__ == "__main__":
    main()
```

## Features

- **Sandbox**: Access sandbox environment information and installed packages
- **Shell**: Execute shell commands with session management
- **File**: Read, write, search, and manage files
- **Jupyter**: Execute Python code in Jupyter kernels
- **Node.js**: Execute JavaScript code in Node.js environment
- **MCP**: Interact with Model Context Protocol servers

## Requirements

- Python 3.8+
- httpx
- pydantic
- typing_extensions (for Python < 3.10)
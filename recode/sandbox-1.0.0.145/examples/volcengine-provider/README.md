# Volcengine Provider Example

This example demonstrates how to use the Volcengine cloud provider to create and manage sandbox instances using the Volcengine VEFAAS API.

## Prerequisites

- Volcengine account with VEFAAS access
- Environment variables set:
  - `VOLCENGINE_ACCESS_KEY`: Your Volcengine access key
  - `VOLCENGINE_SECRET_KEY`: Your Volcengine secret key
  - `VOLCENGINE_REGION`: Region (default: cn-beijing)
- Python 3.11+

## Running the Example

```bash
export VOLCENGINE_ACCESS_KEY="your_access_key"
export VOLCENGINE_SECRET_KEY="your_secret_key"
export VOLCENGINE_REGION="cn-beijing"

uv run main.py
```

## What This Example Does

1. Creates a Volcengine application
2. Waits for the application to be ready
3. Creates a sandbox instance
4. Lists all sandboxes for the function
5. Gets sandbox details and public domain
6. Tests the sandbox by listing files
7. Deletes the sandbox instance

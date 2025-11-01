# Basic File Operations Example

This example demonstrates basic file operations with agent-sandbox, including:

- Uploading files to the sandbox
- Listing files in the sandbox
- Reading files from the sandbox
- Downloading files from the sandbox

## Prerequisites

- A running sandbox instance at `http://localhost:8080`
- Python 3.11+

## Running the Example

```bash
uv run main.py
```

## What This Example Does

1. Reads a local image file (`foo.png`)
2. Uploads it to the sandbox at `/home/sandbox/test.png`
3. Lists files in the sandbox directory
4. Reads the file back from the sandbox
5. Downloads the file from the sandbox to verify the operation

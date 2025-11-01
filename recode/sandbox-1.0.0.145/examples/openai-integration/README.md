# OpenAI Integration Example

This example demonstrates how to integrate OpenAI's function calling with agent-sandbox to enable safe code execution in a sandboxed environment.

## Prerequisites

- A running sandbox instance at `http://localhost:8080`
- Python 3.11+
- OpenAI API key

## Running the Example

```bash
# Set your OpenAI API key
export OPENAI_API_KEY="your_api_key"

# Update the API key in main.py, then run
uv run main.py
```

## What This Example Does

1. Defines a `run_code` function that executes Python or Node.js code in the sandbox
2. Registers the function as an OpenAI tool
3. Sends a natural language request to GPT-4 (e.g., "calculate 1+1")
4. GPT-4 decides to use the `run_code` tool with appropriate code
5. The code is executed in the sandbox and results are returned

## Key Features

- **Safe Code Execution**: All code runs in an isolated sandbox environment
- **Multi-language Support**: Supports both Python (via Jupyter) and Node.js execution
- **OpenAI Function Calling**: Leverages GPT's ability to generate and call functions

## Example Usage

The example is currently configured to calculate `1+1`, but you can modify the task in [main.py](main.py:21):

```python
messages=[{"role": "user", "content": "calculate 1+1"}]
```

Try different tasks like:
- "create a fibonacci sequence generator"
- "calculate the factorial of 10"
- "fetch data from an API and parse it"

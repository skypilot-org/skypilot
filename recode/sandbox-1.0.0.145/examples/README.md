# Agent Sandbox Examples

This directory contains examples demonstrating various use cases and integrations with agent-sandbox.

## Table of Contents

- [basic-file-operations](./basic-file-operations) - Core file operations (upload, read, list, download)
- [oss-upload](./oss-upload) - Stream sandbox files to cloud storage (Volcengine TOS, AWS S3)
- [volcengine-provider](./volcengine-provider) - Volcengine cloud provider integration
- [site-to-markdown](./site-to-markdown) - Website to markdown conversion with browser automation
- [browser-use-integration](./browser-use-integration) - AI-driven browser automation
- [playwright-integration](./playwright-integration) - Browser automation with Playwright
- [openai-integration](./openai-integration) - OpenAI function calling with code execution
- [langgraph-deepagents-integration](./langgraph-deepagents-integration) - LangGraph deep agent with MCP tools integration
- [code-execute](./code-execute) - Execute code in sandbox (Jupyter and Node.js)



## Prerequisites

All examples require:
- Python 3.12+
- A running sandbox instance (most examples use `http://localhost:8080`)
- uv package manager

## Contributing

We welcome contributions of new examples!

### Quick Start (Easiest Way)

Copy the `_template` directory and start coding:

```bash
# Copy template to your new example
cp -r _template my-example-name
cd my-example-name

# Set up environment
cp .env.example .env

# Start coding in main.py
# Run it: uv run main.py
```

The template includes all necessary files - just modify `main.py` with your code!


### Before Submitting

1. Test your example:
   ```bash
   uv run main.py
   ```

2. Update the main `examples/README.md` Table of Contents

3. Keep it focused - one clear purpose per example

### Example Naming

- Use kebab-case: `browser-automation`, `file-operations`
- For integrations: `openai-integration`, `langgraph-deepagents-integration`

### Need Help?

- Check existing examples for reference
- Review the [Agent Sandbox Documentation](https://sandbox.agent-infra.com)
- Open an issue for questions or suggestions

# aio-deepagents

A LangGraph-based deep agent implementation that uses the AIO (All-in-One) sandbox for enhanced research capabilities. This project integrates the DeepAgents framework with MCP (Model Context Protocol) tools to create a powerful research assistant.

## Features

- **Deep Research Agent**: Uses the DeepAgents framework for advanced reasoning and research capabilities
- **MCP Integration**: Connects to AIO sandbox through MCP for tool access
- **OpenAI Compatible**: Supports various LLM providers through OpenAI-compatible API
- **Streaming Support**: Real-time response streaming for better user experience

## Prerequisites

- Python 3.12 or higher
- Docker (for running AIO sandbox)
- An OpenAI-compatible API key (OpenRouter, etc.)

## Quick Start

### 1. Start AIO Docker Sandbox

The AIO sandbox provides various tools and capabilities that the agent can use. Start it first:

**For International Users:**
```bash
docker run --security-opt seccomp=unconfined --security-opt seccomp=unconfined --rm -it -p 8080:8080 ghcr.io/agent-infra/sandbox:latest
```

**For Users in Mainland China:**
```bash
docker run --security-opt seccomp=unconfined --rm -it -p 8080:8080 enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

More information:
- [AIO Sandbox Guide](https://sandbox.agent-infra.com/)
- [MCP Integration](https://sandbox.agent-infra.com/guide/basic/mcp)

### 2. Setup Environment

Create a virtual environment and install dependencies:
```bash
uv venv
uv sync
```

### 3. Configure Environment Variables

Copy the example environment file and configure your API credentials:
```bash
cp .env.example .env
```

Edit `.env` with your API details:
```bash
OPENAI_API_KEY=sk-xxxxx                    # Your API key
OPENAI_MODEL=anthropic/claude-sonnet-4    # Model to use
OPENAI_BASEURL=https://openrouter.ai/api/v1  # API endpoint
```

### 4. Run the Agent

```bash
uv run main.py
```

The agent will connect to the sandbox and answer the example question "what is langgraph?".

## Project Structure

```
aio-deepagents/
├── main.py           # Main application entry point
├── pyproject.toml    # Project configuration and dependencies
├── .env.example      # Environment variables template
└── README.md         # This file
```

## Dependencies

- **deepagents**: Core deep agent framework (≥0.0.5)
- **langchain**: LangChain framework for LLM applications (≥0.3.27)
- **langchain-mcp-adapters**: MCP integration for LangChain (≥0.1.10)
- **langchain-openai**: OpenAI provider for LangChain (≥0.3.0)
- **httpx**: HTTP client with SOCKS proxy support (≥0.27.2)

## Customization

You can customize the agent by modifying `main.py`:

- **Change the question**: Modify the content in the `astream` call
- **Update instructions**: Change the agent's behavior by updating the `instructions` parameter
- **Add more tools**: Connect additional MCP servers in the `MultiServerMCPClient` configuration
- **Switch models**: Update the `OPENAI_MODEL` environment variable

## License

This project is licensed under the terms specified in the LICENSE file.

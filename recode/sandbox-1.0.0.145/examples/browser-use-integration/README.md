# Browser-Use Integration Example

This example demonstrates how to integrate [browser-use](https://github.com/browser-use/browser-use) with agent-sandbox, allowing you to use browser automation agents with a sandboxed browser.

## Prerequisites

- A running sandbox instance at `http://localhost:8080`
- Python 3.11+
- OpenAI API key or compatible LLM endpoint

## Running the Example

```bash
# Set your LLM API credentials
export OPENAI_API_KEY="your_api_key"

# Run the example
uv run main.py
```

## What This Example Does

1. Connects to the sandbox browser via CDP
2. Creates a browser-use Agent with the sandboxed browser session
3. Executes an AI-driven browser automation task
4. The agent will visit DuckDuckGo and search for "browser-use founders"

## Configuration

You can modify the task in [main.py](main.py:19):

```python
agent = Agent(
    task='Visit https://duckduckgo.com and search for "browser-use founders"',
    llm=ChatOpenAI(model="gcp-claude4.1-opus"),
    tools=tools,
    browser_session=browser_session,
)
```

Update the LLM model in the code according to your setup.

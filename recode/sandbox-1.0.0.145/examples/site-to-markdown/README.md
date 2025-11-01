# Site to Markdown Example

This example demonstrates how to combine multiple sandbox features:
- Browser automation with Playwright
- Jupyter code execution for HTML processing
- File operations

The example fetches a website, converts it to markdown, and saves it with a screenshot.

## Prerequisites

- A running sandbox instance at `http://localhost:8080`
- Python 3.11+
- Playwright installed (will be installed automatically with dependencies)

## Running the Example

```bash
# Install Playwright browsers (first time only)
uv run playwright install

# Run the example
uv run main.py
```

## What This Example Does

1. Connects to the sandbox browser via CDP
2. Navigates to a website (https://sandbox.agent-infra.com/)
3. Captures the HTML content and screenshot
4. Uses Jupyter in the sandbox to convert HTML to markdown using `markdownify`
5. Saves the markdown file with embedded screenshot
6. Downloads the result to local filesystem as `output.md`

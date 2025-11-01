# Playwright Integration Example

This example demonstrates how to integrate [Playwright](https://playwright.dev) with agent-sandbox, allowing you to use Playwright's powerful browser automation capabilities with a sandboxed browser.

## Prerequisites

- A running sandbox instance at `http://localhost:8080`
- Python 3.11+
- uv package manager

## Running the Example

```bash
# Install Playwright browsers (first time only)
uv run playwright install chromium

# Run the example
uv run main.py
```

## What This Example Does

1. Connects to the sandbox browser via CDP (Chrome DevTools Protocol)
2. Creates a new browser context and page using Playwright
3. Navigates to DuckDuckGo and performs a search
4. Extracts the first search result's title and link
5. Takes a screenshot of the search results page
6. Saves the screenshot as `search_results.png`

## Configuration

You can modify the automation script in [main.py](main.py) to perform different tasks:

```python
# Navigate to a website
await page.goto("https://example.com")

# Interact with elements
await page.click('button[type="submit"]')
await page.fill('input[name="email"]', "test@example.com")

# Wait for elements
await page.wait_for_selector('.result')

# Extract data
title = await page.title()
text = await page.locator('.content').inner_text()

# Take screenshots
await page.screenshot(path="screenshot.png")
```

## Key Features

- **Browser Automation**: Full Playwright API for browser control
- **Sandboxed Execution**: Browser runs in isolated sandbox environment
- **CDP Integration**: Connect via Chrome DevTools Protocol
- **Screenshot Support**: Capture visual output
- **Element Selection**: Powerful selectors and locators
- **Async/Await**: Modern async Python patterns

## Advanced Usage

### Multiple Pages

```python
page1 = await context.new_page()
page2 = await context.new_page()

await page1.goto("https://example1.com")
await page2.goto("https://example2.com")
```

### Network Interception

```python
await page.route("**/*.{png,jpg,jpeg}", lambda route: route.abort())
```

### Emulation

```python
context = await browser.new_context(
    viewport={'width': 1280, 'height': 720},
    user_agent='Custom User Agent'
)
```

## Resources

- [Playwright Documentation](https://playwright.dev/python/docs/intro)
- [Agent Sandbox Documentation](https://sandbox.agent-infra.com)
- [Playwright Selectors Guide](https://playwright.dev/python/docs/selectors)

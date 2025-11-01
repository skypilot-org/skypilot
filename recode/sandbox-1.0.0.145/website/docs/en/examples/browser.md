# Browser Automation Examples

This guide shows how to use AIO Sandbox for browser automation, web scraping, and UI testing.

## Overview

AIO Sandbox provides multiple ways to interact with browsers:

- **VNC Access**: Visual browser interaction through remote desktop
- **Chrome DevTools Protocol (CDP)**: Programmatic browser control
- **Browser MCP Server**: High-level browser automation tools

## VNC Browser Access

### Basic VNC Interaction

Access the browser visually through VNC:

```bash
# Open VNC interface
open http://localhost:8080/vnc/index.html?autoconnect=true
```

The VNC interface provides:
- Full desktop environment with browser
- Visual interaction capabilities
- Screenshot and screen recording
- Mouse and keyboard input

### VNC in Automation Scripts

Use VNC for scenarios requiring visual verification:

```python
import requests
import time

# Take screenshot via VNC API
response = requests.get("http://localhost:8080/vnc/screenshot")
with open("screenshot.png", "wb") as f:
    f.write(response.content)

# Send keyboard input
requests.post("http://localhost:8080/vnc/keyboard", 
              json={"keys": "Hello World"})
```

## Chrome DevTools Protocol (CDP)

### CDP Connection

Connect to the browser using CDP:

```javascript
const CDP = require('chrome-remote-interface');

async function example() {
    const client = await CDP();
    const {Network, Page, Runtime} = client;
    
    // Enable necessary domains
    await Network.enable();
    await Page.enable();
    
    // Navigate to a page
    await Page.navigate({url: 'https://example.com'});
    await Page.loadEventFired();
    
    // Execute JavaScript
    const result = await Runtime.evaluate({
        expression: 'document.title'
    });
    
    console.log('Page title:', result.result.value);
    
    await client.close();
}

example().catch(console.error);
```

### Python CDP Example

Using Python with pychrome:

```python
import pychrome
import time

# Connect to browser
browser = pychrome.Browser(url="http://localhost:8080")
tab = browser.new_tab()

# Enable page domain
tab.Page.enable()

# Navigate to page
tab.Page.navigate(url="https://httpbin.org/get")
tab.wait(1)

# Get page content
result = tab.Runtime.evaluate(expression="document.body.innerText")
print("Page content:", result['result']['value'])

# Clean up
browser.close_tab(tab)
```

## Browser MCP Server API Reference

The `@agent-infra/mcp-server-browser` package provides the following tools:

### Navigation & Basic Actions
- `browser_navigate` - Navigate to a URL
- `browser_go_back` - Go back to previous page  
- `browser_go_forward` - Go forward to next page
- `browser_close` - Close the browser

### Element Interaction
- `browser_get_clickable_elements` - Get clickable/hoverable/selectable elements
- `browser_click` - Click an element (use with element index)
- `browser_hover` - Hover over an element
- `browser_select` - Select an element
- `browser_form_input_fill` - Fill input fields

### Content Retrieval
- `browser_get_text` - Get page text content
- `browser_get_markdown` - Get page as markdown
- `browser_read_links` - Get all page links
- `browser_screenshot` - Take screenshots

### Advanced Features
- `browser_scroll` - Scroll the page
- `browser_evaluate` - Execute JavaScript
- `browser_new_tab` - Open new tab
- `browser_switch_tab` - Switch between tabs
- `browser_tab_list` - List all tabs
- `browser_get_download_list` - Get downloaded files

### Vision Mode (Optional)
- `browser_vision_screen_capture` - Screenshot for vision mode
- `browser_vision_screen_click` - Vision-based clicking

## Browser MCP Server Integration

### Python with MCP Client

Use the Browser MCP server for high-level automation:

```python
import asyncio
from mcp import Client
import httpx

async def browser_automation():
    async with httpx.AsyncClient() as http_client:
        # Connect to MCP server
        async with Client("http://localhost:8080/mcp") as client:
            
            # Navigate to page
            result = await client.call_tool("browser_navigate", {
                "url": "https://example.com"
            })
            
            # Take screenshot
            screenshot = await client.call_tool("browser_screenshot", {
                "path": "/tmp/screenshot.png"
            })
            
            # Get page text content
            content = await client.call_tool("browser_get_text")
            
            # Get clickable elements
            elements = await client.call_tool("browser_get_clickable_elements")
            
            print("Page content:", content.content[0].text)
            print("Clickable elements:", elements.content)

asyncio.run(browser_automation())
```

### JavaScript MCP Integration

```javascript
const { McpClient } = require('@modelcontextprotocol/sdk');

async function browserAutomation() {
    const client = new McpClient("http://localhost:8080/mcp");
    
    try {
        await client.connect();
        
        // Navigate to page
        await client.callTool("browser_navigate", {
            url: "https://news.ycombinator.com"
        });
        
        // Wait for page load
        await new Promise(resolve => setTimeout(resolve, 2000));
        
        // Get clickable elements (like article titles)
        const elements = await client.callTool("browser_get_clickable_elements");
        
        // Get page markdown content
        const markdown = await client.callTool("browser_get_markdown");
        
        console.log("Clickable elements:", elements.content);
        console.log("Page content:", markdown.content);
        
    } finally {
        await client.disconnect();
    }
}

browserAutomation().catch(console.error);
```

## Web Scraping Examples

### E-commerce Price Monitoring

```python
import asyncio
import json
from datetime import datetime

async def monitor_prices():
    # Product URLs to monitor
    products = [
        {"name": "Laptop", "url": "https://example-store.com/laptop"},
        {"name": "Phone", "url": "https://example-store.com/phone"}
    ]
    
    results = []
    
    for product in products:
        # Use browser to navigate and extract price
        navigation_result = await client.call_tool("browser_navigate", {
            "url": product["url"]
        })
        
        # Get clickable elements and extract price
        elements = await client.call_tool("browser_get_clickable_elements")
        price_text = await client.call_tool("browser_get_text")
        
        results.append({
            "product": product["name"],
            "price": price_text.content[0].text,
            "timestamp": datetime.now().isoformat(),
            "url": product["url"]
        })
    
    # Save results to file via File API
    await client.call_tool("file_write", {
        "path": "/tmp/price_data.json",
        "content": json.dumps(results, indent=2)
    })
    
    return results

# Run price monitoring
prices = asyncio.run(monitor_prices())
print("Price monitoring complete:", prices)
```

### Social Media Content Extraction

```python
async def extract_social_posts():
    # Navigate to social media page
    await client.call_tool("browser_navigate", {
        "url": "https://example-social.com/trending"
    })
    
    # Wait for dynamic content to load
    await asyncio.sleep(3)
    
    # Get page content and clickable elements
    content = await client.call_tool("browser_get_text")
    elements = await client.call_tool("browser_get_clickable_elements")
    
    # Process extracted data
    return {
        "page_content": content.content[0].text,
        "clickable_elements": elements.content
    }
```

## Browser Testing Examples

### Form Automation Testing

```python
async def test_contact_form():
    # Navigate to form page
    await client.call_tool("browser_navigate", {
        "url": "https://example.com/contact"
    })
    
    # Get form elements first
    elements = await client.call_tool("browser_get_clickable_elements")
    
    # Fill out form fields using form_input_fill
    await client.call_tool("browser_form_input_fill", {
        "selector": "input[name='name']",
        "value": "Test User"
    })
    
    await client.call_tool("browser_form_input_fill", {
        "selector": "input[name='email']",  
        "value": "test@example.com"
    })
    
    await client.call_tool("browser_form_input_fill", {
        "selector": "textarea[name='message']",
        "value": "This is a test message"
    })
    
    # Submit form by clicking submit button
    await client.call_tool("browser_click", {
        "index": 0  # Use index of submit button from elements
    })
    
    # Wait for response
    await asyncio.sleep(2)
    
    # Verify success message by getting page text
    page_text = await client.call_tool("browser_get_text")
    
    assert "Thank you" in page_text.content[0].text
    print("Form submission test passed!")
```

### Performance Testing

```python
async def measure_page_performance():
    start_time = time.time()
    
    # Navigate to page
    await client.call_tool("browser_navigate", {
        "url": "https://example-app.com"
    })
    
    # Wait for page to load (use sleep or evaluate JS)
    await asyncio.sleep(3)
    
    load_time = time.time() - start_time
    
    # Get performance metrics using JavaScript evaluation
    metrics = await client.call_tool("browser_evaluate", {
        "expression": "JSON.stringify({loadTime: performance.timing.loadEventEnd - performance.timing.navigationStart, domContentLoaded: performance.timing.domContentLoadedEventEnd - performance.timing.navigationStart})"
    })
    
    return {
        "total_load_time": load_time,
        "performance_metrics": metrics.result
    }
```

## Playwright Integration

For advanced browser automation, integrate Playwright:

```python
from playwright.async_api import async_playwright
import asyncio

async def playwright_example():
    async with async_playwright() as p:
        # Connect to existing browser in AIO Sandbox
        browser = await p.chromium.connect_over_cdp(
            "ws://localhost:8080/cdp"
        )
        
        page = await browser.new_page()
        
        # Navigate and interact
        await page.goto("https://example.com")
        await page.fill("input[name='search']", "test query")
        await page.click("button[type='submit']")
        
        # Wait for results
        await page.wait_for_selector(".search-results")
        
        # Extract data
        results = await page.query_selector_all(".result-item")
        for result in results:
            title = await result.inner_text()
            print(f"Result: {title}")
        
        await browser.close()

asyncio.run(playwright_example())
```

## Selenium Integration

Use Selenium WebDriver with AIO Sandbox:

```python
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

def selenium_example():
    # Configure Chrome options for remote connection
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", "localhost:9222")
    
    # Connect to browser
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        # Navigate and interact
        driver.get("https://example.com")
        
        # Find and interact with elements
        search_box = driver.find_element(By.NAME, "search")
        search_box.send_keys("test query")
        
        submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        submit_button.click()
        
        # Wait and extract results
        driver.implicitly_wait(10)
        results = driver.find_elements(By.CSS_SELECTOR, ".result-item")
        
        for result in results:
            print(f"Result: {result.text}")
            
    finally:
        driver.quit()

selenium_example()
```

## Best Practices

### Resource Management

```python
async def managed_browser_session():
    try:
        # Navigate to starting page
        await client.call_tool("browser_navigate", {
            "url": "https://example.com"
        })
        
        # Perform browser operations
        yield client
        
    finally:
        # Close browser when done
        await client.call_tool("browser_close")
```

### Error Handling

```python
async def robust_browser_automation():
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Attempt browser operation
            result = await client.call_tool("browser_navigate", {
                "url": "https://example.com"
            })
            
            if result.success:
                break
                
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                raise Exception(f"Browser operation failed after {max_retries} retries: {e}")
            
            # Wait before retry
            await asyncio.sleep(2 ** retry_count)
```

### Performance Optimization

```python
async def optimized_scraping():
    # Use multiple tabs for concurrent scraping
    urls = ["url1", "url2", "url3"]
    results = []
    
    for i, url in enumerate(urls):
        if i > 0:
            # Open new tab for additional URLs
            await client.call_tool("browser_new_tab")
            await client.call_tool("browser_switch_tab", {"index": i})
        
        # Navigate to URL
        await client.call_tool("browser_navigate", {"url": url})
        
        # Extract content
        content = await client.call_tool("browser_get_text")
        results.append(content)
    
    return results
```

## Debugging and Monitoring

### Screenshot-based Debugging

```python
async def debug_with_screenshots():
    # Take screenshot before action
    await client.call_tool("browser_screenshot", {
        "path": "/tmp/before_action.png"
    })
    
    # Perform action
    await client.call_tool("browser_click", {
        "selector": ".button"
    })
    
    # Take screenshot after action
    await client.call_tool("browser_screenshot", {
        "path": "/tmp/after_action.png"
    })
    
    # Compare or analyze screenshots as needed
```

### Console Log Monitoring

```python
async def monitor_console_logs():
    # Navigate to page
    await client.call_tool("browser_navigate", {
        "url": "https://example.com"
    })
    
    # Use JavaScript evaluation to check for console errors
    console_check = await client.call_tool("browser_evaluate", {
        "expression": """
        (() => {
            const logs = [];
            const originalError = console.error;
            console.error = (...args) => {
                logs.push({level: 'error', message: args.join(' ')});
                originalError.apply(console, args);
            };
            return JSON.stringify(logs);
        })()
        """
    })
    
    print("Console monitoring result:", console_check.result)
```

## Integration with Other AIO Sandbox Components

### Browser + File Operations

```python
async def browser_to_file_workflow():
    # Scrape data with browser
    await client.call_tool("browser_navigate", {
        "url": "https://api-docs.example.com"
    })
    
    # Extract API documentation
    docs = await client.call_tool("browser_get_text")
    
    # Save to file
    await client.call_tool("file_write", {
        "path": "/tmp/api_docs.txt",
        "content": docs.content[0].text
    })
    
    # Process with shell command
    await client.call_tool("shell_exec", {
        "command": "grep -E 'POST|GET|PUT|DELETE' /tmp/api_docs.txt > /tmp/endpoints.txt"
    })
```

### Browser + Code Execution

```python
async def browser_driven_analysis():
    # Scrape data
    await client.call_tool("browser_navigate", {
        "url": "https://data-source.com"
    })
    
    # Extract JSON data using JavaScript evaluation
    data = await client.call_tool("browser_evaluate", {
        "expression": "document.querySelector('pre.json-data').textContent"
    })
    
    # Process with Python execution
    analysis_code = f"""
import json
import pandas as pd

# Parse the scraped data
data = json.loads('''{data.result}''')

# Perform analysis
df = pd.DataFrame(data)
summary = df.describe()

print("Data Analysis Summary:")
print(summary.to_string())
"""
    
    result = await client.call_tool("jupyter_execute", {
        "code": analysis_code
    })
    
    print("Analysis Result:", result.content)
```

This comprehensive guide covers the main approaches to browser automation with AIO Sandbox. Choose the method that best fits your use case:

- **VNC** for visual interaction and debugging
- **CDP** for low-level browser control
- **MCP Browser Server** for high-level automation
- **Playwright/Selenium** for familiar frameworks

For more advanced scenarios, combine browser automation with other AIO Sandbox components like file operations, shell commands, and code execution.
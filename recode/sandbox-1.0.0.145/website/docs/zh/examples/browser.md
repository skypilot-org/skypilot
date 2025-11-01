# 浏览器自动化示例

本指南展示如何使用 AIO Sandbox 进行浏览器自动化、Web 抓取和 UI 测试。

## 概述

AIO Sandbox 提供多种与浏览器交互的方式：

- **VNC 访问**：通过远程桌面进行可视化浏览器交互
- **Chrome DevTools 协议（CDP）**：程序化浏览器控制
- **浏览器 MCP 服务器**：高级浏览器自动化工具

## VNC 浏览器访问

### 基本 VNC 交互

通过 VNC 可视化访问浏览器：

```bash
# 打开 VNC 界面
open http://localhost:8080/vnc/index.html?autoconnect=true
```

VNC 界面提供：
- 带浏览器的完整桌面环境
- 可视化交互功能
- 截图和屏幕录制
- 鼠标和键盘输入

### 自动化脚本中的 VNC

在需要视觉验证的场景中使用 VNC：

```python
import requests
import time

# 通过 VNC API 截图
response = requests.get("http://localhost:8080/vnc/screenshot")
with open("screenshot.png", "wb") as f:
    f.write(response.content)

# 发送键盘输入
requests.post("http://localhost:8080/vnc/keyboard",
              json={"keys": "Hello World"})
```

## Chrome DevTools 协议（CDP）

### CDP 连接

使用 CDP 连接到浏览器：

```javascript
const CDP = require('chrome-remote-interface');

async function example() {
    const client = await CDP();
    const {Network, Page, Runtime} = client;

    // 启用必要的域
    await Network.enable();
    await Page.enable();

    // 导航到页面
    await Page.navigate({url: 'https://example.com'});
    await Page.loadEventFired();

    // 执行 JavaScript
    const result = await Runtime.evaluate({
        expression: 'document.title'
    });

    console.log('页面标题：', result.result.value);

    await client.close();
}

example().catch(console.error);
```

### Python CDP 示例

使用 Python 和 pychrome：

```python
import pychrome
import time

# 连接到浏览器
browser = pychrome.Browser(url="http://localhost:8080")
tab = browser.new_tab()

# 启用页面域
tab.Page.enable()

# 导航到页面
tab.Page.navigate(url="https://httpbin.org/get")
tab.wait(1)

# 获取页面内容
result = tab.Runtime.evaluate(expression="document.body.innerText")
print("页面内容：", result['result']['value'])

# 清理
browser.close_tab(tab)
```

## 浏览器 MCP 服务器 API 参考

`@agent-infra/mcp-server-browser` 包提供以下工具：

### 导航和基本操作
- `browser_navigate` - 导航到 URL
- `browser_go_back` - 返回上一页
- `browser_go_forward` - 前进到下一页
- `browser_close` - 关闭浏览器

### 元素交互
- `browser_get_clickable_elements` - 获取可点击/可悬停/可选择的元素
- `browser_click` - 点击元素（使用元素索引）
- `browser_hover` - 悬停在元素上
- `browser_select` - 选择元素
- `browser_form_input_fill` - 填充输入字段

### 内容检索
- `browser_get_text` - 获取页面文本内容
- `browser_get_markdown` - 获取页面的 Markdown 格式
- `browser_read_links` - 获取所有页面链接
- `browser_screenshot` - 截图

### 高级功能
- `browser_scroll` - 滚动页面
- `browser_evaluate` - 执行 JavaScript
- `browser_new_tab` - 打开新标签页
- `browser_switch_tab` - 在标签页之间切换
- `browser_tab_list` - 列出所有标签页
- `browser_get_download_list` - 获取下载的文件

### 视觉模式（可选）
- `browser_vision_screen_capture` - 视觉模式截图
- `browser_vision_screen_click` - 基于视觉的点击

## 浏览器 MCP 服务器集成

### Python 与 MCP 客户端

使用浏览器 MCP 服务器进行高级自动化：

```python
import asyncio
from mcp import Client
import httpx

async def browser_automation():
    async with httpx.AsyncClient() as http_client:
        # 连接到 MCP 服务器
        async with Client("http://localhost:8080/mcp") as client:

            # 导航到页面
            result = await client.call_tool("browser_navigate", {
                "url": "https://example.com"
            })

            # 截图
            screenshot = await client.call_tool("browser_screenshot", {
                "path": "/tmp/screenshot.png"
            })

            # 获取页面文本内容
            content = await client.call_tool("browser_get_text")

            # 获取可点击元素
            elements = await client.call_tool("browser_get_clickable_elements")

            print("页面内容：", content.content[0].text)
            print("可点击元素：", elements.content)

asyncio.run(browser_automation())
```

### JavaScript MCP 集成

```javascript
const { McpClient } = require('@modelcontextprotocol/sdk');

async function browserAutomation() {
    const client = new McpClient("http://localhost:8080/mcp");

    try {
        await client.connect();

        // 导航到页面
        await client.callTool("browser_navigate", {
            url: "https://news.ycombinator.com"
        });

        // 等待页面加载
        await new Promise(resolve => setTimeout(resolve, 2000));

        // 获取可点击元素（如文章标题）
        const elements = await client.callTool("browser_get_clickable_elements");

        // 获取页面 Markdown 内容
        const markdown = await client.callTool("browser_get_markdown");

        console.log("可点击元素：", elements.content);
        console.log("页面内容：", markdown.content);

    } finally {
        await client.disconnect();
    }
}

browserAutomation().catch(console.error);
```

## Web 抓取示例

### 电商价格监控

```python
import asyncio
import json
from datetime import datetime

async def monitor_prices():
    # 要监控的产品 URL
    products = [
        {"name": "笔记本电脑", "url": "https://example-store.com/laptop"},
        {"name": "手机", "url": "https://example-store.com/phone"}
    ]

    results = []

    for product in products:
        # 使用浏览器导航并提取价格
        navigation_result = await client.call_tool("browser_navigate", {
            "url": product["url"]
        })

        # 获取可点击元素并提取价格
        elements = await client.call_tool("browser_get_clickable_elements")
        price_text = await client.call_tool("browser_get_text")

        results.append({
            "product": product["name"],
            "price": price_text.content[0].text,
            "timestamp": datetime.now().isoformat(),
            "url": product["url"]
        })

    # 通过文件 API 保存结果到文件
    await client.call_tool("file_write", {
        "path": "/tmp/price_data.json",
        "content": json.dumps(results, indent=2)
    })

    return results

# 运行价格监控
prices = asyncio.run(monitor_prices())
print("价格监控完成：", prices)
```

### 社交媒体内容提取

```python
async def extract_social_posts():
    # 导航到社交媒体页面
    await client.call_tool("browser_navigate", {
        "url": "https://example-social.com/trending"
    })

    # 等待动态内容加载
    await asyncio.sleep(3)

    # 获取页面内容和可点击元素
    content = await client.call_tool("browser_get_text")
    elements = await client.call_tool("browser_get_clickable_elements")

    # 处理提取的数据
    return {
        "page_content": content.content[0].text,
        "clickable_elements": elements.content
    }
```

## 浏览器测试示例

### 表单自动化测试

```python
async def test_contact_form():
    # 导航到表单页面
    await client.call_tool("browser_navigate", {
        "url": "https://example.com/contact"
    })

    # 首先获取表单元素
    elements = await client.call_tool("browser_get_clickable_elements")

    # 使用 form_input_fill 填充表单字段
    await client.call_tool("browser_form_input_fill", {
        "selector": "input[name='name']",
        "value": "测试用户"
    })

    await client.call_tool("browser_form_input_fill", {
        "selector": "input[name='email']",
        "value": "test@example.com"
    })

    await client.call_tool("browser_form_input_fill", {
        "selector": "textarea[name='message']",
        "value": "这是一条测试消息"
    })

    # 点击提交按钮提交表单
    await client.call_tool("browser_click", {
        "index": 0  # 使用元素中提交按钮的索引
    })

    # 等待响应
    await asyncio.sleep(2)

    # 通过获取页面文本验证成功消息
    page_text = await client.call_tool("browser_get_text")

    assert "谢谢" in page_text.content[0].text
    print("表单提交测试通过！")
```

### 性能测试

```python
async def measure_page_performance():
    start_time = time.time()

    # 导航到页面
    await client.call_tool("browser_navigate", {
        "url": "https://example-app.com"
    })

    # 等待页面加载（使用 sleep 或评估 JS）
    await asyncio.sleep(3)

    load_time = time.time() - start_time

    # 使用 JavaScript 评估获取性能指标
    metrics = await client.call_tool("browser_evaluate", {
        "expression": "JSON.stringify({loadTime: performance.timing.loadEventEnd - performance.timing.navigationStart, domContentLoaded: performance.timing.domContentLoadedEventEnd - performance.timing.navigationStart})"
    })

    return {
        "total_load_time": load_time,
        "performance_metrics": metrics.result
    }
```

## Playwright 集成

对于高级浏览器自动化，集成 Playwright：

```python
from playwright.async_api import async_playwright
import asyncio

async def playwright_example():
    async with async_playwright() as p:
        # 连接到 AIO Sandbox 中的现有浏览器
        browser = await p.chromium.connect_over_cdp(
            "ws://localhost:8080/cdp"
        )

        page = await browser.new_page()

        # 导航和交互
        await page.goto("https://example.com")
        await page.fill("input[name='search']", "测试查询")
        await page.click("button[type='submit']")

        # 等待结果
        await page.wait_for_selector(".search-results")

        # 提取数据
        results = await page.query_selector_all(".result-item")
        for result in results:
            title = await result.inner_text()
            print(f"结果：{title}")

        await browser.close()

asyncio.run(playwright_example())
```

## Selenium 集成

使用 Selenium WebDriver 与 AIO Sandbox：

```python
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

def selenium_example():
    # 配置 Chrome 选项以进行远程连接
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", "localhost:9222")

    # 连接到浏览器
    driver = webdriver.Chrome(options=chrome_options)

    try:
        # 导航和交互
        driver.get("https://example.com")

        # 查找和交互元素
        search_box = driver.find_element(By.NAME, "search")
        search_box.send_keys("测试查询")

        submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        submit_button.click()

        # 等待并提取结果
        driver.implicitly_wait(10)
        results = driver.find_elements(By.CSS_SELECTOR, ".result-item")

        for result in results:
            print(f"结果：{result.text}")

    finally:
        driver.quit()

selenium_example()
```

## 最佳实践

### 资源管理

```python
async def managed_browser_session():
    try:
        # 导航到起始页面
        await client.call_tool("browser_navigate", {
            "url": "https://example.com"
        })

        # 执行浏览器操作
        yield client

    finally:
        # 完成后关闭浏览器
        await client.call_tool("browser_close")
```

### 错误处理

```python
async def robust_browser_automation():
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            # 尝试浏览器操作
            result = await client.call_tool("browser_navigate", {
                "url": "https://example.com"
            })

            if result.success:
                break

        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                raise Exception(f"{max_retries} 次重试后浏览器操作失败：{e}")

            # 重试前等待
            await asyncio.sleep(2 ** retry_count)
```

### 性能优化

```python
async def optimized_scraping():
    # 使用多个标签页进行并发抓取
    urls = ["url1", "url2", "url3"]
    results = []

    for i, url in enumerate(urls):
        if i > 0:
            # 为其他 URL 打开新标签页
            await client.call_tool("browser_new_tab")
            await client.call_tool("browser_switch_tab", {"index": i})

        # 导航到 URL
        await client.call_tool("browser_navigate", {"url": url})

        # 提取内容
        content = await client.call_tool("browser_get_text")
        results.append(content)

    return results
```

## 调试和监控

### 基于截图的调试

```python
async def debug_with_screenshots():
    # 操作前截图
    await client.call_tool("browser_screenshot", {
        "path": "/tmp/before_action.png"
    })

    # 执行操作
    await client.call_tool("browser_click", {
        "selector": ".button"
    })

    # 操作后截图
    await client.call_tool("browser_screenshot", {
        "path": "/tmp/after_action.png"
    })

    # 根据需要比较或分析截图
```

### 控制台日志监控

```python
async def monitor_console_logs():
    # 导航到页面
    await client.call_tool("browser_navigate", {
        "url": "https://example.com"
    })

    # 使用 JavaScript 评估检查控制台错误
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

    print("控制台监控结果：", console_check.result)
```

## 与其他 AIO Sandbox 组件集成

### 浏览器 + 文件操作

```python
async def browser_to_file_workflow():
    # 使用浏览器抓取数据
    await client.call_tool("browser_navigate", {
        "url": "https://api-docs.example.com"
    })

    # 提取 API 文档
    docs = await client.call_tool("browser_get_text")

    # 保存到文件
    await client.call_tool("file_write", {
        "path": "/tmp/api_docs.txt",
        "content": docs.content[0].text
    })

    # 使用 Shell 命令处理
    await client.call_tool("shell_exec", {
        "command": "grep -E 'POST|GET|PUT|DELETE' /tmp/api_docs.txt > /tmp/endpoints.txt"
    })
```

### 浏览器 + 代码执行

```python
async def browser_driven_analysis():
    # 抓取数据
    await client.call_tool("browser_navigate", {
        "url": "https://data-source.com"
    })

    # 使用 JavaScript 评估提取 JSON 数据
    data = await client.call_tool("browser_evaluate", {
        "expression": "document.querySelector('pre.json-data').textContent"
    })

    # 使用 Python 执行处理
    analysis_code = f"""
import json
import pandas as pd

# 解析抓取的数据
data = json.loads('''{data.result}''')

# 执行分析
df = pd.DataFrame(data)
summary = df.describe()

print("数据分析摘要：")
print(summary.to_string())
"""

    result = await client.call_tool("jupyter_execute", {
        "code": analysis_code
    })

    print("分析结果：", result.content)
```

这份全面的指南涵盖了使用 AIO Sandbox 进行浏览器自动化的主要方法。选择最适合您用例的方法：

- **VNC** 用于可视化交互和调试
- **CDP** 用于底层浏览器控制
- **MCP 浏览器服务器** 用于高级自动化
- **Playwright/Selenium** 用于熟悉的框架

对于更高级的场景，将浏览器自动化与其他 AIO Sandbox 组件（如文件操作、Shell 命令和代码执行）结合使用。
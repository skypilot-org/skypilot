# Examples

This section provides practical examples and integration guides for using AIO Sandbox in real-world scenarios.

## Quick Examples

### Terminal Integration
Learn how to integrate the WebSocket terminal into your applications:
- [Basic Terminal Client](/examples/terminal) - Simple terminal integration
- [Advanced Terminal Features](/examples/terminal#advanced-features) - Session management and reconnection

### Browser Automation
Explore browser automation capabilities:
- [Browser Use Integration](/examples/browser) - Python browser automation
- [Playwright Integration](/examples/browser#playwright) - Advanced browser control
- [Web Scraping Examples](/examples/browser#scraping) - Data extraction patterns

### Agent Integration
Build AI agents with AIO Sandbox:
- [Basic Agent Setup](/examples/agent) - Connect agents to sandbox
- [MCP Integration](/examples/agent#mcp) - Use Model Context Protocol
- [Multi-Tool Workflows](/examples/agent#workflows) - Combine multiple APIs

## Integration Patterns

### Docker Compose Setup
```yaml
version: '3.8'
services:
  aio-sandbox:
    image: ghcr.io/agent-infra/sandbox:latest
    ports:
      - "8080:8080"
    volumes:
      - sandbox_data:/workspace
    restart: unless-stopped

volumes:
  sandbox_data:
```

### Kubernetes Deployment
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aio-sandbox
spec:
  replicas: 2
  selector:
    matchLabels:
      app: aio-sandbox
  template:
    metadata:
      labels:
        app: aio-sandbox
    spec:
      containers:
      - name: sandbox
        image: ghcr.io/agent-infra/sandbox:latest
        ports:
        - containerPort: 8080
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
---
apiVersion: v1
kind: Service
metadata:
  name: aio-sandbox-service
spec:
  selector:
    app: aio-sandbox
  ports:
  - port: 80
    targetPort: 8080
  type: LoadBalancer
```

## SDK Examples

### Python SDK

Install the Python SDK for AIO Sandbox:

```bash
pip install aio-sandbox
```

#### Basic Configuration

Import and configure the Python client:

```python
from aio_sandbox import AioClient
import asyncio

# Initialize the client
client = AioClient(
    base_url="http://localhost:8080",  # AIO Sandbox URL
    timeout=30.0,  # Request timeout in seconds
    retries=3,     # Number of retry attempts
    retry_delay=1.0  # Delay between retries
)
```

#### Shell Operations

Execute shell commands and manage sessions:

```python
async def shell_example():
    # Execute a simple command
    result = await client.shell.exec(command="ls -la")

    if result.success:
        print(f"Output: {result.data.output}")
        print(f"Exit code: {result.data.exit_code}")

    # Execute with session management
    session_id = "my-session-1"
    await client.shell.exec(
        command="cd /workspace && pwd",
        session_id=session_id
    )

    # Continue in the same session
    result = await client.shell.exec(
        command="ls",
        session_id=session_id
    )

    # Asynchronous execution for long-running tasks
    await client.shell.exec(
        command="python long_script.py",
        async_mode=True,
        session_id=session_id
    )

    # View session output
    view_result = await client.shell.view(session_id=session_id)
    print(view_result.data.output)

# Run the example
asyncio.run(shell_example())
```

#### File Operations

Manage files and directories:

```python
async def file_example():
    # Write a file
    await client.file.write(
        file="/tmp/example.py",
        content="""
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(0, 10, 100)
y = np.sin(x)

plt.plot(x, y)
plt.savefig('/tmp/plot.png')
print("Plot saved!")
        """.strip()
    )

    # Read file content
    content = await client.file.read(file="/tmp/example.py")
    if content.success:
        print(f"File content:\n{content.data.content}")

    # List directory contents
    files = await client.file.list(
        path="/tmp",
        recursive=True,
        include_size=True
    )

    for file_info in files.data.files:
        print(f"{file_info.name}: {file_info.size} bytes")

    # Search in files
    search_result = await client.file.search(
        file="/tmp/example.py",
        regex=r"import \w+"
    )

    if search_result.success:
        for match in search_result.data.matches:
            print(f"Line {match.line}: {match.content}")

    # Find files by pattern
    found_files = await client.file.find(
        path="/tmp",
        glob="*.py"
    )

asyncio.run(file_example())
```

#### Code Execution

Execute Python and JavaScript code securely:

```python
async def code_execution_example():
    # Execute Python code in Jupyter kernel
    jupyter_result = await client.jupyter.execute(
        code="""
import pandas as pd
import numpy as np

# Create sample data
df = pd.DataFrame({
    'x': np.random.randn(100),
    'y': np.random.randn(100)
})

print(f"DataFrame shape: {df.shape}")
print(df.head())
        """,
        timeout=60,
        session_id="data-analysis-session"
    )

    if jupyter_result.success:
        print("Jupyter Output:")
        for output in jupyter_result.data.outputs:
            if output.output_type == "stream":
                print(output.text)
            elif output.output_type == "execute_result":
                print(output.data.get("text/plain", ""))

    # Execute Node.js code
    nodejs_result = await client.nodejs.execute(
        code="""
const fs = require('fs');
const path = require('path');

// Read package.json if it exists
try {
    const packagePath = path.join(process.cwd(), 'package.json');
    if (fs.existsSync(packagePath)) {
        const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
        console.log(`Project: ${pkg.name || 'Unknown'}`);
        console.log(`Version: ${pkg.version || 'Unknown'}`);
    } else {
        console.log('No package.json found');
    }
} catch (error) {
    console.error('Error:', error.message);
}
        """,
        timeout=30
    )

    if nodejs_result.success:
        print(f"Node.js Output: {nodejs_result.data.stdout}")

asyncio.run(code_execution_example())
```

#### MCP Integration

Work with Model Context Protocol services:

```python
async def mcp_example():
    # List available MCP servers
    servers = await client.mcp.list_servers()
    print("Available MCP servers:", servers.data)

    # Get tools from a specific server
    browser_tools = await client.mcp.list_tools(server_name="browser")

    for tool in browser_tools.data.tools:
        print(f"Tool: {tool.name}")
        print(f"Description: {tool.description}")

    # Execute a tool
    screenshot_result = await client.mcp.execute_tool(
        server_name="browser",
        tool_name="screenshot",
        arguments={
            "url": "https://example.com",
            "width": 1920,
            "height": 1080
        }
    )

    if screenshot_result.success:
        # Save screenshot data
        await client.file.write(
            file="/tmp/screenshot.png",
            content=screenshot_result.data.content[0].data,  # Base64 image data
            append=False
        )

asyncio.run(mcp_example())
```

#### Error Handling and Best Practices

```python
async def robust_example():
    try:
        # Always use context managers for resource cleanup
        async with AioClient("http://localhost:8080") as client:
            # Set up error handling
            result = await client.shell.exec("potentially-failing-command")

            if not result.success:
                print(f"Command failed: {result.message}")
                if hasattr(result, 'error_code'):
                    print(f"Error code: {result.error_code}")

            # Check sandbox status
            status = await client.sandbox.get_context()
            print(f"Sandbox uptime: {status.data.uptime}")
            print(f"Available packages: {len(status.data.packages)}")

    except Exception as e:
        print(f"Connection error: {e}")

asyncio.run(robust_example())
```


### Node.js SDK

To install the SDK, use the following command:

```bash
npm install @agent-infra/sandbox
```


#### Basic Configuration

Start by importing the SDK and configuring the client:

```typescript
import { AioClient } from "@agent-infra/sandbox";

const client = new AioClient({
  baseUrl: `https://{aio.sandbox.example}`, //The Url and Port should consistent with the Aio Sandbox
  timeout: 30000, // Optional: request timeout in milliseconds
  retries: 3, // Optional: number of retry attempts
  retryDelay: 1000, // Optional: delay between retries in milliseconds
});
```

#### Shell Execution

Execute shell commands within the sandbox:

```typescript
const response = await client.shellExec({
  command: "ls -la",
});

if (response.success) {
  console.log("Command Output:", response.data.output);
} else {
  console.error("Error:", response.message);
}

// Asynchronous rotation training results, suitable for long-term tasks
const response = await client.shellExecWithPolling({
  command: "ls -la",
  maxWaitTime: 60 * 1000,
});
```

#### File Management

List files in a directory:

```typescript
const fileList = await client.fileList({
  path: "/home/gem",
  recursive: true,
});

if (fileList.success) {
  console.log("Files:", fileList.data.files);
} else {
  console.error("Error:", fileList.message);
}
```

#### Jupyter Code Execution

Run Jupyter notebook code:

```typescript
const jupyterResponse = await client.jupyterExecute({
  code: "print('Hello, Jupyter!')",
  kernel_name: "python3",
});

if (jupyterResponse.success) {
  console.log("Output:", jupyterResponse.data);
} else {
  console.error("Error:", jupyterResponse.message);
}
```

## Next Steps

Ready to implement these patterns? Choose your path:

- **[Terminal Examples](/examples/terminal)** - Start with terminal integration
- **[Browser Examples](/examples/browser)** - Explore browser automation
- **[Agent Integration](/examples/agent)** - Build AI-powered workflows

For additional support:
- Check the [API documentation](/api/) for detailed specifications
- Explore the [GitHub repository](https://github.com/agent-infra/sandbox) for latest updates

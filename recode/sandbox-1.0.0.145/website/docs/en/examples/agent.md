# Agent Integration

This guide shows how to integrate AI agents with AIO Sandbox, leveraging the Model Context Protocol (MCP) and REST APIs for powerful agent workflows.

## Quick Start

### Basic Agent Setup

The simplest way to connect an agent to AIO Sandbox is through the MCP interface:

```python
import asyncio
import aiohttp
from typing import Dict, Any

class AIOSandboxAgent:
    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def execute_shell(self, command: str) -> Dict[str, Any]:
        """Execute shell command in sandbox"""
        url = f"{self.base_url}/v1/shell/exec"
        payload = {"command": command}

        async with self.session.post(url, json=payload) as response:
            return await response.json()

    async def read_file(self, file_path: str) -> Dict[str, Any]:
        """Read file content from sandbox"""
        url = f"{self.base_url}/v1/file/read"
        payload = {"file": file_path}

        async with self.session.post(url, json=payload) as response:
            return await response.json()

    async def write_file(self, file_path: str, content: str) -> Dict[str, Any]:
        """Write file content to sandbox"""
        url = f"{self.base_url}/v1/file/write"
        payload = {"file": file_path, "content": content}

        async with self.session.post(url, json=payload) as response:
            return await response.json()

# Usage example
async def main():
    async with AIOSandboxAgent() as agent:
        # Execute a command
        result = await agent.execute_shell("python --version")
        print(f"Python version: {result}")

        # Create and read a file
        await agent.write_file("/tmp/hello.py", "print('Hello from agent!')")
        await agent.execute_shell("python /tmp/hello.py")

if __name__ == "__main__":
    asyncio.run(main())
```

### MCP Integration

AIO Sandbox provides built-in MCP servers for seamless agent integration:

```python
import json
import websockets
from typing import Dict, List

class MCPClient:
    def __init__(self, mcp_url: str = "ws://localhost:8080/mcp"):
        self.mcp_url = mcp_url
        self.websocket = None

    async def connect(self):
        """Connect to MCP WebSocket interface"""
        self.websocket = await websockets.connect(self.mcp_url)

    async def list_servers(self) -> List[str]:
        """List available MCP servers"""
        message = {
            "jsonrpc": "2.0",
            "method": "servers/list",
            "id": 1
        }
        await self.websocket.send(json.dumps(message))
        response = await self.websocket.recv()
        return json.loads(response)

    async def list_tools(self, server_name: str) -> Dict:
        """List tools available in a specific server"""
        message = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {"server": server_name},
            "id": 2
        }
        await self.websocket.send(json.dumps(message))
        response = await self.websocket.recv()
        return json.loads(response)

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict) -> Dict:
        """Execute a tool on a specific MCP server"""
        message = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "server": server_name,
                "name": tool_name,
                "arguments": arguments
            },
            "id": 3
        }
        await self.websocket.send(json.dumps(message))
        response = await self.websocket.recv()
        return json.loads(response)

# Usage example
async def mcp_example():
    client = MCPClient()
    await client.connect()

    # List available servers
    servers = await client.list_servers()
    print(f"Available servers: {servers}")

    # Use the browser server to navigate
    await client.call_tool("browser", "navigate", {
        "url": "https://example.com"
    })

    # Use the file server to create a file
    await client.call_tool("file", "write", {
        "path": "/tmp/scraped_data.html",
        "content": "<html>...</html>"
    })
```

## Advanced Workflows

### Multi-Tool Agent Workflow

Here's an example of a complex agent that combines browser automation, file operations, and code execution:

```python
class WebScrapingAgent(AIOSandboxAgent):
    async def scrape_and_analyze(self, url: str, analysis_script: str):
        """Complete workflow: scrape website, save data, and analyze"""

        # Step 1: Navigate to website and extract content
        await self.navigate_browser(url)
        content = await self.extract_page_content()

        # Step 2: Save content to file
        data_file = "/tmp/scraped_data.html"
        await self.write_file(data_file, content)

        # Step 3: Create analysis script
        script_file = "/tmp/analyze.py"
        await self.write_file(script_file, analysis_script)

        # Step 4: Execute analysis
        result = await self.execute_shell(f"python {script_file} {data_file}")

        # Step 5: Return results
        return {
            "url": url,
            "content_length": len(content),
            "analysis_result": result
        }

    async def navigate_browser(self, url: str):
        """Use browser MCP server to navigate"""
        # Implementation using MCP browser server
        pass

    async def extract_page_content(self) -> str:
        """Extract page content using browser automation"""
        # Implementation using browser APIs
        pass

# Usage
async def run_scraping_agent():
    analysis_script = '''
import sys
from bs4 import BeautifulSoup

with open(sys.argv[1], 'r') as f:
    content = f.read()

soup = BeautifulSoup(content, 'html.parser')
print(f"Title: {soup.title.string if soup.title else 'No title'}")
print(f"Links found: {len(soup.find_all('a'))}")
print(f"Images found: {len(soup.find_all('img'))}")
'''

    async with WebScrapingAgent() as agent:
        result = await agent.scrape_and_analyze(
            "https://example.com",
            analysis_script
        )
        print(json.dumps(result, indent=2))
```

### Session Management

For long-running agent workflows, manage shell sessions effectively:

```python
class SessionManagedAgent(AIOSandboxAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shell_session_id = None

    async def create_shell_session(self):
        """Create a persistent shell session"""
        result = await self.execute_shell("echo 'Session started'")
        # Extract session ID from response
        self.shell_session_id = result.get('session_id')
        return self.shell_session_id

    async def execute_in_session(self, command: str):
        """Execute command in the persistent session"""
        if not self.shell_session_id:
            await self.create_shell_session()

        url = f"{self.base_url}/v1/shell/exec"
        payload = {
            "command": command,
            "id": self.shell_session_id
        }

        async with self.session.post(url, json=payload) as response:
            return await response.json()

    async def setup_environment(self):
        """Set up a custom environment in the session"""
        commands = [
            "export PYTHONPATH=/workspace:$PYTHONPATH",
            "cd /workspace",
            "pip install -r requirements.txt",
            "echo 'Environment ready'"
        ]

        for cmd in commands:
            result = await self.execute_in_session(cmd)
            print(f"Setup: {cmd} -> {result.get('success', False)}")

# Usage
async def persistent_session_example():
    async with SessionManagedAgent() as agent:
        await agent.setup_environment()

        # Now all commands run in the same environment
        await agent.execute_in_session("python my_script.py")
        await agent.execute_in_session("ls -la results/")
```

## Integration Patterns

### LangChain Integration

Integrate AIO Sandbox with LangChain for advanced agent workflows:

```python
from langchain.tools import BaseTool
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

class SandboxExecuteTool(BaseTool):
    name = "sandbox_execute"
    description = "Execute shell commands in AIO Sandbox"
    agent: AIOSandboxAgent = Field(...)

    def _run(self, command: str) -> str:
        """Execute command synchronously"""
        import asyncio
        return asyncio.run(self._arun(command))

    async def _arun(self, command: str) -> str:
        """Execute command asynchronously"""
        result = await self.agent.execute_shell(command)
        if result.get('success'):
            return result.get('data', {}).get('output', '')
        else:
            return f"Error: {result.get('message', 'Unknown error')}"

class SandboxFileTool(BaseTool):
    name = "sandbox_file_ops"
    description = "Read and write files in AIO Sandbox"
    agent: AIOSandboxAgent = Field(...)

    def _run(self, action: str, path: str, content: str = "") -> str:
        """Perform file operations"""
        import asyncio
        return asyncio.run(self._arun(action, path, content))

    async def _arun(self, action: str, path: str, content: str = "") -> str:
        """Perform file operations asynchronously"""
        if action == "read":
            result = await self.agent.read_file(path)
        elif action == "write":
            result = await self.agent.write_file(path, content)
        else:
            return f"Unknown action: {action}"

        if result.get('success'):
            return str(result.get('data', ''))
        else:
            return f"Error: {result.get('message', 'Unknown error')}"

# Create LangChain agent with sandbox tools
async def create_sandbox_agent():
    sandbox_agent = AIOSandboxAgent()
    await sandbox_agent.__aenter__()

    tools = [
        SandboxExecuteTool(agent=sandbox_agent),
        SandboxFileTool(agent=sandbox_agent)
    ]

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AI assistant with access to a sandbox environment."),
        ("user", "{input}"),
        ("assistant", "{agent_scratchpad}")
    ])

    # Create and return agent (requires LLM configuration)
    # agent = create_openai_functions_agent(llm, tools, prompt)
    # return AgentExecutor(agent=agent, tools=tools)
```

### OpenAI Assistant Integration

Create an OpenAI Assistant with sandbox capabilities:

```python
import openai
from openai import OpenAI

class OpenAISandboxAssistant:
    def __init__(self, api_key: str, sandbox_url: str = "http://localhost:8080"):
        self.client = OpenAI(api_key=api_key)
        self.sandbox = AIOSandboxAgent(sandbox_url)

    async def create_assistant(self):
        """Create OpenAI Assistant with sandbox function calls"""
        assistant = self.client.beta.assistants.create(
            name="AIO Sandbox Assistant",
            instructions="You help users execute code and manage files in a sandbox environment.",
            model="gpt-4-turbo-preview",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": "Execute shell commands in sandbox",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {"type": "string", "description": "Shell command to execute"}
                            },
                            "required": ["command"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "manage_file",
                        "description": "Read or write files in sandbox",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["read", "write"]},
                                "path": {"type": "string", "description": "File path"},
                                "content": {"type": "string", "description": "Content to write (for write action)"}
                            },
                            "required": ["action", "path"]
                        }
                    }
                }
            ]
        )
        return assistant

    async def handle_function_call(self, function_name: str, arguments: dict):
        """Handle function calls from OpenAI Assistant"""
        async with self.sandbox:
            if function_name == "execute_shell":
                return await self.sandbox.execute_shell(arguments["command"])
            elif function_name == "manage_file":
                if arguments["action"] == "read":
                    return await self.sandbox.read_file(arguments["path"])
                elif arguments["action"] == "write":
                    return await self.sandbox.write_file(
                        arguments["path"],
                        arguments.get("content", "")
                    )
```

## Error Handling and Best Practices

### Robust Error Handling

```python
class RobustSandboxAgent(AIOSandboxAgent):
    async def safe_execute(self, command: str, max_retries: int = 3):
        """Execute command with retries and error handling"""
        for attempt in range(max_retries):
            try:
                result = await self.execute_shell(command)
                if result.get('success'):
                    return result
                else:
                    print(f"Attempt {attempt + 1} failed: {result.get('message')}")
            except Exception as e:
                print(f"Exception on attempt {attempt + 1}: {str(e)}")

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        raise Exception(f"Command failed after {max_retries} attempts: {command}")

    async def validate_environment(self):
        """Validate sandbox environment before operations"""
        checks = [
            ("python --version", "Python runtime"),
            ("node --version", "Node.js runtime"),
            ("ls /tmp", "Temporary directory access")
        ]

        results = {}
        for command, description in checks:
            try:
                result = await self.safe_execute(command)
                results[description] = result.get('success', False)
            except Exception as e:
                results[description] = False
                print(f"Validation failed for {description}: {e}")

        return results
```

### Performance Optimization

```python
class OptimizedSandboxAgent(AIOSandboxAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection_pool = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            connector=self.connection_pool,
            timeout=self.timeout
        )
        return self

    async def batch_execute(self, commands: List[str]):
        """Execute multiple commands concurrently"""
        tasks = [self.execute_shell(cmd) for cmd in commands]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return [
            result if not isinstance(result, Exception) else {"error": str(result)}
            for result in results
        ]
```

## Testing Strategies

### Unit Testing

```python
import unittest
from unittest.mock import AsyncMock, patch

class TestSandboxAgent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.agent = AIOSandboxAgent("http://test-sandbox:8080")
        await self.agent.__aenter__()

    async def asyncTearDown(self):
        await self.agent.__aexit__(None, None, None)

    @patch('aiohttp.ClientSession.post')
    async def test_execute_shell_success(self, mock_post):
        # Mock successful response
        mock_response = AsyncMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {"output": "Hello World"}
        }
        mock_post.return_value.__aenter__.return_value = mock_response

        result = await self.agent.execute_shell("echo 'Hello World'")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["output"], "Hello World")

    async def test_file_operations(self):
        # Test file write and read cycle
        test_content = "Test file content"
        test_path = "/tmp/test_file.txt"

        # Write file
        write_result = await self.agent.write_file(test_path, test_content)
        self.assertTrue(write_result.get("success"))

        # Read file
        read_result = await self.agent.read_file(test_path)
        self.assertTrue(read_result.get("success"))
        self.assertEqual(read_result["data"]["content"], test_content)

if __name__ == "__main__":
    unittest.main()
```

## Production Deployment

### Docker Compose for Agent + Sandbox

```yaml
version: '3.8'
services:
  aio-sandbox:
    image: ghcr.io/agent-infra/sandbox:latest
    ports:
      - "8080:8080"
    volumes:
      - sandbox_data:/workspace
    environment:
      - SANDBOX_MEMORY_LIMIT=2g
      - SANDBOX_CPU_LIMIT=1000m
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/v1/sandbox"]
      interval: 30s
      timeout: 10s
      retries: 3

  ai-agent:
    build: ./agent
    environment:
      - SANDBOX_URL=http://aio-sandbox:8080
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    depends_on:
      - aio-sandbox
    restart: unless-stopped

volumes:
  sandbox_data:
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ai-agent-with-sandbox
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ai-agent
  template:
    metadata:
      labels:
        app: ai-agent
    spec:
      containers:
      - name: aio-sandbox
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
        livenessProbe:
          httpGet:
            path: /v1/sandbox
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 30

      - name: ai-agent
        image: your-registry/ai-agent:latest
        env:
        - name: SANDBOX_URL
          value: "http://localhost:8080"
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
```

## Next Steps

Ready to build your own AI agents? Here are some recommended paths:

1. **Start Simple**: Begin with the basic agent setup and gradually add complexity
2. **Choose Your Framework**: Pick LangChain, OpenAI Assistants, or build custom solutions
3. **Test Thoroughly**: Use the provided testing strategies to ensure reliability
4. **Monitor Performance**: Implement logging and metrics for production deployments
5. **Scale Appropriately**: Use Docker Compose for development, Kubernetes for production

For more examples and advanced patterns:
- Check the [Browser Automation examples](/examples/browser)
- Explore [Terminal Integration patterns](/examples/terminal)
- Review the [API documentation](/api/) for detailed specifications
- Visit the [GitHub repository](https://github.com/agent-infra/sandbox) for latest updates

Need help? Join our community or open an issue on GitHub!

# Agent 集成

本指南展示如何将 AI Agent 与 AIO Sandbox 集成，利用模型上下文协议（MCP）和 REST API 实现强大的 Agent 工作流。

## 快速开始

### 基本 Agent 设置

通过 MCP 接口连接 Agent 到 AIO Sandbox 的最简单方法：

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
        """在沙盒中执行 Shell 命令"""
        url = f"{self.base_url}/v1/shell/exec"
        payload = {"command": command}

        async with self.session.post(url, json=payload) as response:
            return await response.json()

    async def read_file(self, file_path: str) -> Dict[str, Any]:
        """从沙盒读取文件内容"""
        url = f"{self.base_url}/v1/file/read"
        payload = {"file": file_path}

        async with self.session.post(url, json=payload) as response:
            return await response.json()

    async def write_file(self, file_path: str, content: str) -> Dict[str, Any]:
        """将文件内容写入沙盒"""
        url = f"{self.base_url}/v1/file/write"
        payload = {"file": file_path, "content": content}

        async with self.session.post(url, json=payload) as response:
            return await response.json()

# 使用示例
async def main():
    async with AIOSandboxAgent() as agent:
        # 执行命令
        result = await agent.execute_shell("python --version")
        print(f"Python 版本：{result}")

        # 创建并读取文件
        await agent.write_file("/tmp/hello.py", "print('来自 Agent 的问候！')")
        await agent.execute_shell("python /tmp/hello.py")

if __name__ == "__main__":
    asyncio.run(main())
```

### MCP 集成

AIO Sandbox 提供内置 MCP 服务器，实现无缝 Agent 集成：

```python
import json
import websockets
from typing import Dict, List

class MCPClient:
    def __init__(self, mcp_url: str = "ws://localhost:8080/mcp"):
        self.mcp_url = mcp_url
        self.websocket = None

    async def connect(self):
        """连接到 MCP WebSocket 接口"""
        self.websocket = await websockets.connect(self.mcp_url)

    async def list_servers(self) -> List[str]:
        """列出可用的 MCP 服务器"""
        message = {
            "jsonrpc": "2.0",
            "method": "servers/list",
            "id": 1
        }
        await self.websocket.send(json.dumps(message))
        response = await self.websocket.recv()
        return json.loads(response)

    async def list_tools(self, server_name: str) -> Dict:
        """列出特定服务器中可用的工具"""
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
        """在特定 MCP 服务器上执行工具"""
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

# 使用示例
async def mcp_example():
    client = MCPClient()
    await client.connect()

    # 列出可用服务器
    servers = await client.list_servers()
    print(f"可用服务器：{servers}")

    # 使用浏览器服务器进行导航
    await client.call_tool("browser", "navigate", {
        "url": "https://example.com"
    })

    # 使用文件服务器创建文件
    await client.call_tool("file", "write", {
        "path": "/tmp/scraped_data.html",
        "content": "<html>...</html>"
    })
```

## 高级工作流

### 多工具 Agent 工作流

这是一个结合浏览器自动化、文件操作和代码执行的复杂 Agent 示例：

```python
class WebScrapingAgent(AIOSandboxAgent):
    async def scrape_and_analyze(self, url: str, analysis_script: str):
        """完整工作流：抓取网站、保存数据并分析"""

        # 步骤 1：导航到网站并提取内容
        await self.navigate_browser(url)
        content = await self.extract_page_content()

        # 步骤 2：将内容保存到文件
        data_file = "/tmp/scraped_data.html"
        await self.write_file(data_file, content)

        # 步骤 3：创建分析脚本
        script_file = "/tmp/analyze.py"
        await self.write_file(script_file, analysis_script)

        # 步骤 4：执行分析
        result = await self.execute_shell(f"python {script_file} {data_file}")

        # 步骤 5：返回结果
        return {
            "url": url,
            "content_length": len(content),
            "analysis_result": result
        }

    async def navigate_browser(self, url: str):
        """使用浏览器 MCP 服务器进行导航"""
        # 使用 MCP 浏览器服务器的实现
        pass

    async def extract_page_content(self) -> str:
        """使用浏览器自动化提取页面内容"""
        # 使用浏览器 API 的实现
        pass

# 使用
async def run_scraping_agent():
    analysis_script = '''
import sys
from bs4 import BeautifulSoup

with open(sys.argv[1], 'r') as f:
    content = f.read()

soup = BeautifulSoup(content, 'html.parser')
print(f"标题：{soup.title.string if soup.title else '无标题'}")
print(f"找到的链接：{len(soup.find_all('a'))}")
print(f"找到的图片：{len(soup.find_all('img'))}")
'''

    async with WebScrapingAgent() as agent:
        result = await agent.scrape_and_analyze(
            "https://example.com",
            analysis_script
        )
        print(json.dumps(result, indent=2))
```

### 会话管理

对于长时间运行的 Agent 工作流，有效管理 Shell 会话：

```python
class SessionManagedAgent(AIOSandboxAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shell_session_id = None

    async def create_shell_session(self):
        """创建持久 Shell 会话"""
        result = await self.execute_shell("echo '会话已启动'")
        # 从响应中提取会话 ID
        self.shell_session_id = result.get('session_id')
        return self.shell_session_id

    async def execute_in_session(self, command: str):
        """在持久会话中执行命令"""
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
        """在会话中设置自定义环境"""
        commands = [
            "export PYTHONPATH=/workspace:$PYTHONPATH",
            "cd /workspace",
            "pip install -r requirements.txt",
            "echo '环境准备就绪'"
        ]

        for cmd in commands:
            result = await self.execute_in_session(cmd)
            print(f"设置：{cmd} -> {result.get('success', False)}")

# 使用
async def persistent_session_example():
    async with SessionManagedAgent() as agent:
        await agent.setup_environment()

        # 现在所有命令都在同一环境中运行
        await agent.execute_in_session("python my_script.py")
        await agent.execute_in_session("ls -la results/")
```

## 集成模式

### LangChain 集成

将 AIO Sandbox 与 LangChain 集成，实现高级 Agent 工作流：

```python
from langchain.tools import BaseTool
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

class SandboxExecuteTool(BaseTool):
    name = "sandbox_execute"
    description = "在 AIO Sandbox 中执行 Shell 命令"
    agent: AIOSandboxAgent = Field(...)

    def _run(self, command: str) -> str:
        """同步执行命令"""
        import asyncio
        return asyncio.run(self._arun(command))

    async def _arun(self, command: str) -> str:
        """异步执行命令"""
        result = await self.agent.execute_shell(command)
        if result.get('success'):
            return result.get('data', {}).get('output', '')
        else:
            return f"错误：{result.get('message', '未知错误')}"

class SandboxFileTool(BaseTool):
    name = "sandbox_file_ops"
    description = "在 AIO Sandbox 中读写文件"
    agent: AIOSandboxAgent = Field(...)

    def _run(self, action: str, path: str, content: str = "") -> str:
        """执行文件操作"""
        import asyncio
        return asyncio.run(self._arun(action, path, content))

    async def _arun(self, action: str, path: str, content: str = "") -> str:
        """异步执行文件操作"""
        if action == "read":
            result = await self.agent.read_file(path)
        elif action == "write":
            result = await self.agent.write_file(path, content)
        else:
            return f"未知操作：{action}"

        if result.get('success'):
            return str(result.get('data', ''))
        else:
            return f"错误：{result.get('message', '未知错误')}"

# 使用沙盒工具创建 LangChain Agent
async def create_sandbox_agent():
    sandbox_agent = AIOSandboxAgent()
    await sandbox_agent.__aenter__()

    tools = [
        SandboxExecuteTool(agent=sandbox_agent),
        SandboxFileTool(agent=sandbox_agent)
    ]

    prompt = ChatPromptTemplate.from_messages([
        ("system", "您是一个可以访问沙盒环境的 AI 助手。"),
        ("user", "{input}"),
        ("assistant", "{agent_scratchpad}")
    ])

    # 创建并返回 Agent（需要 LLM 配置）
    # agent = create_openai_functions_agent(llm, tools, prompt)
    # return AgentExecutor(agent=agent, tools=tools)
```

### OpenAI 助手集成

创建具有沙盒功能的 OpenAI 助手：

```python
import openai
from openai import OpenAI

class OpenAISandboxAssistant:
    def __init__(self, api_key: str, sandbox_url: str = "http://localhost:8080"):
        self.client = OpenAI(api_key=api_key)
        self.sandbox = AIOSandboxAgent(sandbox_url)

    async def create_assistant(self):
        """创建具有沙盒函数调用的 OpenAI 助手"""
        assistant = self.client.beta.assistants.create(
            name="AIO Sandbox 助手",
            instructions="您帮助用户在沙盒环境中执行代码和管理文件。",
            model="gpt-4-turbo-preview",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": "在沙盒中执行 Shell 命令",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {"type": "string", "description": "要执行的 Shell 命令"}
                            },
                            "required": ["command"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "manage_file",
                        "description": "在沙盒中读取或写入文件",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["read", "write"]},
                                "path": {"type": "string", "description": "文件路径"},
                                "content": {"type": "string", "description": "要写入的内容（用于写入操作）"}
                            },
                            "required": ["action", "path"]
                        }
                    }
                }
            ]
        )
        return assistant

    async def handle_function_call(self, function_name: str, arguments: dict):
        """处理来自 OpenAI 助手的函数调用"""
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

## 错误处理和最佳实践

### 健壮的错误处理

```python
class RobustSandboxAgent(AIOSandboxAgent):
    async def safe_execute(self, command: str, max_retries: int = 3):
        """带重试和错误处理的命令执行"""
        for attempt in range(max_retries):
            try:
                result = await self.execute_shell(command)
                if result.get('success'):
                    return result
                else:
                    print(f"第 {attempt + 1} 次尝试失败：{result.get('message')}")
            except Exception as e:
                print(f"第 {attempt + 1} 次尝试异常：{str(e)}")

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # 指数退避

        raise Exception(f"{max_retries} 次尝试后命令失败：{command}")

    async def validate_environment(self):
        """在操作前验证沙盒环境"""
        checks = [
            ("python --version", "Python 运行时"),
            ("node --version", "Node.js 运行时"),
            ("ls /tmp", "临时目录访问")
        ]

        results = {}
        for command, description in checks:
            try:
                result = await self.safe_execute(command)
                results[description] = result.get('success', False)
            except Exception as e:
                results[description] = False
                print(f"{description} 验证失败：{e}")

        return results
```

### 性能优化

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
        """并发执行多个命令"""
        tasks = [self.execute_shell(cmd) for cmd in commands]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return [
            result if not isinstance(result, Exception) else {"error": str(result)}
            for result in results
        ]
```

## 测试策略

### 单元测试

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
        # 模拟成功响应
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
        # 测试文件写入和读取循环
        test_content = "测试文件内容"
        test_path = "/tmp/test_file.txt"

        # 写入文件
        write_result = await self.agent.write_file(test_path, test_content)
        self.assertTrue(write_result.get("success"))

        # 读取文件
        read_result = await self.agent.read_file(test_path)
        self.assertTrue(read_result.get("success"))
        self.assertEqual(read_result["data"]["content"], test_content)

if __name__ == "__main__":
    unittest.main()
```

## 生产部署

### Agent + 沙盒的 Docker Compose

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

### Kubernetes 部署

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

## 下一步

准备构建您自己的 AI Agent？以下是一些推荐路径：

1. **从简单开始**：从基本 Agent 设置开始，逐步增加复杂性
2. **选择您的框架**：选择 LangChain、OpenAI 助手或构建自定义解决方案
3. **彻底测试**：使用提供的测试策略确保可靠性
4. **监控性能**：为生产部署实施日志记录和指标
5. **适当扩展**：开发使用 Docker Compose，生产使用 Kubernetes

更多示例和高级模式：
- 查看[浏览器自动化示例](/examples/browser)
- 探索[终端集成模式](/examples/terminal)
- 查看 [API 文档](/api/) 了解详细规范
- 访问 [GitHub 仓库](https://github.com/agent-infra/sandbox) 获取最新更新

需要帮助？加入我们的社区或在 GitHub 上提出问题！

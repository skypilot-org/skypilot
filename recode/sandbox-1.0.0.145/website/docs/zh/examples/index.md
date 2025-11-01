# 示例

本节提供使用 AIO Sandbox 在实际场景中的实用示例和集成指南。

## 快速示例

### 终端集成
了解如何将 WebSocket 终端集成到您的应用程序中：
- [基本终端客户端](/examples/terminal) - 简单的终端集成
- [高级终端功能](/examples/terminal#advanced-features) - 会话管理和重连

### 浏览器自动化
探索浏览器自动化功能：
- [Browser Use 集成](/examples/browser) - Python 浏览器自动化
- [Playwright 集成](/examples/browser#playwright) - 高级浏览器控制
- [Web 抓取示例](/examples/browser#scraping) - 数据提取模式

### Agent 集成
使用 AIO Sandbox 构建 AI Agent：
- [基本 Agent 设置](/examples/agent) - 将 Agent 连接到沙盒
- [MCP 集成](/examples/agent#mcp) - 使用模型上下文协议
- [多工具工作流](/examples/agent#workflows) - 组合多个 API

## 集成模式

### Docker Compose 设置
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

### Kubernetes 部署
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

## SDK 示例

### Python SDK

安装 AIO Sandbox 的 Python SDK：

```bash
pip install aio-sandbox
```

#### 基本配置

导入并配置 Python 客户端：

```python
from aio_sandbox import AioClient
import asyncio

# 初始化客户端
client = AioClient(
    base_url="http://localhost:8080",  # AIO Sandbox URL
    timeout=30.0,  # 请求超时（秒）
    retries=3,     # 重试次数
    retry_delay=1.0  # 重试之间的延迟
)
```

#### Shell 操作

执行 Shell 命令并管理会话：

```python
async def shell_example():
    # 执行简单命令
    result = await client.shell.exec(command="ls -la")

    if result.success:
        print(f"输出：{result.data.output}")
        print(f"退出码：{result.data.exit_code}")

    # 使用会话管理执行
    session_id = "my-session-1"
    await client.shell.exec(
        command="cd /workspace && pwd",
        session_id=session_id
    )

    # 在同一会话中继续
    result = await client.shell.exec(
        command="ls",
        session_id=session_id
    )

    # 长时间运行任务的异步执行
    await client.shell.exec(
        command="python long_script.py",
        async_mode=True,
        session_id=session_id
    )

    # 查看会话输出
    view_result = await client.shell.view(session_id=session_id)
    print(view_result.data.output)

# 运行示例
asyncio.run(shell_example())
```

#### 文件操作

管理文件和目录：

```python
async def file_example():
    # 写入文件
    await client.file.write(
        file="/tmp/example.py",
        content="""
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(0, 10, 100)
y = np.sin(x)

plt.plot(x, y)
plt.savefig('/tmp/plot.png')
print("图表已保存！")
        """.strip()
    )

    # 读取文件内容
    content = await client.file.read(file="/tmp/example.py")
    if content.success:
        print(f"文件内容：\n{content.data.content}")

    # 列出目录内容
    files = await client.file.list(
        path="/tmp",
        recursive=True,
        include_size=True
    )

    for file_info in files.data.files:
        print(f"{file_info.name}: {file_info.size} 字节")

    # 在文件中搜索
    search_result = await client.file.search(
        file="/tmp/example.py",
        regex=r"import \w+"
    )

    if search_result.success:
        for match in search_result.data.matches:
            print(f"第 {match.line} 行：{match.content}")

    # 按模式查找文件
    found_files = await client.file.find(
        path="/tmp",
        glob="*.py"
    )

asyncio.run(file_example())
```

#### 代码执行

安全地执行 Python 和 JavaScript 代码：

```python
async def code_execution_example():
    # 在 Jupyter 内核中执行 Python 代码
    jupyter_result = await client.jupyter.execute(
        code="""
import pandas as pd
import numpy as np

# 创建示例数据
df = pd.DataFrame({
    'x': np.random.randn(100),
    'y': np.random.randn(100)
})

print(f"DataFrame 形状：{df.shape}")
print(df.head())
        """,
        timeout=60,
        session_id="data-analysis-session"
    )

    if jupyter_result.success:
        print("Jupyter 输出：")
        for output in jupyter_result.data.outputs:
            if output.output_type == "stream":
                print(output.text)
            elif output.output_type == "execute_result":
                print(output.data.get("text/plain", ""))

    # 执行 Node.js 代码
    nodejs_result = await client.nodejs.execute(
        code="""
const fs = require('fs');
const path = require('path');

// 如果存在则读取 package.json
try {
    const packagePath = path.join(process.cwd(), 'package.json');
    if (fs.existsSync(packagePath)) {
        const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
        console.log(`项目：${pkg.name || '未知'}`);
        console.log(`版本：${pkg.version || '未知'}`);
    } else {
        console.log('未找到 package.json');
    }
} catch (error) {
    console.error('错误：', error.message);
}
        """,
        timeout=30
    )

    if nodejs_result.success:
        print(f"Node.js 输出：{nodejs_result.data.stdout}")

asyncio.run(code_execution_example())
```

#### MCP 集成

使用模型上下文协议服务：

```python
async def mcp_example():
    # 列出可用的 MCP 服务器
    servers = await client.mcp.list_servers()
    print("可用的 MCP 服务器：", servers.data)

    # 从特定服务器获取工具
    browser_tools = await client.mcp.list_tools(server_name="browser")

    for tool in browser_tools.data.tools:
        print(f"工具：{tool.name}")
        print(f"描述：{tool.description}")

    # 执行工具
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
        # 保存截图数据
        await client.file.write(
            file="/tmp/screenshot.png",
            content=screenshot_result.data.content[0].data,  # Base64 图像数据
            append=False
        )

asyncio.run(mcp_example())
```

#### 错误处理和最佳实践

```python
async def robust_example():
    try:
        # 始终使用上下文管理器进行资源清理
        async with AioClient("http://localhost:8080") as client:
            # 设置错误处理
            result = await client.shell.exec("potentially-failing-command")

            if not result.success:
                print(f"命令失败：{result.message}")
                if hasattr(result, 'error_code'):
                    print(f"错误代码：{result.error_code}")

            # 检查沙盒状态
            status = await client.sandbox.get_context()
            print(f"沙盒运行时间：{status.data.uptime}")
            print(f"可用包：{len(status.data.packages)}")

    except Exception as e:
        print(f"连接错误：{e}")

asyncio.run(robust_example())
```


### Node.js SDK

要安装 SDK，请使用以下命令：

```bash
npm install @agent-infra/sandbox
```


#### 基本配置

首先导入 SDK 并配置客户端：

```typescript
import { AioClient } from "@agent-infra/sandbox";

const client = new AioClient({
  baseUrl: `https://{aio.sandbox.example}`, // URL 和端口应与 Aio Sandbox 一致
  timeout: 30000, // 可选：请求超时（毫秒）
  retries: 3, // 可选：重试次数
  retryDelay: 1000, // 可选：重试之间的延迟（毫秒）
});
```

#### Shell 执行

在沙盒内执行 Shell 命令：

```typescript
const response = await client.shellExec({
  command: "ls -la",
});

if (response.success) {
  console.log("命令输出：", response.data.output);
} else {
  console.error("错误：", response.message);
}

// 异步轮询训练结果，适用于长期任务
const response = await client.shellExecWithPolling({
  command: "ls -la",
  maxWaitTime: 60 * 1000,
});
```

#### 文件管理

列出目录中的文件：

```typescript
const fileList = await client.fileList({
  path: "/home/gem",
  recursive: true,
});

if (fileList.success) {
  console.log("文件：", fileList.data.files);
} else {
  console.error("错误：", fileList.message);
}
```

#### Jupyter 代码执行

运行 Jupyter notebook 代码：

```typescript
const jupyterResponse = await client.jupyterExecute({
  code: "print('你好，Jupyter！')",
  kernel_name: "python3",
});

if (jupyterResponse.success) {
  console.log("输出：", jupyterResponse.data);
} else {
  console.error("错误：", jupyterResponse.message);
}
```

## 下一步

准备好实施这些模式了吗？选择您的路径：

- **[终端示例](/examples/terminal)** - 从终端集成开始
- **[浏览器示例](/examples/browser)** - 探索浏览器自动化
- **[Agent 集成](/examples/agent)** - 构建 AI 驱动的工作流

如需其他支持：
- 查看 [API 文档](/api/) 了解详细规范
- 探索 [GitHub 仓库](https://github.com/agent-infra/sandbox) 获取最新更新

# 终端集成示例

本节提供与 AIO Sandbox 的 WebSocket 终端接口集成的全面示例。

## 基本终端客户端

### 简单的 Node.js 实现

```javascript
const WebSocket = require('ws');

class TerminalClient {
    constructor(baseUrl = 'ws://localhost:8080') {
        this.baseUrl = baseUrl;
        this.ws = null;
        this.sessionId = null;
    }

    connect(sessionId = null) {
        const wsUrl = `${this.baseUrl}/v1/shell/ws${sessionId ? `?session_id=${sessionId}` : ''}`;

        this.ws = new WebSocket(wsUrl);

        this.ws.on('open', () => {
            console.log('已连接到终端');
        });

        this.ws.on('message', (data) => {
            const message = JSON.parse(data.toString());
            this.handleMessage(message);
        });

        this.ws.on('close', () => {
            console.log('终端连接已关闭');
        });

        this.ws.on('error', (error) => {
            console.error('终端错误：', error);
        });
    }

    handleMessage(message) {
        const { type, data } = message;

        switch (type) {
            case 'session_id':
                this.sessionId = data;
                console.log('会话 ID：', data);
                break;
            case 'output':
                process.stdout.write(data);
                break;
            case 'ready':
                console.log('终端就绪：', data);
                break;
            case 'ping':
                this.sendPong(data);
                break;
            case 'error':
                console.error('服务器错误：', data);
                break;
        }
    }

    sendCommand(command) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'input',
                data: command + '\n'
            }));
        }
    }

    sendPong(timestamp) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'pong',
                timestamp: timestamp
            }));
        }
    }

    resize(cols, rows) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'resize',
                data: { cols, rows }
            }));
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
    }
}

// 使用示例
const terminal = new TerminalClient();
terminal.connect();

// 连接后发送命令
setTimeout(() => {
    terminal.sendCommand('ls -la');
    terminal.sendCommand('pwd');
    terminal.sendCommand('echo "你好，来自 AIO Sandbox！"');
}, 1000);
```

### Python Asyncio 实现

```python
import asyncio
import websockets
import json

class AsyncTerminalClient:
    def __init__(self, base_url="ws://localhost:8080"):
        self.base_url = base_url
        self.websocket = None
        self.session_id = None

    async def connect(self, session_id=None):
        ws_url = f"{self.base_url}/v1/shell/ws"
        if session_id:
            ws_url += f"?session_id={session_id}"

        self.websocket = await websockets.connect(ws_url)
        print("已连接到终端")

        # 启动消息处理器
        asyncio.create_task(self.handle_messages())

    async def handle_messages(self):
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self.process_message(data)
        except websockets.exceptions.ConnectionClosed:
            print("终端连接已关闭")
        except Exception as e:
            print(f"处理消息时出错：{e}")

    async def process_message(self, message):
        msg_type = message.get('type')
        data = message.get('data')

        if msg_type == 'session_id':
            self.session_id = data
            print(f"会话 ID：{data}")
        elif msg_type == 'output':
            print(data, end='')
        elif msg_type == 'ready':
            print(f"终端就绪：{data}")
        elif msg_type == 'ping':
            await self.send_pong(data)
        elif msg_type == 'error':
            print(f"服务器错误：{data}")

    async def send_command(self, command):
        if self.websocket:
            await self.websocket.send(json.dumps({
                'type': 'input',
                'data': command + '\n'
            }))

    async def send_pong(self, timestamp):
        if self.websocket:
            await self.websocket.send(json.dumps({
                'type': 'pong',
                'timestamp': timestamp
            }))

    async def resize_terminal(self, cols, rows):
        if self.websocket:
            await self.websocket.send(json.dumps({
                'type': 'resize',
                'data': {'cols': cols, 'rows': rows}
            }))

    async def disconnect(self):
        if self.websocket:
            await self.websocket.close()

# 使用示例
async def main():
    client = AsyncTerminalClient()
    await client.connect()

    # 等待连接建立
    await asyncio.sleep(1)

    # 发送一些命令
    await client.send_command('ls -la')
    await client.send_command('whoami')
    await client.send_command('python3 --version')

    # 保持活动以接收输出
    await asyncio.sleep(5)

    await client.disconnect()

# 运行示例
asyncio.run(main())
```

## 高级功能

### 会话管理和重连

```javascript
class AdvancedTerminalClient extends TerminalClient {
    constructor(baseUrl = 'ws://localhost:8080') {
        super(baseUrl);
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.heartbeatInterval = null;
    }

    connect(sessionId = null) {
        super.connect(sessionId);

        this.ws.on('close', (code) => {
            this.stopHeartbeat();
            if (code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
                console.log(`连接关闭（${code}）。尝试重连...`);
                this.reconnect();
            }
        });

        this.ws.on('open', () => {
            this.reconnectAttempts = 0;
            this.startHeartbeat();
            console.log('已连接到带会话管理的终端');
        });
    }

    reconnect() {
        this.reconnectAttempts++;
        setTimeout(() => {
            console.log(`重连尝试 ${this.reconnectAttempts}/${this.maxReconnectAttempts}`);
            this.connect(this.sessionId); // 重连到现有会话
        }, this.reconnectDelay * this.reconnectAttempts);
    }

    startHeartbeat() {
        this.heartbeatInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'ping',
                    timestamp: Date.now()
                }));
            }
        }, 30000); // 每 30 秒 ping 一次
    }

    stopHeartbeat() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
    }

    async executeCommand(command, timeout = 10000) {
        return new Promise((resolve, reject) => {
            let output = '';
            let timeoutId;

            const originalHandler = this.handleMessage.bind(this);

            this.handleMessage = (message) => {
                originalHandler(message);

                if (message.type === 'output') {
                    output += message.data;

                    // 简单的命令完成检测
                    if (output.includes('$') && output.trim().endsWith('$')) {
                        clearTimeout(timeoutId);
                        this.handleMessage = originalHandler;
                        resolve(output);
                    }
                }
            };

            timeoutId = setTimeout(() => {
                this.handleMessage = originalHandler;
                reject(new Error('命令超时'));
            }, timeout);

            this.sendCommand(command);
        });
    }
}

// 使用会话持久性
const terminalWithSession = new AdvancedTerminalClient();
terminalWithSession.connect();

// 使用 Promise 执行命令的示例
terminalWithSession.executeCommand('ls -la').then(output => {
    console.log('命令输出：', output);
}).catch(error => {
    console.error('命令失败：', error);
});
```

### 多会话管理

```python
import asyncio
import websockets
import json
from typing import Dict, Optional

class MultiSessionTerminal:
    def __init__(self, base_url="ws://localhost:8080"):
        self.base_url = base_url
        self.sessions: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.session_outputs: Dict[str, str] = {}

    async def create_session(self, session_name: str) -> str:
        """创建新的终端会话"""
        ws_url = f"{self.base_url}/v1/shell/ws"
        websocket = await websockets.connect(ws_url)

        # 等待会话 ID
        session_id = None
        async for message in websocket:
            data = json.loads(message)
            if data.get('type') == 'session_id':
                session_id = data.get('data')
                break

        if session_id:
            self.sessions[session_name] = websocket
            self.session_outputs[session_name] = ""

            # 为此会话启动消息处理器
            asyncio.create_task(self.handle_session_messages(session_name))

            return session_id
        else:
            raise Exception("获取会话 ID 失败")

    async def connect_to_session(self, session_name: str, session_id: str):
        """连接到现有会话"""
        ws_url = f"{self.base_url}/v1/shell/ws?session_id={session_id}"
        websocket = await websockets.connect(ws_url)

        self.sessions[session_name] = websocket
        if session_name not in self.session_outputs:
            self.session_outputs[session_name] = ""

        asyncio.create_task(self.handle_session_messages(session_name))

    async def handle_session_messages(self, session_name: str):
        """处理特定会话的消息"""
        websocket = self.sessions[session_name]

        try:
            async for message in websocket:
                data = json.loads(message)
                await self.process_session_message(session_name, data)
        except websockets.exceptions.ConnectionClosed:
            print(f"会话 {session_name} 连接已关闭")
            if session_name in self.sessions:
                del self.sessions[session_name]

    async def process_session_message(self, session_name: str, message):
        msg_type = message.get('type')
        data = message.get('data')

        if msg_type == 'output':
            self.session_outputs[session_name] += data
            print(f"[{session_name}] {data}", end='')
        elif msg_type == 'ready':
            print(f"[{session_name}] 终端就绪：{data}")
        elif msg_type == 'ping':
            await self.send_pong(session_name, data)

    async def send_command(self, session_name: str, command: str):
        """向特定会话发送命令"""
        if session_name in self.sessions:
            websocket = self.sessions[session_name]
            await websocket.send(json.dumps({
                'type': 'input',
                'data': command + '\n'
            }))
        else:
            raise Exception(f"会话 {session_name} 未找到")

    async def send_pong(self, session_name: str, timestamp):
        if session_name in self.sessions:
            websocket = self.sessions[session_name]
            await websocket.send(json.dumps({
                'type': 'pong',
                'timestamp': timestamp
            }))

    async def get_session_output(self, session_name: str) -> str:
        """获取会话的累积输出"""
        return self.session_outputs.get(session_name, "")

    async def close_session(self, session_name: str):
        """关闭特定会话"""
        if session_name in self.sessions:
            await self.sessions[session_name].close()
            del self.sessions[session_name]
            if session_name in self.session_outputs:
                del self.session_outputs[session_name]

    async def close_all_sessions(self):
        """关闭所有会话"""
        for session_name in list(self.sessions.keys()):
            await self.close_session(session_name)

# 使用示例
async def multi_session_demo():
    terminal = MultiSessionTerminal()

    # 创建多个会话
    session1_id = await terminal.create_session("build")
    session2_id = await terminal.create_session("test")
    session3_id = await terminal.create_session("dev")

    print(f"创建的会话：build={session1_id}, test={session2_id}, dev={session3_id}")

    # 等待会话准备就绪
    await asyncio.sleep(2)

    # 并行运行不同的命令
    await asyncio.gather(
        terminal.send_command("build", "npm run build"),
        terminal.send_command("test", "npm test"),
        terminal.send_command("dev", "npm run dev")
    )

    # 让命令运行
    await asyncio.sleep(10)

    # 获取输出
    build_output = await terminal.get_session_output("build")
    test_output = await terminal.get_session_output("test")

    print("构建输出长度：", len(build_output))
    print("测试输出长度：", len(test_output))

    # 清理
    await terminal.close_all_sessions()

asyncio.run(multi_session_demo())
```

## 与流行库的集成

### xterm.js 前端集成

```html
<!DOCTYPE html>
<html>
<head>
    <title>AIO Sandbox 终端</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
    <style>
        #terminal { height: 400px; width: 100%; }
        .controls { margin: 10px 0; }
        button { margin: 5px; padding: 10px; }
    </style>
</head>
<body>
    <h1>AIO Sandbox 终端界面</h1>

    <div class="controls">
        <button onclick="connectTerminal()">连接</button>
        <button onclick="disconnectTerminal()">断开</button>
        <button onclick="clearTerminal()">清除</button>
        <button onclick="resizeTerminal()">适应</button>
    </div>

    <div id="terminal"></div>

    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>

    <script>
        let terminal, websocket, fitAddon;

        function initTerminal() {
            terminal = new Terminal({
                cursorBlink: true,
                theme: {
                    background: '#1e1e1e',
                    foreground: '#ffffff'
                }
            });

            fitAddon = new FitAddon.FitAddon();
            terminal.loadAddon(fitAddon);

            terminal.open(document.getElementById('terminal'));
            fitAddon.fit();

            terminal.onData(data => {
                if (websocket && websocket.readyState === WebSocket.OPEN) {
                    websocket.send(JSON.stringify({
                        type: 'input',
                        data: data
                    }));
                }
            });

            terminal.onResize(({ cols, rows }) => {
                if (websocket && websocket.readyState === WebSocket.OPEN) {
                    websocket.send(JSON.stringify({
                        type: 'resize',
                        data: { cols, rows }
                    }));
                }
            });
        }

        function connectTerminal() {
            websocket = new WebSocket('ws://localhost:8080/v1/shell/ws');

            websocket.onopen = () => {
                terminal.write('\\r\\n\\x1b[32m已连接到 AIO Sandbox\\x1b[0m\\r\\n');
            };

            websocket.onmessage = (event) => {
                const message = JSON.parse(event.data);
                handleTerminalMessage(message);
            };

            websocket.onclose = () => {
                terminal.write('\\r\\n\\x1b[31m连接已关闭\\x1b[0m\\r\\n');
            };
        }

        function handleTerminalMessage(message) {
            const { type, data } = message;

            switch (type) {
                case 'output':
                    terminal.write(data);
                    break;
                case 'ready':
                    terminal.write(`\\r\\n\\x1b[32m${data}\\x1b[0m\\r\\n`);
                    break;
                case 'session_id':
                    console.log('会话 ID：', data);
                    break;
                case 'ping':
                    websocket.send(JSON.stringify({
                        type: 'pong',
                        timestamp: data
                    }));
                    break;
            }
        }

        function disconnectTerminal() {
            if (websocket) {
                websocket.close();
            }
        }

        function clearTerminal() {
            terminal.clear();
        }

        function resizeTerminal() {
            fitAddon.fit();
        }

        // 页面加载时初始化
        window.onload = () => {
            initTerminal();
        };

        // 窗口调整大小时自动调整
        window.onresize = () => {
            if (fitAddon) {
                fitAddon.fit();
            }
        };
    </script>
</body>
</html>
```

### React 组件

```jsx
import React, { useEffect, useRef, useState } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';

const AIOTerminal = ({ baseUrl = 'ws://localhost:8080' }) => {
    const terminalRef = useRef(null);
    const [terminal, setTerminal] = useState(null);
    const [websocket, setWebsocket] = useState(null);
    const [connected, setConnected] = useState(false);
    const [sessionId, setSessionId] = useState(null);
    const fitAddonRef = useRef(null);

    useEffect(() => {
        // 初始化终端
        const term = new Terminal({
            cursorBlink: true,
            theme: {
                background: '#1e1e1e',
                foreground: '#ffffff'
            }
        });

        const fitAddon = new FitAddon();
        term.loadAddon(fitAddon);
        fitAddonRef.current = fitAddon;

        term.open(terminalRef.current);
        fitAddon.fit();

        setTerminal(term);

        // 处理用户输入
        term.onData(data => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'input',
                    data: data
                }));
            }
        });

        // 处理终端调整大小
        term.onResize(({ cols, rows }) => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'resize',
                    data: { cols, rows }
                }));
            }
        });

        return () => {
            term.dispose();
        };
    }, []);

    const connect = () => {
        const ws = new WebSocket(`${baseUrl}/v1/shell/ws`);

        ws.onopen = () => {
            setConnected(true);
            terminal.write('\\r\\n\\x1b[32m已连接到 AIO Sandbox\\x1b[0m\\r\\n');
        };

        ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            handleMessage(message);
        };

        ws.onclose = () => {
            setConnected(false);
            terminal.write('\\r\\n\\x1b[31m连接已关闭\\x1b[0m\\r\\n');
        };

        ws.onerror = (error) => {
            console.error('WebSocket 错误：', error);
            terminal.write('\\r\\n\\x1b[31m连接错误\\x1b[0m\\r\\n');
        };

        setWebsocket(ws);
    };

    const handleMessage = (message) => {
        const { type, data } = message;

        switch (type) {
            case 'session_id':
                setSessionId(data);
                break;
            case 'output':
                terminal.write(data);
                break;
            case 'ready':
                terminal.write(`\\r\\n\\x1b[32m${data}\\x1b[0m\\r\\n`);
                break;
            case 'ping':
                if (websocket) {
                    websocket.send(JSON.stringify({
                        type: 'pong',
                        timestamp: data
                    }));
                }
                break;
            case 'error':
                terminal.write(`\\r\\n\\x1b[31m错误：${data}\\x1b[0m\\r\\n`);
                break;
        }
    };

    const disconnect = () => {
        if (websocket) {
            websocket.close();
            setWebsocket(null);
        }
    };

    const clear = () => {
        if (terminal) {
            terminal.clear();
        }
    };

    const fit = () => {
        if (fitAddonRef.current) {
            fitAddonRef.current.fit();
        }
    };

    return (
        <div>
            <div style={{ marginBottom: '10px' }}>
                <button onClick={connect} disabled={connected}>
                    连接
                </button>
                <button onClick={disconnect} disabled={!connected}>
                    断开
                </button>
                <button onClick={clear}>清除</button>
                <button onClick={fit}>适应</button>
                <span style={{ marginLeft: '20px' }}>
                    状态：{connected ? '已连接' : '已断开'}
                    {sessionId && ` (会话：${sessionId.substr(0, 8)}...)`}
                </span>
            </div>
            <div
                ref={terminalRef}
                style={{
                    height: '400px',
                    border: '1px solid #ccc',
                    borderRadius: '4px'
                }}
            />
        </div>
    );
};

export default AIOTerminal;
```

## 下一步

- **[浏览器示例](/examples/browser)** - 学习浏览器自动化
- **[Agent 集成](/examples/agent)** - 构建 AI 工作流
- **[API 参考](/api/)** - 详细的 WebSocket API 文档
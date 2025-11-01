# Shell 终端

AIO Sandbox 提供基于 WebSocket 的 Shell 终端，支持实时命令执行和会话管理，`/terminal` 用于 UI 集成。

![](/images/terminal.png)

## WebSocket 连接

### 基本连接
连接到 Shell WebSocket 端点：
```javascript
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');
```

### 会话管理
创建新会话或连接到现有会话：
```javascript
// 新会话
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');

// 连接到现有会话
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws?session_id=abc123');
```

## 消息协议

### 输入消息
向终端发送命令：

```javascript
// 执行命令
ws.send(JSON.stringify({
    type: 'input',
    data: 'ls -la\n'
}));

// 调整终端大小
ws.send(JSON.stringify({
    type: 'resize',
    data: { cols: 80, rows: 24 }
}));

// 心跳响应
ws.send(JSON.stringify({
    type: 'pong',
    timestamp: Date.now()
}));
```

### 输出消息
接收终端响应：

```javascript
ws.onmessage = (event) => {
    const message = JSON.parse(event.data);

    switch (message.type) {
        case 'session_id':
            console.log('会话 ID:', message.data);
            break;

        case 'output':
            terminal.write(message.data);
            break;

        case 'ready':
            console.log('终端就绪:', message.data);
            break;

        case 'ping':
            // 响应心跳
            ws.send(JSON.stringify({
                type: 'pong',
                timestamp: message.data
            }));
            break;
    }
};
```

## 终端集成

### XTerm.js 集成
使用 xterm.js 的完整终端体验：

```html
<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
</head>
<body>
    <div id="terminal"></div>

    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>

    <script>
        const terminal = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            theme: {
                background: '#000000',
                foreground: '#ffffff'
            }
        });

        const fitAddon = new FitAddon.FitAddon();
        terminal.loadAddon(fitAddon);
        terminal.open(document.getElementById('terminal'));
        fitAddon.fit();

        const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');

        terminal.onData(data => {
            ws.send(JSON.stringify({
                type: 'input',
                data: data
            }));
        });

        ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            if (message.type === 'output') {
                terminal.write(message.data);
            }
        };
    </script>
</body>
</html>
```

### 功能
- **实时输出**：即时命令响应
- **会话持久性**：重新连接到现有会话
- **终端调整大小**：动态大小调整
- **心跳监控**：连接健康检查
- **历史恢复**：先前会话输出恢复

## 会话管理

### 会话创建
会话自动创建：
```javascript
// 不带 session_id 连接创建新会话
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');

ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'session_id') {
        // 存储以供重连
        localStorage.setItem('sessionId', message.data);
    }
};
```

### 会话重连
重新连接以保留命令历史：
```javascript
const sessionId = localStorage.getItem('sessionId');
const ws = new WebSocket(`ws://localhost:8080/v1/shell/ws?session_id=${sessionId}`);

ws.onmessage = (event) => {
    const message = JSON.parse(event.data);

    if (message.type === 'restore_output') {
        // 恢复先前会话输出
        terminal.write(message.data);
    } else if (message.type === 'terminal_restored') {
        console.log('会话已恢复:', message.data);
    }
};
```

## 命令执行

### 交互式命令
处理需要用户输入的命令：
```bash
# 需要输入提示的命令无缝工作
sudo passwd user
# 终端中出现密码提示
# 用户输入通过 WebSocket 发送
```

### 长时间运行的命令
监控长时间运行进程的输出：
```bash
# 实时输出流
tail -f /var/log/app.log

# 带实时输出的构建进程
npm run build

# 开发服务器
python -m http.server 8000
```

### 后台进程
管理后台任务：
```bash
# 启动后台进程
nohup python long_running_script.py &

# 使用 jobs 监控
jobs

# 需要时带到前台
fg %1
```

## 文件系统集成

### 共享环境
Shell 在与其他组件相同的文件系统上操作：
```bash
# Shell 中创建的文件立即可用
echo "Hello World" > /tmp/test.txt

# 可通过文件 API 读取
curl -X POST http://localhost:8080/v1/file/read \
  -d '{"file": "/tmp/test.txt"}'

# 在 Code Server 中可用
# 可通过浏览器下载访问
```

### 开发工作流
典型的开发命令：
```bash
# 克隆仓库
git clone https://github.com/user/repo.git

# 安装依赖
cd repo && npm install

# 启动开发服务器
npm run dev

# 服务器可通过 Agent 访问
# http://localhost:8080/proxy/3000/
```

## 安全功能

### 沙盒执行
命令在受控环境中运行：
- 强制执行资源限制
- 文件系统边界
- 网络访问控制
- 进程隔离

### 用户权限
可配置的访问级别：
```bash
# 常规用户操作
ls -la /home/user

# Sudo 操作（如果启用）
sudo apt update

# 系统访问（受控）
ps aux | grep process
```

## 高级功能

### 多个终端
支持并发会话：
```javascript
// 终端 1
const ws1 = new WebSocket('ws://localhost:8080/v1/shell/ws');

// 终端 2
const ws2 = new WebSocket('ws://localhost:8080/v1/shell/ws');

// 每个都有独立的会话和历史
```

### 自定义环境
配置 Shell 环境：
```bash
# 自定义提示符
export PS1="[\u@\h \W]\$ "

# 环境变量
export NODE_ENV=development
export API_KEY=your_key_here

# 路径修改
export PATH=$PATH:/custom/bin
```

### 终端多路复用
使用 screen 或 tmux 进行高级会话管理：
```bash
# 启动 screen 会话
screen -S development

# 分离和重新附加
# Ctrl+A, D（分离）
screen -r development

# 会话内的多个窗口
# Ctrl+A, C（新窗口）
# Ctrl+A, N（下一个窗口）
```

## 故障排除

### 连接问题
```javascript
// 处理连接错误
ws.onerror = (error) => {
    console.error('WebSocket 错误:', error);
    // 实现重连逻辑
};

ws.onclose = (event) => {
    if (event.code !== 1000) {
        // 意外关闭，尝试重连
        setTimeout(reconnect, 3000);
    }
};
```

### 会话恢复
```bash
# 列出活动会话
ps aux | grep shell

# 终止卡住的会话
pkill -f "session_id"

# 如果需要，清除会话数据
rm -rf /tmp/shell_sessions/
```

### 性能优化
- 限制输出缓冲区大小
- 对大量输出使用分页
- 实现输出过滤
- 监控内存使用

准备集成 Shell 功能？查看我们的 [API 文档](/api/) 了解完整的端点详情。
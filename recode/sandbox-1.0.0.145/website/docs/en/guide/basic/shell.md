# Shell Terminal

AIO Sandbox provides a WebSocket-based shell terminal that enables real-time command execution and session management, `/terminal` for UI Integration.

![](/images/terminal.png)

## WebSocket Connection

### Basic Connection
Connect to the shell WebSocket endpoint:
```javascript
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');
```

### Session Management
Create new session or connect to existing:
```javascript
// New session
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');

// Connect to existing session
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws?session_id=abc123');
```

## Message Protocol

### Input Messages
Send commands to the terminal:

```javascript
// Execute command
ws.send(JSON.stringify({
    type: 'input',
    data: 'ls -la\n'
}));

// Resize terminal
ws.send(JSON.stringify({
    type: 'resize',
    data: { cols: 80, rows: 24 }
}));

// Heartbeat response
ws.send(JSON.stringify({
    type: 'pong',
    timestamp: Date.now()
}));
```

### Output Messages
Receive terminal responses:

```javascript
ws.onmessage = (event) => {
    const message = JSON.parse(event.data);

    switch (message.type) {
        case 'session_id':
            console.log('Session ID:', message.data);
            break;

        case 'output':
            terminal.write(message.data);
            break;

        case 'ready':
            console.log('Terminal ready:', message.data);
            break;

        case 'ping':
            // Respond to heartbeat
            ws.send(JSON.stringify({
                type: 'pong',
                timestamp: message.data
            }));
            break;
    }
};
```

## Terminal Integration

### XTerm.js Integration
Complete terminal experience with xterm.js:

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

### Features
- **Real-time Output**: Immediate command response
- **Session Persistence**: Reconnect to existing sessions
- **Terminal Resizing**: Dynamic size adjustment
- **Heartbeat Monitoring**: Connection health checks
- **History Restoration**: Previous session output recovery

## Session Management

### Session Creation
Sessions are created automatically:
```javascript
// Connect without session_id creates new session
const ws = new WebSocket('ws://localhost:8080/v1/shell/ws');

ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'session_id') {
        // Store for reconnection
        localStorage.setItem('sessionId', message.data);
    }
};
```

### Session Reconnection
Reconnect to preserve command history:
```javascript
const sessionId = localStorage.getItem('sessionId');
const ws = new WebSocket(`ws://localhost:8080/v1/shell/ws?session_id=${sessionId}`);

ws.onmessage = (event) => {
    const message = JSON.parse(event.data);

    if (message.type === 'restore_output') {
        // Restore previous session output
        terminal.write(message.data);
    } else if (message.type === 'terminal_restored') {
        console.log('Session restored:', message.data);
    }
};
```

## Command Execution

### Interactive Commands
Handle commands requiring user input:
```bash
# Commands that prompt for input work seamlessly
sudo passwd user
# Password prompt appears in terminal
# User input is sent through WebSocket
```

### Long-Running Commands
Monitor output from long-running processes:
```bash
# Real-time output streaming
tail -f /var/log/app.log

# Build processes with live output
npm run build

# Development servers
python -m http.server 8000
```

### Background Processes
Manage background tasks:
```bash
# Start background process
nohup python long_running_script.py &

# Monitor with jobs
jobs

# Bring to foreground when needed
fg %1
```

## File System Integration

### Shared Environment
Shell operates on the same file system as other components:
```bash
# Files created in shell are immediately available
echo "Hello World" > /tmp/test.txt

# Can be read via File API
curl -X POST http://localhost:8080/v1/file/read \
  -d '{"file": "/tmp/test.txt"}'

# Available in Code Server
# Accessible via browser downloads
```

### Development Workflow
Typical development commands:
```bash
# Clone repository
git clone https://github.com/user/repo.git

# Install dependencies
cd repo && npm install

# Start development server
npm run dev

# Server accessible via proxy
# http://localhost:8080/proxy/3000/
```

## Security Features

### Sandboxed Execution
Commands run in controlled environment:
- Resource limits enforced
- File system boundaries
- Network access controls
- Process isolation

### User Permissions
Configurable access levels:
```bash
# Regular user operations
ls -la /home/user

# Sudo operations (if enabled)
sudo apt update

# System access (controlled)
ps aux | grep process
```

## Advanced Features

### Multiple Terminals
Support for concurrent sessions:
```javascript
// Terminal 1
const ws1 = new WebSocket('ws://localhost:8080/v1/shell/ws');

// Terminal 2
const ws2 = new WebSocket('ws://localhost:8080/v1/shell/ws');

// Each has independent session and history
```

### Custom Environment
Configure shell environment:
```bash
# Custom prompt
export PS1="[\u@\h \W]\$ "

# Environment variables
export NODE_ENV=development
export API_KEY=your_key_here

# Path modifications
export PATH=$PATH:/custom/bin
```

### Terminal Multiplexing
Use screen or tmux for advanced session management:
```bash
# Start screen session
screen -S development

# Detach and reattach
# Ctrl+A, D (detach)
screen -r development

# Multiple windows within session
# Ctrl+A, C (new window)
# Ctrl+A, N (next window)
```

## Troubleshooting

### Connection Issues
```javascript
// Handle connection errors
ws.onerror = (error) => {
    console.error('WebSocket error:', error);
    // Implement reconnection logic
};

ws.onclose = (event) => {
    if (event.code !== 1000) {
        // Unexpected close, attempt reconnection
        setTimeout(reconnect, 3000);
    }
};
```

### Session Recovery
```bash
# List active sessions
ps aux | grep shell

# Kill stuck sessions
pkill -f "session_id"

# Clear session data if needed
rm -rf /tmp/shell_sessions/
```

### Performance Optimization
- Limit output buffer size
- Use pagination for large outputs
- Implement output filtering
- Monitor memory usage

Ready to integrate shell functionality? Check our [API documentation](/api/) for complete endpoint details.

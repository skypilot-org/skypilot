# Terminal Integration Examples

This section provides comprehensive examples for integrating with AIO Sandbox's WebSocket terminal interface.

## Basic Terminal Client

### Simple Node.js Implementation

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
            console.log('Connected to terminal');
        });
        
        this.ws.on('message', (data) => {
            const message = JSON.parse(data.toString());
            this.handleMessage(message);
        });
        
        this.ws.on('close', () => {
            console.log('Terminal connection closed');
        });
        
        this.ws.on('error', (error) => {
            console.error('Terminal error:', error);
        });
    }
    
    handleMessage(message) {
        const { type, data } = message;
        
        switch (type) {
            case 'session_id':
                this.sessionId = data;
                console.log('Session ID:', data);
                break;
            case 'output':
                process.stdout.write(data);
                break;
            case 'ready':
                console.log('Terminal ready:', data);
                break;
            case 'ping':
                this.sendPong(data);
                break;
            case 'error':
                console.error('Server error:', data);
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

// Usage example
const terminal = new TerminalClient();
terminal.connect();

// Send commands after connection
setTimeout(() => {
    terminal.sendCommand('ls -la');
    terminal.sendCommand('pwd');
    terminal.sendCommand('echo "Hello from AIO Sandbox!"');
}, 1000);
```

### Python Asyncio Implementation

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
        print("Connected to terminal")
        
        # Start message handler
        asyncio.create_task(self.handle_messages())
    
    async def handle_messages(self):
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self.process_message(data)
        except websockets.exceptions.ConnectionClosed:
            print("Terminal connection closed")
        except Exception as e:
            print(f"Error handling messages: {e}")
    
    async def process_message(self, message):
        msg_type = message.get('type')
        data = message.get('data')
        
        if msg_type == 'session_id':
            self.session_id = data
            print(f"Session ID: {data}")
        elif msg_type == 'output':
            print(data, end='')
        elif msg_type == 'ready':
            print(f"Terminal ready: {data}")
        elif msg_type == 'ping':
            await self.send_pong(data)
        elif msg_type == 'error':
            print(f"Server error: {data}")
    
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

# Usage example
async def main():
    client = AsyncTerminalClient()
    await client.connect()
    
    # Wait a bit for connection to establish
    await asyncio.sleep(1)
    
    # Send some commands
    await client.send_command('ls -la')
    await client.send_command('whoami')
    await client.send_command('python3 --version')
    
    # Keep alive for output
    await asyncio.sleep(5)
    
    await client.disconnect()

# Run the example
asyncio.run(main())
```

## Advanced Features

### Session Management and Reconnection

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
                console.log(`Connection closed (${code}). Attempting to reconnect...`);
                this.reconnect();
            }
        });
        
        this.ws.on('open', () => {
            this.reconnectAttempts = 0;
            this.startHeartbeat();
            console.log('Connected to terminal with session management');
        });
    }
    
    reconnect() {
        this.reconnectAttempts++;
        setTimeout(() => {
            console.log(`Reconnect attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts}`);
            this.connect(this.sessionId); // Reconnect to existing session
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
        }, 30000); // Ping every 30 seconds
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
                    
                    // Simple command completion detection
                    if (output.includes('$') && output.trim().endsWith('$')) {
                        clearTimeout(timeoutId);
                        this.handleMessage = originalHandler;
                        resolve(output);
                    }
                }
            };
            
            timeoutId = setTimeout(() => {
                this.handleMessage = originalHandler;
                reject(new Error('Command timeout'));
            }, timeout);
            
            this.sendCommand(command);
        });
    }
}

// Usage with session persistence
const terminalWithSession = new AdvancedTerminalClient();
terminalWithSession.connect();

// Example of command execution with promise
terminalWithSession.executeCommand('ls -la').then(output => {
    console.log('Command output:', output);
}).catch(error => {
    console.error('Command failed:', error);
});
```

### Multi-Session Management

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
        \"\"\"Create a new terminal session\"\"\"
        ws_url = f"{self.base_url}/v1/shell/ws"
        websocket = await websockets.connect(ws_url)
        
        # Wait for session ID
        session_id = None
        async for message in websocket:
            data = json.loads(message)
            if data.get('type') == 'session_id':
                session_id = data.get('data')
                break
        
        if session_id:
            self.sessions[session_name] = websocket
            self.session_outputs[session_name] = ""
            
            # Start message handler for this session
            asyncio.create_task(self.handle_session_messages(session_name))
            
            return session_id
        else:
            raise Exception("Failed to get session ID")
    
    async def connect_to_session(self, session_name: str, session_id: str):
        \"\"\"Connect to an existing session\"\"\"
        ws_url = f"{self.base_url}/v1/shell/ws?session_id={session_id}"
        websocket = await websockets.connect(ws_url)
        
        self.sessions[session_name] = websocket
        if session_name not in self.session_outputs:
            self.session_outputs[session_name] = ""
        
        asyncio.create_task(self.handle_session_messages(session_name))
    
    async def handle_session_messages(self, session_name: str):
        \"\"\"Handle messages for a specific session\"\"\"
        websocket = self.sessions[session_name]
        
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.process_session_message(session_name, data)
        except websockets.exceptions.ConnectionClosed:
            print(f"Session {session_name} connection closed")
            if session_name in self.sessions:
                del self.sessions[session_name]
    
    async def process_session_message(self, session_name: str, message):
        msg_type = message.get('type')
        data = message.get('data')
        
        if msg_type == 'output':
            self.session_outputs[session_name] += data
            print(f"[{session_name}] {data}", end='')
        elif msg_type == 'ready':
            print(f"[{session_name}] Terminal ready: {data}")
        elif msg_type == 'ping':
            await self.send_pong(session_name, data)
    
    async def send_command(self, session_name: str, command: str):
        \"\"\"Send command to a specific session\"\"\"
        if session_name in self.sessions:
            websocket = self.sessions[session_name]
            await websocket.send(json.dumps({
                'type': 'input',
                'data': command + '\n'
            }))
        else:
            raise Exception(f"Session {session_name} not found")
    
    async def send_pong(self, session_name: str, timestamp):
        if session_name in self.sessions:
            websocket = self.sessions[session_name]
            await websocket.send(json.dumps({
                'type': 'pong',
                'timestamp': timestamp
            }))
    
    async def get_session_output(self, session_name: str) -> str:
        \"\"\"Get accumulated output for a session\"\"\"
        return self.session_outputs.get(session_name, "")
    
    async def close_session(self, session_name: str):
        \"\"\"Close a specific session\"\"\"
        if session_name in self.sessions:
            await self.sessions[session_name].close()
            del self.sessions[session_name]
            if session_name in self.session_outputs:
                del self.session_outputs[session_name]
    
    async def close_all_sessions(self):
        \"\"\"Close all sessions\"\"\"
        for session_name in list(self.sessions.keys()):
            await self.close_session(session_name)

# Usage example
async def multi_session_demo():
    terminal = MultiSessionTerminal()
    
    # Create multiple sessions
    session1_id = await terminal.create_session("build")
    session2_id = await terminal.create_session("test")
    session3_id = await terminal.create_session("dev")
    
    print(f"Created sessions: build={session1_id}, test={session2_id}, dev={session3_id}")
    
    # Wait for sessions to be ready
    await asyncio.sleep(2)
    
    # Run different commands in parallel
    await asyncio.gather(
        terminal.send_command("build", "npm run build"),
        terminal.send_command("test", "npm test"),
        terminal.send_command("dev", "npm run dev")
    )
    
    # Let commands run
    await asyncio.sleep(10)
    
    # Get outputs
    build_output = await terminal.get_session_output("build")
    test_output = await terminal.get_session_output("test")
    
    print("Build output length:", len(build_output))
    print("Test output length:", len(test_output))
    
    # Clean up
    await terminal.close_all_sessions()

asyncio.run(multi_session_demo())
```

## Integration with Popular Libraries

### xterm.js Frontend Integration

```html
<!DOCTYPE html>
<html>
<head>
    <title>AIO Sandbox Terminal</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
    <style>
        #terminal { height: 400px; width: 100%; }
        .controls { margin: 10px 0; }
        button { margin: 5px; padding: 10px; }
    </style>
</head>
<body>
    <h1>AIO Sandbox Terminal Interface</h1>
    
    <div class="controls">
        <button onclick="connectTerminal()">Connect</button>
        <button onclick="disconnectTerminal()">Disconnect</button>
        <button onclick="clearTerminal()">Clear</button>
        <button onclick="resizeTerminal()">Fit</button>
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
                terminal.write('\\r\\n\\x1b[32mConnected to AIO Sandbox\\x1b[0m\\r\\n');
            };
            
            websocket.onmessage = (event) => {
                const message = JSON.parse(event.data);
                handleTerminalMessage(message);
            };
            
            websocket.onclose = () => {
                terminal.write('\\r\\n\\x1b[31mConnection closed\\x1b[0m\\r\\n');
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
                    console.log('Session ID:', data);
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
        
        // Initialize on page load
        window.onload = () => {
            initTerminal();
        };
        
        // Auto-resize on window resize
        window.onresize = () => {
            if (fitAddon) {
                fitAddon.fit();
            }
        };
    </script>
</body>
</html>
```

### React Component

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
        // Initialize terminal
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
        
        // Handle user input
        term.onData(data => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'input',
                    data: data
                }));
            }
        });
        
        // Handle terminal resize
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
            terminal.write('\\r\\n\\x1b[32mConnected to AIO Sandbox\\x1b[0m\\r\\n');
        };
        
        ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            handleMessage(message);
        };
        
        ws.onclose = () => {
            setConnected(false);
            terminal.write('\\r\\n\\x1b[31mConnection closed\\x1b[0m\\r\\n');
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            terminal.write('\\r\\n\\x1b[31mConnection error\\x1b[0m\\r\\n');
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
                terminal.write(`\\r\\n\\x1b[31mError: ${data}\\x1b[0m\\r\\n`);
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
                    Connect
                </button>
                <button onClick={disconnect} disabled={!connected}>
                    Disconnect
                </button>
                <button onClick={clear}>Clear</button>
                <button onClick={fit}>Fit</button>
                <span style={{ marginLeft: '20px' }}>
                    Status: {connected ? 'Connected' : 'Disconnected'}
                    {sessionId && ` (Session: ${sessionId.substr(0, 8)}...)`}
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

## Next Steps

- **[Browser Examples](/examples/browser)** - Learn browser automation
- **[Agent Integration](/examples/agent)** - Build AI workflows  
- **[API Reference](/api/)** - Detailed WebSocket API docs
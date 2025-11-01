//! WebSocket Terminal - Interactive shell access
//!
//! Provides WebSocket terminal at /v1/shell/ws

use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use futures::{StreamExt, SinkExt};
use tokio::process::Command;
use std::process::Stdio;

/// Terminal server
pub struct TerminalServer {
    port: u16,
}

impl TerminalServer {
    /// Create new terminal server
    pub fn new(port: u16) -> Self {
        Self { port }
    }

    /// Start WebSocket terminal server
    pub async fn start(&self) -> anyhow::Result<()> {
        println!("?? Starting WebSocket terminal on port {}...", self.port);

        let listener = TcpListener::bind(format!("0.0.0.0:{}", self.port)).await?;

        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                tokio::spawn(async move {
                    if let Ok(ws_stream) = accept_async(stream).await {
                        let _ = handle_websocket(ws_stream).await;
                    }
                });
            }
        });

        println!("   ? Terminal server started!");
        println!("      Connect at: ws://localhost:{}/v1/shell/ws", self.port);

        Ok(())
    }
}

/// Handle WebSocket connection
async fn handle_websocket(
    ws_stream: tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>,
) -> anyhow::Result<()> {
    let (mut write, mut read) = ws_stream.split();

    // Start bash process
    let mut child = Command::new("bash")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = child.stdout.take().unwrap();

    // Read from WebSocket and write to stdin
    tokio::spawn(async move {
        while let Some(Ok(msg)) = read.next().await {
            if let tokio_tungstenite::tungstenite::Message::Text(text) = msg {
                let _ = tokio::io::AsyncWriteExt::write_all(&mut stdin, text.as_bytes()).await;
            }
        }
    });

    // Read from stdout and write to WebSocket
    use tokio::io::AsyncReadExt;
    let mut buffer = [0u8; 1024];
    loop {
        match stdout.read(&mut buffer).await {
            Ok(0) => break,
            Ok(n) => {
                let text = String::from_utf8_lossy(&buffer[..n]).to_string();
                let _ = write.send(tokio_tungstenite::tungstenite::Message::Text(text)).await;
            }
            Err(_) => break,
        }
    }

    Ok(())
}

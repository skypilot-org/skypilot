//! MCP Shell Server - exec, create_session, kill

use super::{McpTool, McpToolResult};
use serde_json::json;

pub struct ShellMcp;

impl ShellMcp {
    pub fn list_tools() -> Vec<McpTool> {
        vec![
            McpTool {
                name: "shell.exec".to_string(),
                description: "Execute shell command".to_string(),
                parameters: json!({ "command": "string" }),
            },
            McpTool {
                name: "shell.create_session".to_string(),
                description: "Create persistent shell session".to_string(),
                parameters: json!({}),
            },
            McpTool {
                name: "shell.kill".to_string(),
                description: "Kill shell session".to_string(),
                parameters: json!({ "session_id": "string" }),
            },
        ]
    }

    pub async fn exec(_cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        Ok(McpToolResult { success: true, output: json!({ "stdout": "", "stderr": "" }), error: None })
    }

    pub async fn create_session(_cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        Ok(McpToolResult { success: true, output: json!({ "session_id": "sess-123" }), error: None })
    }

    pub async fn kill(_cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        Ok(McpToolResult { success: true, output: json!({ "killed": true }), error: None })
    }
}

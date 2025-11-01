//! MCP Markitdown Server - convert, extract_text, extract_images

use super::{McpTool, McpToolResult};
use serde_json::json;

pub struct MarkitdownMcp;

impl MarkitdownMcp {
    pub fn list_tools() -> Vec<McpTool> {
        vec![
            McpTool {
                name: "markitdown.convert".to_string(),
                description: "Convert document to markdown".to_string(),
                parameters: json!({ "path": "string", "format": "string" }),
            },
            McpTool {
                name: "markitdown.extract_text".to_string(),
                description: "Extract text from document".to_string(),
                parameters: json!({ "path": "string" }),
            },
            McpTool {
                name: "markitdown.extract_images".to_string(),
                description: "Extract images from document".to_string(),
                parameters: json!({ "path": "string" }),
            },
        ]
    }

    pub async fn convert(_cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        Ok(McpToolResult { success: true, output: json!({ "markdown": "..." }), error: None })
    }

    pub async fn extract_text(_cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        Ok(McpToolResult { success: true, output: json!({ "text": "..." }), error: None })
    }

    pub async fn extract_images(_cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        Ok(McpToolResult { success: true, output: json!({ "images": [] }), error: None })
    }
}

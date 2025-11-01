//! MCP Browser Server
//!
//! Tools: navigate, screenshot, click, type, scroll

use super::{McpTool, McpToolResult};
use serde_json::json;

pub struct BrowserMcp;

impl BrowserMcp {
    /// List browser tools
    pub fn list_tools() -> Vec<McpTool> {
        vec![
            McpTool {
                name: "browser.navigate".to_string(),
                description: "Navigate to a URL".to_string(),
                parameters: json!({
                    "url": "string",
                }),
            },
            McpTool {
                name: "browser.screenshot".to_string(),
                description: "Take a screenshot of the current page".to_string(),
                parameters: json!({
                    "format": "string (png|jpeg)",
                }),
            },
            McpTool {
                name: "browser.click".to_string(),
                description: "Click on an element".to_string(),
                parameters: json!({
                    "selector": "string",
                }),
            },
            McpTool {
                name: "browser.type".to_string(),
                description: "Type text into an element".to_string(),
                parameters: json!({
                    "selector": "string",
                    "text": "string",
                }),
            },
            McpTool {
                name: "browser.scroll".to_string(),
                description: "Scroll the page".to_string(),
                parameters: json!({
                    "x": "number",
                    "y": "number",
                }),
            },
        ]
    }

    /// Navigate to URL
    pub async fn navigate(cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        let url = params.get("url")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing 'url' parameter"))?;

        // Execute playwright/puppeteer command
        let cmd = format!("playwright goto {}", url);
        
        // TODO: Execute in cave
        
        Ok(McpToolResult {
            success: true,
            output: json!({ "url": url, "status": "navigated" }),
            error: None,
        })
    }

    /// Take screenshot
    pub async fn screenshot(cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        let format = params.get("format")
            .and_then(|v| v.as_str())
            .unwrap_or("png");

        Ok(McpToolResult {
            success: true,
            output: json!({ "format": format, "image_base64": "..." }),
            error: None,
        })
    }

    /// Click element
    pub async fn click(cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        let selector = params.get("selector")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing 'selector' parameter"))?;

        Ok(McpToolResult {
            success: true,
            output: json!({ "selector": selector, "action": "clicked" }),
            error: None,
        })
    }

    /// Type text
    pub async fn type_text(cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        let selector = params.get("selector")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing 'selector' parameter"))?;
        
        let text = params.get("text")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("Missing 'text' parameter"))?;

        Ok(McpToolResult {
            success: true,
            output: json!({ "selector": selector, "text": text }),
            error: None,
        })
    }

    /// Scroll page
    pub async fn scroll(cave_id: &str, params: serde_json::Value) -> anyhow::Result<McpToolResult> {
        let x = params.get("x").and_then(|v| v.as_i64()).unwrap_or(0);
        let y = params.get("y").and_then(|v| v.as_i64()).unwrap_or(0);

        Ok(McpToolResult {
            success: true,
            output: json!({ "x": x, "y": y }),
            error: None,
        })
    }
}

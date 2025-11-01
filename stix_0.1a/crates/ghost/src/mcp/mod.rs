//! MCP (Model Context Protocol) Hub
//!
//! MCP Servers:
//! - browser: navigate, screenshot, click, type, scroll
//! - file: read, write, list, search, replace
//! - shell: exec, create_session, kill
//! - markitdown: convert, extract_text, extract_images

pub mod browser;
pub mod file;
pub mod shell;
pub mod markitdown;

use serde::{Deserialize, Serialize};

/// MCP Tool
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpTool {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

/// MCP Tool result
#[derive(Debug, Serialize, Deserialize)]
pub struct McpToolResult {
    pub success: bool,
    pub output: serde_json::Value,
    pub error: Option<String>,
}

/// MCP Hub - Aggregates all MCP servers
pub struct McpHub {
    cave_id: String,
}

impl McpHub {
    /// Create new MCP hub
    pub fn new(cave_id: impl Into<String>) -> Self {
        Self {
            cave_id: cave_id.into(),
        }
    }

    /// List all available tools
    pub fn list_tools(&self) -> Vec<McpTool> {
        let mut tools = Vec::new();
        
        // Browser tools
        tools.extend(browser::BrowserMcp::list_tools());
        
        // File tools
        tools.extend(file::FileMcp::list_tools());
        
        // Shell tools
        tools.extend(shell::ShellMcp::list_tools());
        
        // Markitdown tools
        tools.extend(markitdown::MarkitdownMcp::list_tools());
        
        tools
    }

    /// Execute a tool
    pub async fn execute_tool(
        &self,
        tool_name: &str,
        params: serde_json::Value,
    ) -> anyhow::Result<McpToolResult> {
        match tool_name {
            // Browser tools
            "browser.navigate" => browser::BrowserMcp::navigate(&self.cave_id, params).await,
            "browser.screenshot" => browser::BrowserMcp::screenshot(&self.cave_id, params).await,
            "browser.click" => browser::BrowserMcp::click(&self.cave_id, params).await,
            "browser.type" => browser::BrowserMcp::type_text(&self.cave_id, params).await,
            "browser.scroll" => browser::BrowserMcp::scroll(&self.cave_id, params).await,
            
            // File tools
            "file.read" => file::FileMcp::read(&self.cave_id, params).await,
            "file.write" => file::FileMcp::write(&self.cave_id, params).await,
            "file.list" => file::FileMcp::list(&self.cave_id, params).await,
            "file.search" => file::FileMcp::search(&self.cave_id, params).await,
            "file.replace" => file::FileMcp::replace(&self.cave_id, params).await,
            
            // Shell tools
            "shell.exec" => shell::ShellMcp::exec(&self.cave_id, params).await,
            "shell.create_session" => shell::ShellMcp::create_session(&self.cave_id, params).await,
            "shell.kill" => shell::ShellMcp::kill(&self.cave_id, params).await,
            
            // Markitdown tools
            "markitdown.convert" => markitdown::MarkitdownMcp::convert(&self.cave_id, params).await,
            "markitdown.extract_text" => markitdown::MarkitdownMcp::extract_text(&self.cave_id, params).await,
            "markitdown.extract_images" => markitdown::MarkitdownMcp::extract_images(&self.cave_id, params).await,
            
            _ => Ok(McpToolResult {
                success: false,
                output: serde_json::json!(null),
                error: Some(format!("Unknown tool: {}", tool_name)),
            }),
        }
    }
}

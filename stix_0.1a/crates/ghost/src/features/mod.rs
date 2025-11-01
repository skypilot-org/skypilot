//! Extended Features for Ghost Caves
//!
//! Based on AIO Sandbox features:
//! - VNC (Visual browser interaction)
//! - Code Server (VSCode in browser)
//! - WebSocket Terminal
//! - MCP Hub (Multi-service integration)
//! - File System API
//! - Preview Proxy

pub mod vnc;
pub mod code_server;
pub mod terminal;
pub mod mcp;
pub mod filesystem;
pub mod proxy;

pub use vnc::VncServer;
pub use code_server::CodeServer;
pub use terminal::TerminalServer;
pub use mcp::McpHub;
pub use filesystem::FileSystemApi;
pub use proxy::PreviewProxy;

/// Extended cave configuration
#[derive(Debug, Clone)]
pub struct ExtendedCaveConfig {
    /// Enable VNC server
    pub enable_vnc: bool,
    
    /// Enable Code Server (VSCode)
    pub enable_code_server: bool,
    
    /// Enable WebSocket terminal
    pub enable_terminal: bool,
    
    /// Enable MCP Hub
    pub enable_mcp: bool,
    
    /// Enable file system API
    pub enable_filesystem: bool,
    
    /// Enable preview proxy
    pub enable_proxy: bool,
    
    /// Base port for services
    pub base_port: u16,
}

impl Default for ExtendedCaveConfig {
    fn default() -> Self {
        Self {
            enable_vnc: true,
            enable_code_server: true,
            enable_terminal: true,
            enable_mcp: true,
            enable_filesystem: true,
            enable_proxy: true,
            base_port: 8080,
        }
    }
}

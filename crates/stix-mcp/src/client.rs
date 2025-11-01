use serde::{Deserialize, Serialize};
use anyhow::{Result, Context};
use uuid::Uuid;

/// MCP Client for connecting to the server
pub struct McpClient {
    base_url: String,
    token: Option<String>,
    agent_id: Option<Uuid>,
    client: reqwest::Client,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RegisterRequest {
    pub name: String,
    pub permissions: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RegisterResponse {
    pub agent_id: Uuid,
    pub token: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ToolRequest {
    pub tool_name: String,
    pub parameters: serde_json::Value,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ToolResponse {
    pub success: bool,
    pub result: Option<serde_json::Value>,
    pub error: Option<String>,
}

impl McpClient {
    /// Create a new client
    pub fn new(base_url: String) -> Self {
        Self {
            base_url,
            token: None,
            agent_id: None,
            client: reqwest::Client::new(),
        }
    }

    /// Register as a new agent
    pub async fn register(&mut self, name: String, permissions: Vec<String>) -> Result<Uuid> {
        let url = format!("{}/agents/register", self.base_url);
        
        let req = RegisterRequest { name, permissions };
        
        let response = self.client
            .post(&url)
            .json(&req)
            .send()
            .await
            .context("Failed to send registration request")?;

        let register_response: RegisterResponse = response
            .json()
            .await
            .context("Failed to parse registration response")?;

        self.token = Some(register_response.token);
        self.agent_id = Some(register_response.agent_id);

        Ok(register_response.agent_id)
    }

    /// Execute a tool
    pub async fn execute_tool(
        &self,
        tool_name: String,
        parameters: serde_json::Value,
    ) -> Result<ToolResponse> {
        let token = self.token.as_ref()
            .context("Not authenticated. Call register() first.")?;

        let url = format!("{}/tools/execute", self.base_url);
        
        let req = ToolRequest {
            tool_name,
            parameters,
        };

        let response = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .json(&req)
            .send()
            .await
            .context("Failed to send tool execution request")?;

        let tool_response: ToolResponse = response
            .json()
            .await
            .context("Failed to parse tool response")?;

        Ok(tool_response)
    }

    /// Lock a file
    pub async fn lock_file(&self, file_path: String, duration_secs: Option<u64>) -> Result<ToolResponse> {
        self.execute_tool(
            "lock_file".to_string(),
            serde_json::json!({
                "file_path": file_path,
                "duration_secs": duration_secs,
            }),
        ).await
    }

    /// Unlock a file
    pub async fn unlock_file(&self, file_path: String) -> Result<ToolResponse> {
        self.execute_tool(
            "unlock_file".to_string(),
            serde_json::json!({
                "file_path": file_path,
            }),
        ).await
    }

    /// Update progress
    pub async fn update_progress(
        &self,
        section: String,
        content: String,
        append: bool,
    ) -> Result<ToolResponse> {
        self.execute_tool(
            "update_progress".to_string(),
            serde_json::json!({
                "section": section,
                "content": content,
                "append": append,
            }),
        ).await
    }

    /// Create a task
    pub async fn issue_task(
        &self,
        title: String,
        description: String,
        priority: Option<String>,
        assigned_to: Option<String>,
    ) -> Result<ToolResponse> {
        self.execute_tool(
            "issue_task".to_string(),
            serde_json::json!({
                "title": title,
                "description": description,
                "priority": priority,
                "assigned_to": assigned_to,
            }),
        ).await
    }

    /// Search codebase
    pub async fn search_codebase(
        &self,
        query: String,
        file_pattern: Option<String>,
        case_sensitive: bool,
    ) -> Result<ToolResponse> {
        self.execute_tool(
            "search_codebase".to_string(),
            serde_json::json!({
                "query": query,
                "file_pattern": file_pattern,
                "case_sensitive": case_sensitive,
            }),
        ).await
    }

    /// List all locks
    pub async fn list_locks(&self) -> Result<ToolResponse> {
        self.execute_tool(
            "list_locks".to_string(),
            serde_json::json!({}),
        ).await
    }

    /// Get my logs
    pub async fn get_my_logs(&self) -> Result<Vec<serde_json::Value>> {
        let token = self.token.as_ref()
            .context("Not authenticated")?;

        let url = format!("{}/logs/my", self.base_url);

        let response = self.client
            .get(&url)
            .header("Authorization", format!("Bearer {}", token))
            .send()
            .await
            .context("Failed to get logs")?;

        let logs: Vec<serde_json::Value> = response
            .json()
            .await
            .context("Failed to parse logs")?;

        Ok(logs)
    }

    /// Get agent ID
    pub fn agent_id(&self) -> Option<Uuid> {
        self.agent_id
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_client_creation() {
        let client = McpClient::new("http://localhost:8080".to_string());
        assert!(client.token.is_none());
        assert!(client.agent_id.is_none());
    }
}

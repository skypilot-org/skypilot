//! Executor - High-level code execution interface
//!
//! Provides a simple API for executing code in caves

use crate::ghost::{Ghost, ExecutionResult};
use crate::cave::{CaveConfig, CaveType};

/// Executor - Simplified execution interface
pub struct Executor {
    ghost: Ghost,
}

impl Executor {
    /// Create new executor
    pub async fn new() -> anyhow::Result<Self> {
        Ok(Self {
            ghost: Ghost::new().await?,
        })
    }

    /// Execute Python code in a temporary cave
    pub async fn execute_python(&self, code: &str) -> anyhow::Result<ExecutionResult> {
        self.execute_code(code, "python", "python:3.11-slim").await
    }

    /// Execute Bash script in a temporary cave
    pub async fn execute_bash(&self, code: &str) -> anyhow::Result<ExecutionResult> {
        self.execute_code(code, "bash", "ubuntu:22.04").await
    }

    /// Execute Node.js code in a temporary cave
    pub async fn execute_node(&self, code: &str) -> anyhow::Result<ExecutionResult> {
        self.execute_code(code, "node", "node:20-slim").await
    }

    /// Execute code with custom image
    pub async fn execute_code(
        &self,
        code: &str,
        language: &str,
        image: &str,
    ) -> anyhow::Result<ExecutionResult> {
        // Create temporary cave
        let config = CaveConfig {
            image: image.to_string(),
            auto_remove: true,
            timeout: Some(60), // 1 minute timeout
            ..Default::default()
        };

        let cave_id = self.ghost.create_cave(
            format!("tmp-{}", uuid::Uuid::new_v4()),
            CaveType::Docker,
            config,
        ).await?;

        // Execute code
        let result = self.ghost.execute(&cave_id, code, language).await;

        // Cleanup
        let _ = self.ghost.destroy_cave(&cave_id).await;

        result
    }

    /// Create a persistent cave for multiple executions
    pub async fn create_persistent_cave(
        &self,
        name: impl Into<String>,
        image: impl Into<String>,
    ) -> anyhow::Result<String> {
        let config = CaveConfig {
            image: image.into(),
            auto_remove: false,
            ..Default::default()
        };

        self.ghost.create_cave(name, CaveType::Docker, config).await
    }

    /// Execute in existing cave
    pub async fn execute_in_cave(
        &self,
        cave_id: &str,
        code: &str,
        language: &str,
    ) -> anyhow::Result<ExecutionResult> {
        self.ghost.execute(cave_id, code, language).await
    }

    /// Destroy a cave
    pub async fn destroy_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        self.ghost.destroy_cave(cave_id).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    #[ignore] // Requires Docker
    async fn test_execute_python() {
        let executor = Executor::new().await.unwrap();
        let result = executor.execute_python("print('Hello from Ghost!')").await.unwrap();
        
        assert!(result.stdout.contains("Hello from Ghost!"));
        assert_eq!(result.exit_code, 0);
    }
}

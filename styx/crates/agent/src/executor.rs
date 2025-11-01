//! Task executor - REAL IMPLEMENTATION

use tracing::{info, error};
use serde::{Deserialize, Serialize};
use tokio::process::Command;

#[derive(Debug, Deserialize)]
struct TaskInfo {
    task_id: String,
    name: String,
    command: String,
    args: Option<Vec<String>>,
}

#[derive(Debug, Serialize)]
struct TaskResult {
    task_id: String,
    status: String,
    output: Option<String>,
    error: Option<String>,
}

/// Task executor
pub struct TaskExecutor {
    max_concurrent: usize,
    server_url: String,
    client: reqwest::Client,
}

impl TaskExecutor {
    /// Create new task executor
    pub fn new() -> Self {
        Self {
            max_concurrent: num_cpus::get(),
            server_url: std::env::var("STYX_SERVER_URL")
                .unwrap_or_else(|_| "http://localhost:8080".to_string()),
            client: reqwest::Client::new(),
        }
    }

    /// Poll for new tasks from server
    pub async fn poll_tasks(&self) -> anyhow::Result<Vec<TaskInfo>> {
        info!("Polling for tasks from {}", self.server_url);
        
        let url = format!("{}/api/v1/tasks/pending", self.server_url);
        
        match self.client.get(&url).send().await {
            Ok(response) => {
                if response.status().is_success() {
                    let tasks: Vec<TaskInfo> = response.json().await?;
                    info!("Received {} tasks", tasks.len());
                    Ok(tasks)
                } else {
                    error!("Server returned {}", response.status());
                    Ok(vec![])
                }
            }
            Err(e) => {
                error!("Failed to poll tasks: {}", e);
                Ok(vec![])
            }
        }
    }

    /// Execute a task
    pub async fn execute_task(&self, task: &TaskInfo) -> anyhow::Result<TaskResult> {
        info!("Executing task {}: {}", task.task_id, task.name);
        
        // Build command
        let mut cmd = Command::new(&task.command);
        if let Some(args) = &task.args {
            cmd.args(args);
        }
        
        // Execute command
        match cmd.output().await {
            Ok(output) => {
                let success = output.status.success();
                let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                
                if success {
                    info!("Task {} completed successfully", task.task_id);
                    Ok(TaskResult {
                        task_id: task.task_id.clone(),
                        status: "completed".to_string(),
                        output: Some(stdout),
                        error: None,
                    })
                } else {
                    error!("Task {} failed: {}", task.task_id, stderr);
                    Ok(TaskResult {
                        task_id: task.task_id.clone(),
                        status: "failed".to_string(),
                        output: Some(stdout),
                        error: Some(stderr),
                    })
                }
            }
            Err(e) => {
                error!("Failed to execute task {}: {}", task.task_id, e);
                Ok(TaskResult {
                    task_id: task.task_id.clone(),
                    status: "failed".to_string(),
                    output: None,
                    error: Some(e.to_string()),
                })
            }
        }
    }
    
    /// Report task result back to server
    pub async fn report_result(&self, result: &TaskResult) -> anyhow::Result<()> {
        let url = format!("{}/api/v1/tasks/{}/result", self.server_url, result.task_id);
        
        match self.client.post(&url).json(result).send().await {
            Ok(response) => {
                if response.status().is_success() {
                    info!("Reported result for task {}", result.task_id);
                    Ok(())
                } else {
                    error!("Failed to report result: {}", response.status());
                    Err(anyhow::anyhow!("Report failed: {}", response.status()))
                }
            }
            Err(e) => {
                error!("Failed to report result: {}", e);
                Err(anyhow::anyhow!("Report failed: {}", e))
            }
        }
    }
}

impl Default for TaskExecutor {
    fn default() -> Self {
        Self::new()
    }
}

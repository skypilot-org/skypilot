//! Task executor

use serde::Deserialize;
use serde_json::json;
use tokio::process::Command;
use tokio::time::{sleep, Duration};
use tracing::{error, info, warn};

/// Representation of a task served by the control plane.
#[derive(Debug, Deserialize, Clone)]
pub struct RemoteTask {
    pub id: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub workdir: Option<String>,
}

/// Task executor
pub struct TaskExecutor {
    max_concurrent: usize,
    client: reqwest::Client,
    server_url: String,
}

impl TaskExecutor {
    /// Create new task executor
    pub fn new() -> Self {
        let server_url = std::env::var("STYX_SERVER_URL")
            .unwrap_or_else(|_| "http://localhost:8080".to_string());

        Self {
            max_concurrent: num_cpus::get(),
            client: reqwest::Client::new(),
            server_url,
        }
    }

    fn tasks_endpoint(&self) -> String {
        format!("{}/api/v1/tasks", self.server_url.trim_end_matches('/'))
    }

    /// Poll for new tasks from server
    pub async fn poll_tasks(&self) -> anyhow::Result<()> {
        let url = self.tasks_endpoint();
        let response = self.client.get(&url).send().await?;

        if !response.status().is_success() {
            warn!(status = ?response.status(), "Task poll failed");
            return Ok(());
        }

        let body = response.text().await?;
        if body.trim().is_empty() {
            return Ok(());
        }

        let tasks: Vec<RemoteTask> = match serde_json::from_str(&body) {
            Ok(tasks) => tasks,
            Err(_) => {
                let ids: Vec<String> = serde_json::from_str(&body).unwrap_or_default();
                ids.into_iter()
                    .map(|id| RemoteTask {
                        id,
                        command: "echo".to_string(),
                        args: vec!["Task received".to_string()],
                        workdir: None,
                    })
                    .collect()
            }
        };

        for task in tasks.into_iter().take(self.max_concurrent) {
            self.execute_task(&task).await?;
        }

        Ok(())
    }

    /// Execute a task
    pub async fn execute_task(&self, task: &RemoteTask) -> anyhow::Result<()> {
        info!("Executing task: {}", task.id);

        let mut command = Command::new(&task.command);
        if !task.args.is_empty() {
            command.args(&task.args);
        }
        if let Some(dir) = &task.workdir {
            command.current_dir(dir);
        }

        let output = command.output().await;

        match output {
            Ok(output) => {
                if !output.status.success() {
                    error!(
                        ?task.id,
                        status = ?output.status.code(),
                        stderr = %String::from_utf8_lossy(&output.stderr),
                        "Task failed"
                    );
                } else {
                    info!("Task {} completed", task.id);
                }

                self.report_completion(
                    &task.id,
                    output.status.success(),
                    String::from_utf8_lossy(&output.stdout).trim(),
                    String::from_utf8_lossy(&output.stderr).trim(),
                )
                .await?;
            }
            Err(err) => {
                error!(?task.id, ?err, "Failed to spawn task");
                self.report_completion(&task.id, false, "", &err.to_string())
                    .await?;
            }
        }

        Ok(())
    }

    async fn report_completion(
        &self,
        task_id: &str,
        success: bool,
        stdout: &str,
        stderr: &str,
    ) -> anyhow::Result<()> {
        let url = format!("{}/{}", self.tasks_endpoint(), task_id);

        let payload = json!({
            "status": if success { "completed" } else { "failed" },
            "stdout": stdout,
            "stderr": stderr,
        });

        let response = self.client.post(url).json(&payload).send().await;
        if let Err(err) = response {
            warn!(?task_id, ?err, "Failed to report completion");
        }

        // Back off slightly to avoid hammering the server
        sleep(Duration::from_millis(25)).await;
        Ok(())
    }
}

impl Default for TaskExecutor {
    fn default() -> Self {
        Self::new()
    }
}

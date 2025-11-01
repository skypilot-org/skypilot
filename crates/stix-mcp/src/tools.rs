use serde::{Deserialize, Serialize};
use uuid::Uuid;
use anyhow::Result;
use std::path::PathBuf;
use crate::agent::{Agent, Permission};
use crate::locks::LockManager;
use crate::logger::AuditLogger;

/// Request to execute a tool
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolRequest {
    pub tool_name: String,
    pub parameters: serde_json::Value,
}

/// Response from tool execution
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResponse {
    pub success: bool,
    pub result: Option<serde_json::Value>,
    pub error: Option<String>,
}

/// Tool: Lock a file for exclusive access
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LockFileParams {
    pub file_path: String,
    pub duration_secs: Option<u64>,
}

/// Tool: Unlock a file
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnlockFileParams {
    pub file_path: String,
}

/// Tool: Update progress in PROGRESS.md
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateProgressParams {
    pub section: String,
    pub content: String,
    pub append: bool,
}

/// Tool: Create or update a task
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IssueTaskParams {
    pub title: String,
    pub description: String,
    pub priority: Option<String>,
    pub assigned_to: Option<String>,
}

/// Tool: Search codebase
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchCodebaseParams {
    pub query: String,
    pub file_pattern: Option<String>,
    pub case_sensitive: bool,
}

/// Tool executor
pub struct ToolExecutor {
    lock_manager: LockManager,
    audit_logger: AuditLogger,
    workspace_root: PathBuf,
}

impl ToolExecutor {
    pub fn new(
        lock_manager: LockManager,
        audit_logger: AuditLogger,
        workspace_root: PathBuf,
    ) -> Self {
        Self {
            lock_manager,
            audit_logger,
            workspace_root,
        }
    }

    /// Execute a tool request
    pub async fn execute(
        &self,
        agent: &Agent,
        request: ToolRequest,
    ) -> Result<ToolResponse> {
        let start = std::time::Instant::now();

        // Check rate limit
        self.audit_logger.check_rate_limit(agent.id)?;

        // Execute the tool based on name
        let result = match request.tool_name.as_str() {
            "lock_file" => self.lock_file(agent, request.parameters.clone()).await,
            "unlock_file" => self.unlock_file(agent, request.parameters.clone()).await,
            "update_progress" => self.update_progress(agent, request.parameters.clone()).await,
            "issue_task" => self.issue_task(agent, request.parameters.clone()).await,
            "search_codebase" => self.search_codebase(agent, request.parameters.clone()).await,
            "list_locks" => self.list_locks(agent).await,
            _ => {
                return Ok(ToolResponse {
                    success: false,
                    result: None,
                    error: Some(format!("Unknown tool: {}", request.tool_name)),
                });
            }
        };

        let duration_ms = start.elapsed().as_millis() as u64;

        // Log the action
        let log_result = match &result {
            Ok(response) => {
                if response.success {
                    Some(format!("Success: {:?}", response.result))
                } else {
                    Some(format!("Error: {:?}", response.error))
                }
            }
            Err(e) => Some(format!("Error: {}", e)),
        };

        self.audit_logger.log_action(
            agent.id,
            agent.name.clone(),
            request.tool_name,
            "execute".to_string(),
            request.parameters,
            log_result,
            Some(duration_ms),
        )?;

        result
    }

    async fn lock_file(&self, agent: &Agent, params: serde_json::Value) -> Result<ToolResponse> {
        if !agent.has_permission(&Permission::LockFiles) {
            return Ok(ToolResponse {
                success: false,
                result: None,
                error: Some("Permission denied: LockFiles".to_string()),
            });
        }

        let params: LockFileParams = serde_json::from_value(params)?;
        let file_path = PathBuf::from(&params.file_path);

        match self.lock_manager.lock_file(
            file_path.clone(),
            agent.id,
            agent.name.clone(),
            params.duration_secs,
        ) {
            Ok(_) => Ok(ToolResponse {
                success: true,
                result: Some(serde_json::json!({
                    "file_path": params.file_path,
                    "locked": true,
                })),
                error: None,
            }),
            Err(e) => Ok(ToolResponse {
                success: false,
                result: None,
                error: Some(e.to_string()),
            }),
        }
    }

    async fn unlock_file(&self, agent: &Agent, params: serde_json::Value) -> Result<ToolResponse> {
        if !agent.has_permission(&Permission::LockFiles) {
            return Ok(ToolResponse {
                success: false,
                result: None,
                error: Some("Permission denied: LockFiles".to_string()),
            });
        }

        let params: UnlockFileParams = serde_json::from_value(params)?;
        let file_path = PathBuf::from(&params.file_path);

        match self.lock_manager.unlock_file(&file_path, agent.id) {
            Ok(_) => Ok(ToolResponse {
                success: true,
                result: Some(serde_json::json!({
                    "file_path": params.file_path,
                    "unlocked": true,
                })),
                error: None,
            }),
            Err(e) => Ok(ToolResponse {
                success: false,
                result: None,
                error: Some(e.to_string()),
            }),
        }
    }

    async fn update_progress(&self, agent: &Agent, params: serde_json::Value) -> Result<ToolResponse> {
        if !agent.has_permission(&Permission::UpdateProgress) {
            return Ok(ToolResponse {
                success: false,
                result: None,
                error: Some("Permission denied: UpdateProgress".to_string()),
            });
        }

        let params: UpdateProgressParams = serde_json::from_value(params)?;
        let progress_file = self.workspace_root.join("PROGRESS.md");

        let result = if params.append {
            self.append_to_progress(&progress_file, &params.section, &params.content).await
        } else {
            self.write_progress_section(&progress_file, &params.section, &params.content).await
        };

        match result {
            Ok(_) => Ok(ToolResponse {
                success: true,
                result: Some(serde_json::json!({
                    "section": params.section,
                    "updated": true,
                })),
                error: None,
            }),
            Err(e) => Ok(ToolResponse {
                success: false,
                result: None,
                error: Some(e.to_string()),
            }),
        }
    }

    async fn append_to_progress(
        &self,
        progress_file: &PathBuf,
        section: &str,
        content: &str,
    ) -> Result<()> {
        use std::fs::OpenOptions;
        use std::io::Write;

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(progress_file)?;

        writeln!(file, "\n## {} - {}", section, chrono::Utc::now().format("%Y-%m-%d %H:%M:%S"))?;
        writeln!(file, "{}", content)?;

        Ok(())
    }

    async fn write_progress_section(
        &self,
        progress_file: &PathBuf,
        section: &str,
        content: &str,
    ) -> Result<()> {
        use std::fs;

        let existing_content = if progress_file.exists() {
            fs::read_to_string(progress_file)?
        } else {
            String::new()
        };

        // Simple implementation: append to end
        // A more sophisticated version would update specific sections
        let new_content = format!(
            "{}\n\n## {} - {}\n{}\n",
            existing_content.trim(),
            section,
            chrono::Utc::now().format("%Y-%m-%d %H:%M:%S"),
            content
        );

        fs::write(progress_file, new_content)?;
        Ok(())
    }

    async fn issue_task(&self, agent: &Agent, params: serde_json::Value) -> Result<ToolResponse> {
        if !agent.has_permission(&Permission::CreateTasks) {
            return Ok(ToolResponse {
                success: false,
                result: None,
                error: Some("Permission denied: CreateTasks".to_string()),
            });
        }

        let params: IssueTaskParams = serde_json::from_value(params)?;
        
        // Create task in TODO.md
        let todo_file = self.workspace_root.join("TODO.md");
        let task_id = Uuid::new_v4();
        
        match self.create_task(&todo_file, &task_id, &params).await {
            Ok(_) => Ok(ToolResponse {
                success: true,
                result: Some(serde_json::json!({
                    "task_id": task_id.to_string(),
                    "title": params.title,
                })),
                error: None,
            }),
            Err(e) => Ok(ToolResponse {
                success: false,
                result: None,
                error: Some(e.to_string()),
            }),
        }
    }

    async fn create_task(
        &self,
        todo_file: &PathBuf,
        task_id: &Uuid,
        params: &IssueTaskParams,
    ) -> Result<()> {
        use std::fs::OpenOptions;
        use std::io::Write;

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(todo_file)?;

        writeln!(file, "\n## Task: {} [{}]", params.title, task_id)?;
        writeln!(file, "**Created:** {}", chrono::Utc::now().format("%Y-%m-%d %H:%M:%S"))?;
        if let Some(ref priority) = params.priority {
            writeln!(file, "**Priority:** {}", priority)?;
        }
        if let Some(ref assigned_to) = params.assigned_to {
            writeln!(file, "**Assigned to:** {}", assigned_to)?;
        }
        writeln!(file, "\n{}\n", params.description)?;
        writeln!(file, "---")?;

        Ok(())
    }

    async fn search_codebase(&self, agent: &Agent, params: serde_json::Value) -> Result<ToolResponse> {
        if !agent.has_permission(&Permission::SearchCodebase) {
            return Ok(ToolResponse {
                success: false,
                result: None,
                error: Some("Permission denied: SearchCodebase".to_string()),
            });
        }

        let params: SearchCodebaseParams = serde_json::from_value(params)?;
        
        // Simple grep-based search
        match self.perform_search(&params).await {
            Ok(results) => Ok(ToolResponse {
                success: true,
                result: Some(serde_json::json!({
                    "query": params.query,
                    "matches": results,
                })),
                error: None,
            }),
            Err(e) => Ok(ToolResponse {
                success: false,
                result: None,
                error: Some(e.to_string()),
            }),
        }
    }

    async fn perform_search(&self, params: &SearchCodebaseParams) -> Result<Vec<String>> {
        use std::process::Command;

        let mut cmd = Command::new("grep");
        cmd.arg("-r");
        
        if !params.case_sensitive {
            cmd.arg("-i");
        }
        
        cmd.arg("-n"); // Line numbers
        cmd.arg(&params.query);
        cmd.arg(&self.workspace_root);

        if let Some(ref pattern) = params.file_pattern {
            cmd.arg("--include").arg(pattern);
        }

        let output = cmd.output()?;
        let results = String::from_utf8_lossy(&output.stdout);
        
        let matches: Vec<String> = results
            .lines()
            .take(100) // Limit results
            .map(|s| s.to_string())
            .collect();

        Ok(matches)
    }

    async fn list_locks(&self, agent: &Agent) -> Result<ToolResponse> {
        if !agent.has_permission(&Permission::ViewLogs) {
            return Ok(ToolResponse {
                success: false,
                result: None,
                error: Some("Permission denied: ViewLogs".to_string()),
            });
        }

        let locks = self.lock_manager.list_locks();
        
        Ok(ToolResponse {
            success: true,
            result: Some(serde_json::to_value(&locks)?),
            error: None,
        })
    }
}

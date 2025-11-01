//! Task builder pattern for fluent task construction

use super::{Task, RetryPolicy, TaskId};
use crate::error::{Error, Result};
use std::collections::HashMap;
use std::time::Duration;

/// Builder for constructing tasks with a fluent interface
#[derive(Debug, Default)]
pub struct TaskBuilder {
    name: Option<String>,
    description: Option<String>,
    run: Option<String>,
    setup: Option<String>,
    workdir: Option<String>,
    env: HashMap<String, String>,
    dependencies: Vec<TaskId>,
    retry_policy: Option<RetryPolicy>,
    timeout: Option<Duration>,
    metadata: HashMap<String, String>,
}

impl TaskBuilder {
    /// Create a new task builder
    pub fn new() -> Self {
        Self::default()
    }

    /// Set the task name (required)
    pub fn name(mut self, name: impl Into<String>) -> Self {
        self.name = Some(name.into());
        self
    }

    /// Set the task description
    pub fn description(mut self, description: impl Into<String>) -> Self {
        self.description = Some(description.into());
        self
    }

    /// Set the run command (required)
    pub fn run(mut self, run: impl Into<String>) -> Self {
        self.run = Some(run.into());
        self
    }

    /// Set the setup command
    pub fn setup(mut self, setup: impl Into<String>) -> Self {
        self.setup = Some(setup.into());
        self
    }

    /// Set the working directory
    pub fn workdir(mut self, workdir: impl Into<String>) -> Self {
        self.workdir = Some(workdir.into());
        self
    }

    /// Add an environment variable
    pub fn env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.insert(key.into(), value.into());
        self
    }

    /// Add multiple environment variables
    pub fn envs(mut self, envs: HashMap<String, String>) -> Self {
        self.env.extend(envs);
        self
    }

    /// Add a task dependency
    pub fn dependency(mut self, task_id: impl Into<TaskId>) -> Self {
        self.dependencies.push(task_id.into());
        self
    }

    /// Add multiple task dependencies
    pub fn dependencies(mut self, task_ids: Vec<TaskId>) -> Self {
        self.dependencies.extend(task_ids);
        self
    }

    /// Set the retry policy
    pub fn retry_policy(mut self, policy: RetryPolicy) -> Self {
        self.retry_policy = Some(policy);
        self
    }

    /// Enable default retry policy
    pub fn with_retry(mut self) -> Self {
        self.retry_policy = Some(RetryPolicy::default());
        self
    }

    /// Set custom retry configuration
    pub fn retry(mut self, max_attempts: u32, retry_delay: Duration) -> Self {
        self.retry_policy = Some(RetryPolicy {
            max_attempts,
            retry_delay,
            backoff_multiplier: 2.0,
            max_retry_delay: Duration::from_secs(300),
        });
        self
    }

    /// Set the task timeout
    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = Some(timeout);
        self
    }

    /// Add metadata key-value pair
    pub fn metadata(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.metadata.insert(key.into(), value.into());
        self
    }

    /// Add multiple metadata entries
    pub fn metadatas(mut self, metadata: HashMap<String, String>) -> Self {
        self.metadata.extend(metadata);
        self
    }

    /// Build the task, validating required fields
    pub fn build(self) -> Result<Task> {
        let name = self
            .name
            .ok_or_else(|| Error::task_validation("Task name is required"))?;

        let run = self
            .run
            .ok_or_else(|| Error::task_validation("Task run command is required"))?;

        if run.trim().is_empty() {
            return Err(Error::task_validation("Task run command cannot be empty"));
        }

        let mut task = Task::new(name, run);
        task.description = self.description;
        task.setup = self.setup;
        task.workdir = self.workdir;
        task.env = self.env;
        task.dependencies = self.dependencies;
        task.retry_policy = self.retry_policy;
        task.timeout = self.timeout;
        task.metadata = self.metadata;

        Ok(task)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_builder_basic() {
        let task = TaskBuilder::new()
            .name("my-task")
            .run("echo hello")
            .build()
            .unwrap();

        assert_eq!(task.name, "my-task");
        assert_eq!(task.run, "echo hello");
    }

    #[test]
    fn test_builder_full() {
        let task = TaskBuilder::new()
            .name("complex-task")
            .description("A complex task")
            .run("python train.py")
            .setup("pip install -r requirements.txt")
            .workdir("/workspace")
            .env("CUDA_VISIBLE_DEVICES", "0")
            .env("BATCH_SIZE", "32")
            .dependency("prep-task")
            .with_retry()
            .timeout(Duration::from_secs(3600))
            .metadata("version", "1.0")
            .build()
            .unwrap();

        assert_eq!(task.name, "complex-task");
        assert_eq!(task.description, Some("A complex task".to_string()));
        assert_eq!(task.run, "python train.py");
        assert_eq!(task.env.get("CUDA_VISIBLE_DEVICES"), Some(&"0".to_string()));
        assert_eq!(task.dependencies.len(), 1);
        assert!(task.retry_policy.is_some());
        assert_eq!(task.timeout, Some(Duration::from_secs(3600)));
    }

    #[test]
    fn test_builder_missing_required() {
        let result = TaskBuilder::new().name("test").build();
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("run command"));

        let result = TaskBuilder::new().run("echo test").build();
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("name"));
    }

    #[test]
    fn test_builder_empty_run() {
        let result = TaskBuilder::new()
            .name("test")
            .run("   ")
            .build();
        assert!(result.is_err());
    }

    #[test]
    fn test_builder_retry_config() {
        let task = TaskBuilder::new()
            .name("retry-task")
            .run("echo test")
            .retry(5, Duration::from_secs(30))
            .build()
            .unwrap();

        let policy = task.retry_policy.unwrap();
        assert_eq!(policy.max_attempts, 5);
        assert_eq!(policy.retry_delay, Duration::from_secs(30));
    }
}

//! Task validation logic

use super::Task;
use crate::error::{Error, Result};

/// Task validator
pub struct TaskValidator;

impl TaskValidator {
    /// Validate a task
    pub fn validate(task: &Task) -> Result<()> {
        Self::validate_name(&task.name)?;
        Self::validate_run(&task.run)?;
        Self::validate_dependencies(task)?;
        Self::validate_timeout(task)?;
        Self::validate_retry_policy(task)?;
        Ok(())
    }

    /// Validate task name
    fn validate_name(name: &str) -> Result<()> {
        if name.is_empty() {
            return Err(Error::task_validation("Task name cannot be empty"));
        }

        if name.len() > 255 {
            return Err(Error::task_validation(
                "Task name cannot exceed 255 characters",
            ));
        }

        // Check for invalid characters
        if !name
            .chars()
            .all(|c| c.is_alphanumeric() || c == '-' || c == '_')
        {
            return Err(Error::task_validation(
                "Task name can only contain alphanumeric characters, hyphens, and underscores",
            ));
        }

        Ok(())
    }

    /// Validate run command
    fn validate_run(run: &str) -> Result<()> {
        if run.trim().is_empty() {
            return Err(Error::task_validation("Task run command cannot be empty"));
        }

        if run.len() > 65536 {
            return Err(Error::task_validation(
                "Task run command cannot exceed 64KB",
            ));
        }

        Ok(())
    }

    /// Validate dependencies
    fn validate_dependencies(task: &Task) -> Result<()> {
        // Check for self-dependency
        if task.dependencies.contains(&task.id) {
            return Err(Error::task_validation("Task cannot depend on itself"));
        }

        // Check for duplicate dependencies
        let mut unique_deps = task.dependencies.clone();
        unique_deps.sort();
        unique_deps.dedup();
        if unique_deps.len() != task.dependencies.len() {
            return Err(Error::task_validation(
                "Task has duplicate dependencies",
            ));
        }

        Ok(())
    }

    /// Validate timeout
    fn validate_timeout(task: &Task) -> Result<()> {
        if let Some(timeout) = task.timeout {
            if timeout.as_secs() == 0 {
                return Err(Error::task_validation("Timeout must be greater than 0"));
            }

            if timeout.as_secs() > 86400 * 7 {
                // 7 days
                return Err(Error::task_validation(
                    "Timeout cannot exceed 7 days",
                ));
            }
        }

        Ok(())
    }

    /// Validate retry policy
    fn validate_retry_policy(task: &Task) -> Result<()> {
        if let Some(policy) = &task.retry_policy {
            if policy.max_attempts == 0 {
                return Err(Error::task_validation(
                    "Retry max_attempts must be greater than 0",
                ));
            }

            if policy.max_attempts > 100 {
                return Err(Error::task_validation(
                    "Retry max_attempts cannot exceed 100",
                ));
            }

            if policy.retry_delay.as_secs() == 0 {
                return Err(Error::task_validation(
                    "Retry delay must be greater than 0",
                ));
            }

            if policy.backoff_multiplier < 1.0 {
                return Err(Error::task_validation(
                    "Backoff multiplier must be at least 1.0",
                ));
            }

            if policy.backoff_multiplier > 10.0 {
                return Err(Error::task_validation(
                    "Backoff multiplier cannot exceed 10.0",
                ));
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::task::{RetryPolicy, TaskBuilder};
    use std::time::Duration;

    #[test]
    fn test_valid_task() {
        let task = TaskBuilder::new()
            .name("valid-task")
            .run("echo hello")
            .build()
            .unwrap();

        assert!(TaskValidator::validate(&task).is_ok());
    }

    #[test]
    fn test_invalid_name_empty() {
        let mut task = Task::new("valid", "echo test");
        task.name = "".to_string();
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_name_too_long() {
        let mut task = Task::new("valid", "echo test");
        task.name = "a".repeat(256);
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_name_special_chars() {
        let mut task = Task::new("valid", "echo test");
        task.name = "invalid@name".to_string();
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_run_empty() {
        let mut task = Task::new("valid", "echo test");
        task.run = "   ".to_string();
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_self_dependency() {
        let mut task = Task::new("test", "echo test");
        task.dependencies.push(task.id.clone());
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_duplicate_dependencies() {
        let mut task = Task::new("test", "echo test");
        task.dependencies.push("dep1".to_string());
        task.dependencies.push("dep1".to_string());
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_timeout_zero() {
        let mut task = Task::new("test", "echo test");
        task.timeout = Some(Duration::from_secs(0));
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_timeout_too_long() {
        let mut task = Task::new("test", "echo test");
        task.timeout = Some(Duration::from_secs(86400 * 8)); // 8 days
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_retry_zero_attempts() {
        let mut task = Task::new("test", "echo test");
        task.retry_policy = Some(RetryPolicy {
            max_attempts: 0,
            retry_delay: Duration::from_secs(10),
            backoff_multiplier: 2.0,
            max_retry_delay: Duration::from_secs(300),
        });
        assert!(TaskValidator::validate(&task).is_err());
    }

    #[test]
    fn test_invalid_retry_backoff() {
        let mut task = Task::new("test", "echo test");
        task.retry_policy = Some(RetryPolicy {
            max_attempts: 3,
            retry_delay: Duration::from_secs(10),
            backoff_multiplier: 0.5, // Invalid
            max_retry_delay: Duration::from_secs(300),
        });
        assert!(TaskValidator::validate(&task).is_err());
    }
}

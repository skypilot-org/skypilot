//! Command output structures

use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Result of command execution
pub type CommandResult<T> = std::result::Result<T, crate::Error>;

/// Output from command execution
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandOutput {
    /// Exit code of the command
    pub exit_code: i32,
    
    /// Standard output (stdout)
    pub stdout: String,
    
    /// Standard error (stderr)
    pub stderr: String,
    
    /// Execution duration
    pub duration: Duration,
    
    /// Whether the command succeeded (exit code 0)
    pub success: bool,
}

impl CommandOutput {
    /// Create a new command output
    pub fn new(exit_code: i32, stdout: String, stderr: String, duration: Duration) -> Self {
        Self {
            exit_code,
            stdout,
            stderr,
            duration,
            success: exit_code == 0,
        }
    }

    /// Check if command succeeded
    pub fn is_success(&self) -> bool {
        self.success
    }

    /// Check if command failed
    pub fn is_failure(&self) -> bool {
        !self.success
    }

    /// Get combined output (stdout + stderr)
    pub fn combined_output(&self) -> String {
        format!("{}{}", self.stdout, self.stderr)
    }

    /// Get trimmed stdout
    pub fn stdout_trimmed(&self) -> &str {
        self.stdout.trim()
    }

    /// Get trimmed stderr
    pub fn stderr_trimmed(&self) -> &str {
        self.stderr.trim()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_command_output_success() {
        let output = CommandOutput::new(
            0,
            "hello world\n".to_string(),
            String::new(),
            Duration::from_secs(1),
        );

        assert!(output.is_success());
        assert!(!output.is_failure());
        assert_eq!(output.exit_code, 0);
        assert_eq!(output.stdout_trimmed(), "hello world");
    }

    #[test]
    fn test_command_output_failure() {
        let output = CommandOutput::new(
            1,
            String::new(),
            "error occurred\n".to_string(),
            Duration::from_secs(1),
        );

        assert!(!output.is_success());
        assert!(output.is_failure());
        assert_eq!(output.exit_code, 1);
        assert_eq!(output.stderr_trimmed(), "error occurred");
    }

    #[test]
    fn test_combined_output() {
        let output = CommandOutput::new(
            0,
            "stdout\n".to_string(),
            "stderr\n".to_string(),
            Duration::from_secs(1),
        );

        assert_eq!(output.combined_output(), "stdout\nstderr\n");
    }
}

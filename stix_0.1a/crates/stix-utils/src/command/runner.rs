//! Command runner implementation

use super::output::{CommandOutput, CommandResult};
use crate::Error;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::time::timeout;
use tracing::{debug, trace, warn};

/// Command runner for executing shell commands
#[derive(Debug, Clone)]
pub struct CommandRunner {
    /// Working directory for command execution
    working_dir: Option<PathBuf>,
    
    /// Environment variables
    env_vars: HashMap<String, String>,
    
    /// Timeout duration
    timeout: Option<Duration>,
    
    /// Whether to capture output
    capture_output: bool,
    
    /// Whether to inherit stdio
    inherit_stdio: bool,
}

impl Default for CommandRunner {
    fn default() -> Self {
        Self::new()
    }
}

impl CommandRunner {
    /// Create a new command runner
    pub fn new() -> Self {
        Self {
            working_dir: None,
            env_vars: HashMap::new(),
            timeout: None,
            capture_output: true,
            inherit_stdio: false,
        }
    }

    /// Set working directory
    pub fn working_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.working_dir = Some(dir.into());
        self
    }

    /// Add environment variable
    pub fn env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env_vars.insert(key.into(), value.into());
        self
    }

    /// Add multiple environment variables
    pub fn envs(mut self, vars: HashMap<String, String>) -> Self {
        self.env_vars.extend(vars);
        self
    }

    /// Set timeout
    pub fn timeout(mut self, duration: Duration) -> Self {
        self.timeout = Some(duration);
        self
    }

    /// Disable output capture (for streaming)
    pub fn no_capture(mut self) -> Self {
        self.capture_output = false;
        self
    }

    /// Inherit parent's stdio
    pub fn inherit_stdio(mut self) -> Self {
        self.inherit_stdio = true;
        self
    }

    /// Execute a command
    pub async fn execute(&self, command: &str) -> CommandResult<CommandOutput> {
        debug!("Executing command: {}", command);
        
        let start = Instant::now();
        
        // Split command into program and args
        let parts: Vec<&str> = command.split_whitespace().collect();
        if parts.is_empty() {
            return Err(Error::command_execution("Empty command"));
        }

        let program = parts[0];
        let args = &parts[1..];

        // Build command
        let mut cmd = Command::new(program);
        cmd.args(args);

        // Set working directory
        if let Some(ref dir) = self.working_dir {
            cmd.current_dir(dir);
        }

        // Set environment variables
        for (key, value) in &self.env_vars {
            cmd.env(key, value);
        }

        // Configure stdio
        if self.inherit_stdio {
            cmd.stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());
        } else if self.capture_output {
            cmd.stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
        }

        // Execute with optional timeout
        let result = if let Some(timeout_duration) = self.timeout {
            match timeout(timeout_duration, self.run_command(cmd)).await {
                Ok(result) => result,
                Err(_) => {
                    return Err(Error::command_timeout(timeout_duration.as_secs()));
                }
            }
        } else {
            self.run_command(cmd).await
        };

        let duration = start.elapsed();
        
        match result {
            Ok((exit_code, stdout, stderr)) => {
                let output = CommandOutput::new(exit_code, stdout, stderr, duration);
                
                if output.is_success() {
                    trace!("Command succeeded in {:?}", duration);
                } else {
                    warn!(
                        "Command failed with exit code {} in {:?}",
                        exit_code, duration
                    );
                }
                
                Ok(output)
            }
            Err(e) => Err(e),
        }
    }

    /// Execute command and return only stdout
    pub async fn execute_stdout(&self, command: &str) -> CommandResult<String> {
        let output = self.execute(command).await?;
        Ok(output.stdout)
    }

    /// Execute command and ensure success
    pub async fn execute_checked(&self, command: &str) -> CommandResult<CommandOutput> {
        let output = self.execute(command).await?;
        
        if output.is_failure() {
            return Err(Error::command_execution(format!(
                "Command failed with exit code {}: {}",
                output.exit_code,
                output.stderr_trimmed()
            )));
        }
        
        Ok(output)
    }

    /// Run the command and capture output
    async fn run_command(
        &self,
        mut cmd: Command,
    ) -> CommandResult<(i32, String, String)> {
        if !self.capture_output {
            // Don't capture output
            let status = cmd.status().await?;
            let exit_code = status.code().unwrap_or(-1);
            return Ok((exit_code, String::new(), String::new()));
        }

        // Spawn process
        let mut child = cmd.spawn()?;

        // Capture stdout
        let stdout_future = async {
            if let Some(stdout) = child.stdout.take() {
                let mut reader = BufReader::new(stdout);
                let mut output = String::new();
                let mut line = String::new();
                
                while reader.read_line(&mut line).await? > 0 {
                    trace!("stdout: {}", line.trim_end());
                    output.push_str(&line);
                    line.clear();
                }
                
                Ok::<String, std::io::Error>(output)
            } else {
                Ok(String::new())
            }
        };

        // Capture stderr
        let stderr_future = async {
            if let Some(stderr) = child.stderr.take() {
                let mut reader = BufReader::new(stderr);
                let mut output = String::new();
                let mut line = String::new();
                
                while reader.read_line(&mut line).await? > 0 {
                    trace!("stderr: {}", line.trim_end());
                    output.push_str(&line);
                    line.clear();
                }
                
                Ok::<String, std::io::Error>(output)
            } else {
                Ok(String::new())
            }
        };

        // Wait for both outputs
        let (stdout_result, stderr_result) = tokio::join!(stdout_future, stderr_future);

        let stdout = stdout_result?;
        let stderr = stderr_result?;

        // Wait for process to complete
        let status = child.wait().await?;
        let exit_code = status.code().unwrap_or(-1);

        Ok((exit_code, stdout, stderr))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_simple_command() {
        let runner = CommandRunner::new();
        let output = runner.execute("echo hello").await.unwrap();
        
        assert!(output.is_success());
        assert_eq!(output.stdout_trimmed(), "hello");
        assert_eq!(output.exit_code, 0);
    }

    #[tokio::test]
    async fn test_command_with_args() {
        let runner = CommandRunner::new();
        let output = runner.execute("echo hello world").await.unwrap();
        
        assert!(output.is_success());
        assert_eq!(output.stdout_trimmed(), "hello world");
    }

    #[tokio::test]
    async fn test_failed_command() {
        let runner = CommandRunner::new();
        let output = runner.execute("false").await.unwrap();
        
        assert!(output.is_failure());
        assert_ne!(output.exit_code, 0);
    }

    #[tokio::test]
    async fn test_execute_checked_success() {
        let runner = CommandRunner::new();
        let result = runner.execute_checked("echo test").await;
        
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_execute_checked_failure() {
        let runner = CommandRunner::new();
        let result = runner.execute_checked("false").await;
        
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_execute_stdout() {
        let runner = CommandRunner::new();
        let stdout = runner.execute_stdout("echo test").await.unwrap();
        
        assert_eq!(stdout.trim(), "test");
    }

    #[tokio::test]
    async fn test_with_env() {
        let runner = CommandRunner::new().env("TEST_VAR", "test_value");
        
        #[cfg(unix)]
        let output = runner.execute("printenv TEST_VAR").await.unwrap();
        
        #[cfg(unix)]
        assert_eq!(output.stdout_trimmed(), "test_value");
    }

    #[tokio::test]
    async fn test_with_timeout() {
        let runner = CommandRunner::new().timeout(Duration::from_millis(100));
        
        // This should timeout on Unix systems
        #[cfg(unix)]
        {
            let result = runner.execute("sleep 1").await;
            assert!(result.is_err());
        }
    }

    #[tokio::test]
    async fn test_working_directory() {
        let runner = CommandRunner::new().working_dir("/tmp");
        
        #[cfg(unix)]
        let output = runner.execute("pwd").await.unwrap();
        
        #[cfg(unix)]
        assert_eq!(output.stdout_trimmed(), "/tmp");
    }
}

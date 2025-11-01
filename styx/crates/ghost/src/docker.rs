//! Docker Cave Manager
//!
//! Manages Docker container caves

use bollard::Docker;
use bollard::container::{Config, CreateContainerOptions, StartContainerOptions, StopContainerOptions};
use bollard::models::HostConfig;
use std::collections::HashMap;

use crate::cave::Cave;
use crate::ghost::ExecutionResult;

/// Docker cave manager
pub struct DockerCaveManager {
    docker: Docker,
}

impl DockerCaveManager {
    /// Create new Docker manager
    pub async fn new() -> anyhow::Result<Self> {
        let docker = Docker::connect_with_local_defaults()
            .map_err(|e| anyhow::anyhow!("Failed to connect to Docker: {}", e))?;

        // Test connection
        docker.ping().await
            .map_err(|e| anyhow::anyhow!("Docker ping failed: {}", e))?;

        println!("?? Docker: Connected successfully");

        Ok(Self { docker })
    }

    /// Create a Docker cave
    pub async fn create_cave(&self, cave: &Cave) -> anyhow::Result<()> {
        println!("   ?? Creating Docker container cave...");

        // Prepare environment variables
        let mut env_vars = vec![];
        for (key, value) in &cave.env {
            env_vars.push(format!("{}={}", key, value));
        }

        // Add secrets as env vars
        for (key, value) in &cave.secrets {
            env_vars.push(format!("{}={}", key, value));
        }

        // Create host config with resource limits
        let host_config = HostConfig {
            memory: cave.config.memory_limit.map(|m| (m * 1024 * 1024) as i64),
            nano_cpus: cave.config.cpu_limit.map(|c| (c * 1_000_000_000.0) as i64),
            network_mode: cave.config.network_mode.clone(),
            auto_remove: Some(cave.config.auto_remove),
            ..Default::default()
        };

        // Create container config
        let config = Config {
            image: Some(cave.config.image.clone()),
            cmd: cave.config.command.clone(),
            working_dir: cave.config.workdir.clone(),
            env: Some(env_vars),
            host_config: Some(host_config),
            ..Default::default()
        };

        // Create container
        let options = CreateContainerOptions {
            name: cave.id.clone(),
            ..Default::default()
        };

        self.docker.create_container(Some(options), config).await
            .map_err(|e| anyhow::anyhow!("Failed to create container: {}", e))?;

        // Start container
        self.docker.start_container(&cave.id, None::<StartContainerOptions<String>>).await
            .map_err(|e| anyhow::anyhow!("Failed to start container: {}", e))?;

        println!("      ? Docker container '{}' created and started", cave.id);

        Ok(())
    }

    /// Execute code in Docker cave
    pub async fn execute(
        &self,
        cave_id: &str,
        code: &str,
        language: &str,
    ) -> anyhow::Result<ExecutionResult> {
        use bollard::exec::{CreateExecOptions, StartExecResults};

        let start_time = std::time::Instant::now();

        // Determine command based on language
        let cmd = match language {
            "python" => vec!["python3", "-c", code],
            "bash" => vec!["bash", "-c", code],
            "node" | "javascript" => vec!["node", "-e", code],
            _ => return Err(anyhow::anyhow!("Unsupported language: {}", language)),
        };

        // Create exec instance
        let exec = self.docker.create_exec(
            cave_id,
            CreateExecOptions {
                attach_stdout: Some(true),
                attach_stderr: Some(true),
                cmd: Some(cmd.iter().map(|s| s.to_string()).collect()),
                ..Default::default()
            },
        ).await
            .map_err(|e| anyhow::anyhow!("Failed to create exec: {}", e))?;

        // Start exec
        let mut stdout_data = String::new();
        let mut stderr_data = String::new();

        if let StartExecResults::Attached { mut output, .. } = self.docker.start_exec(&exec.id, None).await
            .map_err(|e| anyhow::anyhow!("Failed to start exec: {}", e))? {
            
            use futures::StreamExt;
            while let Some(Ok(msg)) = output.next().await {
                match msg {
                    bollard::container::LogOutput::StdOut { message } => {
                        stdout_data.push_str(&String::from_utf8_lossy(&message));
                    }
                    bollard::container::LogOutput::StdErr { message } => {
                        stderr_data.push_str(&String::from_utf8_lossy(&message));
                    }
                    _ => {}
                }
            }
        }

        let duration_ms = start_time.elapsed().as_millis() as u64;

        // Get exit code
        let inspect = self.docker.inspect_exec(&exec.id).await
            .map_err(|e| anyhow::anyhow!("Failed to inspect exec: {}", e))?;

        let exit_code = inspect.exit_code.unwrap_or(0) as i32;

        Ok(ExecutionResult {
            stdout: stdout_data,
            stderr: stderr_data,
            exit_code,
            duration_ms,
        })
    }

    /// Stop Docker cave
    pub async fn stop_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        self.docker.stop_container(cave_id, None::<StopContainerOptions>).await
            .map_err(|e| anyhow::anyhow!("Failed to stop container: {}", e))?;

        Ok(())
    }

    /// Destroy Docker cave
    pub async fn destroy_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        use bollard::container::RemoveContainerOptions;

        self.docker.remove_container(cave_id, Some(RemoveContainerOptions {
            force: true,
            ..Default::default()
        })).await
            .map_err(|e| anyhow::anyhow!("Failed to remove container: {}", e))?;

        Ok(())
    }

    /// Get logs from Docker cave
    pub async fn get_logs(&self, cave_id: &str) -> anyhow::Result<String> {
        use bollard::container::LogsOptions;
        use futures::StreamExt;

        let mut logs = String::new();

        let options = Some(LogsOptions::<String> {
            stdout: true,
            stderr: true,
            ..Default::default()
        });

        let mut stream = self.docker.logs(cave_id, options);

        while let Some(Ok(msg)) = stream.next().await {
            match msg {
                bollard::container::LogOutput::StdOut { message } |
                bollard::container::LogOutput::StdErr { message } => {
                    logs.push_str(&String::from_utf8_lossy(&message));
                }
                _ => {}
            }
        }

        Ok(logs)
    }
}

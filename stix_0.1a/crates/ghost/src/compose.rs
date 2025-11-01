//! Docker Compose Cave Manager
//!
//! Manages Docker Compose multi-container caves

use std::process::Stdio;
use tokio::process::Command;
use std::path::PathBuf;

use crate::cave::Cave;
use crate::ghost::ExecutionResult;

/// Docker Compose cave manager
pub struct ComposeCaveManager {
    compose_files_dir: PathBuf,
}

impl ComposeCaveManager {
    /// Create new Compose manager
    pub async fn new() -> anyhow::Result<Self> {
        let compose_dir = dirs::home_dir()
            .ok_or_else(|| anyhow::anyhow!("Cannot find home directory"))?
            .join(".ghost/compose");

        // Create directory
        tokio::fs::create_dir_all(&compose_dir).await?;

        // Check if docker-compose is installed
        let output = Command::new("docker-compose")
            .arg("--version")
            .output()
            .await;

        if output.is_err() {
            println!("??  Docker Compose: Not installed, trying 'docker compose'...");
        } else {
            println!("?? Docker Compose: Available");
        }

        Ok(Self {
            compose_files_dir: compose_dir,
        })
    }

    /// Create a Compose cave
    pub async fn create_cave(&self, cave: &Cave) -> anyhow::Result<()> {
        println!("   ?? Creating Docker Compose cave...");

        // Generate docker-compose.yml
        let compose_file = self.compose_files_dir.join(format!("{}.yml", cave.id));
        
        let compose_content = self.generate_compose_file(cave)?;
        
        tokio::fs::write(&compose_file, compose_content).await?;

        // Start compose stack
        let output = self.run_compose_command(&cave.id, &["up", "-d"]).await?;

        if !output.status.success() {
            return Err(anyhow::anyhow!(
                "Failed to start compose stack: {}",
                String::from_utf8_lossy(&output.stderr)
            ));
        }

        println!("      ? Compose stack '{}' created", cave.id);

        Ok(())
    }

    /// Generate docker-compose.yml content
    fn generate_compose_file(&self, cave: &Cave) -> anyhow::Result<String> {
        use serde_yaml::Value;
        use std::collections::BTreeMap;

        let mut services = BTreeMap::new();
        
        // Main service
        let mut service = BTreeMap::new();
        service.insert("image".to_string(), Value::String(cave.config.image.clone()));
        
        if let Some(ref cmd) = cave.config.command {
            service.insert("command".to_string(), Value::Sequence(
                cmd.iter().map(|s| Value::String(s.clone())).collect()
            ));
        }

        if let Some(ref workdir) = cave.config.workdir {
            service.insert("working_dir".to_string(), Value::String(workdir.clone()));
        }

        // Environment variables
        if !cave.env.is_empty() || !cave.secrets.is_empty() {
            let mut env = Vec::new();
            for (key, value) in &cave.env {
                env.push(Value::String(format!("{}={}", key, value)));
            }
            for (key, value) in &cave.secrets {
                env.push(Value::String(format!("{}={}", key, value)));
            }
            service.insert("environment".to_string(), Value::Sequence(env));
        }

        // Resource limits
        let mut deploy = BTreeMap::new();
        let mut resources = BTreeMap::new();
        let mut limits = BTreeMap::new();
        
        if let Some(cpus) = cave.config.cpu_limit {
            limits.insert("cpus".to_string(), Value::String(format!("{}", cpus)));
        }
        if let Some(mem) = cave.config.memory_limit {
            limits.insert("memory".to_string(), Value::String(format!("{}M", mem)));
        }
        
        if !limits.is_empty() {
            resources.insert("limits".to_string(), Value::Mapping(limits.into_iter().collect()));
            deploy.insert("resources".to_string(), Value::Mapping(resources.into_iter().collect()));
            service.insert("deploy".to_string(), Value::Mapping(deploy.into_iter().collect()));
        }

        services.insert("main".to_string(), Value::Mapping(service.into_iter().collect()));

        let mut compose = BTreeMap::new();
        compose.insert("version".to_string(), Value::String("3.8".to_string()));
        compose.insert("services".to_string(), Value::Mapping(services.into_iter().collect()));

        serde_yaml::to_string(&Value::Mapping(compose.into_iter().collect()))
            .map_err(|e| anyhow::anyhow!("Failed to generate compose file: {}", e))
    }

    /// Execute code in Compose cave
    pub async fn execute(
        &self,
        cave_id: &str,
        code: &str,
        language: &str,
    ) -> anyhow::Result<ExecutionResult> {
        let start_time = std::time::Instant::now();

        // Determine command
        let cmd = match language {
            "python" => vec!["python3", "-c", code],
            "bash" => vec!["bash", "-c", code],
            _ => return Err(anyhow::anyhow!("Unsupported language: {}", language)),
        };

        // Execute in main service
        let mut exec_args = vec!["exec", "-T", "main"];
        exec_args.extend(cmd.iter().map(|s| s.as_ref()));

        let output = self.run_compose_command(cave_id, &exec_args).await?;

        let duration_ms = start_time.elapsed().as_millis() as u64;

        Ok(ExecutionResult {
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            exit_code: output.status.code().unwrap_or(1),
            duration_ms,
        })
    }

    /// Stop Compose cave
    pub async fn stop_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        self.run_compose_command(cave_id, &["stop"]).await?;
        Ok(())
    }

    /// Destroy Compose cave
    pub async fn destroy_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        self.run_compose_command(cave_id, &["down", "-v"]).await?;

        // Remove compose file
        let compose_file = self.compose_files_dir.join(format!("{}.yml", cave_id));
        let _ = tokio::fs::remove_file(compose_file).await;

        Ok(())
    }

    /// Get logs from Compose cave
    pub async fn get_logs(&self, cave_id: &str) -> anyhow::Result<String> {
        let output = self.run_compose_command(cave_id, &["logs"]).await?;
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }

    /// Run docker-compose command
    async fn run_compose_command(
        &self,
        cave_id: &str,
        args: &[&str],
    ) -> anyhow::Result<std::process::Output> {
        let compose_file = self.compose_files_dir.join(format!("{}.yml", cave_id));

        let mut cmd = Command::new("docker-compose");
        cmd.arg("-f")
            .arg(&compose_file)
            .arg("-p")
            .arg(cave_id)
            .args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        cmd.output().await
            .map_err(|e| anyhow::anyhow!("Failed to run docker-compose: {}", e))
    }
}

//! Code Server - Full VSCode experience in browser
//!
//! Provides VSCode at /code-server/

use tokio::process::Command;
use std::process::Stdio;

/// Code Server manager
pub struct CodeServer {
    port: u16,
    password: Option<String>,
}

impl CodeServer {
    /// Create new Code Server
    pub fn new(port: u16) -> Self {
        Self {
            port,
            password: None,
        }
    }

    /// Set password
    pub fn with_password(mut self, password: impl Into<String>) -> Self {
        self.password = Some(password.into());
        self
    }

    /// Start Code Server in cave
    pub async fn start(&self, cave_id: &str) -> anyhow::Result<()> {
        println!("?? Starting Code Server on port {}...", self.port);

        // Install code-server
        let install_cmd = r#"
            curl -fsSL https://code-server.dev/install.sh | sh
        "#;

        self.exec_in_cave(cave_id, install_cmd).await?;

        // Start code-server
        let mut start_cmd = format!(
            "code-server --bind-addr 0.0.0.0:{} --auth none",
            self.port
        );

        if let Some(ref password) = self.password {
            start_cmd = format!(
                "code-server --bind-addr 0.0.0.0:{} --auth password",
                self.port
            );
            // Set password via env
            let env_cmd = format!("export PASSWORD={}", password);
            self.exec_in_cave(cave_id, &env_cmd).await?;
        }

        // Start in background
        let bg_cmd = format!("{} &", start_cmd);
        self.exec_in_cave(cave_id, &bg_cmd).await?;

        println!("   ? Code Server started!");
        println!("      Access at: http://localhost:{}", self.port);

        Ok(())
    }

    /// Execute command in cave
    async fn exec_in_cave(&self, cave_id: &str, command: &str) -> anyhow::Result<()> {
        Command::new("docker")
            .args(&["exec", cave_id, "bash", "-c", command])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .await?;
        Ok(())
    }
}

// Docker installation
//!
//! Python: sky/provision/instance_setup.py (Docker section)
//! Rust: styx-sky/src/provision/docker.rs

use tokio::process::Command;
use std::process::Stdio;
use tracing::info;

use crate::clouds::ClusterHandle;
use crate::exceptions::{Result, SkyError};

/// Docker setup helper
pub struct DockerSetup;

impl DockerSetup {
    /// Setup Docker
    pub async fn setup(handle: &ClusterHandle, ssh_user: &str) -> Result<()> {
        info!("?? Installing Docker...");
        
        let script = r#"
            set -e
            
            # Check if Docker already installed
            if command -v docker &> /dev/null; then
                echo "Docker already installed: $(docker --version)"
                exit 0
            fi
            
            # Install Docker
            curl -fsSL https://get.docker.com -o get-docker.sh
            sudo sh get-docker.sh
            rm get-docker.sh
            
            # Add user to docker group
            sudo usermod -aG docker $USER
            
            # Start Docker service
            sudo systemctl start docker
            sudo systemctl enable docker
            
            # Install docker-compose
            sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
            sudo chmod +x /usr/local/bin/docker-compose
            
            echo "? Docker installed successfully"
            echo "Note: You may need to re-login for docker group to take effect"
        "#;
        
        Self::ssh_execute(handle, ssh_user, script).await?;
        
        Ok(())
    }

    /// SSH execute helper
    async fn ssh_execute(
        handle: &ClusterHandle,
        ssh_user: &str,
        command: &str,
    ) -> Result<String> {
        let ip = handle.head_ip.as_ref()
            .ok_or_else(|| SkyError::ProvisionError("No head IP".to_string()))?;
        
        let ssh_cmd = format!(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {}@{} '{}'",
            ssh_user, ip, command.replace('\'', "'\\''")
        );
        
        let output = Command::new("sh")
            .arg("-c")
            .arg(&ssh_cmd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| SkyError::ProvisionError(format!("SSH failed: {}", e)))?;
        
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(SkyError::ProvisionError(format!("Docker setup failed: {}", stderr)));
        }
        
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

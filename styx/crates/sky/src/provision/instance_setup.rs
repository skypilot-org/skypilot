//! Instance setup utilities
//!
//! Python: sky/provision/instance_setup.py
//! Rust: styx-sky/src/provision/instance_setup.rs
//!
//! Handles post-provision instance configuration:
//! - File syncing
//! - Environment variables
//! - User scripts
//! - Working directory setup

use tokio::process::Command;
use std::process::Stdio;
use tracing::{info, error};

use crate::clouds::ClusterHandle;
use crate::exceptions::{Result, SkyError};

/// Instance setup helper
pub struct InstanceSetup;

impl InstanceSetup {
    /// Sync files to instance using rsync
    pub async fn sync_files(
        handle: &ClusterHandle,
        ssh_user: &str,
        local_path: &str,
        remote_path: &str,
    ) -> Result<()> {
        let ip = handle.head_ip.as_ref()
            .ok_or_else(|| SkyError::ProvisionError("No head IP".to_string()))?;
        
        info!("?? Syncing {} to {}:{}", local_path, ip, remote_path);
        
        let rsync_cmd = format!(
            "rsync -avz --progress -e 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' {} {}@{}:{}",
            local_path, ssh_user, ip, remote_path
        );
        
        let output = Command::new("sh")
            .arg("-c")
            .arg(&rsync_cmd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| SkyError::ProvisionError(format!("rsync failed: {}", e)))?;
        
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            error!("File sync failed: {}", stderr);
            return Err(SkyError::ProvisionError(format!("File sync failed: {}", stderr)));
        }
        
        info!("? Files synced successfully");
        Ok(())
    }

    /// Setup environment variables
    pub async fn setup_env_vars(
        handle: &ClusterHandle,
        ssh_user: &str,
        env_vars: &std::collections::HashMap<String, String>,
    ) -> Result<()> {
        if env_vars.is_empty() {
            return Ok(());
        }
        
        info!("??  Setting up environment variables...");
        
        let mut script = String::from("set -e\n");
        
        // Add to ~/.bashrc
        for (key, value) in env_vars {
            script.push_str(&format!("echo 'export {}=\"{}\"' >> ~/.bashrc\n", key, value));
        }
        
        // Source bashrc
        script.push_str("source ~/.bashrc\n");
        script.push_str("echo '? Environment variables set'\n");
        
        Self::ssh_execute(handle, ssh_user, &script).await?;
        
        Ok(())
    }

    /// Run setup script
    pub async fn run_setup_script(
        handle: &ClusterHandle,
        ssh_user: &str,
        script: &str,
    ) -> Result<()> {
        info!("?? Running setup script...");
        
        let output = Self::ssh_execute(handle, ssh_user, script).await?;
        
        // Print output
        for line in output.lines() {
            info!("   {}", line);
        }
        
        Ok(())
    }

    /// Create working directory
    pub async fn create_workdir(
        handle: &ClusterHandle,
        ssh_user: &str,
        workdir: &str,
    ) -> Result<()> {
        info!("?? Creating working directory: {}", workdir);
        
        let script = format!("mkdir -p {} && cd {} && pwd", workdir, workdir);
        
        Self::ssh_execute(handle, ssh_user, &script).await?;
        
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
            return Err(SkyError::ProvisionError(format!("Command failed: {}", stderr)));
        }
        
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

//! Skylet - Remote agent helper
//!
//! Provides lightweight remote command execution and file transfer utilities
//! compatible with the expectations of the rest of the crate.

use std::path::Path;

use tokio::process::Command;

use crate::clouds::ClusterHandle;
use crate::exceptions::{Result, SkyError};

/// Remote agent used to interact with provisioned clusters.
#[derive(Debug, Clone)]
pub struct Skylet {
    handle: ClusterHandle,
    user: String,
}

impl Skylet {
    /// Create a new Skylet for the supplied cluster handle.
    pub fn new(handle: ClusterHandle) -> Self {
        Self {
            handle,
            user: "ubuntu".to_string(),
        }
    }

    /// Override the SSH user.
    pub fn with_user(mut self, user: impl Into<String>) -> Self {
        self.user = user.into();
        self
    }

    /// Returns the public address this skylet connects to.
    pub fn address(&self) -> Option<&str> {
        self.handle.head_ip.as_deref()
    }

    /// Execute a shell command on the remote cluster.
    pub async fn exec(&self, command: &str) -> Result<String> {
        match self.handle.cloud.as_str() {
            "local" => self.exec_local(command).await,
            _ => self.exec_remote(command).await,
        }
    }

    /// Upload a file to the remote machine.
    pub async fn upload(&self, local: impl AsRef<Path>, remote: &str) -> Result<()> {
        match self.handle.cloud.as_str() {
            "local" => {
                tokio::fs::copy(local.as_ref(), remote)
                    .await
                    .map_err(|e| SkyError::BackendError(e.to_string()))?;
                Ok(())
            }
            _ => {
                let address = self
                    .address()
                    .ok_or_else(|| SkyError::BackendError("Cluster has no head IP".to_string()))?;

                let target = format!("{}@{}:{}", self.user, address, remote);

                let status = Command::new("scp")
                    .arg("-o")
                    .arg("StrictHostKeyChecking=no")
                    .arg("-o")
                    .arg("UserKnownHostsFile=/dev/null")
                    .arg(local.as_ref())
                    .arg(&target)
                    .status()
                    .await
                    .map_err(|e| SkyError::BackendError(e.to_string()))?;

                if status.success() {
                    Ok(())
                } else {
                    Err(SkyError::BackendError(format!(
                        "scp exited with status {}",
                        status
                    )))
                }
            }
        }
    }

    /// Download a file from the remote machine.
    pub async fn download(&self, remote: &str, local: impl AsRef<Path>) -> Result<()> {
        match self.handle.cloud.as_str() {
            "local" => {
                tokio::fs::copy(remote, local.as_ref())
                    .await
                    .map_err(|e| SkyError::BackendError(e.to_string()))?;
                Ok(())
            }
            _ => {
                let address = self
                    .address()
                    .ok_or_else(|| SkyError::BackendError("Cluster has no head IP".to_string()))?;
                let source = format!("{}@{}:{}", self.user, address, remote);

                let status = Command::new("scp")
                    .arg("-o")
                    .arg("StrictHostKeyChecking=no")
                    .arg("-o")
                    .arg("UserKnownHostsFile=/dev/null")
                    .arg(&source)
                    .arg(local.as_ref())
                    .status()
                    .await
                    .map_err(|e| SkyError::BackendError(e.to_string()))?;

                if status.success() {
                    Ok(())
                } else {
                    Err(SkyError::BackendError(format!(
                        "scp exited with status {}",
                        status
                    )))
                }
            }
        }
    }

    async fn exec_local(&self, command: &str) -> Result<String> {
        let output = Command::new("sh")
            .arg("-c")
            .arg(command)
            .output()
            .await
            .map_err(|e| SkyError::BackendError(e.to_string()))?;

        if !output.status.success() {
            return Err(SkyError::BackendError(
                String::from_utf8_lossy(&output.stderr).to_string(),
            ));
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }

    async fn exec_remote(&self, command: &str) -> Result<String> {
        let address = self
            .address()
            .ok_or_else(|| SkyError::BackendError("Cluster has no head IP".to_string()))?;

        let ssh_cmd = format!(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {user}@{address} '{command}'",
            user = self.user,
            address = address,
            command = command.replace('\'', "'\"'\"'")
        );

        let output = Command::new("sh")
            .arg("-c")
            .arg(&ssh_cmd)
            .output()
            .await
            .map_err(|e| SkyError::BackendError(e.to_string()))?;

        if !output.status.success() {
            return Err(SkyError::BackendError(
                String::from_utf8_lossy(&output.stderr).to_string(),
            ));
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

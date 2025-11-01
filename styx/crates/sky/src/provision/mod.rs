//! Provisioning infrastructure
//!
//! Python: sky/provision/provisioner.py + instance_setup.py
//! Rust: styx-sky/src/provision/
//!
//! COMPLETE PROVISIONING PIPELINE:
//! 1. Instance provisioning (cloud-specific)
//! 2. SSH setup & wait
//! 3. System dependencies (apt-get)
//! 4. Python & pip
//! 5. Conda environment
//! 6. Docker installation
//! 7. GPU drivers (CUDA, cuDNN)
//! 8. Ray installation
//! 9. User scripts

use async_trait::async_trait;
use std::collections::HashMap;
use tracing::{info, warn, error};

use crate::clouds::{CloudProvider, ClusterHandle};
use crate::exceptions::{Result, SkyError};
use crate::resources::Resources;

pub mod instance_setup;
pub mod gpu;
pub mod conda;
pub mod docker;

pub use instance_setup::InstanceSetup;
pub use gpu::GpuSetup;
pub use conda::CondaSetup;
pub use docker::DockerSetup;

/// Provisioning configuration
#[derive(Debug, Clone)]
pub struct ProvisionConfig {
    /// Install system dependencies
    pub install_system_deps: bool,
    /// Install Python & pip
    pub install_python: bool,
    /// Setup Conda environment
    pub setup_conda: bool,
    /// Install Docker
    pub install_docker: bool,
    /// Install GPU drivers
    pub install_gpu_drivers: bool,
    /// Install Ray
    pub install_ray: bool,
    /// Custom setup commands
    pub custom_setup: Vec<String>,
    /// SSH user
    pub ssh_user: String,
    /// SSH retries
    pub ssh_retries: u32,
    /// SSH retry interval (seconds)
    pub ssh_retry_interval: u64,
}

impl Default for ProvisionConfig {
    fn default() -> Self {
        Self {
            install_system_deps: true,
            install_python: true,
            setup_conda: false, // Optional
            install_docker: false, // Optional
            install_gpu_drivers: false, // Auto-detect
            install_ray: true,
            custom_setup: vec![],
            ssh_user: "ubuntu".to_string(),
            ssh_retries: 30,
            ssh_retry_interval: 2,
        }
    }
}

/// Provisioner - Orchestrates instance provisioning
pub struct Provisioner {
    cloud_provider: Box<dyn CloudProvider>,
    config: ProvisionConfig,
}

impl Provisioner {
    /// Create new provisioner
    pub fn new(cloud_provider: Box<dyn CloudProvider>) -> Self {
        Self {
            cloud_provider,
            config: ProvisionConfig::default(),
        }
    }

    /// Create provisioner with custom config
    pub fn with_config(cloud_provider: Box<dyn CloudProvider>, config: ProvisionConfig) -> Self {
        Self {
            cloud_provider,
            config,
        }
    }

    /// Provision and setup a cluster
    pub async fn provision_cluster(
        &self,
        cluster_name: &str,
        resources: &Resources,
    ) -> Result<ClusterHandle> {
        info!("?? Provisioner: Starting cluster '{}'", cluster_name);
        
        // Phase 1: Provision VMs
        info!("?? Phase 1/7: Provisioning VMs...");
        let handle = self.cloud_provider.provision(cluster_name, resources).await?;
        info!("? VMs provisioned: {}", handle.head_ip.as_ref().unwrap_or(&"N/A".to_string()));
        
        // Phase 2: Wait for SSH
        info!("?? Phase 2/7: Waiting for SSH...");
        self.wait_for_ssh(&handle).await?;
        info!("? SSH is ready");
        
        // Phase 3: System dependencies
        if self.config.install_system_deps {
            info!("?? Phase 3/7: Installing system dependencies...");
            self.install_system_dependencies(&handle).await?;
            info!("? System dependencies installed");
        } else {
            info!("??  Phase 3/7: Skipping system dependencies");
        }
        
        // Phase 4: Python & pip
        if self.config.install_python {
            info!("?? Phase 4/7: Setting up Python...");
            self.setup_python(&handle).await?;
            info!("? Python ready");
        } else {
            info!("??  Phase 4/7: Skipping Python setup");
        }
        
        // Phase 5: Conda (optional)
        if self.config.setup_conda {
            info!("?? Phase 5/7: Setting up Conda...");
            CondaSetup::setup(&handle, &self.config.ssh_user).await?;
            info!("? Conda ready");
        } else {
            info!("??  Phase 5/7: Skipping Conda");
        }
        
        // Phase 6: Docker (optional)
        if self.config.install_docker {
            info!("?? Phase 6/7: Installing Docker...");
            DockerSetup::setup(&handle, &self.config.ssh_user).await?;
            info!("? Docker ready");
        } else {
            info!("??  Phase 6/7: Skipping Docker");
        }
        
        // Phase 7: GPU drivers (if GPUs detected)
        if self.config.install_gpu_drivers || self.needs_gpu(resources) {
            info!("?? Phase 7/7: Installing GPU drivers...");
            match GpuSetup::setup(&handle, &self.config.ssh_user).await {
                Ok(_) => info!("? GPU drivers installed"),
                Err(e) => warn!("??  GPU driver installation failed: {} (continuing)", e),
            }
        } else {
            info!("??  Phase 7/7: Skipping GPU drivers");
        }
        
        // Phase 8: Ray
        if self.config.install_ray {
            info!("??  Phase 8/8: Installing Ray...");
            self.setup_ray(&handle).await?;
            info!("? Ray ready");
        } else {
            info!("??  Phase 8/8: Skipping Ray");
        }
        
        // Phase 9: Custom setup
        if !self.config.custom_setup.is_empty() {
            info!("??  Phase 9/9: Running custom setup...");
            for cmd in &self.config.custom_setup {
                info!("   Executing: {}", cmd);
                self.ssh_execute(&handle, cmd).await?;
            }
            info!("? Custom setup complete");
        }
        
        info!("?? Cluster '{}' is READY!", cluster_name);
        
        Ok(handle)
    }

    /// Wait for SSH to be available
    async fn wait_for_ssh(&self, handle: &ClusterHandle) -> Result<()> {
        let mut retries = 0;
        loop {
            match self.ssh_execute(handle, "echo 'SSH ready'").await {
                Ok(_) => return Ok(()),
                Err(_) if retries < self.config.ssh_retries => {
                    retries += 1;
                    if retries % 5 == 0 {
                        info!("   Still waiting... (attempt {}/{})", retries, self.config.ssh_retries);
                    }
                    tokio::time::sleep(tokio::time::Duration::from_secs(self.config.ssh_retry_interval)).await;
                }
                Err(e) => {
                    error!("SSH connection failed after {} retries", retries);
                    return Err(e);
                }
            }
        }
    }

    /// Install system dependencies
    async fn install_system_dependencies(&self, handle: &ClusterHandle) -> Result<()> {
        let script = r#"
            set -e
            
            # Update package list
            sudo apt-get update -qq
            
            # Install essential packages
            sudo apt-get install -y -qq \
                build-essential \
                curl \
                wget \
                git \
                rsync \
                tmux \
                vim \
                htop \
                unzip \
                ca-certificates \
                gnupg \
                lsb-release
            
            echo "? System dependencies installed"
        "#;
        
        self.ssh_execute(handle, script).await?;
        Ok(())
    }

    /// Setup Python & pip
    async fn setup_python(&self, handle: &ClusterHandle) -> Result<()> {
        let script = r#"
            set -e
            
            # Check if Python 3 is installed
            if ! command -v python3 &> /dev/null; then
                echo "Installing Python 3..."
                sudo apt-get install -y python3 python3-pip python3-venv
            else
                echo "Python 3 already installed: $(python3 --version)"
            fi
            
            # Upgrade pip
            python3 -m pip install --upgrade pip setuptools wheel --quiet
            
            echo "? Python setup complete"
        "#;
        
        self.ssh_execute(handle, script).await?;
        Ok(())
    }

    /// Setup Ray
    async fn setup_ray(&self, handle: &ClusterHandle) -> Result<()> {
        let script = r#"
            set -e
            
            # Install Ray
            if ! command -v ray &> /dev/null; then
                echo "Installing Ray..."
                pip install -q ray[default]
            else
                echo "Ray already installed: $(ray --version)"
            fi
            
            echo "? Ray setup complete"
        "#;
        
        self.ssh_execute(handle, script).await?;
        Ok(())
    }

    /// Check if resources need GPU
    fn needs_gpu(&self, resources: &Resources) -> bool {
        // Check if accelerators are specified
        resources.accelerators.is_some()
    }

    /// Execute command via SSH
    async fn ssh_execute(&self, handle: &ClusterHandle, command: &str) -> Result<String> {
        use tokio::process::Command;
        use std::process::Stdio;
        
        let ip = handle.head_ip.as_ref()
            .ok_or_else(|| SkyError::ProvisionError("No head IP available".to_string()))?;
        
        let ssh_cmd = format!(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 {}@{} '{}'",
            self.config.ssh_user, ip, command.replace('\'', "'\\''")
        );
        
        let output = Command::new("sh")
            .arg("-c")
            .arg(&ssh_cmd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| SkyError::ProvisionError(format!("SSH execution failed: {}", e)))?;
        
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(SkyError::ProvisionError(format!("Command failed: {}", stderr)));
        }
        
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_provision_config_default() {
        let config = ProvisionConfig::default();
        assert_eq!(config.ssh_user, "ubuntu");
        assert_eq!(config.ssh_retries, 30);
        assert!(config.install_system_deps);
        assert!(config.install_python);
        assert!(config.install_ray);
    }
}

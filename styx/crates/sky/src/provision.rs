//! Cluster Provisioning - ECHTE Implementation!
//!
//! Handles actual VM/container provisioning on clouds

use std::collections::HashMap;
use tokio::process::Command;

use crate::clouds::{CloudProvider, ClusterHandle};
use crate::exceptions::Result;
use crate::resources::Resources;

/// Provisioner for cloud resources
pub struct Provisioner {
    cloud: Box<dyn CloudProvider>,
}

impl Provisioner {
    pub fn new(cloud: Box<dyn CloudProvider>) -> Self {
        Self { cloud }
    }

    /// Provision a cluster with given resources
    pub async fn provision(
        &self,
        cluster_name: &str,
        resources: &Resources,
        num_nodes: usize,
    ) -> Result<ClusterHandle> {
        println!("?? Provisioning {} node(s)...", num_nodes);
        
        // 1. Create cloud resources
        let handle = self.cloud.provision(cluster_name, resources).await?;
        
        // 2. Wait for instances to be accessible
        self.wait_for_ssh(&handle).await?;
        
        // 3. Install system packages
        self.install_system_packages(&handle).await?;
        
        // 4. Setup Python environment
        self.setup_python_env(&handle).await?;
        
        println!("? Provisioning complete!");
        
        Ok(handle)
    }

    /// Wait for SSH to become available
    async fn wait_for_ssh(&self, handle: &ClusterHandle) -> Result<()> {
        let ip = handle.head_ip.as_ref().ok_or_else(|| {
            crate::exceptions::SkyError::ProvisioningError("No IP available".to_string())
        })?;

        println!("   Waiting for SSH on {}...", ip);
        
        for attempt in 1..=30 {
            let result = Command::new("ssh")
                .args(&[
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=5",
                    &format!("ubuntu@{}", ip),
                    "echo", "ready"
                ])
                .output()
                .await;

            if result.is_ok() && result.unwrap().status.success() {
                println!("   SSH is ready!");
                return Ok(());
            }

            if attempt < 30 {
                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
            }
        }

        Err(crate::exceptions::SkyError::ProvisioningError(
            "SSH timeout".to_string()
        ))
    }

    /// Install system packages
    async fn install_system_packages(&self, handle: &ClusterHandle) -> Result<()> {
        let ip = handle.head_ip.as_ref().unwrap();
        
        println!("   Installing system packages...");
        
        let install_cmd = r#"
            sudo apt-get update -qq
            sudo apt-get install -y python3-pip python3-venv rsync
        "#;

        self.ssh_exec(ip, install_cmd).await?;
        
        Ok(())
    }

    /// Setup Python environment
    async fn setup_python_env(&self, handle: &ClusterHandle) -> Result<()> {
        let ip = handle.head_ip.as_ref().unwrap();
        
        println!("   Setting up Python environment...");
        
        let setup_cmd = r#"
            python3 -m venv ~/.sky-venv || true
            source ~/.sky-venv/bin/activate
            pip install -q --upgrade pip setuptools wheel
        "#;

        self.ssh_exec(ip, setup_cmd).await?;
        
        Ok(())
    }

    /// Execute SSH command
    async fn ssh_exec(&self, ip: &str, command: &str) -> Result<String> {
        let output = Command::new("ssh")
            .args(&[
                "-o", "StrictHostKeyChecking=no",
                &format!("ubuntu@{}", ip),
                command
            ])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::ProvisioningError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::ProvisioningError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

/// Setup configuration for provisioning
#[derive(Debug, Clone)]
pub struct ProvisionConfig {
    pub ssh_user: String,
    pub ssh_key_path: Option<String>,
    pub setup_commands: Vec<String>,
}

impl Default for ProvisionConfig {
    fn default() -> Self {
        Self {
            ssh_user: "ubuntu".to_string(),
            ssh_key_path: None,
            setup_commands: vec![],
        }
    }
}

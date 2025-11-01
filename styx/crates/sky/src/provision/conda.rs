//! Conda environment setup
//!
//! Python: sky/provision/instance_setup.py (Conda section)
//! Rust: styx-sky/src/provision/conda.rs

use tokio::process::Command;
use std::process::Stdio;
use tracing::info;

use crate::clouds::ClusterHandle;
use crate::exceptions::{Result, SkyError};

/// Conda setup helper
pub struct CondaSetup;

impl CondaSetup {
    /// Setup Conda (Miniconda)
    pub async fn setup(handle: &ClusterHandle, ssh_user: &str) -> Result<()> {
        info!("?? Installing Miniconda...");
        
        let script = r#"
            set -e
            
            # Check if conda already installed
            if command -v conda &> /dev/null; then
                echo "Conda already installed: $(conda --version)"
                exit 0
            fi
            
            # Download Miniconda
            wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh -q
            
            # Install Miniconda
            bash miniconda.sh -b -p $HOME/miniconda
            rm miniconda.sh
            
            # Add to PATH
            echo 'export PATH=$HOME/miniconda/bin:$PATH' >> ~/.bashrc
            source ~/.bashrc
            
            # Initialize conda
            conda init bash
            
            # Create default environment
            conda create -n sky python=3.10 -y
            
            echo "? Conda installed successfully"
        "#;
        
        Self::ssh_execute(handle, ssh_user, script).await?;
        
        Ok(())
    }

    /// Create conda environment
    pub async fn create_env(
        handle: &ClusterHandle,
        ssh_user: &str,
        env_name: &str,
        python_version: &str,
    ) -> Result<()> {
        info!("?? Creating Conda environment '{}'...", env_name);
        
        let script = format!(
            "conda create -n {} python={} -y && echo '? Environment created'",
            env_name, python_version
        );
        
        Self::ssh_execute(handle, ssh_user, &script).await?;
        
        Ok(())
    }

    /// Install packages in conda environment
    pub async fn install_packages(
        handle: &ClusterHandle,
        ssh_user: &str,
        env_name: &str,
        packages: &[String],
    ) -> Result<()> {
        if packages.is_empty() {
            return Ok(());
        }
        
        info!("?? Installing packages in '{}'...", env_name);
        
        let packages_str = packages.join(" ");
        let script = format!(
            "conda activate {} && conda install {} -y",
            env_name, packages_str
        );
        
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
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {}@{} 'bash -l -c \"{}\"'",
            ssh_user, ip, command.replace('"', "\\\"")
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
            return Err(SkyError::ProvisionError(format!("Conda setup failed: {}", stderr)));
        }
        
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

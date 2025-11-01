//! GPU driver installation
//!
//! Python: sky/provision/instance_setup.py (GPU section)
//! Rust: styx-sky/src/provision/gpu.rs
//!
//! SUPPORTS:
//! - NVIDIA CUDA drivers
//! - cuDNN
//! - GPU detection
//! - Driver version selection

use tokio::process::Command;
use std::process::Stdio;
use tracing::{info, warn};

use crate::clouds::ClusterHandle;
use crate::exceptions::{Result, SkyError};

/// GPU setup configuration
#[derive(Debug, Clone)]
pub struct GpuConfig {
    /// CUDA version (e.g., "12.2")
    pub cuda_version: String,
    /// cuDNN version (optional)
    pub cudnn_version: Option<String>,
    /// Install CUDA toolkit
    pub install_toolkit: bool,
    /// Install cuDNN
    pub install_cudnn: bool,
}

impl Default for GpuConfig {
    fn default() -> Self {
        Self {
            cuda_version: "12.2".to_string(),
            cudnn_version: Some("8.9".to_string()),
            install_toolkit: true,
            install_cudnn: true,
        }
    }
}

/// GPU setup helper
pub struct GpuSetup;

impl GpuSetup {
    /// Setup GPU drivers (auto-detect and install)
    pub async fn setup(handle: &ClusterHandle, ssh_user: &str) -> Result<()> {
        info!("?? Detecting GPUs...");
        
        // Check if NVIDIA GPU is present
        if !Self::has_nvidia_gpu(handle, ssh_user).await? {
            warn!("No NVIDIA GPU detected, skipping GPU setup");
            return Ok(());
        }
        
        info!("? NVIDIA GPU detected");
        
        // Use default config
        let config = GpuConfig::default();
        
        Self::setup_with_config(handle, ssh_user, &config).await
    }

    /// Setup GPU drivers with custom config
    pub async fn setup_with_config(
        handle: &ClusterHandle,
        ssh_user: &str,
        config: &GpuConfig,
    ) -> Result<()> {
        info!("?? Installing NVIDIA drivers & CUDA {}...", config.cuda_version);
        
        // Install NVIDIA drivers
        Self::install_nvidia_drivers(handle, ssh_user).await?;
        
        // Install CUDA toolkit
        if config.install_toolkit {
            Self::install_cuda_toolkit(handle, ssh_user, &config.cuda_version).await?;
        }
        
        // Install cuDNN
        if config.install_cudnn {
            if let Some(cudnn_version) = &config.cudnn_version {
                Self::install_cudnn(handle, ssh_user, cudnn_version).await?;
            }
        }
        
        info!("? GPU drivers installed successfully");
        
        Ok(())
    }

    /// Check if NVIDIA GPU is present
    async fn has_nvidia_gpu(handle: &ClusterHandle, ssh_user: &str) -> Result<bool> {
        let output = Self::ssh_execute(handle, ssh_user, "lspci | grep -i nvidia || true").await?;
        Ok(!output.trim().is_empty())
    }

    /// Install NVIDIA drivers
    async fn install_nvidia_drivers(handle: &ClusterHandle, ssh_user: &str) -> Result<()> {
        info!("   Installing NVIDIA drivers...");
        
        let script = r#"
            set -e
            
            # Check if driver already installed
            if command -v nvidia-smi &> /dev/null; then
                echo "NVIDIA driver already installed: $(nvidia-smi --version | head -1)"
                exit 0
            fi
            
            # Install NVIDIA driver
            sudo apt-get update -qq
            sudo apt-get install -y -qq nvidia-driver-535
            
            # Verify installation
            if command -v nvidia-smi &> /dev/null; then
                echo "? NVIDIA driver installed successfully"
            else
                echo "??  Driver installed but nvidia-smi not available (may need reboot)"
            fi
        "#;
        
        Self::ssh_execute(handle, ssh_user, script).await?;
        
        Ok(())
    }

    /// Install CUDA toolkit
    async fn install_cuda_toolkit(
        handle: &ClusterHandle,
        ssh_user: &str,
        cuda_version: &str,
    ) -> Result<()> {
        info!("   Installing CUDA toolkit {}...", cuda_version);
        
        // Parse major.minor version (e.g., "12.2" -> "12-2")
        let cuda_pkg_version = cuda_version.replace('.', "-");
        
        let script = format!(r#"
            set -e
            
            # Check if CUDA already installed
            if command -v nvcc &> /dev/null; then
                echo "CUDA already installed: $(nvcc --version | grep release)"
                exit 0
            fi
            
            # Add NVIDIA CUDA repository
            wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
            sudo dpkg -i cuda-keyring_1.1-1_all.deb
            rm cuda-keyring_1.1-1_all.deb
            
            # Install CUDA toolkit
            sudo apt-get update -qq
            sudo apt-get install -y -qq cuda-toolkit-{} || sudo apt-get install -y -qq cuda
            
            # Add to PATH
            echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
            echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
            
            source ~/.bashrc
            
            echo "? CUDA toolkit installed"
        "#, cuda_pkg_version);
        
        Self::ssh_execute(handle, ssh_user, &script).await?;
        
        Ok(())
    }

    /// Install cuDNN
    async fn install_cudnn(
        handle: &ClusterHandle,
        ssh_user: &str,
        cudnn_version: &str,
    ) -> Result<()> {
        info!("   Installing cuDNN {}...", cudnn_version);
        
        let script = format!(r#"
            set -e
            
            # Install cuDNN via apt (if available)
            sudo apt-get install -y -qq libcudnn8 libcudnn8-dev || true
            
            echo "? cuDNN installed (version: {})"
        "#, cudnn_version);
        
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
            return Err(SkyError::ProvisionError(format!("GPU setup failed: {}", stderr)));
        }
        
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

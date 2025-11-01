//! Ghost - The Cave Orchestrator
//!
//! Manages all caves (isolated execution environments)

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::cave::{Cave, CaveConfig, CaveStatus, CaveType};
use crate::docker::DockerCaveManager;
use crate::compose::ComposeCaveManager;
use crate::kubernetes::KubernetesCaveManager;
use crate::secrets::SecretsManager;

/// Ghost - The main orchestrator
pub struct Ghost {
    /// Active caves
    caves: Arc<RwLock<HashMap<String, Cave>>>,
    
    /// Docker manager
    docker: DockerCaveManager,
    
    /// Compose manager
    compose: ComposeCaveManager,
    
    /// Kubernetes manager
    kubernetes: KubernetesCaveManager,
    
    /// Secrets manager
    secrets: Arc<SecretsManager>,
}

impl Ghost {
    /// Create new Ghost instance
    pub async fn new() -> anyhow::Result<Self> {
        Ok(Self {
            caves: Arc::new(RwLock::new(HashMap::new())),
            docker: DockerCaveManager::new().await?,
            compose: ComposeCaveManager::new().await?,
            kubernetes: KubernetesCaveManager::new().await?,
            secrets: Arc::new(SecretsManager::new()?),
        })
    }

    /// Create a new cave
    pub async fn create_cave(
        &self,
        name: impl Into<String>,
        cave_type: CaveType,
        config: CaveConfig,
    ) -> anyhow::Result<String> {
        let mut cave = Cave::new(name, cave_type.clone(), config);
        
        println!("?? Ghost: Creating cave '{}'...", cave.name);
        
        // Encrypt secrets before storing
        for (key, value) in &cave.secrets {
            let encrypted = self.secrets.encrypt(value)?;
            cave.secrets.insert(key.clone(), encrypted);
        }
        
        // Create cave based on type
        match cave_type {
            CaveType::Docker => {
                self.docker.create_cave(&cave).await?;
            }
            CaveType::Compose => {
                self.compose.create_cave(&cave).await?;
            }
            CaveType::Kubernetes => {
                self.kubernetes.create_cave(&cave).await?;
            }
        }
        
        cave.update_status(CaveStatus::Running);
        
        let cave_id = cave.id.clone();
        
        // Store cave
        let mut caves = self.caves.write().await;
        caves.insert(cave_id.clone(), cave);
        
        println!("   ? Cave '{}' created!", cave_id);
        
        Ok(cave_id)
    }

    /// Execute code in a cave
    pub async fn execute(
        &self,
        cave_id: &str,
        code: &str,
        language: &str,
    ) -> anyhow::Result<ExecutionResult> {
        println!("?? Ghost: Executing {} code in cave '{}'...", language, cave_id);
        
        let caves = self.caves.read().await;
        let cave = caves.get(cave_id)
            .ok_or_else(|| anyhow::anyhow!("Cave not found: {}", cave_id))?;

        if !cave.is_running() {
            return Err(anyhow::anyhow!("Cave is not running"));
        }

        // Execute based on cave type
        let result = match cave.cave_type {
            CaveType::Docker => {
                self.docker.execute(cave_id, code, language).await?
            }
            CaveType::Compose => {
                self.compose.execute(cave_id, code, language).await?
            }
            CaveType::Kubernetes => {
                self.kubernetes.execute(cave_id, code, language).await?
            }
        };

        println!("   ? Execution complete!");
        
        Ok(result)
    }

    /// Stop a cave
    pub async fn stop_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        println!("?? Ghost: Stopping cave '{}'...", cave_id);
        
        let mut caves = self.caves.write().await;
        let cave = caves.get_mut(cave_id)
            .ok_or_else(|| anyhow::anyhow!("Cave not found: {}", cave_id))?;

        // Stop based on type
        match cave.cave_type {
            CaveType::Docker => {
                self.docker.stop_cave(cave_id).await?;
            }
            CaveType::Compose => {
                self.compose.stop_cave(cave_id).await?;
            }
            CaveType::Kubernetes => {
                self.kubernetes.stop_cave(cave_id).await?;
            }
        }

        cave.update_status(CaveStatus::Stopped);
        
        println!("   ? Cave stopped!");
        
        Ok(())
    }

    /// Destroy a cave
    pub async fn destroy_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        println!("?? Ghost: Destroying cave '{}'...", cave_id);
        
        let mut caves = self.caves.write().await;
        let cave = caves.get_mut(cave_id)
            .ok_or_else(|| anyhow::anyhow!("Cave not found: {}", cave_id))?;

        // Destroy based on type
        match cave.cave_type {
            CaveType::Docker => {
                self.docker.destroy_cave(cave_id).await?;
            }
            CaveType::Compose => {
                self.compose.destroy_cave(cave_id).await?;
            }
            CaveType::Kubernetes => {
                self.kubernetes.destroy_cave(cave_id).await?;
            }
        }

        cave.update_status(CaveStatus::Destroyed);
        caves.remove(cave_id);
        
        println!("   ? Cave destroyed!");
        
        Ok(())
    }

    /// List all caves
    pub async fn list_caves(&self) -> Vec<Cave> {
        let caves = self.caves.read().await;
        caves.values().cloned().collect()
    }

    /// Get cave by ID
    pub async fn get_cave(&self, cave_id: &str) -> Option<Cave> {
        let caves = self.caves.read().await;
        caves.get(cave_id).cloned()
    }

    /// Get cave logs
    pub async fn get_logs(&self, cave_id: &str) -> anyhow::Result<String> {
        let caves = self.caves.read().await;
        let cave = caves.get(cave_id)
            .ok_or_else(|| anyhow::anyhow!("Cave not found: {}", cave_id))?;

        match cave.cave_type {
            CaveType::Docker => self.docker.get_logs(cave_id).await,
            CaveType::Compose => self.compose.get_logs(cave_id).await,
            CaveType::Kubernetes => self.kubernetes.get_logs(cave_id).await,
        }
    }
}

/// Execution result
#[derive(Debug, Clone)]
pub struct ExecutionResult {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub duration_ms: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_ghost_creation() {
        let ghost = Ghost::new().await;
        assert!(ghost.is_ok());
    }
}

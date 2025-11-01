//! SSH server for Git

use anyhow::Result;
use russh::server::{Server, Session, Handler};
use russh::*;
use std::sync::Arc;
use crate::config::SshConfig;

/// SSH server
pub struct SshServer {
    config: Arc<SshConfig>,
}

impl SshServer {
    pub async fn new(config: &SshConfig) -> Result<Self> {
        Ok(Self {
            config: Arc::new(config.clone()),
        })
    }
    
    pub async fn run(self) -> Result<()> {
        // TODO: Implement full SSH server
        // This would require implementing russh::server::Handler
        // For now, this is a stub
        tracing::info!("SSH server would start on port {}", self.config.port);
        Ok(())
    }
}

/// Git SSH handler
pub struct GitSshHandler;

#[async_trait::async_trait]
impl Handler for GitSshHandler {
    type Error = anyhow::Error;
    
    async fn channel_open_session(
        self,
        channel: Channel<Msg>,
        session: Session,
    ) -> Result<(Self, bool, Session), Self::Error> {
        Ok((self, true, session))
    }
}

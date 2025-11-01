//! # Styx Agent
//!
//! Remote execution agent for Styx.

pub mod executor;
pub mod heartbeat;
pub mod system;

pub use executor::TaskExecutor;
pub use heartbeat::HeartbeatService;
pub use system::SystemMonitor;

/// Agent version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

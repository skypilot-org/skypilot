//! Task definition and management
//!
//! Provides the core Task abstraction for defining workloads to be executed.

mod task;
mod builder;
mod validator;

pub use task::{Task, TaskId, TaskStatus, RetryPolicy};
pub use builder::TaskBuilder;
pub use validator::TaskValidator;

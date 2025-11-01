//! Global state management
//!
//! Persistent state layer with storage-agnostic interfaces

mod traits;
mod db_store;
mod transitions;

pub use traits::{TaskStore, GraphStore, KvStore};
pub use db_store::DbTaskStore;
pub use transitions::{TaskTransition, StateTransition};

use crate::{Error, Result};
use async_trait::async_trait;

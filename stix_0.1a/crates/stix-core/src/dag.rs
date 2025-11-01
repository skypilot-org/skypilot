//! Directed Acyclic Graph execution
//!
//! Provides dependency-aware task execution with cycle detection and parallel processing.

use crate::error::{Error, Result};
use crate::task::{Task, TaskId, TaskStatus};
use daggy::{Dag as DaggyDag, NodeIndex, Walker};

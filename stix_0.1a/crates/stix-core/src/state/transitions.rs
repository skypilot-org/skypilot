//! State transitions with strict enforcement

use crate::task::TaskStatus;

/// Task state transition
pub struct TaskTransition;

impl TaskTransition {
    /// Check if transition is valid
    pub fn is_valid(from: TaskStatus, to: TaskStatus) -> bool {
        match (from, to) {
            // Pending can go to Running or Cancelled
            (TaskStatus::Pending, TaskStatus::Running) => true,
            (TaskStatus::Pending, TaskStatus::Cancelled) => true,
            
            // Running can go to Success, Failed, or Cancelled
            (TaskStatus::Running, TaskStatus::Success) => true,
            (TaskStatus::Running, TaskStatus::Failed) => true,
            (TaskStatus::Running, TaskStatus::Cancelled) => true,
            
            // Failed can go to Retrying or stay Failed
            (TaskStatus::Failed, TaskStatus::Retrying) => true,
            (TaskStatus::Failed, TaskStatus::Failed) => true,
            
            // Retrying can go to Running
            (TaskStatus::Retrying, TaskStatus::Running) => true,
            (TaskStatus::Retrying, TaskStatus::Pending) => true,
            
            // Same state is always valid
            _ if from == to => true,
            
            // All other transitions invalid
            _ => false,
        }
    }

    /// Get allowed next states
    pub fn allowed_next_states(from: TaskStatus) -> Vec<TaskStatus> {
        match from {
            TaskStatus::Pending => vec![TaskStatus::Running, TaskStatus::Cancelled],
            TaskStatus::Running => vec![TaskStatus::Success, TaskStatus::Failed, TaskStatus::Cancelled],
            TaskStatus::Failed => vec![TaskStatus::Retrying, TaskStatus::Failed],
            TaskStatus::Retrying => vec![TaskStatus::Running, TaskStatus::Pending],
            TaskStatus::Success => vec![],
            TaskStatus::Cancelled => vec![],
        }
    }
}

/// State transition trait
pub trait StateTransition {
    /// Apply transition
    fn transition(&mut self, to: TaskStatus) -> Result<(), String>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_transitions() {
        assert!(TaskTransition::is_valid(TaskStatus::Pending, TaskStatus::Running));
        assert!(TaskTransition::is_valid(TaskStatus::Running, TaskStatus::Success));
        assert!(TaskTransition::is_valid(TaskStatus::Failed, TaskStatus::Retrying));
        assert!(TaskTransition::is_valid(TaskStatus::Retrying, TaskStatus::Running));
    }

    #[test]
    fn test_invalid_transitions() {
        assert!(!TaskTransition::is_valid(TaskStatus::Success, TaskStatus::Running));
        assert!(!TaskTransition::is_valid(TaskStatus::Cancelled, TaskStatus::Running));
        assert!(!TaskTransition::is_valid(TaskStatus::Pending, TaskStatus::Success));
    }

    #[test]
    fn test_allowed_next_states() {
        let next = TaskTransition::allowed_next_states(TaskStatus::Pending);
        assert_eq!(next.len(), 2);
        assert!(next.contains(&TaskStatus::Running));
        
        let next = TaskTransition::allowed_next_states(TaskStatus::Success);
        assert_eq!(next.len(), 0);
    }
}

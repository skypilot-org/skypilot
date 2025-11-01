use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};
use dashmap::DashMap;
use std::sync::Arc;
use anyhow::Result;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;

/// Represents an action logged in the system
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEntry {
    pub id: Uuid,
    pub agent_id: Uuid,
    pub agent_name: String,
    pub tool_name: String,
    pub action: String,
    pub parameters: serde_json::Value,
    pub result: Option<String>,
    pub timestamp: DateTime<Utc>,
    pub duration_ms: Option<u64>,
}

/// Rate limit tracking for agents
#[derive(Debug, Clone)]
struct RateLimit {
    count: u32,
    window_start: DateTime<Utc>,
}

/// Manages logging and rate limiting
#[derive(Clone)]
pub struct AuditLogger {
    logs: Arc<DashMap<Uuid, Vec<LogEntry>>>,
    rate_limits: Arc<DashMap<Uuid, RateLimit>>,
    max_requests_per_minute: u32,
    log_file: Option<PathBuf>,
}

impl AuditLogger {
    pub fn new(max_requests_per_minute: u32, log_file: Option<PathBuf>) -> Self {
        Self {
            logs: Arc::new(DashMap::new()),
            rate_limits: Arc::new(DashMap::new()),
            max_requests_per_minute,
            log_file,
        }
    }

    /// Log an action
    pub fn log_action(
        &self,
        agent_id: Uuid,
        agent_name: String,
        tool_name: String,
        action: String,
        parameters: serde_json::Value,
        result: Option<String>,
        duration_ms: Option<u64>,
    ) -> Result<()> {
        let entry = LogEntry {
            id: Uuid::new_v4(),
            agent_id,
            agent_name: agent_name.clone(),
            tool_name: tool_name.clone(),
            action: action.clone(),
            parameters: parameters.clone(),
            result: result.clone(),
            timestamp: Utc::now(),
            duration_ms,
        };

        // Add to in-memory logs
        self.logs
            .entry(agent_id)
            .or_insert_with(Vec::new)
            .push(entry.clone());

        // Write to file if configured
        if let Some(ref log_file) = self.log_file {
            self.write_to_file(&entry, log_file)?;
        }

        Ok(())
    }

    fn write_to_file(&self, entry: &LogEntry, log_file: &PathBuf) -> Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_file)?;

        let log_line = format!(
            "[{}] {} ({}) - {} - {} - {}\n",
            entry.timestamp.format("%Y-%m-%d %H:%M:%S"),
            entry.agent_name,
            entry.agent_id,
            entry.tool_name,
            entry.action,
            serde_json::to_string(&entry.parameters)?
        );

        file.write_all(log_line.as_bytes())?;
        Ok(())
    }

    /// Check rate limit for an agent
    pub fn check_rate_limit(&self, agent_id: Uuid) -> Result<()> {
        let now = Utc::now();
        
        let mut should_allow = false;
        
        self.rate_limits
            .entry(agent_id)
            .and_modify(|limit| {
                // Check if we're in a new window
                let window_duration = chrono::Duration::minutes(1);
                if now - limit.window_start > window_duration {
                    // Reset the window
                    limit.count = 1;
                    limit.window_start = now;
                    should_allow = true;
                } else if limit.count < self.max_requests_per_minute {
                    // Increment count
                    limit.count += 1;
                    should_allow = true;
                } else {
                    should_allow = false;
                }
            })
            .or_insert_with(|| {
                should_allow = true;
                RateLimit {
                    count: 1,
                    window_start: now,
                }
            });

        if !should_allow {
            anyhow::bail!(
                "Rate limit exceeded for agent {}. Max {} requests per minute.",
                agent_id,
                self.max_requests_per_minute
            );
        }

        Ok(())
    }

    /// Get logs for a specific agent
    pub fn get_agent_logs(&self, agent_id: Uuid) -> Vec<LogEntry> {
        self.logs
            .get(&agent_id)
            .map(|logs| logs.clone())
            .unwrap_or_default()
    }

    /// Get all logs
    pub fn get_all_logs(&self) -> Vec<LogEntry> {
        let mut all_logs = Vec::new();
        for entry in self.logs.iter() {
            all_logs.extend(entry.value().clone());
        }
        all_logs.sort_by(|a, b| a.timestamp.cmp(&b.timestamp));
        all_logs
    }

    /// Get recent logs (last N entries)
    pub fn get_recent_logs(&self, limit: usize) -> Vec<LogEntry> {
        let mut all_logs = self.get_all_logs();
        all_logs.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
        all_logs.into_iter().take(limit).collect()
    }

    /// Clear logs for an agent
    pub fn clear_agent_logs(&self, agent_id: Uuid) {
        self.logs.remove(&agent_id);
    }

    /// Get rate limit status for an agent
    pub fn get_rate_limit_status(&self, agent_id: Uuid) -> Option<(u32, u32)> {
        self.rate_limits.get(&agent_id).map(|limit| {
            (limit.count, self.max_requests_per_minute)
        })
    }
}

impl Default for AuditLogger {
    fn default() -> Self {
        Self::new(100, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_logging() {
        let logger = AuditLogger::new(10, None);
        let agent_id = Uuid::new_v4();

        logger
            .log_action(
                agent_id,
                "TestAgent".to_string(),
                "lock_file".to_string(),
                "Lock file for editing".to_string(),
                serde_json::json!({"file": "test.rs"}),
                Some("Success".to_string()),
                Some(42),
            )
            .unwrap();

        let logs = logger.get_agent_logs(agent_id);
        assert_eq!(logs.len(), 1);
        assert_eq!(logs[0].tool_name, "lock_file");
    }

    #[test]
    fn test_rate_limiting() {
        let logger = AuditLogger::new(2, None);
        let agent_id = Uuid::new_v4();

        // First two requests should succeed
        assert!(logger.check_rate_limit(agent_id).is_ok());
        assert!(logger.check_rate_limit(agent_id).is_ok());

        // Third request should fail
        assert!(logger.check_rate_limit(agent_id).is_err());
    }
}

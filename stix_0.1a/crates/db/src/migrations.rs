//! Database migrations

/// Migration version
pub const CURRENT_VERSION: i32 = 1;

/// SQL migrations
pub const MIGRATIONS: &[&str] = &[
    // Migration 1: Initial schema
    r#"
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        command TEXT NOT NULL,
        args TEXT NOT NULL,
        status TEXT NOT NULL,
        priority INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        error TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
    
    CREATE TABLE IF NOT EXISTS instances (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        provider TEXT NOT NULL,
        instance_type TEXT NOT NULL,
        state TEXT NOT NULL,
        region TEXT NOT NULL,
        public_ip TEXT,
        private_ip TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_instances_state ON instances(state);
    CREATE INDEX IF NOT EXISTS idx_instances_provider ON instances(provider);
    
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    INSERT OR IGNORE INTO schema_version (version, applied_at) 
    VALUES (1, datetime('now'));
    "#,
];

/// Run all pending migrations
pub async fn run_migrations(pool: &sqlx::Pool<sqlx::Sqlite>) -> Result<(), sqlx::Error> {
    for (i, migration) in MIGRATIONS.iter().enumerate() {
        tracing::info!("Running migration {}", i + 1);
        sqlx::query(migration).execute(pool).await?;
    }
    Ok(())
}

-- Key-value store for global state
CREATE TABLE kv (
  k TEXT PRIMARY KEY,
  v BLOB NOT NULL,
  updated_at TEXT NOT NULL
);

-- Index for timestamp queries
CREATE INDEX idx_kv_updated_at ON kv(updated_at);

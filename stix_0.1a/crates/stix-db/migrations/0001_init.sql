-- Initial schema for STIX database
-- Tasks table stores task metadata and status
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  retries INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 0,
  payload BLOB,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Edges table stores task dependencies (DAG edges)
CREATE TABLE edges (
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  PRIMARY KEY (src, dst),
  FOREIGN KEY (src) REFERENCES tasks(id) ON DELETE CASCADE,
  FOREIGN KEY (dst) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Index for fast status queries
CREATE INDEX idx_tasks_status ON tasks(status);

-- Index for efficient dependency lookups
CREATE INDEX idx_edges_dst ON edges(dst);
CREATE INDEX idx_edges_src ON edges(src);

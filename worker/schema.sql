-- Spend counters for the chat proxy. One row per window, swept on expiry.
CREATE TABLE IF NOT EXISTS counters (
  key        TEXT PRIMARY KEY,
  n          INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS counters_expiry ON counters (expires_at);

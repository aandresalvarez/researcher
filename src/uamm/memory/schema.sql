-- SQLite schema for memory and steps (PRD §8.1). Columns storing text must be redacted.

CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY,
  ts REAL,
  key TEXT,         -- "fact:"|"trace:"|"summary:"|"tool:"
  text TEXT,
  embedding BLOB,   -- for sqlite-vec (optional; FAISS externalizes vectors)
  domain TEXT,      -- "fact"|"trace"|"summary"|"tool"
  recency REAL,
  tokens INT,
  embedding_model TEXT,
  workspace TEXT,   -- workspace slug
  created_by TEXT   -- user or api key label
);
CREATE INDEX IF NOT EXISTS idx_mem_key_ts ON memory(key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mem_domain ON memory(domain);

-- Optional FTS5 accelerator for memory search (best-effort)
-- If FTS5 is unavailable in the SQLite build, these will be ignored at runtime.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id, text);

CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
  INSERT INTO memory_fts (id, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
  UPDATE memory_fts SET text = new.text WHERE id = old.id;
END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
  DELETE FROM memory_fts WHERE id = old.id;
END;

-- RAG corpus tables
CREATE TABLE IF NOT EXISTS corpus (
  id TEXT PRIMARY KEY,
  ts REAL,
  title TEXT,
  url TEXT,
  text TEXT,
  meta TEXT,
  workspace TEXT,   -- workspace slug
  created_by TEXT   -- user or api key label
);

CREATE VIRTUAL TABLE IF NOT EXISTS corpus_fts USING fts5(id, title, text);

CREATE TRIGGER IF NOT EXISTS corpus_ai AFTER INSERT ON corpus BEGIN
  INSERT INTO corpus_fts (id, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS corpus_au AFTER UPDATE ON corpus BEGIN
  UPDATE corpus_fts SET title = new.title, text = new.text WHERE id = old.id;
END;
CREATE TRIGGER IF NOT EXISTS corpus_ad AFTER DELETE ON corpus BEGIN
  DELETE FROM corpus_fts WHERE id = old.id;
END;

-- Track local files ingested into the corpus to avoid reprocessing
CREATE TABLE IF NOT EXISTS corpus_files (
  path TEXT PRIMARY KEY,
  mtime REAL,
  doc_id TEXT,
  meta TEXT,
  workspace TEXT
);

-- CP calibration artifacts (bootstrap)
CREATE TABLE IF NOT EXISTS cp_artifacts (
  id TEXT PRIMARY KEY,
  ts REAL,
  run_id TEXT,
  domain TEXT,
  S REAL,
  accepted INTEGER,
  correct INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cp_run ON cp_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_cp_domain ON cp_artifacts(domain);

CREATE TABLE IF NOT EXISTS cp_reference (
  domain TEXT PRIMARY KEY,
  run_id TEXT,
  target_mis REAL,
  tau REAL,
  stats_json TEXT,
  snne_quantiles TEXT,
  updated REAL
);

CREATE TABLE IF NOT EXISTS eval_runs (
  run_id TEXT,
  suite_id TEXT,
  ts REAL,
  metrics_json TEXT,
  by_domain_json TEXT,
  records_json TEXT,
  notes TEXT,
  PRIMARY KEY (run_id, suite_id)
);

CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY,
  ts REAL,
  step INTEGER,
  question TEXT,        -- REDACTED text only
  answer TEXT,          -- REDACTED text only
  domain TEXT,          -- request domain (e.g., biomed|analytics|code)
  workspace TEXT,       -- workspace slug
  s1 REAL,               -- SNNE_norm or SE_norm
  s2 REAL,
  final_score REAL,
  cp_accept INTEGER,     -- 0/1
  action TEXT,           -- "accept"|"iterate"|"abstain"
  reason TEXT,
  is_refinement INTEGER, -- 0/1
  status TEXT,           -- "ok"|"incomplete"|"error"
  latency_ms INTEGER,
  usage TEXT,            -- JSON: tokens, duration
  pack_ids TEXT,         -- JSON list
  issues TEXT,           -- JSON from verifier
  tools_used TEXT,       -- JSON list
  change_summary TEXT,   -- compact summary of changes/evidence
  trace_json TEXT,       -- JSON of last step trace (structured)
  eval_id TEXT,
  dataset_case_id TEXT,
  is_gold INTEGER,
  gold_correct INTEGER
);

-- Workspaces & access control (simple)
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  slug TEXT UNIQUE,
  name TEXT,
  created REAL
);

CREATE TABLE IF NOT EXISTS workspace_keys (
  id TEXT PRIMARY KEY,
  workspace TEXT,
  key_hash TEXT,
  role TEXT,       -- admin|editor|viewer
  label TEXT,
  active INTEGER,
  created REAL
);
CREATE INDEX IF NOT EXISTS idx_ws_keys_ws ON workspace_keys(workspace);
CREATE INDEX IF NOT EXISTS idx_ws_keys_hash ON workspace_keys(key_hash);

CREATE TABLE IF NOT EXISTS workspace_members (
  workspace TEXT,
  user_id TEXT,
  role TEXT,
  added REAL,
  PRIMARY KEY (workspace, user_id)
);

-- Workspace policy packs (applied configuration overlays)
CREATE TABLE IF NOT EXISTS workspace_policies (
  workspace TEXT PRIMARY KEY,
  policy_name TEXT,
  json TEXT,
  updated REAL
);

-- Unified prompts database schema
-- Required user-facing fields: title, body

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS prompts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,

  -- provenance / traceability
  source        TEXT NOT NULL,          -- e.g. 'fabric_patterns', 'repo_csv', 'repo_file'
  source_repo   TEXT,                   -- e.g. 'awesome-chatgpt-prompts-main'
  source_path   TEXT,                   -- relative file path or CSV row key

  body_sha256   TEXT NOT NULL,
  imported_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_prompts_source
  ON prompts(source, COALESCE(source_repo, ''), COALESCE(source_path, ''));

CREATE INDEX IF NOT EXISTS ix_prompts_title
  ON prompts(title);

CREATE INDEX IF NOT EXISTS ix_prompts_sha
  ON prompts(body_sha256);

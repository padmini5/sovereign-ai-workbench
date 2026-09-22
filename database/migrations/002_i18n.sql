-- 002_i18n.sql — Step 19 multilingual schema (PostgreSQL).
-- Runtime (SQLite) applies the equivalent DDL automatically; this file is
-- the authoritative migration for Postgres deployments.
-- Apply:  psql $DATABASE_URL -f database/migrations/002_i18n.sql

-- Answer language per stored message:
ALTER TABLE messages ADD COLUMN IF NOT EXISTS lang TEXT NOT NULL DEFAULT 'en';

-- User language preference (for deployments where auth lives in Postgres;
-- the default SQLite auth.db gains a users.lang column automatically):
CREATE TABLE IF NOT EXISTS user_prefs (
  user_id TEXT PRIMARY KEY,
  lang    TEXT NOT NULL DEFAULT 'en'
);

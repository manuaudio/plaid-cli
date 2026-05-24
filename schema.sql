-- plaid-cli schema
-- Run once to initialize your local database.
--
-- SQLite (default):
--   sqlite3 ~/.plaid-cli/plaid.db < schema.sql
--
-- PostgreSQL (if PLAID_DB_URL is set):
--   psql "$PLAID_DB_URL" < schema.sql
--
-- NOTE: This file uses SQLite syntax. Postgres differences are noted inline.
-- For Postgres: replace INTEGER PRIMARY KEY AUTOINCREMENT with BIGSERIAL PRIMARY KEY,
--               replace TEXT with VARCHAR/TEXT as preferred,
--               replace REAL with NUMERIC, and enable uuid-ossp for UUID generation.

-- ── plaid_items ──────────────────────────────────────────────────────────────
-- One row per linked institution (bank, brokerage, etc.).
-- item_id is the Plaid-assigned identifier.
-- accounts: JSON array from Plaid metadata (type, subtype, mask, name, account_id).
-- cursor: Plaid transactions sync cursor (NULL until first sync).

CREATE TABLE IF NOT EXISTS plaid_items (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    -- Postgres: id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id         TEXT NOT NULL UNIQUE,
    institution_name TEXT NOT NULL,
    institution_id  TEXT,          -- Plaid institution_id (e.g. ins_128026 for Capital One)
    accounts        TEXT NOT NULL DEFAULT '[]',  -- JSON array; Postgres: JSONB
    cursor          TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    -- Postgres: created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── bank_transactions ─────────────────────────────────────────────────────────
-- Transactions synced via Plaid /transactions/sync.
-- amount_cents: sign convention — positive = money IN (credit/deposit), negative = money OUT (debit/purchase).
--   Plaid's native sign is inverted (positive = debit); we flip it on ingest.
-- raw: full Plaid transaction object for any fields not promoted to columns.

CREATE TABLE IF NOT EXISTS bank_transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Postgres: id BIGSERIAL PRIMARY KEY,
    plaid_item_id   TEXT NOT NULL REFERENCES plaid_items(id),
    -- Postgres: plaid_item_id UUID NOT NULL REFERENCES plaid_items(id),
    plaid_txn_id    TEXT NOT NULL UNIQUE,
    account_id      TEXT NOT NULL,
    posted_on       TEXT,          -- Postgres: DATE
    amount_cents    INTEGER NOT NULL,
    name            TEXT,
    merchant_name   TEXT,
    pending         INTEGER NOT NULL DEFAULT 0,  -- Postgres: BOOLEAN NOT NULL DEFAULT FALSE
    raw             TEXT,          -- Postgres: JSONB
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    -- Postgres: created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bt_item ON bank_transactions(plaid_item_id);
CREATE INDEX IF NOT EXISTS idx_bt_posted ON bank_transactions(posted_on);
CREATE INDEX IF NOT EXISTS idx_bt_name ON bank_transactions(name);

"""Shared fixtures for plaid-cli tests."""
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """
    Spin up a temp SQLite DB initialized with schema.sql, then patch the module's
    DB_URL and SQLITE_PATH so all _db_connect() calls hit this DB.
    """
    import plaid_cli.__main__ as m

    db_path = tmp_path / "test.db"
    schema = (Path(__file__).parent.parent / "schema.sql").read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()

    monkeypatch.setattr(m, "DB_URL", "")
    monkeypatch.setattr(m, "SQLITE_PATH", db_path)
    return db_path


# ── helpers used across multiple test modules ─────────────────────────────────

def insert_item(db_path, item_id, institution_name,
                accounts_json="[]", institution_id=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO plaid_items (id, item_id, institution_name, institution_id, accounts)"
        " VALUES (?, ?, ?, ?, ?)",
        (item_id, item_id, institution_name, institution_id, accounts_json),
    )
    conn.commit()
    conn.close()


def insert_txn(db_path, item_id, txn_id, posted_on, amount_cents, name,
               pending=0, created_at=None):
    conn = sqlite3.connect(str(db_path))
    if created_at:
        conn.execute(
            "INSERT INTO bank_transactions"
            " (plaid_item_id, plaid_txn_id, account_id, posted_on, amount_cents, name, pending, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, txn_id, "acct-1", posted_on, amount_cents, name, pending, created_at),
        )
    else:
        conn.execute(
            "INSERT INTO bank_transactions"
            " (plaid_item_id, plaid_txn_id, account_id, posted_on, amount_cents, name, pending)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, txn_id, "acct-1", posted_on, amount_cents, name, pending),
        )
    conn.commit()
    conn.close()

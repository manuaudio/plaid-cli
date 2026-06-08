"""Integration tests for DB-backed subcommands using a temp SQLite fixture.

All tests use the `db` fixture from conftest.py which patches SQLITE_PATH
to a fresh in-memory-like temp DB, so no ~/.plaid-cli state is involved.
"""
import argparse
import json
import sqlite3
from io import StringIO
from unittest.mock import patch

import pytest

import plaid_cli.__main__ as m
from conftest import insert_item, insert_txn


class TestCmdItems:
    def test_empty_db_returns_empty_list(self, db):
        args = argparse.Namespace(json=True)
        out = StringIO()
        with patch("sys.stdout", out):
            rc = m.cmd_items(args)
        assert rc == 0
        assert json.loads(out.getvalue()) == []

    def test_single_item_json(self, db):
        insert_item(db, "item-001", "Chase")
        args = argparse.Namespace(json=True)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_items(args)
        result = json.loads(out.getvalue())
        assert len(result) == 1
        assert result[0]["institution"] == "Chase"
        assert result[0]["uuid"] == "item-001"
        assert result[0]["has_cursor"] is False

    def test_multiple_items_json(self, db):
        insert_item(db, "item-001", "Chase")
        insert_item(db, "item-002", "Bank of America")
        args = argparse.Namespace(json=True)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_items(args)
        result = json.loads(out.getvalue())
        assert len(result) == 2
        names = {r["institution"] for r in result}
        assert names == {"Chase", "Bank of America"}

    def test_human_output_contains_institution_name(self, db):
        insert_item(db, "item-001", "Chase")
        args = argparse.Namespace(json=False)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_items(args)
        output = out.getvalue()
        assert "Chase" in output
        assert "1 Plaid items" in output


class TestCmdTxns:
    def test_no_transactions_returns_empty_list(self, db):
        insert_item(db, "item-001", "Chase")
        args = argparse.Namespace(json=True, days=30, item=None, search=None, limit=50)
        out = StringIO()
        with patch("sys.stdout", out):
            rc = m.cmd_txns(args)
        assert rc == 0
        assert json.loads(out.getvalue()) == []

    def test_single_transaction_in_json(self, db):
        insert_item(db, "item-001", "Chase")
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -1234, "Coffee Shop")
        args = argparse.Namespace(json=True, days=30, item=None, search=None, limit=50)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_txns(args)
        result = json.loads(out.getvalue())
        assert len(result) == 1
        assert result[0]["name"] == "Coffee Shop"
        assert result[0]["amount_cents"] == -1234
        assert result[0]["institution"] == "Chase"

    def test_limit_is_respected(self, db):
        insert_item(db, "item-001", "Chase")
        for i in range(10):
            insert_txn(db, "item-001", f"txn-{i}", "2026-06-01", -100, f"Shop {i}")
        args = argparse.Namespace(json=True, days=30, item=None, search=None, limit=3)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_txns(args)
        result = json.loads(out.getvalue())
        assert len(result) == 3

    def test_search_filter_case_insensitive(self, db):
        insert_item(db, "item-001", "Chase")
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -100, "Starbucks")
        insert_txn(db, "item-001", "txn-2", "2026-06-01", -200, "McDonald's")
        args = argparse.Namespace(json=True, days=30, item=None, search="STARBUCKS", limit=50)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_txns(args)
        result = json.loads(out.getvalue())
        assert len(result) == 1
        assert result[0]["name"] == "Starbucks"

    def test_pending_flag_preserved(self, db):
        insert_item(db, "item-001", "Chase")
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -500, "Amazon", pending=1)
        args = argparse.Namespace(json=True, days=30, item=None, search=None, limit=50)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_txns(args)
        result = json.loads(out.getvalue())
        assert result[0]["pending"] is True

    def test_human_output_formats_money(self, db):
        insert_item(db, "item-001", "Chase")
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -1234, "Coffee Shop")
        args = argparse.Namespace(json=False, days=30, item=None, search=None, limit=50)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_txns(args)
        output = out.getvalue()
        assert "Coffee Shop" in output
        assert "$12.34" in output

    def test_days_lookback_filters_old_transactions(self, db):
        insert_item(db, "item-001", "Chase")
        insert_txn(db, "item-001", "txn-recent", "2026-06-01", -100, "Recent")
        insert_txn(db, "item-001", "txn-old", "2020-01-01", -200, "Old")
        args = argparse.Namespace(json=True, days=30, item=None, search=None, limit=50)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_txns(args)
        result = json.loads(out.getvalue())
        names = {r["name"] for r in result}
        assert "Recent" in names
        assert "Old" not in names


class TestCmdHealth:
    def test_empty_db_returns_no_stale(self, db):
        args = argparse.Namespace(json=True, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            rc = m.cmd_health(args)
        assert rc == 0
        result = json.loads(out.getvalue())
        assert result["items"] == []
        assert result["real_stale_count"] == 0

    def test_depository_with_no_sync_is_stale(self, db):
        insert_item(db, "item-001", "Chase",
                    accounts_json='[{"type": "depository"}]')
        args = argparse.Namespace(json=True, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            rc = m.cmd_health(args)
        assert rc == 1  # stale exits 1
        result = json.loads(out.getvalue())
        item = result["items"][0]
        assert item["stale"] is True
        assert item["status"] == "STALE"
        assert result["real_stale_count"] == 1

    def test_depository_recently_synced_is_fresh(self, db):
        insert_item(db, "item-001", "Chase",
                    accounts_json='[{"type": "depository"}]')
        # Default created_at = now → 0 h ago → fresh
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -100, "Shop")
        args = argparse.Namespace(json=True, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            rc = m.cmd_health(args)
        assert rc == 0
        result = json.loads(out.getvalue())
        item = result["items"][0]
        assert item["status"] == "fresh"
        assert item["stale"] is False

    def test_investment_item_never_counts_as_stale(self, db):
        insert_item(db, "item-001", "Fidelity",
                    accounts_json='[{"type": "investment"}]')
        args = argparse.Namespace(json=True, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            rc = m.cmd_health(args)
        assert rc == 0
        result = json.loads(out.getvalue())
        assert result["real_stale_count"] == 0
        item = result["items"][0]
        assert item["stale"] is False

    def test_primary_endpoint_routing(self, db):
        insert_item(db, "item-001", "Fidelity",
                    accounts_json='[{"type": "investment"}]')
        insert_item(db, "item-002", "Chase",
                    accounts_json='[{"type": "depository"}]')
        insert_item(db, "item-003", "SoFi",
                    accounts_json='[{"type": "loan"}]')
        insert_item(db, "item-004", "Visa",
                    accounts_json='[{"type": "credit"}]')
        args = argparse.Namespace(json=True, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_health(args)
        by_inst = {r["institution"]: r for r in json.loads(out.getvalue())["items"]}
        assert by_inst["Fidelity"]["primary_endpoint"] == "holdings"
        assert by_inst["Chase"]["primary_endpoint"] == "transactions"
        assert by_inst["SoFi"]["primary_endpoint"] == "liabilities"
        assert by_inst["Visa"]["primary_endpoint"] == "transactions"  # credit → transactions

    def test_stale_threshold_respected(self, db):
        insert_item(db, "item-001", "Chase",
                    accounts_json='[{"type": "depository"}]')
        # Insert a txn with created_at ~12 h ago
        from datetime import datetime, timedelta, timezone
        twelve_h_ago = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -100, "Shop",
                   created_at=twelve_h_ago)

        # With 24 h threshold → not stale (12 h < 24 h)
        args = argparse.Namespace(json=True, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            rc_24 = m.cmd_health(args)

        # With 6 h threshold → stale (12 h > 6 h)
        args6 = argparse.Namespace(json=True, stale_after_hours=6)
        out6 = StringIO()
        with patch("sys.stdout", out6):
            rc_6 = m.cmd_health(args6)

        assert rc_24 == 0
        assert rc_6 == 1

    def test_human_output_shows_institution(self, db):
        insert_item(db, "item-001", "Wells Fargo",
                    accounts_json='[{"type": "depository"}]')
        insert_txn(db, "item-001", "txn-1", "2026-06-01", -100, "Shop")
        args = argparse.Namespace(json=False, stale_after_hours=24)
        out = StringIO()
        with patch("sys.stdout", out):
            m.cmd_health(args)
        assert "Wells Fargo" in out.getvalue()


class TestResolveItem:
    def test_resolves_by_institution_name_prefix(self, db):
        insert_item(db, "item-001", "Chase Bank")
        uuid, item_id, _ = m._resolve_item("chase")
        assert item_id == "item-001"

    def test_resolves_by_exact_item_id(self, db):
        insert_item(db, "item-001", "Chase")
        uuid, item_id, _ = m._resolve_item("item-001")
        assert item_id == "item-001"

    def test_ambiguous_match_raises_system_exit(self, db):
        insert_item(db, "item-001", "Chase Checking")
        insert_item(db, "item-002", "Chase Savings")
        with pytest.raises(SystemExit):
            m._resolve_item("chase")

    def test_not_found_raises_system_exit(self, db):
        with pytest.raises(SystemExit):
            m._resolve_item("no-such-bank-xyz")

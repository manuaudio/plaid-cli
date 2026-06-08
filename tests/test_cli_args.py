"""Tests for CLI argument parsing via main() with mocked subcommand functions.

Because the parser is built inside main(), we patch the module-level cmd_*
names BEFORE calling main(). Python resolves those names at parse time, so
the patched function ends up in args.func.
"""
import sys

import pytest
import plaid_cli.__main__ as m


def _run(argv, cmd_name, monkeypatch):
    """Call main() with given argv, capture the Namespace passed to cmd_name."""
    captured = {}

    def fake_cmd(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(m, cmd_name, fake_cmd)
    monkeypatch.setattr(sys, "argv", ["plaid"] + argv)
    rc = m.main()
    assert rc == 0
    return captured["args"]


class TestTxnsArgs:
    def test_defaults(self, monkeypatch):
        args = _run(["txns"], "cmd_txns", monkeypatch)
        assert args.days == 30
        assert args.limit == 50
        assert args.json is False
        assert args.item is None
        assert args.search is None

    def test_custom_days(self, monkeypatch):
        args = _run(["txns", "--days", "7"], "cmd_txns", monkeypatch)
        assert args.days == 7

    def test_custom_limit(self, monkeypatch):
        args = _run(["txns", "--limit", "100"], "cmd_txns", monkeypatch)
        assert args.limit == 100

    def test_json_flag(self, monkeypatch):
        args = _run(["txns", "--json"], "cmd_txns", monkeypatch)
        assert args.json is True

    def test_search_option(self, monkeypatch):
        args = _run(["txns", "--search", "starbucks"], "cmd_txns", monkeypatch)
        assert args.search == "starbucks"

    def test_item_option(self, monkeypatch):
        args = _run(["txns", "--item", "Chase"], "cmd_txns", monkeypatch)
        assert args.item == "Chase"


class TestHealthArgs:
    def test_default_stale_hours(self, monkeypatch):
        args = _run(["health"], "cmd_health", monkeypatch)
        assert args.stale_after_hours == 24

    def test_custom_stale_hours(self, monkeypatch):
        args = _run(["health", "--stale-after-hours", "48"], "cmd_health", monkeypatch)
        assert args.stale_after_hours == 48

    def test_json_flag(self, monkeypatch):
        args = _run(["health", "--json"], "cmd_health", monkeypatch)
        assert args.json is True


class TestBalanceArgs:
    def test_type_filter_depository(self, monkeypatch):
        args = _run(["balance", "--type", "depository"], "cmd_balance", monkeypatch)
        assert args.type == "depository"

    def test_type_filter_credit(self, monkeypatch):
        args = _run(["balance", "--type", "credit"], "cmd_balance", monkeypatch)
        assert args.type == "credit"

    def test_item_filter(self, monkeypatch):
        args = _run(["balance", "--item", "Chase"], "cmd_balance", monkeypatch)
        assert args.item == "Chase"

    def test_no_filters_default(self, monkeypatch):
        args = _run(["balance"], "cmd_balance", monkeypatch)
        assert args.type is None
        assert args.item is None


class TestSyncArgs:
    def test_item_filter(self, monkeypatch):
        args = _run(["sync", "--item", "Chase"], "cmd_sync", monkeypatch)
        assert args.item == "Chase"

    def test_no_item_default(self, monkeypatch):
        args = _run(["sync"], "cmd_sync", monkeypatch)
        assert args.item is None


class TestRelinkArgs:
    def test_positional_item(self, monkeypatch):
        args = _run(["relink", "Chase"], "cmd_relink", monkeypatch)
        assert args.item == "Chase"

    def test_products_option(self, monkeypatch):
        args = _run(["relink", "Chase", "--products", "investments,liabilities"],
                    "cmd_relink", monkeypatch)
        assert args.products == "investments,liabilities"

    def test_missing_item_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["plaid", "relink"])
        with pytest.raises(SystemExit) as exc_info:
            m.main()
        assert exc_info.value.code != 0


class TestMissingSubcommand:
    def test_no_subcommand_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["plaid"])
        with pytest.raises(SystemExit) as exc_info:
            m.main()
        assert exc_info.value.code != 0

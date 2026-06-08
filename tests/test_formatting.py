"""Tests for pure formatting helpers: _money, _ago, _item_account_types."""
from datetime import datetime, timedelta, timezone

import pytest
import plaid_cli.__main__ as m


class TestMoney:
    def test_zero(self):
        assert m._money(0) == " $0.00"

    def test_positive_dollar(self):
        assert m._money(100) == " $1.00"

    def test_negative_dollar(self):
        assert m._money(-100) == "-$1.00"

    def test_large_positive_with_comma(self):
        assert m._money(1_000_000) == " $10,000.00"

    def test_large_negative_with_comma(self):
        assert m._money(-999_999) == "-$9,999.99"

    def test_partial_cents(self):
        assert m._money(150) == " $1.50"

    def test_single_cent(self):
        assert m._money(1) == " $0.01"

    def test_sign_space_for_positive(self):
        # positive values lead with a space so columns align with negative
        val = m._money(500)
        assert val.startswith(" ")

    def test_sign_dash_for_negative(self):
        val = m._money(-500)
        assert val.startswith("-")


class TestAgo:
    def test_none_returns_never(self):
        assert m._ago(None) == "never"

    def test_recent_seconds(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert m._ago(ts) == "30s"

    def test_minutes(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=5, seconds=30)
        assert m._ago(ts) == "5m"

    def test_hours(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        assert m._ago(ts) == "3h"

    def test_days(self):
        ts = datetime.now(timezone.utc) - timedelta(days=2, hours=3)
        assert m._ago(ts) == "2d"

    def test_iso_string_input(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert m._ago(ts) == "1h"

    def test_naive_datetime_treated_as_utc(self):
        # naive datetimes are assumed UTC; should still produce a relative string
        ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=45)
        result = m._ago(ts)
        assert result.endswith("m")

    def test_invalid_string_passthrough(self):
        # non-ISO strings that can't be parsed are returned as-is
        assert m._ago("not-a-date") == "not-a-date"

    def test_boundary_under_60s(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=59)
        assert m._ago(ts).endswith("s")

    def test_boundary_under_3600s(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=3599)
        assert m._ago(ts).endswith("m")

    def test_boundary_under_86400s(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=86399)
        assert m._ago(ts).endswith("h")

    def test_boundary_86400s_plus(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=86401)
        assert m._ago(ts).endswith("d")


class TestItemAccountTypes:
    def test_empty_json_array(self):
        assert m._item_account_types("[]") == set()

    def test_single_type_string(self):
        assert m._item_account_types('[{"type": "depository"}]') == {"depository"}

    def test_multiple_types(self):
        result = m._item_account_types('[{"type": "credit"}, {"type": "depository"}]')
        assert result == {"credit", "depository"}

    def test_deduplicates(self):
        result = m._item_account_types('[{"type": "depository"}, {"type": "depository"}]')
        assert result == {"depository"}

    def test_list_input(self):
        result = m._item_account_types([{"type": "investment"}, {"type": "loan"}])
        assert result == {"investment", "loan"}

    def test_invalid_json_returns_empty(self):
        assert m._item_account_types("not json") == set()

    def test_none_input_returns_empty(self):
        assert m._item_account_types(None) == set()

    def test_all_standard_types(self):
        data = [
            {"type": "depository"},
            {"type": "credit"},
            {"type": "investment"},
            {"type": "loan"},
        ]
        assert m._item_account_types(data) == {"depository", "credit", "investment", "loan"}

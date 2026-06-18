"""Tests for _item_account_types(accounts_json) — account-type extraction."""
import json
import pytest
from plaid_cli import __main__ as cli


def test_json_string_input():
    data = json.dumps([{"type": "depository"}, {"type": "credit"}])
    result = cli._item_account_types(data)
    assert result == {"depository", "credit"}


def test_already_parsed_list():
    data = [{"type": "investment"}, {"type": "loan"}]
    result = cli._item_account_types(data)
    assert result == {"investment", "loan"}


def test_none_returns_empty_set():
    assert cli._item_account_types(None) == set()


def test_malformed_json_returns_empty_set():
    assert cli._item_account_types("{not valid json}") == set()


def test_empty_list_returns_empty_set():
    assert cli._item_account_types([]) == set()


def test_element_missing_type_key():
    # a.get("type", "") returns "" when "type" is absent → {""}
    result = cli._item_account_types([{"subtype": "checking"}])
    assert result == {""}


def test_mixed_with_and_without_type():
    data = [{"type": "depository"}, {"subtype": "savings"}]
    result = cli._item_account_types(data)
    assert "depository" in result
    assert "" in result


def test_single_type():
    result = cli._item_account_types([{"type": "depository"}])
    assert result == {"depository"}


def test_deduplication():
    data = [{"type": "depository"}, {"type": "depository"}]
    result = cli._item_account_types(data)
    assert result == {"depository"}

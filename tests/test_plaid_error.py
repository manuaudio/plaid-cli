"""Tests for _parse_plaid_error(exc) — Plaid API exception parsing."""
import json
import pytest
from plaid.exceptions import ApiException
from plaid_cli import __main__ as cli


def _make_exc(body=None):
    """Build a minimal ApiException with the given body string."""
    exc = ApiException()
    exc.body = body
    return exc


def test_error_code_and_message():
    body = json.dumps({
        "error_code": "INSTITUTION_DOWN",
        "error_message": "The institution is temporarily unavailable.",
    })
    exc = _make_exc(body)
    result = cli._parse_plaid_error(exc)
    assert result == "[INSTITUTION_DOWN] The institution is temporarily unavailable."


def test_only_error_message():
    body = json.dumps({
        "error_message": "Something went wrong.",
    })
    exc = _make_exc(body)
    result = cli._parse_plaid_error(exc)
    assert result == "Something went wrong."


def test_non_json_body_falls_back_to_str_exc():
    exc = _make_exc(body="this is not json {{")
    result = cli._parse_plaid_error(exc)
    assert isinstance(result, str)
    assert len(result) <= 120


def test_empty_body_falls_back_to_str_exc():
    exc = _make_exc(body=None)
    result = cli._parse_plaid_error(exc)
    assert isinstance(result, str)


def test_empty_error_code_uses_message():
    body = json.dumps({"error_code": "", "error_message": "Just a message."})
    exc = _make_exc(body)
    result = cli._parse_plaid_error(exc)
    assert result == "Just a message."


def test_result_is_always_str():
    exc = _make_exc(body=json.dumps({"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "re-auth needed"}))
    assert isinstance(cli._parse_plaid_error(exc), str)

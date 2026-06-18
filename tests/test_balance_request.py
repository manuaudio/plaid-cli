"""Tests for _balance_request(token, institution_id) — Capital One quirk handling."""
import pytest
from plaid_cli import __main__ as cli

_TOKEN = "access-sandbox-test-token"
_CAPITAL_ONE_ID = "ins_128026"


def test_capital_one_has_options():
    req = cli._balance_request(_TOKEN, _CAPITAL_ONE_ID)
    options = getattr(req, "options", None)
    assert options is not None, "Capital One request must include options"


def test_capital_one_options_has_min_last_updated():
    req = cli._balance_request(_TOKEN, _CAPITAL_ONE_ID)
    options = getattr(req, "options", None)
    assert options is not None
    mlu = getattr(options, "min_last_updated_datetime", None)
    assert mlu is not None, "Capital One options must carry min_last_updated_datetime"


def test_other_institution_no_options():
    req = cli._balance_request(_TOKEN, "ins_999999")
    options = getattr(req, "options", None)
    assert options is None, "Non-Capital-One request must not include options"


def test_none_institution_id_no_options():
    req = cli._balance_request(_TOKEN, None)
    options = getattr(req, "options", None)
    assert options is None, "None institution_id must not include options"


def test_capital_one_access_token_set():
    req = cli._balance_request(_TOKEN, _CAPITAL_ONE_ID)
    assert req.access_token == _TOKEN


def test_other_institution_access_token_set():
    req = cli._balance_request(_TOKEN, "ins_000001")
    assert req.access_token == _TOKEN

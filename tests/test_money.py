"""Tests for _money(cents) — money formatting helper."""
import pytest
from plaid_cli import __main__ as cli


def test_money_negative():
    assert cli._money(-123456) == "-$1,234.56"


def test_money_zero():
    assert cli._money(0) == " $0.00"


def test_money_positive_small():
    assert cli._money(500) == " $5.00"


def test_money_positive_large():
    assert cli._money(1234567) == " $12,345.67"


def test_money_negative_small():
    assert cli._money(-1) == "-$0.01"


def test_money_sign_prefix_negative():
    result = cli._money(-100)
    assert result.startswith("-")


def test_money_sign_prefix_positive():
    result = cli._money(100)
    assert result.startswith(" ")


def test_money_two_decimal_places():
    result = cli._money(100)
    # Should have exactly 2 decimal places
    dollar_part = result.lstrip(" -$").replace(",", "")
    assert "." in dollar_part
    assert len(dollar_part.split(".")[1]) == 2

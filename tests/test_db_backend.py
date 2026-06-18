"""Tests for _db_is_postgres(), _placeholder(), _placeholders() — DB backend helpers."""
import pytest
from plaid_cli import __main__ as cli


def test_postgres_url_full(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "postgresql://user:pass@localhost:5432/mydb")
    assert cli._db_is_postgres() is True


def test_postgres_url_short(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "postgres://user@localhost/mydb")
    assert cli._db_is_postgres() is True


def test_sqlite_empty_url(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "")
    assert cli._db_is_postgres() is False


def test_sqlite_explicit_path(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "/path/to/plaid.db")
    assert cli._db_is_postgres() is False


def test_placeholder_postgres(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "postgresql://localhost/db")
    assert cli._placeholder() == "%s"


def test_placeholder_sqlite(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "")
    assert cli._placeholder() == "?"


def test_placeholders_single_value_sqlite(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "")
    ph_str, vals = cli._placeholders("alpha")
    assert ph_str == "?"
    assert vals == ("alpha",)


def test_placeholders_three_values_sqlite(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "")
    ph_str, vals = cli._placeholders("a", "b", "c")
    assert ph_str == "?, ?, ?"
    assert vals == ("a", "b", "c")


def test_placeholders_single_value_postgres(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "postgresql://localhost/db")
    ph_str, vals = cli._placeholders("x")
    assert ph_str == "%s"
    assert vals == ("x",)


def test_placeholders_three_values_postgres(monkeypatch):
    monkeypatch.setattr(cli, "DB_URL", "postgresql://localhost/db")
    ph_str, vals = cli._placeholders(1, 2, 3)
    assert ph_str == "%s, %s, %s"
    assert vals == (1, 2, 3)

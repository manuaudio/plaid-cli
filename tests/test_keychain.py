"""Tests for _keychain_get(item_id) — env-var token resolution."""
import sys
import pytest
from plaid_cli import __main__ as cli


def test_env_var_resolution(monkeypatch):
    """Env var PLAID_TOKEN_ITEM_ABC is returned for item_id 'item-abc'."""
    monkeypatch.setenv("PLAID_TOKEN_ITEM_ABC", "access-sandbox-abc-123")
    result = cli._keychain_get("item-abc")
    assert result == "access-sandbox-abc-123"


def test_env_key_transform_uppercase(monkeypatch):
    """item_id is upper-cased: 'MyItem' → PLAID_TOKEN_MYITEM."""
    monkeypatch.setenv("PLAID_TOKEN_MYITEM", "token-value")
    result = cli._keychain_get("MyItem")
    assert result == "token-value"


def test_env_key_transform_hyphens_to_underscores(monkeypatch):
    """Hyphens become underscores: 'foo-bar-baz' → PLAID_TOKEN_FOO_BAR_BAZ."""
    monkeypatch.setenv("PLAID_TOKEN_FOO_BAR_BAZ", "token-xyz")
    result = cli._keychain_get("foo-bar-baz")
    assert result == "token-xyz"


def test_missing_token_raises_runtime_error(monkeypatch):
    """When no env var matches and not on macOS (patched to linux), raises RuntimeError."""
    # Ensure no matching env var exists
    monkeypatch.delenv("PLAID_TOKEN_DOES_NOT_EXIST_ZZZ", raising=False)
    # Force the non-macOS path so we skip Keychain and go straight to RuntimeError
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="No access token found"):
        cli._keychain_get("does-not-exist-zzz")


def test_env_var_takes_precedence(monkeypatch):
    """Env var is checked first — macOS Keychain is never reached when env var set."""
    monkeypatch.setenv("PLAID_TOKEN_PRIORITY_ITEM", "env-token")
    # Even on darwin, the env path should short-circuit before any Keychain call
    result = cli._keychain_get("priority-item")
    assert result == "env-token"

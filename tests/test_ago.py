"""Tests for _ago(ts) — relative-time formatting helper."""
from datetime import datetime, timedelta, timezone
import pytest
from plaid_cli import __main__ as cli


def test_none_returns_never():
    assert cli._ago(None) == "never"


def test_non_iso_string_returned_unchanged():
    assert cli._ago("not-a-timestamp") == "not-a-timestamp"


def test_another_invalid_string_returned_unchanged():
    assert cli._ago("yesterday at noon") == "yesterday at noon"


def test_recent_seconds():
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    result = cli._ago(ts)
    assert result.endswith("s"), f"Expected suffix 's', got: {result!r}"


def test_recent_minutes():
    ts = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = cli._ago(ts)
    assert result.endswith("m"), f"Expected suffix 'm', got: {result!r}"


def test_recent_hours():
    ts = datetime.now(timezone.utc) - timedelta(hours=3)
    result = cli._ago(ts)
    assert result.endswith("h"), f"Expected suffix 'h', got: {result!r}"


def test_several_days():
    ts = datetime.now(timezone.utc) - timedelta(days=5)
    result = cli._ago(ts)
    assert result.endswith("d"), f"Expected suffix 'd', got: {result!r}"


def test_iso_string_parsed_correctly():
    # A valid ISO timestamp string about 10 seconds ago
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    result = cli._ago(ts)
    assert result.endswith("s"), f"Expected suffix 's' for recent ISO string, got: {result!r}"


def test_naive_datetime_treated_as_utc():
    # Naive datetimes are coerced to UTC by the implementation
    ts = datetime.utcnow() - timedelta(seconds=45)
    result = cli._ago(ts)
    assert result.endswith("s"), f"Expected 's' suffix for naive datetime, got: {result!r}"

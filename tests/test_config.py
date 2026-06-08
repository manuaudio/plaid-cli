"""Tests for config/env helpers: _db_is_postgres, _placeholder, _placeholders,
_env_required, _keychain_get, _parse_plaid_error."""
import subprocess

import pytest
import plaid_cli.__main__ as m


class TestDbIsPostgres:
    def test_empty_url_is_not_postgres(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "")
        assert not m._db_is_postgres()

    def test_postgresql_scheme(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "postgresql://user@localhost/db")
        assert m._db_is_postgres()

    def test_short_postgres_scheme(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "postgres://user@localhost/db")
        assert m._db_is_postgres()

    def test_sqlite_url_is_not_postgres(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "sqlite:///foo.db")
        assert not m._db_is_postgres()

    def test_partial_match_not_postgres(self, monkeypatch):
        # Must start with the scheme, not just contain it
        monkeypatch.setattr(m, "DB_URL", "mysql://user@localhost/db")
        assert not m._db_is_postgres()


class TestPlaceholder:
    def test_sqlite_placeholder(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "")
        assert m._placeholder() == "?"

    def test_postgres_placeholder(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "postgresql://user@localhost/db")
        assert m._placeholder() == "%s"


class TestPlaceholders:
    def test_single_value_sqlite(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "")
        ph, vals = m._placeholders("x")
        assert ph == "?"
        assert vals == ("x",)

    def test_three_values_sqlite(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "")
        ph, vals = m._placeholders("a", "b", "c")
        assert ph == "?, ?, ?"
        assert vals == ("a", "b", "c")

    def test_two_values_postgres(self, monkeypatch):
        monkeypatch.setattr(m, "DB_URL", "postgresql://user@localhost/db")
        ph, vals = m._placeholders("a", "b")
        assert ph == "%s, %s"
        assert vals == ("a", "b")


class TestEnvRequired:
    def test_reads_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.setenv("PLAID_CLI_TEST_VAR_READ", "hello")
        assert m._env_required("PLAID_CLI_TEST_VAR_READ") == "hello"

    def test_missing_raises_runtime_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.delenv("PLAID_CLI_VAR_ABSENT_XYZ", raising=False)
        with pytest.raises(RuntimeError, match="not set"):
            m._env_required("PLAID_CLI_VAR_ABSENT_XYZ")

    def test_reads_from_dotenv_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.delenv("PLAID_CLI_DOTENV_VAR", raising=False)
        (tmp_path / ".env").write_text("PLAID_CLI_DOTENV_VAR=from-file\n")
        assert m._env_required("PLAID_CLI_DOTENV_VAR") == "from-file"

    def test_dotenv_strips_double_quotes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.delenv("PLAID_CLI_QUOTED_VAR", raising=False)
        (tmp_path / ".env").write_text('PLAID_CLI_QUOTED_VAR="quoted-val"\n')
        assert m._env_required("PLAID_CLI_QUOTED_VAR") == "quoted-val"

    def test_dotenv_strips_single_quotes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.delenv("PLAID_CLI_SINGLE_VAR", raising=False)
        (tmp_path / ".env").write_text("PLAID_CLI_SINGLE_VAR='single-val'\n")
        assert m._env_required("PLAID_CLI_SINGLE_VAR") == "single-val"

    def test_env_takes_precedence_over_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.setenv("PLAID_CLI_BOTH_VAR", "from-env")
        (tmp_path / ".env").write_text("PLAID_CLI_BOTH_VAR=from-file\n")
        assert m._env_required("PLAID_CLI_BOTH_VAR") == "from-env"

    def test_error_message_is_helpful(self, monkeypatch, tmp_path):
        monkeypatch.setattr(m, "DATA_DIR", tmp_path)
        monkeypatch.delenv("PLAID_CLI_NO_SUCH_KEY_42", raising=False)
        with pytest.raises(RuntimeError) as exc_info:
            m._env_required("PLAID_CLI_NO_SUCH_KEY_42")
        msg = str(exc_info.value)
        assert "PLAID_CLI_NO_SUCH_KEY_42" in msg
        assert "export" in msg


class TestKeychainGet:
    def test_reads_from_env_var(self, monkeypatch):
        monkeypatch.setenv("PLAID_TOKEN_ITEM001", "access-sandbox-abc")
        assert m._keychain_get("item001") == "access-sandbox-abc"

    def test_env_key_uppercase_conversion(self, monkeypatch):
        monkeypatch.setenv("PLAID_TOKEN_MY_ITEM_ID", "token-value")
        assert m._keychain_get("my-item-id") == "token-value"

    def test_hyphens_become_underscores(self, monkeypatch):
        monkeypatch.setenv("PLAID_TOKEN_A_B_C", "tok")
        assert m._keychain_get("a-b-c") == "tok"

    def test_missing_raises_runtime_error(self, monkeypatch):
        env_key = "PLAID_TOKEN_NO_SUCH_ITEM_ZZZ_999"
        monkeypatch.delenv(env_key, raising=False)
        # Prevent actual keychain/subprocess call
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: (_ for _ in ()).throw(
                subprocess.CalledProcessError(44, "security")
            ),
        )
        with pytest.raises(RuntimeError, match="No access token found"):
            m._keychain_get("no-such-item-zzz-999")


class TestParsePlaidError:
    def test_error_code_and_message(self):
        class FakeExc:
            body = '{"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "auth expired"}'
        result = m._parse_plaid_error(FakeExc())
        assert "[ITEM_LOGIN_REQUIRED]" in result
        assert "auth expired" in result

    def test_message_only(self):
        class FakeExc:
            body = '{"error_message": "something went wrong"}'
        result = m._parse_plaid_error(FakeExc())
        assert "something went wrong" in result

    def test_empty_body_falls_back_to_str(self):
        class FakeExc:
            body = None
            def __str__(self):
                return "raw connection error"
        result = m._parse_plaid_error(FakeExc())
        assert result  # non-empty

    def test_invalid_json_body(self):
        class FakeExc:
            body = "not json"
            def __str__(self):
                return "raw error"
        result = m._parse_plaid_error(FakeExc())
        assert result

    def test_truncated_to_120_chars(self):
        long_msg = "x" * 200
        class FakeExc:
            body = None
            def __str__(self):
                return long_msg
        result = m._parse_plaid_error(FakeExc())
        assert len(result) <= 120

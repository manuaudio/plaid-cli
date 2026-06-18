"""conftest.py — must run before any test module imports plaid_cli.__main__.

The module reads PLAID_DATA_DIR at import time and calls DATA_DIR.mkdir(…).
Set it to a temp directory here so the suite never touches ~/.plaid-cli.
"""
import os
import tempfile

os.environ.setdefault("PLAID_DATA_DIR", tempfile.mkdtemp(prefix="plaid-cli-tests-"))
# Prevent the module from trying to read real Plaid credentials at import time.
os.environ.setdefault("PLAID_CLIENT_ID", "test-client-id")
os.environ.setdefault("PLAID_SECRET", "test-secret")

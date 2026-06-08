# plaid-cli tests

## Running

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "." pytest
pytest
```

No Plaid credentials or network access required. All tests use either pure
Python logic or a temp SQLite DB created from `schema.sql`.

## Coverage

| File | What it tests |
|------|---------------|
| `test_formatting.py` | `_money`, `_ago`, `_item_account_types` |
| `test_config.py` | `_db_is_postgres`, `_placeholder`, `_placeholders`, `_env_required`, `_keychain_get`, `_parse_plaid_error` |
| `test_cli_args.py` | Argparse: defaults, required args, flags for all subcommands |
| `test_db_commands.py` | `cmd_items`, `cmd_txns`, `cmd_health`, `_resolve_item` via SQLite fixture |

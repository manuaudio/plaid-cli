# plaid-cli

A terminal-first CLI for [Plaid](https://plaid.com) — sync transactions, check live balances, view investment holdings, inspect liabilities, and manage linked institutions. No web dashboard required.

```
$ plaid balance
Live balances (12 accounts)
  institution                         account                                        mask  balance
  ─────────────────────────────────── ────────────────────────────────────────────── ───── ──────────────
  Chase                               Total Checking                                 1234   $4,218.09
  Vanguard                            Brokerage Account                              5678  $42,317.55
  ...
  NET: assets $58,124.40 − liabilities $3,401.22 = $54,723.18
```

## Features

| Subcommand | Description |
|-----------|-------------|
| `sync` | Pull new transactions via `/transactions/sync` |
| `items` | List all linked institutions with last-sync status |
| `balance` | Live balances across all accounts (one Plaid API call per institution) |
| `holdings` | Investment positions: ticker, quantity, price, value |
| `liabilities` | Credit cards (APR, min payment, due date) + loans |
| `txns` | Query the local transaction store with filters |
| `link` | Start an in-browser Plaid Link flow to add a new institution |
| `relink` | Re-auth an existing institution (for `ITEM_LOGIN_REQUIRED` errors) |
| `health` | Sync staleness report per institution; exits 1 if anything is stale |

All subcommands support `--json` for piping output to `jq` or other tools.

## Requirements

- Python 3.10+
- A [Plaid account](https://dashboard.plaid.com/signup) with **production** or **sandbox** credentials
- SQLite (included with Python) or PostgreSQL

## Installation

```bash
# Recommended: install in an isolated environment
pipx install plaid-cli

# Or with pip
pip install plaid-cli

# Or from source
git clone https://github.com/manuaudio/plaid-cli
cd plaid-cli
pip install -e .
```

## Configuration

Set the following environment variables (or put them in `~/.plaid-cli/.env`):

```bash
# Required
export PLAID_CLIENT_ID=your_client_id
export PLAID_SECRET=your_secret

# Optional — defaults shown
export PLAID_ENV=production          # or: sandbox
export PLAID_DB_URL=                 # empty = SQLite at ~/.plaid-cli/plaid.db
                                     # Postgres: postgresql://user@host:5432/dbname
export PLAID_KEYCHAIN_SERVICE=plaid-cli  # macOS Keychain service name
export PLAID_DATA_DIR=~/.plaid-cli   # where the SQLite DB + .env live
```

### Getting Plaid credentials

1. Sign up at [dashboard.plaid.com](https://dashboard.plaid.com)
2. Create an application
3. Copy your **Client ID** and **Secret** (use the Sandbox secret for testing)
4. For real bank connections you need a **Production** account (Plaid approval required)

### Database setup

Run the schema once to create the tables:

```bash
# SQLite (default — no setup needed beyond this)
sqlite3 ~/.plaid-cli/plaid.db < schema.sql

# PostgreSQL
psql "$PLAID_DB_URL" < schema.sql
```

## Usage

### Link your first institution

```bash
# Start the link server
plaid link
# Opens http://127.0.0.1:5174 — click the button and connect your bank
# Requires: pip install flask
```

### Sync transactions

```bash
plaid sync              # sync all institutions
plaid sync --item Chase # sync one institution by name
```

### Check balances

```bash
plaid balance                        # all accounts
plaid balance --type depository      # only checking/savings
plaid balance --item Vanguard        # one institution
plaid balance --json | jq '.[].balance_current'
```

### View investments

```bash
plaid holdings
plaid holdings --item Fidelity --json
```

### View liabilities

```bash
plaid liabilities          # all credit cards + loans
plaid liabilities --json   # machine-readable
```

### Query transactions

```bash
plaid txns                          # last 30 days
plaid txns --days 90                # last 90 days
plaid txns --search "amazon"        # name/merchant filter
plaid txns --item Chase --limit 20
plaid txns --json | jq '.[] | select(.amount_cents < -10000)'
```

### Health check

```bash
plaid health                        # exits 1 if any depository item is stale
plaid health --stale-after-hours 48 # custom staleness threshold
plaid health --json
```

### Re-auth after login expiry

When you see `[ITEM_LOGIN_REQUIRED]`, re-auth:

```bash
plaid relink "Chase"
# Follow the instructions — starts the link server in update mode
```

### Add investment or liability products to an existing item

```bash
plaid relink "Fidelity" --products investments,liabilities
# Then open the link server URL and complete the consent flow
```

## Access token storage

Access tokens for each linked institution are stored securely:

- **macOS**: in the system Keychain under service `plaid-cli` (or your `PLAID_KEYCHAIN_SERVICE`)
- **Any OS**: in environment variable `PLAID_TOKEN_<ITEM_ID>` (uppercased, hyphens → underscores)

To manually inspect or add a token on macOS:

```bash
# Store
security add-generic-password -s plaid-cli -a <item_id> -w <access_token>

# Retrieve
security find-generic-password -s plaid-cli -a <item_id> -w
```

## Known quirks

**Capital One** requires a `min_last_updated_datetime` parameter in `/accounts/balance/get`. plaid-cli handles this automatically.

**Investment / liabilities products** require explicit consent during link. If `plaid holdings` or `plaid liabilities` returns `ADDITIONAL_CONSENT_REQUIRED`, run `plaid relink <name> --products investments,liabilities`.

**`plaid health`** only flags depository and credit accounts as "stale". Investment and loan items don't appear in `/transactions/sync` — use `plaid holdings` / `plaid liabilities` for those.

## Optional dependencies

| Extra | Install | Needed for |
|-------|---------|-----------|
| Flask | `pip install flask` | `plaid link` (in-browser link flow) |
| psycopg | `pip install "plaid-cli[postgres]"` | PostgreSQL backend |
| certifi | included by default | up-to-date CA bundle for HTTPS |

## Contributing

Issues and pull requests welcome at [github.com/manuaudio/plaid-cli](https://github.com/manuaudio/plaid-cli).

The CLI is intentionally simple and dependency-light. New subcommands should follow the existing pattern: a `cmd_<name>` function, registered in `main()`, with `--json` support.

## License

MIT — see [LICENSE](LICENSE).

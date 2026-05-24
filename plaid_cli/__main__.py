#!/usr/bin/env python3
"""plaid-cli — terminal-first Plaid CLI.

Subcommands:
  sync          Pull new transactions from Plaid /transactions/sync for one or all items.
  items         List registered institutions + last sync time + cursor age + account-type breakdown.
  balance       Show current balances per account (live Plaid /accounts/balance/get).
  holdings      Investment holdings (brokerage, IRA, 529 positions).
  liabilities   Loans + credit-card details: APR, minimum payment, due date, balance.
  txns          Query the local bank_transactions table.
  link          Start the Flask link server on http://127.0.0.1:5174 for adding a new institution.
  relink ITEM   Generate a Plaid update-mode link token for re-auth (use when ITEM_LOGIN_REQUIRED).
  health        Per-item health, classified by account type. Only depository staleness is RED.

Configuration:
  PLAID_CLIENT_ID   Plaid client ID (required)
  PLAID_SECRET      Plaid secret key (required)
  PLAID_DB_URL      Database URL (optional; defaults to SQLite at ~/.plaid-cli/plaid.db)
                    Postgres: postgresql://user@host:5432/dbname
                    SQLite:   (default) ~/.plaid-cli/plaid.db
  PLAID_KEYCHAIN_SERVICE  macOS Keychain service name for access tokens (default: plaid-cli)
  PLAID_ENV         Plaid environment: production (default) or sandbox

Conventions:
  - Read-only by default. The only mutating ops are: `sync` (writes to bank_transactions),
    `link`/`relink` (writes new keychain item + plaid_items row).
  - Access tokens stored per-item in macOS Keychain (macOS) or env vars PLAID_TOKEN_<ITEM_ID>.
  - Output format: human-readable tables by default. Use `--json` on any subcommand for piping.
  - Capital One quirk: balance requires `min_last_updated_datetime`; handled automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

if sys.version_info < (3, 10):
    print(
        "ERROR: plaid-cli requires Python 3.10+.\n"
        f"Current interpreter: {sys.executable} ({sys.version})",
        file=sys.stderr,
    )
    sys.exit(2)

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Force certifi for HTTPS (some environments lack an up-to-date CA bundle).
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass  # certifi is optional; system CA bundle will be used

from plaid.api import plaid_api
from plaid.api_client import ApiClient, Configuration
from plaid.exceptions import ApiException
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.accounts_balance_get_request_options import AccountsBalanceGetRequestOptions
from plaid.model.country_code import CountryCode
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

# Institutions that REQUIRE `min_last_updated_datetime` in /accounts/balance/get.
# Plaid documents this as a per-institution quirk; Capital One is the canonical case.
_BALANCE_NEEDS_MIN_LAST_UPDATED: set[str] = {"ins_128026"}  # Capital One

# Account type → which Plaid endpoint actually returns useful data.
_TYPE_TO_ENDPOINT: dict[str, str] = {
    "depository": "transactions",
    "credit": "liabilities",
    "loan": "liabilities",
    "investment": "holdings",
}

# ── config + paths ────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("PLAID_DATA_DIR", Path.home() / ".plaid-cli"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = os.environ.get("PLAID_DB_URL", "")  # empty = use SQLite
SQLITE_PATH = DATA_DIR / "plaid.db"
KEYCHAIN_SERVICE = os.environ.get("PLAID_KEYCHAIN_SERVICE", "plaid-cli")
PLAID_ENV = os.environ.get("PLAID_ENV", "production")


def _db_is_postgres() -> bool:
    return DB_URL.startswith("postgresql://") or DB_URL.startswith("postgres://")


def _db_connect():
    """Return a DB connection — Postgres if PLAID_DB_URL is set, else SQLite."""
    if _db_is_postgres():
        import psycopg  # type: ignore[import]
        return psycopg.connect(DB_URL)
    else:
        import sqlite3
        conn = sqlite3.connect(str(SQLITE_PATH))
        conn.row_factory = sqlite3.Row
        return conn


def _placeholder(n: int = 1) -> str:
    """Return the right placeholder for the current DB backend."""
    if _db_is_postgres():
        return "%s"
    return "?"


def _placeholders(*vals) -> tuple[str, tuple]:
    """Return (placeholder_str, values_tuple) for the current DB."""
    ph = _placeholder()
    phs = ", ".join(ph for _ in vals)
    return phs, tuple(vals)


def _env_required(k: str) -> str:
    """Get a required env var, with helpful error message."""
    v = os.environ.get(k, "").strip()
    if not v:
        # Also check a local .env file in the data dir
        env_file = DATA_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{k}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        raise RuntimeError(
            f"Required config '{k}' not set.\n"
            f"  Set it in your shell: export {k}=...\n"
            f"  Or create {DATA_DIR}/.env and add {k}=...\n"
            "  See: plaid-cli --help for full configuration docs."
        )
    return v


def _plaid_client() -> plaid_api.PlaidApi:
    client_id = _env_required("PLAID_CLIENT_ID")
    secret = _env_required("PLAID_SECRET")
    host = (
        "https://production.plaid.com"
        if PLAID_ENV == "production"
        else "https://sandbox.plaid.com"
    )
    cfg = Configuration(
        host=host,
        api_key={"clientId": client_id, "secret": secret},
    )
    return plaid_api.PlaidApi(ApiClient(cfg))


def _keychain_get(item_id: str) -> str:
    """Retrieve an access token for a Plaid item.

    Priority:
      1. Environment variable PLAID_TOKEN_<ITEM_ID> (uppercased, hyphens → underscores)
      2. macOS Keychain (service=PLAID_KEYCHAIN_SERVICE, account=item_id)

    To store a token manually:
      macOS:  security add-generic-password -s plaid-cli -a <item_id> -w <access_token>
      Other:  export PLAID_TOKEN_<ITEM_ID>=<access_token>
    """
    # Check env var first (works on any OS, useful for CI)
    env_key = "PLAID_TOKEN_" + item_id.upper().replace("-", "_")
    val = os.environ.get(env_key, "").strip()
    if val:
        return val

    # macOS Keychain
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", item_id, "-w"],
                capture_output=True,
                text=True,
                check=True,
            )
            return r.stdout.strip()
        except subprocess.CalledProcessError:
            pass

    raise RuntimeError(
        f"No access token found for item '{item_id}'.\n"
        f"  macOS: security add-generic-password -s {KEYCHAIN_SERVICE} -a {item_id} -w <token>\n"
        f"  Any OS: export PLAID_TOKEN_{item_id.upper()}=<token>"
    )


def _keychain_set(item_id: str, token: str) -> None:
    """Store an access token for a Plaid item."""
    if sys.platform == "darwin":
        subprocess.run(
            [
                "security", "add-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", item_id,
                "-w", token,
                "-U",  # update if exists
            ],
            check=True,
            capture_output=True,
        )
    else:
        env_key = "PLAID_TOKEN_" + item_id.upper().replace("-", "_")
        print(
            f"{YELLOW}Non-macOS: store this token in your environment:{RST}\n"
            f"  export {env_key}={token}",
            file=sys.stderr,
        )


# ── ANSI helpers ──────────────────────────────────────────────────────────────

if sys.stdout.isatty():
    GREEN, YELLOW, RED, DIM, BOLD, RST = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
else:
    GREEN = YELLOW = RED = DIM = BOLD = RST = ""


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else " "
    return f"{sign}${abs(cents)/100:,.2f}"


def _ago(ts: datetime | None) -> str:
    if ts is None:
        return "never"
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs//60}m"
    if secs < 86400:
        return f"{secs//3600}h"
    return f"{secs//86400}d"


def _resolve_item(name_or_id: str) -> tuple[str, str, str]:
    """Return (uuid, item_id, access_token_item_id) for an item identified by uuid prefix or institution_name."""
    with _db_connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, item_id, institution_name FROM plaid_items")
        rows = cur.fetchall()
    n = name_or_id.lower()

    def _field(r, i):
        try:
            return r[i]
        except (KeyError, IndexError):
            return None

    matches = [r for r in rows if (
        str(_field(r, 0) or "").startswith(n)
        or str(_field(r, 1) or "").lower() == n
        or n in str(_field(r, 2) or "").lower()
    )]
    if not matches:
        raise SystemExit(f"no plaid_item matched '{name_or_id}'. try `plaid items` to list.")
    if len(matches) > 1:
        names = ", ".join(str(m[2]) for m in matches)
        raise SystemExit(f"ambiguous '{name_or_id}' matched {len(matches)}: {names}")
    r = matches[0]
    uuid = str(r[0])
    item_id = str(r[1])
    return uuid, item_id, item_id  # use item_id as the keychain account name


# ── subcommands ───────────────────────────────────────────────────────────────


def cmd_sync(args: argparse.Namespace) -> int:
    """Sync transactions via Plaid /transactions/sync. One item or all."""
    client = _plaid_client()
    with _db_connect() as conn:
        cur = conn.cursor()
        if args.item:
            uuid, _, item_id = _resolve_item(args.item)
            cur.execute(
                "SELECT id, item_id, institution_name FROM plaid_items WHERE id = ?",
                (uuid,)
            ) if not _db_is_postgres() else cur.execute(
                "SELECT id::text, item_id, institution_name FROM plaid_items WHERE id = %s",
                (uuid,)
            )
        else:
            cur.execute(
                "SELECT id, item_id, institution_name FROM plaid_items"
                if not _db_is_postgres()
                else "SELECT id::text, item_id, institution_name FROM plaid_items"
            )
        items_meta = cur.fetchall()

        # Fetch cursors separately
        cur.execute(
            "SELECT id, cursor FROM plaid_items"
            if not _db_is_postgres()
            else "SELECT id::text, cursor FROM plaid_items"
        )
        cursor_map = {str(r[0]): r[1] for r in cur.fetchall()}

    total_added = 0
    per_item: list[dict] = []

    for row in items_meta:
        item_uuid = str(row[0])
        _item_id = str(row[1])
        inst = str(row[2])
        cursor = cursor_map.get(item_uuid)

        try:
            token = _keychain_get(_item_id)
        except Exception as e:
            per_item.append({"institution": inst, "error": f"token missing: {e}"})
            continue

        added_total: list[dict] = []
        modified_total: list[dict] = []
        try:
            while True:
                req = TransactionsSyncRequest(access_token=token, cursor=cursor or "")
                r = client.transactions_sync(req)
                added_total += r["added"]
                modified_total += r["modified"]
                cursor = r["next_cursor"]
                if not r["has_more"]:
                    break
        except Exception as e:
            per_item.append({"institution": inst, "error": str(e)[:200]})
            continue

        item_added = 0
        with _db_connect() as conn:
            cur = conn.cursor()
            ph = _placeholder()
            for t in added_total + modified_total:
                if _db_is_postgres():
                    cur.execute(
                        """INSERT INTO bank_transactions
                            (plaid_item_id, plaid_txn_id, account_id, posted_on, amount_cents,
                             name, merchant_name, pending, raw)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                            ON CONFLICT (plaid_txn_id) DO UPDATE SET
                                amount_cents = EXCLUDED.amount_cents,
                                pending = EXCLUDED.pending""",
                        (
                            item_uuid, t["transaction_id"], t["account_id"], t["date"],
                            int(-t["amount"] * 100),
                            t["name"], t.get("merchant_name"), t.get("pending", False),
                            json.dumps(t, default=str),
                        ),
                    )
                else:
                    cur.execute(
                        """INSERT INTO bank_transactions
                            (plaid_item_id, plaid_txn_id, account_id, posted_on, amount_cents,
                             name, merchant_name, pending, raw)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (plaid_txn_id) DO UPDATE SET
                                amount_cents = excluded.amount_cents,
                                pending = excluded.pending""",
                        (
                            item_uuid, t["transaction_id"], t["account_id"],
                            str(t["date"]),
                            int(-t["amount"] * 100),
                            t["name"], t.get("merchant_name"), int(t.get("pending", False)),
                            json.dumps(t, default=str),
                        ),
                    )
                item_added += cur.rowcount
            cur.execute(
                "UPDATE plaid_items SET cursor = ? WHERE id = ?" if not _db_is_postgres()
                else "UPDATE plaid_items SET cursor = %s WHERE id = %s",
                (cursor, item_uuid),
            )
            conn.commit()

        per_item.append({"institution": inst, "added": item_added, "raw_total": len(added_total) + len(modified_total)})
        total_added += item_added

    if args.json:
        print(json.dumps({"total_added": total_added, "per_item": per_item}, indent=2))
        return 0

    print(f"{BOLD}Plaid sync — {total_added} transactions inserted/updated{RST}")
    for r in per_item:
        if "error" in r:
            print(f"  {RED}✗{RST} {r['institution']:<40} {r['error']}")
        else:
            color = GREEN if r["added"] > 0 else DIM
            print(f"  {color}✓{RST} {r['institution']:<40} added={r['added']} (api={r.get('raw_total',0)})")
    return 0


def cmd_items(args: argparse.Namespace) -> int:
    """List registered plaid_items + last sync time + cursor age."""
    with _db_connect() as conn:
        cur = conn.cursor()
        if _db_is_postgres():
            cur.execute(
                """SELECT pi.id::text, pi.institution_name,
                          jsonb_array_length(pi.accounts::jsonb) AS num_accts,
                          pi.created_at,
                          (SELECT max(created_at) FROM bank_transactions WHERE plaid_item_id = pi.id) AS last_sync,
                          pi.cursor IS NOT NULL AS has_cursor
                   FROM plaid_items pi
                   ORDER BY pi.created_at"""
            )
        else:
            cur.execute(
                """SELECT pi.id, pi.institution_name,
                          (SELECT count(*) FROM json_each(pi.accounts)) AS num_accts,
                          pi.created_at,
                          (SELECT max(created_at) FROM bank_transactions WHERE plaid_item_id = pi.id) AS last_sync,
                          pi.cursor IS NOT NULL AS has_cursor
                   FROM plaid_items pi
                   ORDER BY pi.created_at"""
            )
        rows = cur.fetchall()

    if args.json:
        out = [
            {
                "uuid": str(r[0]),
                "institution": r[1],
                "accounts": r[2],
                "created_at": str(r[3]) if r[3] else None,
                "last_sync": str(r[4]) if r[4] else None,
                "has_cursor": bool(r[5]),
            }
            for r in rows
        ]
        print(json.dumps(out, indent=2))
        return 0

    print(f"{BOLD}{len(rows)} Plaid items registered{RST}")
    print(f"  {'institution':<42} {'accts':>5} {'last sync':>12} {'cursor':>8}")
    print(f"  {'─'*42} {'─'*5} {'─'*12} {'─'*8}")
    for r in rows:
        uuid, inst, na, _, last_sync, has_cur = r[0], r[1], r[2], r[3], r[4], r[5]
        ago = _ago(last_sync)
        ago_color = GREEN
        if ago == "never":
            ago_color = RED
        elif "d" in ago:
            ago_color = RED
        elif "h" in ago:
            ago_color = YELLOW
        cur_str = "✓" if has_cur else "—"
        print(f"  {inst:<42} {na:>5} {ago_color}{ago:>12}{RST} {cur_str:>8}")
    return 0


def _balance_request(token: str, institution_id: str | None) -> AccountsBalanceGetRequest:
    if institution_id in _BALANCE_NEEDS_MIN_LAST_UPDATED:
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        opts = AccountsBalanceGetRequestOptions(min_last_updated_datetime=seven_days_ago)
        return AccountsBalanceGetRequest(access_token=token, options=opts)
    return AccountsBalanceGetRequest(access_token=token)


def cmd_balance(args: argparse.Namespace) -> int:
    """Show current balances per account via live Plaid /accounts/balance/get."""
    client = _plaid_client()
    with _db_connect() as conn:
        cur = conn.cursor()
        if args.item:
            uuid, _, item_id = _resolve_item(args.item)
            cur.execute(
                "SELECT id, item_id, institution_name, institution_id FROM plaid_items WHERE id = ?"
                if not _db_is_postgres()
                else "SELECT id::text, item_id, institution_name, institution_id FROM plaid_items WHERE id = %s",
                (uuid,),
            )
        else:
            cur.execute(
                "SELECT id, item_id, institution_name, institution_id FROM plaid_items"
                if not _db_is_postgres()
                else "SELECT id::text, item_id, institution_name, institution_id FROM plaid_items"
            )
        items = cur.fetchall()

    out: list[dict] = []
    for row in items:
        _uuid, item_id, inst, inst_id = str(row[0]), str(row[1]), str(row[2]), row[3]
        try:
            token = _keychain_get(item_id)
            req = _balance_request(token, inst_id)
            r = client.accounts_balance_get(req)
            for a in r["accounts"]:
                acct_type = str(a["type"])
                if args.type and acct_type != args.type:
                    continue
                out.append({
                    "institution": inst,
                    "account_id": a["account_id"],
                    "name": a["name"],
                    "mask": a.get("mask"),
                    "type": acct_type,
                    "subtype": str(a["subtype"]) if a.get("subtype") else None,
                    "balance_current": a["balances"]["current"],
                    "balance_available": a["balances"].get("available"),
                    "iso_currency_code": a["balances"].get("iso_currency_code", "USD"),
                })
        except ApiException as e:
            out.append({"institution": inst, "error": _parse_plaid_error(e)})
        except Exception as e:
            out.append({"institution": inst, "error": str(e)[:200]})

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"{BOLD}Live balances ({len(out)} accounts){RST}")
    if args.type:
        print(f"  filter: type={args.type}")
    print(f"  {'institution':<35} {'account':<46} {'mask':>5} {'balance':>14}")
    print(f"  {'─'*35} {'─'*46} {'─'*5} {'─'*14}")
    total_assets = 0.0
    total_liab = 0.0
    seen_errors: list[str] = []
    for r in out:
        if "error" in r:
            seen_errors.append(f"{r['institution']}: {r['error']}")
            continue
        nm = (r["name"] or "")[:44]
        bal = r["balance_current"] or 0.0
        mask = r.get("mask") or ""
        is_liab = r["type"] in ("credit", "loan")
        if is_liab:
            total_liab += bal
            color = RED
        else:
            total_assets += bal
            color = GREEN
        print(f"  {r['institution']:<35} {nm:<46} {mask:>5} {color}${bal:>12,.2f}{RST}")
    print(f"  {'─'*35} {'─'*46} {'─'*5} {'─'*14}")
    net = total_assets - total_liab
    net_color = GREEN if net >= 0 else RED
    print(f"  {BOLD}NET{RST}: assets {GREEN}${total_assets:,.2f}{RST} − liabilities {RED}${total_liab:,.2f}{RST} = {net_color}{BOLD}${net:,.2f}{RST}")
    if seen_errors:
        print()
        print(f"  {YELLOW}{len(seen_errors)} institution(s) returned errors:{RST}")
        for e in seen_errors:
            print(f"    {RED}✗{RST} {e}")
    return 0


def _parse_plaid_error(exc: ApiException) -> str:
    try:
        body = json.loads(exc.body) if exc.body else {}
        ec = body.get("error_code", "")
        em = body.get("error_message", "")
        return f"[{ec}] {em}" if ec else em or str(exc)[:120]
    except Exception:
        return str(exc)[:120]


def cmd_holdings(args: argparse.Namespace) -> int:
    """Investment holdings via Plaid /investments/holdings/get."""
    client = _plaid_client()
    with _db_connect() as conn:
        cur = conn.cursor()
        if args.item:
            uuid, _, item_id = _resolve_item(args.item)
            cur.execute(
                "SELECT item_id, institution_name FROM plaid_items WHERE id = ?"
                if not _db_is_postgres()
                else "SELECT item_id, institution_name FROM plaid_items WHERE id = %s",
                (uuid,),
            )
        else:
            if _db_is_postgres():
                cur.execute(
                    """SELECT item_id, institution_name FROM plaid_items
                       WHERE EXISTS (
                           SELECT 1 FROM jsonb_array_elements(plaid_items.accounts::jsonb) acct
                           WHERE acct->>'type' = 'investment'
                       )"""
                )
            else:
                cur.execute(
                    """SELECT item_id, institution_name FROM plaid_items
                       WHERE EXISTS (
                           SELECT 1 FROM json_each(plaid_items.accounts)
                           WHERE json_extract(value, '$.type') = 'investment'
                       )"""
                )
        items = cur.fetchall()

    out: list[dict] = []
    for row in items:
        item_id, inst = str(row[0]), str(row[1])
        try:
            token = _keychain_get(item_id)
            r = client.investments_holdings_get(InvestmentsHoldingsGetRequest(access_token=token))
            sec_by_id = {s["security_id"]: s for s in r["securities"]}
            acct_by_id = {a["account_id"]: a for a in r["accounts"]}
            for h in r["holdings"]:
                sec = sec_by_id.get(h["security_id"], {})
                acct = acct_by_id.get(h["account_id"], {})
                out.append({
                    "institution": inst,
                    "account": acct.get("name", ""),
                    "mask": acct.get("mask"),
                    "ticker": sec.get("ticker_symbol") or sec.get("name", "")[:20],
                    "name": sec.get("name", ""),
                    "type": str(sec.get("type", "")),
                    "quantity": h.get("quantity"),
                    "cost_basis": h.get("cost_basis"),
                    "institution_price": h.get("institution_price"),
                    "institution_value": h.get("institution_value"),
                    "iso_currency_code": h.get("iso_currency_code", "USD"),
                })
        except ApiException as e:
            out.append({"institution": inst, "error": _parse_plaid_error(e)})
        except Exception as e:
            out.append({"institution": inst, "error": str(e)[:200]})

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"{BOLD}Investment holdings ({len([r for r in out if 'error' not in r])} positions){RST}")
    print(f"  {'institution':<35} {'account':<28} {'ticker':<8} {'qty':>10} {'price':>10} {'value':>14}")
    print(f"  {'─'*35} {'─'*28} {'─'*8} {'─'*10} {'─'*10} {'─'*14}")
    total_value = 0.0
    seen_errors: list[str] = []
    for r in out:
        if "error" in r:
            seen_errors.append(f"{r['institution']}: {r['error']}")
            continue
        val = r["institution_value"] or 0.0
        total_value += val
        qty = r["quantity"] or 0
        price = r["institution_price"] or 0
        ticker = (r["ticker"] or "?")[:8]
        nm = (r["account"] or "")[:28]
        print(f"  {r['institution']:<35} {nm:<28} {ticker:<8} {qty:>10,.2f} {price:>10,.2f} {GREEN}${val:>12,.2f}{RST}")
    print(f"  {'─'*35} {'─'*28} {'─'*8} {'─'*10} {'─'*10} {'─'*14}")
    print(f"  {BOLD}TOTAL value: {GREEN}${total_value:,.2f}{RST}")
    if seen_errors:
        print()
        print(f"  {YELLOW}{len(seen_errors)} institution(s) returned errors:{RST}")
        consent_needed: list[str] = []
        for e in seen_errors:
            print(f"    {RED}✗{RST} {e}")
            if "ADDITIONAL_CONSENT_REQUIRED" in e:
                consent_needed.append(e.split(":")[0])
        if consent_needed:
            print()
            print(f"  {BOLD}Fix:{RST} re-auth with investments scope. For each:")
            for iname in consent_needed:
                print(f"    {BOLD}plaid relink \"{iname}\" --products investments{RST}")
            print(f"  Then follow the link-server prompt.")
    return 0


def cmd_liabilities(args: argparse.Namespace) -> int:
    """Loan + credit-card details via Plaid /liabilities/get."""
    client = _plaid_client()
    with _db_connect() as conn:
        cur = conn.cursor()
        if args.item:
            uuid, _, item_id = _resolve_item(args.item)
            cur.execute(
                "SELECT item_id, institution_name FROM plaid_items WHERE id = ?"
                if not _db_is_postgres()
                else "SELECT item_id, institution_name FROM plaid_items WHERE id = %s",
                (uuid,),
            )
        else:
            if _db_is_postgres():
                cur.execute(
                    """SELECT item_id, institution_name FROM plaid_items
                       WHERE EXISTS (
                           SELECT 1 FROM jsonb_array_elements(plaid_items.accounts::jsonb) acct
                           WHERE acct->>'type' IN ('credit', 'loan')
                       )"""
                )
            else:
                cur.execute(
                    """SELECT item_id, institution_name FROM plaid_items
                       WHERE EXISTS (
                           SELECT 1 FROM json_each(plaid_items.accounts)
                           WHERE json_extract(value, '$.type') IN ('credit', 'loan')
                       )"""
                )
        items = cur.fetchall()

    out: list[dict] = []
    for row in items:
        item_id, inst = str(row[0]), str(row[1])
        try:
            token = _keychain_get(item_id)
            r = client.liabilities_get(LiabilitiesGetRequest(access_token=token))
            acct_by_id = {a["account_id"]: a for a in r["accounts"]}
            liab = r["liabilities"]
            for c in liab.get("credit", []) or []:
                acct = acct_by_id.get(c["account_id"], {})
                aprs = c.get("aprs", []) or []
                apr_pct = aprs[0]["apr_percentage"] if aprs else None
                out.append({
                    "institution": inst,
                    "kind": "credit",
                    "account": acct.get("name", ""),
                    "mask": acct.get("mask"),
                    "balance": acct.get("balances", {}).get("current"),
                    "last_payment_amount": c.get("last_payment_amount"),
                    "last_payment_date": str(c.get("last_payment_date", "")) or None,
                    "next_payment_due_date": str(c.get("next_payment_due_date", "")) or None,
                    "minimum_payment_amount": c.get("minimum_payment_amount"),
                    "apr_percentage": apr_pct,
                })
            for m in liab.get("mortgage", []) or []:
                acct = acct_by_id.get(m["account_id"], {})
                out.append({
                    "institution": inst,
                    "kind": "mortgage",
                    "account": acct.get("name", ""),
                    "mask": acct.get("mask"),
                    "balance": acct.get("balances", {}).get("current"),
                    "interest_rate": (m.get("interest_rate") or {}).get("percentage"),
                    "next_monthly_payment": m.get("next_monthly_payment"),
                    "next_payment_due_date": str(m.get("next_payment_due_date", "")) or None,
                    "loan_term": m.get("loan_term"),
                })
            for s in liab.get("student", []) or []:
                acct = acct_by_id.get(s["account_id"], {})
                out.append({
                    "institution": inst,
                    "kind": "student",
                    "account": acct.get("name", ""),
                    "mask": acct.get("mask"),
                    "balance": acct.get("balances", {}).get("current"),
                    "interest_rate_percentage": s.get("interest_rate_percentage"),
                    "minimum_payment_amount": s.get("minimum_payment_amount"),
                    "next_payment_due_date": str(s.get("next_payment_due_date", "")) or None,
                })
        except ApiException as e:
            out.append({"institution": inst, "error": _parse_plaid_error(e)})
        except Exception as e:
            out.append({"institution": inst, "error": str(e)[:200]})

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    real = [r for r in out if "error" not in r]
    print(f"{BOLD}Liabilities ({len(real)} accounts){RST}")
    if not real:
        print(f"  {DIM}(none){RST}")
    for r in real:
        print()
        print(f"  {BOLD}{r['institution']}{RST} · {r['account']} ({r.get('mask') or '—'}) · {DIM}{r['kind']}{RST}")
        bal = r.get("balance")
        if bal is not None:
            print(f"    balance:          {RED}${bal:>10,.2f}{RST}")
        if r["kind"] == "credit":
            apr = r.get("apr_percentage")
            if apr:
                print(f"    APR:              {apr:>10.2f}%")
            mp = r.get("minimum_payment_amount")
            if mp:
                print(f"    min payment:      ${mp:>10,.2f}")
            due = r.get("next_payment_due_date")
            if due:
                print(f"    next due:         {due}")
            lp_amt = r.get("last_payment_amount")
            lp_dt = r.get("last_payment_date")
            if lp_amt or lp_dt:
                print(f"    last payment:     ${lp_amt or 0:,.2f} on {lp_dt or '?'}")
        elif r["kind"] == "mortgage":
            rate = r.get("interest_rate")
            if rate:
                print(f"    rate:             {rate:>10.3f}%")
            nm = r.get("next_monthly_payment")
            if nm:
                print(f"    monthly payment:  ${nm:>10,.2f}")
            due = r.get("next_payment_due_date")
            if due:
                print(f"    next due:         {due}")
            term = r.get("loan_term")
            if term:
                print(f"    term:             {term}")
        elif r["kind"] == "student":
            rate = r.get("interest_rate_percentage")
            if rate:
                print(f"    rate:             {rate:>10.3f}%")
            mp = r.get("minimum_payment_amount")
            if mp:
                print(f"    min payment:      ${mp:>10,.2f}")
            due = r.get("next_payment_due_date")
            if due:
                print(f"    next due:         {due}")
    errs = [r for r in out if "error" in r]
    if errs:
        print()
        print(f"  {YELLOW}{len(errs)} institution(s) returned errors:{RST}")
        consent_needed: list[str] = []
        for e in errs:
            print(f"    {RED}✗{RST} {e['institution']}: {e['error']}")
            if "ADDITIONAL_CONSENT_REQUIRED" in e["error"]:
                consent_needed.append(e["institution"])
        if consent_needed:
            print()
            print(f"  {BOLD}Fix:{RST} re-auth with liabilities scope. For each:")
            for iname in consent_needed:
                print(f"    {BOLD}plaid relink \"{iname}\" --products liabilities{RST}")
    return 0


def cmd_txns(args: argparse.Namespace) -> int:
    """Query bank_transactions table with filters."""
    days = args.days or 30
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    ph = _placeholder()
    sql = f"""SELECT bt.posted_on, bt.amount_cents, bt.name, bt.merchant_name, bt.pending,
                    pi.institution_name
             FROM bank_transactions bt
             LEFT JOIN plaid_items pi ON pi.id = bt.plaid_item_id
             WHERE bt.posted_on >= {ph}"""
    params: list = [cutoff]
    if args.item:
        uuid, _, _ = _resolve_item(args.item)
        sql += f" AND bt.plaid_item_id = {ph}"
        params.append(uuid)
    if args.search:
        sql += f" AND (LOWER(bt.name) LIKE {ph} OR LOWER(COALESCE(bt.merchant_name,'')) LIKE {ph})"
        like = f"%{args.search.lower()}%"
        params += [like, like]
    sql += f" ORDER BY bt.posted_on DESC, bt.amount_cents LIMIT {ph}"
    params.append(args.limit)

    with _db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

    if args.json:
        out = [
            {
                "posted_on": str(r[0]) if r[0] else None,
                "amount_cents": r[1],
                "name": r[2],
                "merchant_name": r[3],
                "pending": bool(r[4]),
                "institution": r[5],
            }
            for r in rows
        ]
        print(json.dumps(out, indent=2))
        return 0

    print(f"{BOLD}{len(rows)} transactions (last {days}d){RST}")
    if args.item:
        print(f"  filter: item={args.item}")
    if args.search:
        print(f"  filter: search='{args.search}'")
    print(f"  {'date':<12} {'amount':>12} {'inst':<22} {'name':<50}")
    print(f"  {'─'*12} {'─'*12} {'─'*22} {'─'*50}")
    for row in rows:
        posted, cents, name, merchant, pending, inst = row[0], row[1], row[2], row[3], row[4], row[5]
        nm = (merchant or name or "")[:50]
        cents = cents or 0
        color = GREEN if cents > 0 else (RED if cents < 0 else DIM)
        pend = f" {YELLOW}(pending){RST}" if pending else ""
        print(f"  {str(posted):<12} {color}{_money(cents):>12}{RST} {(inst or '?')[:22]:<22} {nm:<50}{pend}")
    return 0


def cmd_link(_args: argparse.Namespace) -> int:
    """Start the Plaid link server on http://127.0.0.1:5174.

    This starts a local Flask server that renders the Plaid Link flow.
    Open http://127.0.0.1:5174 in your browser, complete the flow, and the
    access token + institution info are stored in your local DB and Keychain.

    Prerequisites:
      pip install flask
      PLAID_CLIENT_ID and PLAID_SECRET must be set.
    """
    try:
        from flask import Flask, jsonify, render_template_string, request  # type: ignore[import]
    except ImportError:
        print(f"{RED}flask is required for the link server:{RST}", file=sys.stderr)
        print("  pip install flask", file=sys.stderr)
        return 1

    client = _plaid_client()
    app = Flask(__name__)

    _HTML = """<!DOCTYPE html>
<html>
<head><title>Plaid Link</title>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
</head>
<body>
<h2>Plaid Link</h2>
<p>Link token: <code id="lt">{{ link_token }}</code></p>
<button id="btn">Open Plaid Link</button>
<pre id="out"></pre>
<script>
var handler = Plaid.create({
  token: "{{ link_token }}",
  onSuccess: function(public_token, metadata) {
    document.getElementById('out').textContent = 'Exchanging token...';
    fetch('/exchange', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({public_token: public_token, metadata: metadata})
    }).then(r=>r.json()).then(d=>{
      document.getElementById('out').textContent = JSON.stringify(d, null, 2);
    });
  },
  onExit: function(err) {
    if (err) document.getElementById('out').textContent = JSON.stringify(err, null, 2);
  }
});
document.getElementById('btn').onclick = function() { handler.open(); };
// auto-open if ?auto=1
if (window.location.search.includes('auto=1')) handler.open();
</script>
</body></html>"""

    @app.route("/")
    def index():
        link_token_param = request.args.get("link_token")
        if link_token_param:
            return render_template_string(_HTML, link_token=link_token_param)
        req = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id="plaid-cli-user"),
            client_name="plaid-cli",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        r = client.link_token_create(req)
        return render_template_string(_HTML, link_token=r["link_token"])

    @app.route("/exchange", methods=["POST"])
    def exchange():
        from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
        data = request.json
        public_token = data["public_token"]
        metadata = data.get("metadata", {})
        r = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token)
        )
        access_token = r["access_token"]
        item_id = r["item_id"]

        # Get institution info
        inst_name = (metadata.get("institution") or {}).get("name", "Unknown")
        inst_id = (metadata.get("institution") or {}).get("institution_id")
        accounts = metadata.get("accounts", [])

        # Store access token in Keychain
        _keychain_set(item_id, access_token)

        # Store item in DB
        ph = _placeholder()
        with _db_connect() as conn:
            cur = conn.cursor()
            if _db_is_postgres():
                cur.execute(
                    """INSERT INTO plaid_items (item_id, institution_name, institution_id, accounts)
                       VALUES (%s, %s, %s, %s::jsonb)
                       ON CONFLICT (item_id) DO UPDATE SET
                           institution_name = EXCLUDED.institution_name,
                           accounts = EXCLUDED.accounts""",
                    (item_id, inst_name, inst_id, json.dumps(accounts)),
                )
            else:
                cur.execute(
                    """INSERT INTO plaid_items (item_id, institution_name, institution_id, accounts)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (item_id) DO UPDATE SET
                           institution_name = excluded.institution_name,
                           accounts = excluded.accounts""",
                    (item_id, inst_name, inst_id, json.dumps(accounts)),
                )
            conn.commit()

        return jsonify({
            "ok": True,
            "item_id": item_id,
            "institution": inst_name,
            "accounts": len(accounts),
            "message": f"Linked! Run: plaid sync --item '{inst_name}'"
        })

    print(f"{BOLD}Starting Plaid link server on http://127.0.0.1:5174{RST}")
    print(f"{DIM}Open that URL in your browser, click the button, complete the flow.{RST}")
    print(f"{DIM}Stop with Ctrl-C when done.{RST}")
    app.run(host="127.0.0.1", port=5174, debug=False)
    return 0


def cmd_relink(args: argparse.Namespace) -> int:
    """Generate a Plaid update-mode link token for re-auth of an existing item."""
    uuid, item_id, _ = _resolve_item(args.item)
    try:
        token = _keychain_get(item_id)
    except Exception as e:
        print(f"{RED}token missing for {item_id}: {e}{RST}", file=sys.stderr)
        return 1
    client = _plaid_client()
    kwargs: dict = dict(
        user=LinkTokenCreateRequestUser(client_user_id="plaid-cli-user"),
        client_name="plaid-cli",
        country_codes=[CountryCode("US")],
        language="en",
        access_token=token,
    )
    if args.products:
        valid = {"transactions", "investments", "liabilities", "auth", "identity"}
        wanted = [p.strip() for p in args.products.split(",")]
        bad = [p for p in wanted if p not in valid]
        if bad:
            print(f"{RED}unknown product(s): {bad}. valid: {sorted(valid)}{RST}", file=sys.stderr)
            return 1
        kwargs["additional_consented_products"] = [Products(p) for p in wanted]

    req = LinkTokenCreateRequest(**kwargs)
    try:
        r = client.link_token_create(req)
    except ApiException as e:
        msg = _parse_plaid_error(e)
        print(f"{RED}Plaid rejected the relink request: {msg}{RST}", file=sys.stderr)
        return 1

    print(f"{BOLD}Update-mode link token created{RST}")
    print(f"  item_id:     {item_id}")
    print(f"  expiration:  {r.get('expiration', 'unknown')}")
    if args.products:
        print(f"  + products:  {args.products}")
    print()
    print(f"Next:")
    print(f"  1. {BOLD}plaid link{RST}  (in another terminal — starts the link server)")
    print(f"  2. open {BOLD}http://127.0.0.1:5174/?link_token={r['link_token']}&auto=1{RST}")
    print(f"  3. Complete the Plaid Link flow in your browser.")
    print(f"  4. {BOLD}plaid sync --item {args.item}{RST}  to verify.")
    return 0


def _item_account_types(accounts_json: Any) -> set[str]:
    try:
        data = json.loads(accounts_json) if isinstance(accounts_json, str) else accounts_json
        return {str(a.get("type", "")) for a in (data or [])}
    except Exception:
        return set()


def cmd_health(args: argparse.Namespace) -> int:
    """Per-item health classified by account type."""
    threshold_hours = args.stale_after_hours
    with _db_connect() as conn:
        cur = conn.cursor()
        if _db_is_postgres():
            cur.execute(
                """SELECT pi.id::text, pi.institution_name, pi.cursor IS NOT NULL AS has_cursor,
                          pi.accounts::text,
                          (SELECT max(created_at) FROM bank_transactions WHERE plaid_item_id = pi.id) AS last_sync,
                          (SELECT count(*) FROM bank_transactions WHERE plaid_item_id = pi.id) AS total_txns
                   FROM plaid_items pi
                   ORDER BY pi.institution_name"""
            )
        else:
            cur.execute(
                """SELECT pi.id, pi.institution_name, pi.cursor IS NOT NULL AS has_cursor,
                          pi.accounts,
                          (SELECT max(created_at) FROM bank_transactions WHERE plaid_item_id = pi.id) AS last_sync,
                          (SELECT count(*) FROM bank_transactions WHERE plaid_item_id = pi.id) AS total_txns
                   FROM plaid_items pi
                   ORDER BY pi.institution_name"""
            )
        rows = cur.fetchall()

    real_stale = 0
    out: list[dict] = []
    for row in rows:
        uuid, inst, has_cur, accts_json, last_sync, total_txns = (
            str(row[0]), str(row[1]), bool(row[2]), row[3], row[4], row[5]
        )
        if last_sync:
            if isinstance(last_sync, str):
                try:
                    last_sync = datetime.fromisoformat(last_sync)
                except ValueError:
                    last_sync = None
            if last_sync:
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                hrs = (datetime.now(timezone.utc) - last_sync).total_seconds() / 3600
            else:
                hrs = float("inf")
        else:
            hrs = float("inf")

        types = _item_account_types(accts_json)
        primary_endpoint = "transactions" if "depository" in types or "credit" in types else (
            "holdings" if "investment" in types else (
                "liabilities" if "loan" in types else "transactions"
            )
        )
        if "depository" in types or "credit" in types:
            stale = hrs > threshold_hours
            if stale:
                real_stale += 1
            if hrs == float("inf"):
                status = "STALE"
            elif hrs > threshold_hours:
                status = "STALE"
            elif hrs > threshold_hours / 2:
                status = "aging"
            else:
                status = "fresh"
        else:
            stale = False
            status = f"use `plaid {primary_endpoint}`"

        out.append({
            "institution": inst,
            "uuid": uuid[:8],
            "account_types": sorted(types),
            "primary_endpoint": primary_endpoint,
            "last_sync_hours_ago": round(hrs, 1) if hrs != float("inf") else None,
            "total_txns": total_txns,
            "has_cursor": has_cur,
            "stale": stale,
            "status": status,
        })

    if args.json:
        print(json.dumps({"items": out, "real_stale_count": real_stale}, indent=2))
        return 1 if real_stale else 0

    print(f"{BOLD}Plaid health (depository stale threshold: {threshold_hours}h){RST}")
    print()
    print(f"  {'institution':<42} {'types':<25} {'last sync':>12} {'txns':>7} {'status':<22}")
    print(f"  {'─'*42} {'─'*25} {'─'*12} {'─'*7} {'─'*22}")
    for r in out:
        types_str = ",".join(r["account_types"])[:24]
        hrs = r["last_sync_hours_ago"]
        ago = "never" if hrs is None else f"{hrs:.1f}h"
        status = r["status"]
        if status == "fresh":
            color = GREEN
        elif status == "aging":
            color = YELLOW
        elif status == "STALE":
            color = RED
        else:
            color = DIM
        print(f"  {r['institution']:<42} {DIM}{types_str:<25}{RST} {color}{ago:>12}{RST} {r['total_txns']:>7} {color}{status:<22}{RST}")
    print()
    if real_stale:
        print(f"  {RED}{real_stale} depository item(s) genuinely stale.{RST}")
        print(f"  Try: {BOLD}plaid sync{RST}  — re-sync all items")
        print(f"       {BOLD}plaid relink <NAME>{RST}  — if login expired")
        return 1
    print(f"  {GREEN}All depository items fresh.{RST}")
    print(f"  Non-depository items: use `plaid holdings` / `plaid liabilities`.")
    return 0


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        prog="plaid",
        description="plaid-cli — terminal-first Plaid banking CLI",
        epilog=(
            "Configuration: set PLAID_CLIENT_ID + PLAID_SECRET in env or ~/.plaid-cli/.env\n"
            "Database:      SQLite (default) or set PLAID_DB_URL=postgresql://..."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync", help="Sync transactions for one or all items")
    sp.add_argument("--item", help="Institution name, item_id, or uuid prefix")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("items", help="List registered Plaid institutions")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_items)

    sp = sub.add_parser("balance", help="Show live balances per account (Plaid API call)")
    sp.add_argument("--item", help="Institution name, item_id, or uuid prefix")
    sp.add_argument("--type", choices=["depository", "credit", "loan", "investment"],
                    help="Filter by account type")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_balance)

    sp = sub.add_parser("holdings", help="Investment holdings (brokerage / IRA / 529 positions)")
    sp.add_argument("--item", help="Institution name (default: all items with investment accounts)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_holdings)

    sp = sub.add_parser("liabilities", help="Loan + credit-card details (APR, due date, min payment)")
    sp.add_argument("--item", help="Institution name (default: all items with loan/credit accounts)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_liabilities)

    sp = sub.add_parser("txns", help="Query the local bank_transactions table")
    sp.add_argument("--days", type=int, default=30, help="Lookback window (default 30)")
    sp.add_argument("--item", help="Institution filter")
    sp.add_argument("--search", help="Substring search on name/merchant")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_txns)

    sp = sub.add_parser("link", help="Start the link server for adding a new institution (requires flask)")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("relink", help="Generate update-mode link token for re-auth (ITEM_LOGIN_REQUIRED)")
    sp.add_argument("item", help="Institution name, item_id, or uuid prefix")
    sp.add_argument("--products",
                    help="Comma-separated products to add consent for, e.g. 'investments,liabilities'")
    sp.set_defaults(func=cmd_relink)

    sp = sub.add_parser("health", help="Per-item sync health; exits 1 if any depository item is stale")
    sp.add_argument("--stale-after-hours", type=int, default=24,
                    help="Treat depository item as stale after N hours without a sync (default 24)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_health)

    args = p.parse_args()

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}interrupted{RST}", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as e:
        print(f"{RED}error: {e}{RST}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

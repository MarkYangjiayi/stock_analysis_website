#!/usr/bin/env python3
"""Edit the personal watchlist from a terminal through the site's API.

Usage:
  python scripts/watchlist.py ls
  python scripts/watchlist.py add NVDA AMD    # NVDA ends up first, like adding on the page
  python scripts/watchlist.py rm NVDA
  python scripts/watchlist.py core            # list the core tier (thesis tracking)
  python scripts/watchlist.py core add MELI   # a listed ticker; at most 8 are core
  python scripts/watchlist.py core rm MELI    # back to rule alerts only

Reads the Admin Key from FINBRAIN_ADMIN_API_KEY. FINBRAIN_API_URL overrides
https://finbrain.icu, e.g. http://127.0.0.1:8000 for a local backend.
Standard library only, so any python3 can run it outside the project's venv;
install it as a command with: install -m 755 scripts/watchlist.py ~/.local/bin/watchlist
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, parse, request

DEFAULT_API_URL = "https://finbrain.icu"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _NoRedirect(request.HTTPRedirectHandler):
    # Following a redirect would resend the Admin Key to wherever it points.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _api_url() -> str:
    url = os.environ.get("FINBRAIN_API_URL", DEFAULT_API_URL).rstrip("/")
    parts = parse.urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
        sys.exit(f"Refusing to send the Admin Key to {url}: use https, or http only for localhost.")
    return url


def _call(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("FINBRAIN_ADMIN_API_KEY", "").strip()
    if not key:
        sys.exit("FINBRAIN_ADMIN_API_KEY is not set; it lives in ~/.config/secrets.env.")
    url = _api_url()
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(f"{url}{path}", data=data, method=method)
    req.add_header("X-API-Key", key)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "finbrain-watchlist-cli")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with request.build_opener(_NoRedirect).open(req, timeout=20) as response:
            return json.load(response)
    except error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", "")
        except (ValueError, AttributeError):
            detail = ""
        sys.exit(f"{method} {path} failed: HTTP {exc.code} {detail}".rstrip())
    except error.URLError as exc:
        sys.exit(f"Cannot reach {url}: {exc.reason}")


def _canonical(ticker: str) -> str:
    # Mirrors services.security_master.canonicalize_ticker for US listings.
    value = ticker.strip().upper()
    return value if "." in value else f"{value}.US"


def _position(tickers: list[str], ticker: str) -> str:
    return f"#{tickers.index(ticker) + 1} of {len(tickers)}"


def list_tickers(_: list[str]) -> None:
    state = _call("GET", "/api/personal/watchlist")
    core = set(state.get("core", []))
    for index, ticker in enumerate(state["tickers"], start=1):
        print(f"{index:>3}  {ticker:<12}{'core' if ticker in core else ''}".rstrip())
    print(f"{len(state['tickers'])} tickers, {len(core)} core")


def add_tickers(tickers: list[str]) -> None:
    symbols = list(dict.fromkeys(_canonical(ticker) for ticker in tickers))
    added: dict[str, bool] = {}
    # Each add goes to the front, so add the last argument first to keep the given order.
    for symbol in reversed(symbols):
        result = _call("POST", "/api/personal/watchlist/items", {"ticker": symbol})
        added[symbol] = result["added"]
    for symbol in symbols:
        status = "added" if added[symbol] else "already listed"
        print(f"{symbol}: {status} ({_position(result['tickers'], symbol)})")


def remove_tickers(tickers: list[str]) -> None:
    # Removing a ticker drops its core flag; say so, since adding it back does not restore it.
    core = set(_call("GET", "/api/personal/watchlist").get("core", []))
    for symbol in dict.fromkeys(_canonical(ticker) for ticker in tickers):
        result = _call("DELETE", f"/api/personal/watchlist/items/{parse.quote(symbol, safe='')}")
        status = "removed" if result["removed"] else "was not listed"
        if result["removed"] and symbol in core:
            status += ", and is no longer core"
        print(f"{symbol}: {status} ({len(result['tickers'])} tickers left)")


def list_core(_: list[str]) -> None:
    core = _call("GET", "/api/personal/watchlist").get("core", [])
    for ticker in core:
        print(ticker)
    print(f"{len(core)} core")


def set_core(tickers: list[str], core: bool) -> None:
    for symbol in dict.fromkeys(_canonical(ticker) for ticker in tickers):
        result = _call(
            "PUT",
            f"/api/personal/watchlist/items/{parse.quote(symbol, safe='')}/core",
            {"core": core},
        )
        if core:
            status = "marked core" if result["changed"] else "already core"
        else:
            status = "back to the rule tier" if result["changed"] else "was not core"
        print(f"{symbol}: {status} ({len(result['core'])} core)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Edit the personal watchlist.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ls", help="list the watchlist in order").set_defaults(run=list_tickers, tickers=[])
    add = commands.add_parser("add", help="add tickers to the front")
    add.add_argument("tickers", nargs="+")
    add.set_defaults(run=add_tickers)
    remove = commands.add_parser("rm", help="remove tickers")
    remove.add_argument("tickers", nargs="+")
    remove.set_defaults(run=remove_tickers)
    core = commands.add_parser("core", help="list the core tier, or mark and unmark tickers")
    core.set_defaults(run=list_core, tickers=[])
    core_commands = core.add_subparsers(dest="core_command")
    core_add = core_commands.add_parser("add", help="mark listed tickers as core")
    core_add.add_argument("tickers", nargs="+")
    core_add.set_defaults(run=lambda tickers: set_core(tickers, True))
    core_remove = core_commands.add_parser("rm", help="move tickers back to the rule tier")
    core_remove.add_argument("tickers", nargs="+")
    core_remove.set_defaults(run=lambda tickers: set_core(tickers, False))
    args = parser.parse_args()
    args.run(args.tickers)


if __name__ == "__main__":
    main()

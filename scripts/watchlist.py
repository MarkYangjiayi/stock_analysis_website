#!/usr/bin/env python3
"""Edit the personal watchlist from a terminal through the site's API.

Usage:
  python scripts/watchlist.py ls
  python scripts/watchlist.py add NVDA AMD    # NVDA ends up first, like adding on the page
  python scripts/watchlist.py rm NVDA

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
    tickers = _call("GET", "/api/personal/watchlist")["tickers"]
    for index, ticker in enumerate(tickers, start=1):
        print(f"{index:>3}  {ticker}")
    print(f"{len(tickers)} tickers")


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
    for symbol in dict.fromkeys(_canonical(ticker) for ticker in tickers):
        result = _call("DELETE", f"/api/personal/watchlist/items/{parse.quote(symbol, safe='')}")
        status = "removed" if result["removed"] else "was not listed"
        print(f"{symbol}: {status} ({len(result['tickers'])} tickers left)")


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
    args = parser.parse_args()
    args.run(args.tickers)


if __name__ == "__main__":
    main()

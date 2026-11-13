"""Fetch 1-month performance for the ETF watchlist in etfs.json.

Data comes from Yahoo Finance's public chart endpoint (no API key). It is
unofficial, so every failure is caught and reported instead of crashing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ETFS_FILE = Path(__file__).with_name("etfs.json")
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d"
TIMEOUT = 10


def _use_color() -> bool:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        os.system("")  # turns on ANSI colour support in the Windows console
    return True


def fetch_month_change(ticker: str) -> dict:
    """Return {'price', 'change', 'currency'} for the last month, or {'error'}."""
    req = urllib.request.Request(
        CHART_URL.format(ticker=ticker), headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            result = json.load(resp)["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c]
        first, last = closes[0], closes[-1]
        return {
            "price": last,
            "change": (last / first - 1) * 100,
            "currency": result["meta"].get("currency", ""),
        }
    except Exception as e:  # network, HTTP, or unexpected JSON shape
        return {"error": type(e).__name__}


def load_watchlist(path: Path = ETFS_FILE) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def render_etf_table() -> str:
    watchlist = load_watchlist()
    etfs = watchlist["etfs"]
    with ThreadPoolExecutor(max_workers=len(etfs)) as pool:
        data = list(pool.map(lambda e: fetch_month_change(e["ticker"]), etfs))

    rows = [{**e, **d} for e, d in zip(etfs, data)]
    ok = sorted((r for r in rows if "error" not in r), key=lambda r: r["change"], reverse=True)
    failed = [r for r in rows if "error" in r]

    if not ok:
        return "\nETF watchlist: couldn't load market data right now (are you online?)."

    color = _use_color()
    green, red, reset = ("\033[32m", "\033[31m", "\033[0m") if color else ("", "", "")

    headers = ("#", "ETF", "Ticker", "Price", "1 month")
    body = []
    for n, r in enumerate(ok, 1):
        trend = "UP" if r["change"] >= 0 else "DOWN"
        body.append((
            str(n),
            r["name"],
            r["ticker"].split(".")[0],
            f"{r['price']:,.2f} {r['currency']}",
            f"{r['change']:+.2f}% {trend}",
        ))

    widths = [max(len(row[i]) for row in [headers, *body]) for i in range(5)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    align = ("rjust", "ljust", "ljust", "rjust", "rjust")

    def line(cells, paint=""):
        parts = [getattr(c, a)(w) for c, a, w in zip(cells, align, widths)]
        if paint:
            parts[-1] = f"{paint}{parts[-1]}{reset}"
        return "| " + " | ".join(parts) + " |"

    out = [
        "\nETF ideas for your investments: 1-month performance, best to worst",
        sep,
        line(headers),
        sep,
        *(line(b, green if r["change"] >= 0 else red) for b, r in zip(body, ok)),
        sep,
        f"Source: {watchlist.get('source', 'Yahoo Finance')}. Not investment advice.",
    ]
    if failed:
        out.append("Couldn't load: " + ", ".join(r["ticker"] for r in failed))
    return "\n".join(out)


if __name__ == "__main__":
    print(render_etf_table())

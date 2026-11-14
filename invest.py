"""Your own ETF mix: how the Investments money of each budget is split across ETFs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import store
from etf import fetch_month_change, load_watchlist
from rates import CURRENCIES, convert, fetch_rates
from ui import ask, confirm, fmt_money, grid

FILE = "invest.json"

USAGE = """\
Investment plan:
  budget invest              show your ETF mix (with today's prices)
  budget invest set          choose your ETFs and the % for each
  budget invest clear        delete your ETF mix"""


def load_plan() -> list[dict]:
    return store.load(FILE, {"etfs": []})["etfs"]


def normalise_ticker(text: str) -> str:
    """VWCE -> VWCE.DE (Xetra, prices in EUR); tickers with a dot are kept."""
    text = text.strip().upper()
    return text if "." in text else f"{text}.DE"


def _quotes(plan: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=max(1, len(plan))) as pool:
        return list(pool.map(lambda e: fetch_month_change(e["ticker"]), plan))


def render_plan(amount: Decimal | None = None, currency: str = "RON", rates: dict | None = None) -> list[str]:
    """Table of the ETF mix; with `amount`, how much to buy of each and about how many shares."""
    plan = load_plan()
    if not plan:
        return []
    quotes = _quotes(plan)
    headers = ["ETF", "Ticker", "%"]
    if amount is not None:
        headers.append(f"Buy ({currency})")
    headers += ["Price"]
    if amount is not None:
        headers.append("~ Shares")

    rows = []
    for item, q in zip(plan, quotes):
        pct = Decimal(str(item["percent"]))
        row = [item["name"], item["ticker"].split(".")[0], f"{pct.normalize():f}%"]
        buy = (amount * pct / 100).quantize(Decimal("0.01")) if amount is not None else None
        if buy is not None:
            row.append(fmt_money(buy))
        if "error" in q:
            row.append("unavailable")
            if buy is not None:
                row.append("-")
        else:
            price = Decimal(str(q["price"]))
            row.append(f"{fmt_money(price)} {q['currency']}")
            if buy is not None:
                shares = "-"
                if q["currency"] == currency:
                    shares = f"{buy / price:.4f}"
                elif rates and q["currency"] in CURRENCIES:
                    shares = f"{convert(buy, currency, q['currency'], rates) / price:.4f}"
                row.append(shares)
        rows.append(row)

    align = "llr" + ("r" if amount is not None else "") + "r" + ("r" if amount is not None else "")
    title = (f"Your investment plan for {fmt_money(amount)} {currency}:" if amount is not None
             else "Your investment plan:")
    out = ["", title, *grid(headers, rows, align)]
    out.append("Shares are approximate (fractional shares). Prices from Yahoo Finance. Not investment advice.")
    return out


def hint() -> list[str]:
    return ["", "Tip: set your own ETF mix with 'budget invest set' to see what to buy each month."]


def set_plan() -> None:
    watch = {e["ticker"]: e["name"] for e in load_watchlist()["etfs"]}
    print("Build your ETF mix. Type a ticker like VWCE (Xetra, in EUR) or a full Yahoo")
    print("ticker like VWCE.DE. 'budget etfs' shows popular ones. The %s must add up to 100.")
    plan: list[dict] = []
    total = Decimal(0)
    while total < 100:
        text = ask(f"ETF ticker #{len(plan) + 1}: ")
        if not text:
            print("  Please type a ticker.")
            continue
        ticker = normalise_ticker(text)
        if any(p["ticker"] == ticker for p in plan):
            print(f"  {ticker} is already in your mix.")
            continue
        quote = fetch_month_change(ticker)
        if "error" in quote:
            print(f"  Couldn't find {ticker} on Yahoo Finance (or you're offline). Check the ticker.")
            continue
        name = watch.get(ticker, quote["long_name"])
        print(f"  {name}: {fmt_money(Decimal(str(quote['price'])))} {quote['currency']}")
        left = 100 - total
        while True:
            text = ask(f"  % for {ticker.split('.')[0]} (Enter = {left:g}): ")
            try:
                pct = left if not text else Decimal(text.rstrip("%"))
                if not 0 < pct <= left:
                    raise ValueError
                break
            except (ValueError, ArithmeticError):
                print(f"  Please enter a number above 0 and at most {left:g}.")
        plan.append({"ticker": ticker, "name": name, "percent": float(pct)})
        total += pct
    store.save(FILE, {"etfs": plan})
    print("Saved your ETF mix.")
    print("\n".join(render_plan()))


def run(args: list[str]) -> int:
    action = args[0].lower() if args else ""
    if action == "":
        lines = render_plan()
        print("\n".join(lines) if lines else "You have no investment plan yet.")
        print("\n" + USAGE)
    elif action == "set":
        if load_plan() and not confirm("Replace your current ETF mix?", default=False):
            return 0
        set_plan()
    elif action == "clear":
        if not load_plan():
            print("You have no investment plan.")
        elif confirm("Delete your ETF mix?", default=False):
            store.remove(FILE)
            print("Deleted your ETF mix.")
    else:
        print(USAGE)
        return 2
    return 0

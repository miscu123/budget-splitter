#!/usr/bin/env python3
"""Budget splitter: split an income amount across categories based on its type.

Usage:
    python budget.py                    # interactive: pick a type, enter an amount
    python budget.py salary 3600        # non-interactive
    python budget.py --list             # show the available income types
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

PLANS_FILE = Path(__file__).with_name("plans.json")
CENT = Decimal("0.01")


def load_plans(path: Path = PLANS_FILE) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)["plans"]


def split_amount(amount: Decimal, items: list[dict]) -> list[Decimal]:
    """Split `amount` proportionally to each item's weight, rounded to cents.

    Any rounding leftover goes to the last item so the parts always add up.
    """
    total_weight = sum(Decimal(str(i["weight"])) for i in items)
    parts = [
        (amount * Decimal(str(i["weight"])) / total_weight).quantize(CENT, ROUND_HALF_UP)
        for i in items
    ]
    parts[-1] += amount - sum(parts)
    return parts


def build_rows(amount: Decimal, plan: dict) -> list[tuple[str, Decimal, Decimal]]:
    """Return (label, percent, amount) rows, with sub-splits indented under their parent."""
    rows = []
    categories = plan["categories"]
    for cat, part in zip(categories, split_amount(amount, categories)):
        rows.append((cat["name"], part / amount * 100, part))
        if cat.get("split"):
            for sub, sub_part in zip(cat["split"], split_amount(part, cat["split"])):
                rows.append((f"  - {sub['name']}", sub_part / amount * 100, sub_part))
    return rows


def fmt_money(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_table(amount: Decimal, plan: dict) -> str:
    rows = build_rows(amount, plan)
    headers = ("Category", "%", "Amount")
    body = [(name, f"{pct:.1f}%", fmt_money(val)) for name, pct, val in rows]
    footer = ("Total", "100.0%", fmt_money(amount))

    widths = [max(len(r[i]) for r in [headers, *body, footer]) for i in range(3)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells):
        name, pct, val = cells
        return (
            f"| {name.ljust(widths[0])} | {pct.rjust(widths[1])} | {val.rjust(widths[2])} |"
        )

    out = [
        f"\n{plan['label']}: {fmt_money(amount)}",
        sep,
        line(headers),
        sep,
        *(line(r) for r in body),
        sep,
        line(footer),
        sep,
    ]
    return "\n".join(out)


def parse_amount(text: str) -> Decimal:
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a valid amount")
    if value <= 0:
        raise ValueError("amount must be greater than 0")
    return value


HELP_WORDS = {"/help", "help", "/h", "/?", "?"}

USAGE = """\
Usage:
  budget                    pick an income type from a menu, then enter the amount
  budget <type> <amount>    e.g. budget salary 3600
  budget --list             show income types and how each one is split
  budget /help              show this help
  --no-etfs                 skip the ETF table (shown for types with investments)

Tip: type /help at the menu prompt to see the splits before choosing."""


def choose_plan(plans: dict) -> str:
    keys = list(plans)

    def show_menu():
        print("What type of income is it?  (type /help to see how each one is split)")
        for n, key in enumerate(keys, 1):
            print(f"  {n}) {plans[key]['label']}")

    show_menu()
    while True:
        choice = input("Choose a number or name: ").strip("﻿ \t\r\n").lower()
        if choice in HELP_WORDS:
            list_plans(plans)
            show_menu()
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            return keys[int(choice) - 1]
        if choice in plans:
            return choice
        print("  Not a valid option, try again (or type /help).")


def ask_amount() -> Decimal:
    while True:
        try:
            return parse_amount(input("Amount to budget: "))
        except ValueError as e:
            print(f"  {e}, try again.")


def list_plans(plans: dict) -> None:
    """Print every income type with the % each category gets."""
    print("\nIncome types and how they are split:")
    for n, (key, plan) in enumerate(plans.items(), 1):
        rows = build_rows(Decimal(100), plan)
        width = max(len(name) for name, _, _ in rows)
        print(f"\n  {n}) {plan['label']}  ({key})")
        for name, pct, _ in rows:
            print(f"       {name.ljust(width)}  {pct:5.1f}%")
    print()


def print_help(plans: dict) -> None:
    print(USAGE)
    list_plans(plans)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="budget",
        description="Split an income amount into your budget categories.",
        add_help=False,
    )
    parser.add_argument("type", nargs="?", help="income type (see --list)")
    parser.add_argument("amount", nargs="?", help="amount to budget, e.g. 3600")
    parser.add_argument("--list", action="store_true", help="list income types and exit")
    parser.add_argument("-h", "--help", action="store_true", help="show help and exit")
    parser.add_argument("--no-etfs", action="store_true", help="skip the ETF performance table")
    args = parser.parse_args(argv)

    plans = load_plans()

    if args.help or (args.type and args.type.lower() in HELP_WORDS):
        print_help(plans)
        return 0

    if args.list:
        list_plans(plans)
        return 0

    try:
        if args.type is None:
            key = choose_plan(plans)
        elif args.type.lower() in plans:
            key = args.type.lower()
        else:
            parser.error(f"unknown income type '{args.type}'. Options: {', '.join(plans)}")

        if args.amount is None:
            amount = ask_amount()
        else:
            try:
                amount = parse_amount(args.amount)
            except ValueError as e:
                parser.error(str(e))
    except (KeyboardInterrupt, EOFError):
        print()
        return 1

    print(render_table(amount, plans[key]))

    if has_investments(plans[key]) and not args.no_etfs:
        from etf import render_etf_table

        print("\nLoading ETF data...", end="\r")
        print(render_etf_table())
    return 0


def has_investments(plan: dict) -> bool:
    """True if any category or sub-category of the plan is about investing."""
    names = [c["name"] for c in plan["categories"]]
    names += [s["name"] for c in plan["categories"] for s in c.get("split", [])]
    return any("invest" in n.lower() for n in names)


if __name__ == "__main__":
    sys.exit(main())

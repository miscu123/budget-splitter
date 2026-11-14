#!/usr/bin/env python3
"""Budget splitter: split an income amount across categories based on its type.

Usage:
    python budget.py                    # interactive: pick a type and currency, enter an amount
    python budget.py salary 3600        # non-interactive (default currency)
    python budget.py salary 500 eur     # amount in EUR (RON, EUR or USD)
    python budget.py --list             # show the available income types
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from rates import CURRENCIES, convert, describe, fetch_rates

PLANS_FILE = Path(__file__).with_name("plans.json")
CENT = Decimal("0.01")


def load_config(path: Path = PLANS_FILE) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_plans(path: Path = PLANS_FILE) -> dict:
    return load_config(path)["plans"]


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


def build_rows(
    amount: Decimal, plan: dict, fixed: dict[str, Decimal] | None = None
) -> list[tuple[str, Decimal, Decimal]]:
    """Return (label, percent, amount) rows, with sub-splits indented under their parent.

    `fixed` maps category names to a set amount (0 = not paid, row left out). Fixed
    amounts come off the top, other non-flexible categories keep their normal share,
    and whatever is left goes to the flexible categories by weight.
    """
    fixed = fixed or {}
    categories = plan["categories"]
    parts = dict(zip((c["name"] for c in categories), split_amount(amount, categories)))
    if fixed:
        flexible = flexible_categories(plan, fixed)
        pool = amount - sum(fixed.values()) - sum(
            parts[c["name"]] for c in categories if c["name"] not in fixed and c not in flexible
        )
        if flexible:
            parts.update(zip((c["name"] for c in flexible), split_amount(pool, flexible)))
        parts.update(fixed)

    rows = []
    for cat in categories:
        part = parts[cat["name"]]
        if cat["name"] in fixed and part == 0:
            continue
        rows.append((cat["name"], part / amount * 100, part))
        if cat.get("split"):
            for sub, sub_part in zip(cat["split"], split_amount(part, cat["split"])):
                rows.append((f"  - {sub['name']}", sub_part / amount * 100, sub_part))
    return rows


def flexible_categories(plan: dict, fixed: dict) -> list[dict]:
    """Categories that absorb money freed (or used up) by fixed costs.

    Those marked "flexible" in plans.json; if none are marked, every non-fixed one.
    """
    marked = [c for c in plan["categories"] if c.get("flexible") and c["name"] not in fixed]
    return marked or [c for c in plan["categories"] if c["name"] not in fixed]


def max_fixed_total(amount: Decimal, plan: dict) -> Decimal:
    """The most that fixed costs can add up to: the amount minus the other set categories."""
    categories = plan["categories"]
    asked = {c["name"] for c in asked_categories(plan)}
    parts = dict(zip((c["name"] for c in categories), split_amount(amount, categories)))
    flexible = flexible_categories(plan, asked)
    return amount - sum(parts[c["name"]] for c in categories
                        if c["name"] not in asked and c not in flexible)


def asked_categories(plan: dict) -> list[dict]:
    """Categories the user is asked about (do you pay it, and how much)."""
    return [c for c in plan["categories"] if c.get("ask")]


def flag_name(category: dict) -> str:
    return category["name"].lower().replace(" ", "-")


def ask_fixed_costs(
    amount: Decimal, plan: dict, currency: str, known: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Ask, for each asked category not already in `known`, whether it's paid and how much."""
    fixed = dict(known)
    defaults = dict(zip((c["name"] for c in plan["categories"]),
                        split_amount(amount, plan["categories"])))
    for cat in asked_categories(plan):
        name = cat["name"]
        if name in fixed:
            continue
        noun = name.lower()
        while True:
            yn = input(f"Do you pay {noun}? [Y/n]: ").strip("﻿ \t\r\n").lower()
            if yn in ("", "y", "yes", "n", "no"):
                break
            print("  Please answer y or n.")
        if yn in ("n", "no"):
            fixed[name] = Decimal(0)
            continue
        left = max_fixed_total(amount, plan) - sum(fixed.values())
        while True:
            text = input(
                f"How much is your {noun}? ({currency}, Enter = {fmt_money(defaults[name])}): "
            ).strip()
            try:
                value = defaults[name] if not text else parse_amount(text, allow_zero=True)
            except ValueError as e:
                print(f"  {e}, try again.")
                continue
            if value > left:
                print(f"  That's more than the budget can cover (at most {fmt_money(left)} {currency}"
                      " after your other bills), try again.")
                continue
            fixed[name] = value
            break
    return fixed


def fmt_money(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_table(
    amount: Decimal,
    plan: dict,
    currency: str = "RON",
    rates: dict | None = None,
    fixed: dict[str, Decimal] | None = None,
) -> str:
    """Budget table in `currency`, plus converted columns for the other currencies if rates are known."""
    fixed = fixed or {}
    others = [c for c in CURRENCIES if c != currency] if rates else []

    def money_cells(value: Decimal) -> list[str]:
        return [fmt_money(value)] + [fmt_money(convert(value, currency, c, rates)) for c in others]

    rows = build_rows(amount, plan, fixed)
    headers = ("Category", "%", currency, *others)
    body = [(name, f"{pct:.1f}%", *money_cells(val)) for name, pct, val in rows]
    footer = ("Total", "100.0%", *money_cells(amount))

    cols = len(headers)
    widths = [max(len(r[i]) for r in [headers, *body, footer]) for i in range(cols)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells):
        parts = [cells[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(cells[1:], widths[1:])]
        return "| " + " | ".join(parts) + " |"

    title = f"\n{plan['label']}: {fmt_money(amount)} {currency}"
    if others:
        title += "  (= " + " / ".join(f"{m} {c}" for m, c in zip(money_cells(amount)[1:], others)) + ")"

    out = [title, sep, line(headers), sep, *(line(r) for r in body), sep, line(footer), sep]
    paid = [f"{n} {fmt_money(v)}" for n, v in fixed.items() if v > 0]
    unpaid = [n for n, v in fixed.items() if v == 0]
    if fixed:
        names = [c["name"] for c in flexible_categories(plan, fixed)]
        flex = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        if paid:
            out.append(f"Fixed costs taken off first: {', '.join(paid)} {currency}.")
        if unpaid:
            out.append(f"Not paying: {' and '.join(unpaid)}.")
        out.append(f"{flex} adjust to cover the difference.")
    if rates:
        out.append(describe(rates))
    else:
        out.append("Exchange rates unavailable right now, so only the original currency is shown.")
    return "\n".join(out)


def parse_amount(text: str, allow_zero: bool = False) -> Decimal:
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a valid amount")
    if not value.is_finite():
        raise ValueError(f"'{text}' is not a valid amount")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError("amount must be greater than 0" if not allow_zero else "amount can't be negative")
    return value.quantize(CENT, ROUND_HALF_UP)


HELP_WORDS = {"/help", "help", "/h", "/?", "?"}

USAGE = """\
Usage:
  budget                    pick an income type and currency, then enter the amount
  budget <type> <amount>    e.g. budget salary 3600   (uses the default currency, RON)
  budget <type> <amount> <currency>
                            e.g. budget freelancing 500 eur   (RON, EUR or USD)
  budget --list             show income types and how each one is split
  budget /help              show this help
  --no-etfs                 skip the ETF table (shown for types with investments)

Monthly salary asks whether you pay rent and utilities, and how much. Those amounts
are taken off first and the rest is split by the plan; if you don't pay one, its
money goes to the other categories. To skip the questions, pass them as options:
  --rent AMOUNT             e.g. --rent 1200   (0 = you don't pay rent)
  --utilities AMOUNT        e.g. --utilities 0

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


def parse_currency(text: str) -> str:
    cur = text.strip().upper()
    if cur not in CURRENCIES:
        raise ValueError(f"unknown currency '{text}'. Options: {', '.join(CURRENCIES)}")
    return cur


def ask_currency(default: str) -> str:
    options = "  ".join(f"{n}) {c}" for n, c in enumerate(CURRENCIES, 1))
    while True:
        choice = input(f"Currency of the amount? {options}  [Enter = {default}]: ").strip()
        if not choice:
            return default
        if choice.isdigit() and 1 <= int(choice) <= len(CURRENCIES):
            return CURRENCIES[int(choice) - 1]
        try:
            return parse_currency(choice)
        except ValueError as e:
            print(f"  {e}, try again.")


def ask_amount(currency: str) -> Decimal:
    while True:
        try:
            return parse_amount(input(f"Amount to budget ({currency}): "))
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
    config = load_config()
    plans = config["plans"]
    default_currency = config.get("default_currency", "RON").upper()

    parser = argparse.ArgumentParser(
        prog="budget",
        description="Split an income amount into your budget categories.",
        add_help=False,
    )
    parser.add_argument("type", nargs="?", help="income type (see --list)")
    parser.add_argument("amount", nargs="?", help="amount to budget, e.g. 3600")
    parser.add_argument("currency", nargs="?", help="currency of the amount: RON, EUR or USD")
    parser.add_argument("--list", action="store_true", help="list income types and exit")
    parser.add_argument("-h", "--help", action="store_true", help="show help and exit")
    parser.add_argument("--no-etfs", action="store_true", help="skip the ETF performance table")
    # One option per asked category across all plans, e.g. --rent 1200 (0 = don't pay it).
    flags = {}
    for plan in plans.values():
        for cat in asked_categories(plan):
            flags.setdefault(flag_name(cat), cat["name"])
    for flag in flags:
        parser.add_argument(f"--{flag}", metavar="AMOUNT", help=f"fixed {flag} amount, 0 if you don't pay it")
    args = parser.parse_args(argv)

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
            currency = ask_currency(default_currency)
            amount = ask_amount(currency)
        else:
            try:
                amount = parse_amount(args.amount)
                currency = parse_currency(args.currency) if args.currency else default_currency
            except ValueError as e:
                parser.error(str(e))

        # Fixed costs given as options (--rent 1200, --utilities 0); ask about the rest.
        plan = plans[key]
        known = {}
        for cat in asked_categories(plan):
            value = getattr(args, flag_name(cat).replace("-", "_"))
            if value is not None:
                try:
                    known[cat["name"]] = parse_amount(value, allow_zero=True)
                except ValueError as e:
                    parser.error(f"--{flag_name(cat)}: {e}")
        if known and sum(known.values()) > max_fixed_total(amount, plan):
            parser.error(f"fixed costs are more than the budget can cover "
                         f"(at most {fmt_money(max_fixed_total(amount, plan))} {currency})")
        if asked_categories(plan):
            fixed = ask_fixed_costs(amount, plan, currency, known)
        else:
            fixed = {}
    except (KeyboardInterrupt, EOFError):
        print()
        return 1

    print(render_table(amount, plans[key], currency, fetch_rates(), fixed))

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

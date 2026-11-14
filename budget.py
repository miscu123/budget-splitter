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
import os
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
    parts, _ = allocate(amount, plan, fixed)

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


def allocate(amount: Decimal, plan: dict, fixed: dict[str, Decimal]) -> tuple[dict, bool]:
    """Work out each category's amount. Returns (amounts by name, squeezed).

    Fixed amounts come first. Other non-flexible categories (e.g. Car fuel) keep
    their normal share and flexible ones share what's left. If fixed costs leave
    too little for that, flexible categories get 0 and the other non-flexible
    ones shrink to fit; `squeezed` is then True.
    """
    categories = plan["categories"]
    parts = dict(zip((c["name"] for c in categories), split_amount(amount, categories)))
    if not fixed:
        return parts, False

    flexible = flexible_categories(plan, fixed)
    steady = [c for c in categories if c["name"] not in fixed and c not in flexible]
    remaining = amount - sum(fixed.values())
    pool = remaining - sum(parts[c["name"]] for c in steady)
    squeezed = pool < 0
    if squeezed:
        parts.update((c["name"], Decimal("0.00")) for c in flexible)
        if steady:
            parts.update(zip((c["name"] for c in steady), split_amount(remaining, steady)))
    elif flexible:
        parts.update(zip((c["name"] for c in flexible), split_amount(pool, flexible)))
    parts.update(fixed)
    return parts, squeezed


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
            yn = input(f"Do you pay for {noun}? [Y/n]: ").strip("﻿ \t\r\n").lower()
            if yn in ("", "y", "yes", "n", "no"):
                break
            print("  Please answer y or n.")
        if yn in ("n", "no"):
            fixed[name] = Decimal(0)
            continue
        left = amount - sum(fixed.values())
        while True:
            text = input(
                f"How much do you pay for {noun}? ({currency}, Enter = {fmt_money(defaults[name])}): "
            ).strip()
            try:
                value = defaults[name] if not text else parse_amount(text, allow_zero=True)
            except ValueError as e:
                print(f"  {e}, try again.")
                continue
            if value > left:
                print(f"  That's more than what's left of your income ({fmt_money(left)} {currency}), try again.")
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
            out.append(f"Not paying: {', '.join(unpaid)}.")
        if allocate(amount, plan, fixed)[1]:
            steady = [n for n, _, _ in rows
                      if not n.startswith(" ") and n not in fixed and n not in names]
            shrunk = f"{', '.join(steady)} reduced to fit, and n" if steady else "N"
            out.append(f"{shrunk}othing is left for {flex}.")
        else:
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

TOOL_COMMANDS = {"etfs", "rates"}


def build_help(config: dict) -> str:
    """The full command reference shown by budget /help."""
    plans = config["plans"]
    keys = list(plans)
    cur = config.get("default_currency", "RON").upper()
    food = config.get("min_food")
    food_text = f"{food['amount']} {food.get('currency', cur)}" if food else "nothing"
    flags = []
    for plan in plans.values():
        for cat in asked_categories(plan):
            if flag_name(cat) not in flags:
                flags.append(flag_name(cat))

    def row(left: str, right: str = "") -> str:
        return f"  {left:<26}{right}".rstrip()

    lines = [
        "BUDGET SPLITTER - all commands",
        "",
        "COMMANDS",
        row("budget", "Interactive: choose income type, currency and amount"),
        row("budget <type> <amount>", f"Split an amount in {cur}, e.g. budget salary 3600"),
        row("budget <type> <amount> <currency>"),
        row("", "Split an amount in RON, EUR or USD, e.g. budget freelancing 500 eur"),
        row("budget --list", "Show the income types and each one's % split"),
        row("budget etfs", "Show only the ETF table (1-month performance)"),
        row("budget rates", "Show today's RON / EUR / USD exchange rates"),
        row("budget /help", "Show this help (also: help, -h, --help, /?)"),
        "",
        "INCOME TYPES (<type>)",
        *(row(k, plans[k]["label"]) for k in keys),
        "",
        "OPTIONS (add them to a budget command)",
        *(row(f"--{f} AMOUNT", f"Set your {f.replace('-', ' ')}, skip that question (0 = don't pay it)")
          for f in flags),
        row("--no-etfs", "Don't show the ETF table"),
        row("", "e.g. budget salary 3600 --rent 1200 --utilities 0 --no-etfs"),
        "",
        "AT THE PROMPTS",
        row("Income type", f"A number (1-{len(keys)}) or a name, e.g. salary"),
        row("", "/help shows every split, then the menu again"),
        row("Currency", f"1, 2, 3 or RON, EUR, USD (Enter = {cur})"),
        row("Do you pay for ...?", "y or n (Enter = yes)"),
        row("How much ...?", "The amount (Enter = the plan's usual amount)"),
        row("Ctrl+C", "Quit"),
        "",
        "WHAT YOU GET",
        row("Budget table", "Amount per category, also in the other two currencies"),
        row("", "(official BNR rates, or ECB rates as a backup)"),
        row("Fixed costs", "Monthly salary: rent, utilities and subscriptions come off"),
        row("", "first; Free spendings, Wishlist and Savings take the difference"),
        row("Warning", f"When those bills plus {food_text} for food exceed the income"),
        row("ETF table", "For types with investments: top 10 and bottom 3 of ~40"),
        row("", "popular ETFs by 1-month change (not investment advice)"),
        "",
        "SETTINGS (files in the budget-splitter folder)",
        row("plans.json", "Income types, categories and weights, default currency and"),
        row("", 'food minimum; "ask" and "flexible" mark the special categories'),
        row("etfs.json", "The ETF watchlist and how many to show"),
    ]
    return "\n".join(lines)


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


def print_help(config: dict) -> None:
    print(build_help(config))


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
        print_help(config)
        return 0

    if args.type and args.type.lower() in TOOL_COMMANDS:
        if args.type.lower() == "etfs":
            from etf import render_etf_table

            print(render_etf_table())
        else:
            rates = fetch_rates()
            print(describe(rates) if rates else "Couldn't load exchange rates (are you online?).")
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
        if sum(known.values()) > amount:
            parser.error(f"fixed costs ({fmt_money(sum(known.values()))} {currency}) "
                         f"are more than the whole amount ({fmt_money(amount)} {currency})")
        if asked_categories(plan):
            fixed = ask_fixed_costs(amount, plan, currency, known)
        else:
            fixed = {}
    except (KeyboardInterrupt, EOFError):
        print()
        return 1

    rates = fetch_rates()
    print(render_table(amount, plans[key], currency, rates, fixed))
    warning = affordability_warning(amount, fixed, currency, rates, config.get("min_food"))
    if warning:
        print(warning)

    if has_investments(plans[key]) and not args.no_etfs:
        from etf import render_etf_table

        print("\nLoading ETF data...", end="\r")
        print(render_etf_table())
    return 0


def affordability_warning(
    amount: Decimal,
    fixed: dict[str, Decimal],
    currency: str,
    rates: dict | None,
    min_food: dict | None,
) -> str | None:
    """Warn if the fixed costs plus a minimum for food are more than the income."""
    bills = sum(fixed.values())
    if not fixed or bills == 0:
        return None

    food = Decimal(0)
    if min_food:
        food = Decimal(str(min_food["amount"]))
        food_cur = min_food.get("currency", currency).upper()
        if food_cur != currency:
            # Without exchange rates we can't convert the food minimum; check bills alone.
            food = convert(food, food_cur, currency, rates) if rates else Decimal(0)
    needed = bills + food
    if amount >= needed:
        return None

    paid = [n.lower() for n, v in fixed.items() if v > 0]
    bills_text = paid[0] if len(paid) == 1 else ", ".join(paid[:-1]) + " and " + paid[-1]
    food_text = f" plus at least {fmt_money(food)} {currency} for food" if food else ""
    lines = [
        "",
        "WARNING: your expenses are too high for this income.",
        f"  Your {bills_text} ({fmt_money(bills)} {currency}){food_text} come to "
        f"{fmt_money(needed)} {currency},",
        f"  which is {fmt_money(needed - amount)} {currency} more than this income "
        f"({fmt_money(amount)} {currency}).",
        "  Consider:",
        "    - cheaper housing or utilities (a smaller place, a roommate, a better energy plan)",
        "    - cancelling or sharing subscriptions you don't really use",
        "    - a second job or extra income (freelancing, a side gig)",
    ]
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        if os.name == "nt":
            os.system("")  # turns on ANSI colour support in the Windows console
        lines[1] = f"\033[1;31m{lines[1]}\033[0m"
    return "\n".join(lines)


def has_investments(plan: dict) -> bool:
    """True if any category or sub-category of the plan is about investing."""
    names = [c["name"] for c in plan["categories"]]
    names += [s["name"] for c in plan["categories"] for s in c.get("split", [])]
    return any("invest" in n.lower() for n in names)


if __name__ == "__main__":
    sys.exit(main())

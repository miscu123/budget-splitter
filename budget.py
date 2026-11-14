#!/usr/bin/env python3
"""Budget splitter: split an income amount across categories based on its type.

Usage:
    python budget.py                    # interactive: pick a type and currency, enter an amount
    python budget.py salary 3600        # non-interactive (default currency)
    python budget.py salary 500 eur     # amount in EUR (RON, EUR or USD)
    python budget.py /help              # every command
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import goals
import history
import invest
import store
import ui
from rates import CURRENCIES, convert, describe, fetch_rates
from ui import CENT, HELP_WORDS, ask, confirm, fmt_money, grid, paint, parse_amount  # noqa: F401

PLANS_FILE = Path(__file__).with_name("plans.json")
BILLS_FILE = "bills.json"
TOOL_COMMANDS = {"etfs", "rates"}
DATA_COMMANDS = {"history", "goals", "invest", "bills"}

# Set in main() so /help typed at any prompt can show the full reference.
_CONFIG: dict = {}
_RATES: dict = {}


def load_config(path: Path = PLANS_FILE) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_plans(path: Path = PLANS_FILE) -> dict:
    return load_config(path)["plans"]


def get_rates() -> dict | None:
    """Exchange rates, fetched at most once per run."""
    if "value" not in _RATES:
        _RATES["value"] = fetch_rates()
    return _RATES["value"]


# ---------------------------------------------------------------- splitting


def split_amount(amount: Decimal, items: list[dict]) -> list[Decimal]:
    """Split `amount` proportionally to each item's weight, rounded to cents.

    Any rounding leftover goes to the last item so the parts always add up.
    """
    total_weight = sum(Decimal(str(i["weight"])) for i in items)
    parts = [(amount * Decimal(str(i["weight"])) / total_weight).quantize(CENT, ROUND_HALF_UP)
             for i in items]
    parts[-1] += amount - sum(parts)
    return parts


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


def build_rows(
    amount: Decimal, plan: dict, fixed: dict[str, Decimal] | None = None, redirect: bool = False
) -> list[tuple[str, Decimal, Decimal]]:
    """Return (label, percent, amount) rows, with sub-splits indented under their parent.

    `fixed` maps category names to a set amount (0 = not paid, row left out).
    `redirect` moves the Investments sub-split into Savings (emergency fund first).
    """
    fixed = fixed or {}
    parts, _ = allocate(amount, plan, fixed)

    rows = []
    for cat in plan["categories"]:
        part = parts[cat["name"]]
        if cat["name"] in fixed and part == 0:
            continue
        rows.append((cat["name"], part / amount * 100, part))
        if cat.get("split"):
            subs = dict(zip((s["name"] for s in cat["split"]), split_amount(part, cat["split"])))
            if redirect:
                saving = next((n for n in subs if "saving" in n.lower()), None)
                for n in subs:
                    if saving and "invest" in n.lower():
                        subs[saving] += subs[n]
                        subs[n] = Decimal("0.00")
            for name, sub_part in subs.items():
                rows.append((f"  - {name}", sub_part / amount * 100, sub_part))
    return rows


def leaf_items(rows: list[tuple[str, Decimal, Decimal]]) -> list[dict]:
    """The rows money really ends up in: sub-splits instead of their parent."""
    items = []
    for i, (label, _, value) in enumerate(rows):
        is_sub = label.startswith("  - ")
        has_subs = i + 1 < len(rows) and rows[i + 1][0].startswith("  - ")
        if is_sub:
            items.append({"category": label[4:], "group": parent, "amount": value})
        else:
            parent = label
            if not has_subs:
                items.append({"category": label, "group": label, "amount": value})
    return items


def has_investments(plan: dict) -> bool:
    """True if any category or sub-category of the plan is about investing."""
    names = [c["name"] for c in plan["categories"]]
    names += [s["name"] for c in plan["categories"] for s in c.get("split", [])]
    return any("invest" in n.lower() for n in names)


# ---------------------------------------------------------------- fixed costs


def asked_categories(plan: dict) -> list[dict]:
    """Categories the user is asked about (do you pay it, and how much)."""
    return [c for c in plan["categories"] if c.get("ask")]


def flag_name(category: dict) -> str:
    return category["name"].lower().replace(" ", "-")


def remembered_bills(currency: str) -> dict[str, Decimal] | None:
    """The bills saved last time, converted to `currency` (None if none or not convertible)."""
    saved = store.load(BILLS_FILE, None)
    if not saved or not saved.get("bills"):
        return None
    bills = {k: Decimal(v) for k, v in saved["bills"].items()}
    if saved["currency"] != currency:
        rates = get_rates()
        if not rates:
            return None
        bills = {k: convert(v, saved["currency"], currency, rates) for k, v in bills.items()}
    return bills


def describe_bills(bills: dict[str, Decimal], currency: str) -> str:
    return ", ".join(f"{k} {fmt_money(v) if v else 'not paying'}" for k, v in bills.items()) + f" {currency}"


def ask_fixed_costs(
    amount: Decimal, plan: dict, currency: str, known: dict[str, Decimal],
    remembered: dict[str, Decimal] | None = None,
) -> dict[str, Decimal]:
    """Ask, for each asked category not already in `known`, whether it's paid and how much."""
    fixed = dict(known)
    names = [c["name"] for c in asked_categories(plan)]
    missing = [n for n in names if n not in fixed]
    remembered = {k: v for k, v in (remembered or {}).items() if k in names}

    if missing and remembered and all(n in remembered for n in missing):
        offer = {n: remembered[n] for n in missing}
        if sum(fixed.values()) + sum(offer.values()) > amount:
            print(f"Your saved bills ({describe_bills(offer, currency)}) are more than this amount.")
        elif confirm(f"Same bills as last time? {describe_bills(offer, currency)}"):
            fixed.update(offer)
            return fixed

    defaults = dict(zip((c["name"] for c in plan["categories"]),
                        split_amount(amount, plan["categories"])))
    defaults.update(remembered)
    for name in missing:
        noun = name.lower()
        if not confirm(f"Do you pay for {noun}?"):
            fixed[name] = Decimal(0)
            continue
        left = amount - sum(fixed.values())
        while True:
            value = ui.ask_amount(
                f"How much do you pay for {noun}? ({currency}, Enter = {fmt_money(defaults[name])}): ",
                default=defaults[name], allow_zero=True,
            )
            if value > left:
                print(f"  That's more than what's left of your income ({fmt_money(left)} {currency}), try again.")
                continue
            fixed[name] = value
            break
    return fixed


def save_bills(fixed: dict[str, Decimal], currency: str) -> None:
    store.save(BILLS_FILE, {"currency": currency, "bills": {k: str(v) for k, v in fixed.items()}})


def bills_command(args: list[str]) -> int:
    saved = store.load(BILLS_FILE, None)
    if args and args[0].lower() == "clear":
        if not saved:
            print("No bills are saved.")
        elif confirm("Forget your saved bills?", default=False):
            store.remove(BILLS_FILE)
            print("Forgot your saved bills. You'll be asked again next time.")
        return 0
    if not saved:
        print("No bills saved yet. They're saved the first time you budget a monthly salary.")
    else:
        bills = {k: Decimal(v) for k, v in saved["bills"].items()}
        print("Your saved bills: " + describe_bills(bills, saved["currency"]))
    print("\nBills:\n  budget bills               show your saved bills\n"
          "  budget bills clear         forget them (you'll be asked again)")
    return 0


# ---------------------------------------------------------------- output


def render_table(
    amount: Decimal,
    plan: dict,
    currency: str = "RON",
    rates: dict | None = None,
    fixed: dict[str, Decimal] | None = None,
    redirect: bool = False,
) -> str:
    """Budget table in `currency`, plus converted columns for the other currencies if rates are known."""
    fixed = fixed or {}
    others = [c for c in CURRENCIES if c != currency] if rates else []

    def money_cells(value: Decimal) -> list[str]:
        return [fmt_money(value)] + [fmt_money(convert(value, currency, c, rates)) for c in others]

    rows = build_rows(amount, plan, fixed, redirect)
    body = [[name, f"{pct:.1f}%", *money_cells(val)] for name, pct, val in rows]
    table = grid(["Category", "%", currency, *others], body, "l" + "r" * (2 + len(others)),
                 footer=["Total", "100.0%", *money_cells(amount)])

    title = f"\n{plan['label']}: {fmt_money(amount)} {currency}"
    if others:
        title += "  (= " + " / ".join(f"{m} {c}" for m, c in zip(money_cells(amount)[1:], others)) + ")"

    out = [title, *table]
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
    if redirect:
        out.append("Investments go to Savings until your emergency fund is full ('budget goals').")
    if rates:
        out.append(describe(rates))
    else:
        out.append("Exchange rates unavailable right now, so only the original currency is shown.")
    return "\n".join(out)


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
    return "\n".join([
        "",
        paint("WARNING: your expenses are too high for this income.", "1;31"),
        f"  Your {bills_text} ({fmt_money(bills)} {currency}){food_text} come to "
        f"{fmt_money(needed)} {currency},",
        f"  which is {fmt_money(needed - amount)} {currency} more than this income "
        f"({fmt_money(amount)} {currency}).",
        "  Consider:",
        "    - cheaper housing or utilities (a smaller place, a roommate, a better energy plan)",
        "    - cancelling or sharing subscriptions you don't really use",
        "    - a second job or extra income (freelancing, a side gig)",
    ])


def save_to_history(date: dt.date, key: str, plan: dict, amount: Decimal, currency: str,
                    items: list[dict], rates: dict | None) -> int | None:
    if currency != "RON" and not rates:
        print("Can't save to history right now: exchange rates are needed to record it in RON.")
        return None

    def ron(value: Decimal) -> Decimal:
        return value if currency == "RON" else convert(value, currency, "RON", rates)

    stored = [{**i, "amount_ron": ron(i["amount"])} for i in items]
    # Make the RON items add up to the RON total exactly.
    total_ron = ron(amount)
    if stored:
        stored[-1]["amount_ron"] += total_ron - sum(i["amount_ron"] for i in stored)
    return store.add_entry(date, key, plan["label"], currency, amount, total_ron, stored)


# ---------------------------------------------------------------- prompts and help


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
        return f"  {left:<28}{right}".rstrip()

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
        "YOUR DATA (kept in the data folder on this computer, never uploaded)",
        row("budget history", "This year's saved budgets and a yearly summary"),
        row("budget history 2025 | all", "A specific year, or every year"),
        row("budget history delete <#>", "Delete one entry (e.g. a mistake)"),
        row("budget history clear", "Delete the whole history"),
        row("budget goals", "Your savings goals, progress and time left"),
        row("budget goals add", "Add a goal paid from Savings or Wishlist money"),
        row("budget goals emergency", "Set up an emergency fund (3-6 months of essentials)"),
        row("budget goals delete <#>", "Delete a goal"),
        row("budget invest", "Your ETF mix, with today's prices"),
        row("budget invest set", "Choose your ETFs and the % for each"),
        row("budget invest clear", "Delete your ETF mix"),
        row("budget bills", "Your saved rent, utilities and subscriptions"),
        row("budget bills clear", "Forget them (you'll be asked again)"),
        "",
        "INCOME TYPES (<type>)",
        *(row(k, plans[k]["label"]) for k in keys),
        "",
        "OPTIONS (add them to a budget command)",
        *(row(f"--{f} AMOUNT", f"Set your {f.replace('-', ' ')}, skip that question (0 = don't pay it)")
          for f in flags),
        row("--save / --no-save", "Save to history (or not) without asking"),
        row("--date YYYY-MM-DD", "Date to save it under (default: today)"),
        row("--no-etfs", "Don't show the ETF table"),
        row("", "e.g. budget salary 3600 --rent 1200 --utilities 0 --save"),
        "",
        "AT THE PROMPTS",
        row("/help", "Works at any prompt: shows this help, then asks again"),
        row("/list", "Works at any prompt: shows each income type's % split"),
        row("Income type", f"A number (1-{len(keys)}) or a name, e.g. salary"),
        row("Currency", f"1, 2, 3 or RON, EUR, USD (Enter = {cur})"),
        row("Same bills as last time?", "y = reuse your saved bills, n = answer the questions"),
        row("Do you pay for ...?", "y or n (Enter = yes)"),
        row("How much ...?", "The amount (Enter = last time's, or the plan's usual)"),
        row("Save ... to your history?", "y = record it for history, summary and goals"),
        row("Ctrl+C", "Quit"),
        "",
        "WHAT YOU GET",
        row("Budget table", "Amount per category, also in the other two currencies"),
        row("", "(official BNR rates, or ECB rates as a backup)"),
        row("Fixed costs", "Monthly salary: rent, utilities and subscriptions come off"),
        row("", "first; Free spendings, Wishlist and Savings take the difference"),
        row("Warning", f"When those bills plus {food_text} for food exceed the income"),
        row("Investment plan", "What to buy of each ETF in your mix, about how many shares"),
        row("Goals", "Progress of your savings goals after each budget"),
        row("ETF table", "For types with investments: top 10 and bottom 3 of ~40"),
        row("", "popular ETFs by 1-month change (not investment advice)"),
        "",
        "SETTINGS (files in the budget-splitter folder)",
        row("plans.json", "Income types, categories and weights, default currency and"),
        row("", 'food minimum; "ask" and "flexible" mark the special categories'),
        row("etfs.json", "The ETF watchlist and how many to show"),
        row("data/", "Your bills, history, goals and ETF mix (not on GitHub)"),
    ]
    return "\n".join(lines)


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


def choose_plan(plans: dict) -> str:
    keys = list(plans)

    def show_menu():
        print("What type of income is it?  (type /help for all commands, /list for the splits)")
        for n, key in enumerate(keys, 1):
            print(f"  {n}) {plans[key]['label']}")

    show_menu()
    while True:
        choice = ask("Choose a number or name: ", after_help=show_menu).lower()
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
        choice = ask(f"Currency of the amount? {options}  [Enter = {default}]: ")
        if not choice:
            return default
        if choice.isdigit() and 1 <= int(choice) <= len(CURRENCIES):
            return CURRENCIES[int(choice) - 1]
        try:
            return parse_currency(choice)
        except ValueError as e:
            print(f"  {e}, try again.")


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    config = load_config()
    _CONFIG.update(config)
    ui.on_help = lambda: print("\n" + build_help(_CONFIG) + "\n")
    ui.on_list = lambda: list_plans(_CONFIG["plans"])
    plans = config["plans"]
    default_currency = config.get("default_currency", "RON").upper()

    command = argv[0].lower() if argv else ""
    try:
        if command == "history":
            return history.run(argv[1:])
        if command == "goals":
            return goals.run(argv[1:], config)
        if command == "invest":
            return invest.run(argv[1:])
        if command == "bills":
            return bills_command(argv[1:])
    except (KeyboardInterrupt, EOFError):
        print()
        return 1

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
    save_group = parser.add_mutually_exclusive_group()
    save_group.add_argument("--save", action="store_true", help="save to history without asking")
    save_group.add_argument("--no-save", action="store_true", help="don't save to history")
    parser.add_argument("--date", help="date to save the budget under, YYYY-MM-DD")
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
            rates = get_rates()
            print(describe(rates) if rates else "Couldn't load exchange rates (are you online?).")
        return 0

    if args.list:
        list_plans(plans)
        return 0

    try:
        date = dt.date.fromisoformat(args.date) if args.date else store.today()
    except ValueError:
        parser.error(f"--date: '{args.date}' is not a date like 2026-11-30")

    try:
        if args.type is None:
            key = choose_plan(plans)
        elif args.type.lower() in plans:
            key = args.type.lower()
        else:
            parser.error(f"unknown income type '{args.type}'. Options: {', '.join(plans)}")

        if args.amount is None:
            currency = ask_currency(default_currency)
            amount = ui.ask_amount(f"Amount to budget ({currency}): ")
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
            remembered = remembered_bills(currency)
            fixed = ask_fixed_costs(amount, plan, currency, known, remembered)
            if fixed != remembered:  # unchanged bills keep their original currency (no rounding drift)
                save_bills(fixed, currency)
        else:
            fixed = {}

        rates = get_rates()
        redirect = has_investments(plan) and goals.emergency_redirect_active()
        rows = build_rows(amount, plan, fixed, redirect)
        items = leaf_items(rows)
        print(render_table(amount, plan, currency, rates, fixed, redirect))
        warning = affordability_warning(amount, fixed, currency, rates, config.get("min_food"))
        if warning:
            print(warning)

        invest_amount = sum((i["amount"] for i in items if "invest" in i["category"].lower()), Decimal(0))
        if invest_amount > 0:
            print("\n".join(invest.render_plan(invest_amount, currency, rates) or invest.hint()))

        print()
        if args.save or (not args.no_save and confirm("Save this budget to your history?")):
            entry_id = save_to_history(date, key, plan, amount, currency, items, rates)
            if entry_id:
                print(f"Saved as entry #{entry_id} ({date}). See it with 'budget history'.")

        if goals.load_goals() and any(i["category"] in goals.SOURCES for i in items):
            print("\n".join(goals.render_progress()))
    except (KeyboardInterrupt, EOFError):
        print()
        return 1

    if has_investments(plan) and not args.no_etfs:
        from etf import render_etf_table

        print("\nLoading ETF data...", end="\r")
        print(render_etf_table())
    return 0


if __name__ == "__main__":
    sys.exit(main())

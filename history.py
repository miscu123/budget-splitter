"""History of saved budgets: list, yearly summary, delete an entry, clear all."""

from __future__ import annotations

from decimal import Decimal

import store
from ui import ask, confirm, fmt_money, grid

USAGE = """\
History:
  budget history             this year's budgets and a summary
  budget history 2025        a specific year
  budget history all         every year
  budget history delete <#>  delete one entry (e.g. a mistake)
  budget history clear       delete the whole history"""


def show(year: str = "") -> None:
    entries = store.entries()
    if year == "all":
        chosen, period = entries, "all years"
    else:
        y = int(year) if year else store.today().year
        chosen, period = [e for e in entries if e["date"].year == y], str(y)
    if not chosen:
        print(f"No saved budgets for {period}." if entries else "Your history is empty.")
        print("Budgets are saved when you answer yes to 'Save this budget to your history?'.")
        return

    rows = [[str(e["id"]), e["date"].isoformat(), e["label"],
             f"{fmt_money(e['amount'])} {e['currency']}", fmt_money(e["amount_ron"])] for e in chosen]
    print(f"\nHistory, {period} ({len(chosen)} entr{'y' if len(chosen) == 1 else 'ies'}):")
    print("\n".join(grid(["#", "Date", "Type", "Amount", "In RON"], rows, "rllrr")))

    totals: dict[str, Decimal] = {}
    for e in chosen:
        for item in e["items"]:
            totals[item["category"]] = totals.get(item["category"], Decimal(0)) + item["amount_ron"]
    income = sum((e["amount_ron"] for e in chosen), Decimal(0))
    rows = [[name, fmt_money(value), f"{value / income * 100:.1f}%" if income else "-"]
            for name, value in totals.items()]
    print(f"\nSummary, {period} (RON):")
    print("\n".join(grid(["Category", "Total", "% of income"], rows, "lrr",
                         footer=["Income", fmt_money(income), "100.0%"])))

    def total(name: str) -> str:
        return fmt_money(totals.get(name, Decimal(0)))

    lead = "This year" if period == str(store.today().year) else f"In {period}"
    print(f"{lead}: {fmt_money(income)} RON income, {total('Savings')} saved, "
          f"{total('Investments')} invested, {total('Wishlist')} on wishlist.")


def delete(number: str) -> None:
    entries = {e["id"]: e for e in store.entries()}
    if not number:
        number = ask("Which entry number? (see 'budget history'): ")
    try:
        entry = entries[int(number)]
    except (ValueError, KeyError):
        print(f"There's no entry #{number}. Run 'budget history' to see the numbers.")
        return
    desc = f"#{entry['id']} ({entry['date']}, {entry['label']}, {fmt_money(entry['amount'])} {entry['currency']})"
    if confirm(f"Delete entry {desc}?", default=False):
        store.delete_entry(entry["id"])
        print(f"Deleted entry {desc}.")


def clear() -> None:
    count = len(store.entries())
    if not count:
        print("Your history is already empty.")
        return
    answer = ask(f"This deletes all {count} saved budgets and can't be undone. Type 'yes' to confirm: ")
    if answer.lower() == "yes":
        store.clear_history()
        print(f"Deleted all {count} entries.")
    else:
        print("Nothing deleted.")


def run(args: list[str]) -> int:
    action = args[0].lower() if args else ""
    if action in ("", "all") or action.isdigit():
        show(action)
    elif action in ("delete", "remove"):
        delete(args[1] if len(args) > 1 else "")
    elif action == "clear":
        clear()
    else:
        print(USAGE)
        return 2
    if action in ("", "all") or action.isdigit():
        print("\n" + USAGE)
    return 0

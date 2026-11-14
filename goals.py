"""Savings goals: targets funded from the Savings or Wishlist money in your history.

Each saved budget's Savings (or Wishlist) amount fills the goals that draw from
it, in order: the emergency fund first, then the others in the order you added
them. Amounts are kept in RON.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import store
from rates import convert, fetch_rates
from ui import ask, ask_amount, confirm, fmt_money, grid

FILE = "goals.json"
SOURCES = ("Savings", "Wishlist")
EMERGENCY_NAME = "Emergency fund"
RATE_WINDOW_DAYS = 90


def load_goals() -> list[dict]:
    return store.load(FILE, {"goals": []})["goals"]


def save_goals(goals: list[dict]) -> None:
    store.save(FILE, {"goals": goals})


def queue(goals: list[dict]) -> list[dict]:
    """Funding order: the emergency fund first, then the rest by id."""
    return sorted(goals, key=lambda g: (not g.get("emergency"), g["id"]))


def progress(goals: list[dict] | None = None, entries: list[dict] | None = None) -> list[dict]:
    """Each goal with saved, target, remaining and months_left (None = unknown, 0 = done)."""
    goals = load_goals() if goals is None else goals
    entries = store.entries() if entries is None else entries
    ordered = queue(goals)
    saved = {g["id"]: Decimal(g.get("saved_before", "0")) for g in ordered}

    for entry in entries:
        for item in entry["items"]:
            money = item["amount_ron"]
            for g in ordered:
                if money <= 0:
                    break
                if g["source"] != item["category"] or dt.date.fromisoformat(g["created"]) > entry["date"]:
                    continue
                take = min(Decimal(g["target"]) - saved[g["id"]], money)
                if take > 0:
                    saved[g["id"]] += take
                    money -= take

    # Monthly rate per source: recent contributions / number of months they cover.
    since = store.today() - dt.timedelta(days=RATE_WINDOW_DAYS)
    monthly = {}
    for source in SOURCES:
        recent = [(e["date"], i["amount_ron"]) for e in entries if e["date"] > since
                  for i in e["items"] if i["category"] == source]
        months = {(d.year, d.month) for d, _ in recent}
        monthly[source] = sum(a for _, a in recent) / len(months) if months else Decimal(0)

    results, ahead = [], {s: Decimal(0) for s in SOURCES}
    for g in ordered:
        target = Decimal(g["target"])
        have = min(saved[g["id"]], target)
        remaining = target - have
        ahead[g["source"]] = ahead.get(g["source"], Decimal(0)) + remaining
        rate = monthly.get(g["source"], Decimal(0))
        if remaining == 0:
            months_left = 0
        elif rate > 0:
            months_left = math.ceil(ahead[g["source"]] / rate)
        else:
            months_left = None
        results.append({**g, "saved": have, "target_d": target, "remaining": remaining,
                        "months_left": months_left})
    return results


def emergency_redirect_active() -> bool:
    """True while an emergency fund that asked for it isn't full yet."""
    return any(g.get("emergency") and g.get("redirect_investments") and g["remaining"] > 0
               for g in progress())


def bar(fraction: Decimal, width: int = 10) -> str:
    filled = min(width, int(fraction * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def render_progress(results: list[dict] | None = None, title: str = "Your savings goals") -> list[str]:
    results = progress() if results is None else results
    if not results:
        return []
    rows = []
    for n, g in enumerate(results, 1):
        pct = g["saved"] / g["target_d"] if g["target_d"] else Decimal(1)
        if g["months_left"] == 0:
            when = "done!"
        elif g["months_left"] is None:
            when = f"no recent {g['source'].lower()} yet"
        else:
            m = g["months_left"]
            when = f"about {m} month{'s' if m != 1 else ''} to go"
        rows.append([str(n), g["name"] + (" *" if g.get("emergency") else ""), g["source"],
                     fmt_money(g["saved"]), fmt_money(g["target_d"]),
                     f"{bar(pct)} {pct * 100:3.0f}%", when])
    out = ["", f"{title} (RON):"]
    out += grid(["#", "Goal", "From", "Saved", "Target", "Progress", "Time left"], rows, "rllrrll")
    notes = ["Time left assumes you keep saving at your recent monthly rate."]
    if any(g.get("emergency") for g in results):
        notes.insert(0, "* The emergency fund is filled first.")
    if any(g.get("emergency") and g.get("redirect_investments") and g["remaining"] > 0 for g in results):
        notes.append("Investments are paused (sent to Savings) until the emergency fund is full.")
    return out + notes


# ---------------------------------------------------------------- commands

USAGE = """\
Savings goals:
  budget goals               show your goals and progress
  budget goals add           add a goal (e.g. a laptop from Wishlist money)
  budget goals emergency     set up an emergency fund (3-6 months of bills and food)
  budget goals delete <#>    delete a goal"""


def _ask_source() -> str:
    while True:
        choice = ask("Paid from which money? 1) Savings  2) Wishlist  [Enter = 1]: ").lower()
        if choice in ("", "1", "savings"):
            return "Savings"
        if choice in ("2", "wishlist"):
            return "Wishlist"
        print("  Please choose 1 or 2.")


def _next_id(goals: list[dict]) -> int:
    return max((g["id"] for g in goals), default=0) + 1


def add_goal() -> None:
    goals = load_goals()
    while True:
        name = ask("Goal name (e.g. New laptop): ")
        if name:
            break
        print("  Please type a name.")
    target = ask_amount("Target amount (RON): ")
    source = _ask_source()
    before = ask_amount("Already saved toward it? (RON, Enter = 0): ", default=Decimal(0), allow_zero=True)
    goals.append({"id": _next_id(goals), "name": name, "target": str(target), "source": source,
                  "saved_before": str(min(before, target)), "created": store.today().isoformat()})
    save_goals(goals)
    print(f"Added goal '{name}': {fmt_money(target)} RON from {source}.")
    print("\n".join(render_progress()))


def _monthly_essentials(config: dict) -> Decimal | None:
    """Saved bills + food minimum per month, in RON (None if bills aren't saved)."""
    saved = store.load("bills.json", None)
    if not saved:
        return None
    total = sum((Decimal(v) for v in saved["bills"].values()), Decimal(0))
    food = config.get("min_food") or {}
    food_amount = Decimal(str(food.get("amount", 0)))
    food_cur = food.get("currency", "RON").upper()
    rates = None
    if saved["currency"] != "RON" or food_cur != "RON":
        rates = fetch_rates()
        if not rates:
            return None
    if saved["currency"] != "RON":
        total = convert(total, saved["currency"], "RON", rates)
    if food_cur != "RON":
        food_amount = convert(food_amount, food_cur, "RON", rates)
    return total + food_amount


def setup_emergency(config: dict) -> None:
    goals = load_goals()
    existing = next((g for g in goals if g.get("emergency")), None)
    essentials = _monthly_essentials(config)
    if essentials:
        print(f"Your monthly essentials (saved bills + food): {fmt_money(essentials)} RON")
        if not confirm("Use that?"):
            essentials = None
    if not essentials:
        essentials = ask_amount("Monthly essentials: rent + utilities + subscriptions + food (RON): ")
    months = None
    while months is None:
        text = ask("How many months should it cover? (3-6 is common, Enter = 6): ")
        try:
            months = 6 if not text else int(text)
            if not 1 <= months <= 24:
                raise ValueError
        except ValueError:
            months = None
            print("  Please enter a whole number of months (1-24).")
    target = (essentials * months).quantize(Decimal("0.01"))
    redirect = confirm("Until it's full, also put your Investments money into Savings?", default=False)

    if existing:
        existing.update(target=str(target), months=months, redirect_investments=redirect)
        print(f"Updated your emergency fund: {months} months x {fmt_money(essentials)} = {fmt_money(target)} RON.")
    else:
        before = ask_amount("Already saved toward it? (RON, Enter = 0): ", default=Decimal(0), allow_zero=True)
        goals.append({"id": _next_id(goals), "name": EMERGENCY_NAME, "target": str(target),
                      "source": "Savings", "saved_before": str(min(before, target)),
                      "created": store.today().isoformat(), "emergency": True, "months": months,
                      "redirect_investments": redirect})
        print(f"Added an emergency fund: {months} months x {fmt_money(essentials)} = {fmt_money(target)} RON.")
    save_goals(goals)
    print("\n".join(render_progress()))


def delete_goal(number: str) -> None:
    results = progress()
    try:
        goal = results[int(number) - 1]
        if int(number) < 1:
            raise IndexError
    except (ValueError, IndexError):
        print(f"There's no goal #{number}. Run 'budget goals' to see the numbers.")
        return
    if confirm(f"Delete goal #{number} '{goal['name']}'?", default=False):
        save_goals([g for g in load_goals() if g["id"] != goal["id"]])
        print(f"Deleted '{goal['name']}'.")


def run(args: list[str], config: dict) -> int:
    action = args[0].lower() if args else ""
    if action == "":
        lines = render_progress()
        print("\n".join(lines) if lines else "You have no savings goals yet.")
        print("\n" + USAGE)
    elif action == "add":
        add_goal()
    elif action == "emergency":
        setup_emergency(config)
    elif action in ("delete", "remove") and len(args) > 1:
        delete_goal(args[1])
    else:
        print(USAGE)
        return 2
    return 0

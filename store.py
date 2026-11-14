"""Your personal data: remembered bills, history, goals and investment plan.

Everything lives in the `data` folder next to the script (git ignores it, so it
never ends up on GitHub). Set BUDGET_DATA_DIR to use another folder.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(os.environ.get("BUDGET_DATA_DIR") or Path(__file__).resolve().with_name("data"))
HISTORY_FILE = "history.csv"
FIELDS = ["id", "date", "type", "label", "currency", "category", "group", "amount", "amount_ron"]
INCOME = "Income"


def today() -> dt.date:
    """Today's date (BUDGET_TODAY=YYYY-MM-DD overrides it, for testing)."""
    override = os.environ.get("BUDGET_TODAY")
    return dt.date.fromisoformat(override) if override else dt.date.today()


def load(name: str, default):
    path = DATA_DIR / name
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save(name: str, data) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def remove(name: str) -> bool:
    path = DATA_DIR / name
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------- history


def _read_rows() -> list[dict]:
    path = DATA_DIR / HISTORY_FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / HISTORY_FILE
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def entries() -> list[dict]:
    """History entries, oldest first. Each has id, date, type, label, currency,
    amount, amount_ron and items: [{category, group, amount, amount_ron}]."""
    by_id: dict[int, dict] = {}
    for row in _read_rows():
        eid = int(row["id"])
        entry = by_id.setdefault(eid, {
            "id": eid,
            "date": dt.date.fromisoformat(row["date"]),
            "type": row["type"],
            "label": row["label"],
            "currency": row["currency"],
            "items": [],
        })
        amount, amount_ron = Decimal(row["amount"]), Decimal(row["amount_ron"])
        if row["category"] == INCOME:
            entry["amount"], entry["amount_ron"] = amount, amount_ron
        else:
            entry["items"].append({"category": row["category"], "group": row["group"],
                                   "amount": amount, "amount_ron": amount_ron})
    return sorted(by_id.values(), key=lambda e: (e["date"], e["id"]))


def add_entry(date: dt.date, type_: str, label: str, currency: str,
              amount: Decimal, amount_ron: Decimal, items: list[dict]) -> int:
    rows = _read_rows()
    new_id = max((int(r["id"]) for r in rows), default=0) + 1
    base = {"id": new_id, "date": date.isoformat(), "type": type_, "label": label, "currency": currency}
    rows.append({**base, "category": INCOME, "group": INCOME,
                 "amount": str(amount), "amount_ron": str(amount_ron)})
    for item in items:
        rows.append({**base, "category": item["category"], "group": item["group"],
                     "amount": str(item["amount"]), "amount_ron": str(item["amount_ron"])})
    _write_rows(rows)
    return new_id


def delete_entry(entry_id: int) -> bool:
    rows = _read_rows()
    kept = [r for r in rows if int(r["id"]) != entry_id]
    if len(kept) == len(rows):
        return False
    _write_rows(kept)
    return True


def clear_history() -> int:
    count = len(entries())
    remove(HISTORY_FILE)
    return count

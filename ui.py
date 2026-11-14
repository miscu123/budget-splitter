"""Small shared helpers for prompts, money formatting and text tables."""

from __future__ import annotations

import os
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
HELP_WORDS = {"/help", "help", "/h", "/?", "?"}
LIST_WORDS = {"/list", "list", "--list"}

# budget.py sets these so /help and /list work at every prompt.
on_help = None
on_list = None


def fmt_money(value: Decimal) -> str:
    return f"{value:,.2f}"


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


def ask(prompt: str, after_help=None) -> str:
    """input() that answers /help and /list at any prompt, then asks again."""
    while True:
        text = input(prompt).strip("﻿ \t\r\n")
        word = text.lower()
        if word in HELP_WORDS and on_help:
            on_help()
        elif word in LIST_WORDS and on_list:
            on_list()
        else:
            return text
        if after_help:
            after_help()


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = ask(f"{prompt} {hint}: ").lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer y or n.")


def ask_amount(prompt: str, default: Decimal | None = None, allow_zero: bool = False) -> Decimal:
    while True:
        text = ask(prompt)
        if not text and default is not None:
            return default
        try:
            return parse_amount(text, allow_zero=allow_zero)
        except ValueError as e:
            print(f"  {e}, try again.")


def use_color() -> bool:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        os.system("")  # turns on ANSI colour support in the Windows console
    return True


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if use_color() else text


def grid(headers: list[str], rows: list[list[str]], align: str, footer: list[str] | None = None) -> list[str]:
    """A +---+ text table. `align` has one letter per column: l(eft) or r(ight)."""
    all_rows = [headers, *rows] + ([footer] if footer else [])
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells):
        parts = [c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(cells, widths, align)]
        return "| " + " | ".join(parts) + " |"

    out = [sep, line(headers), sep, *(line(r) for r in rows), sep]
    if footer:
        out += [line(footer), sep]
    return out

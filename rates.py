"""Exchange rates between RON, EUR and USD.

Primary source: the official daily reference rates of the National Bank of
Romania (BNR). Fallback: Frankfurter (European Central Bank rates). Neither
needs an API key. If both fail, fetch_rates() returns None.
"""

from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

CURRENCIES = ("RON", "EUR", "USD")
BNR_URL = "https://curs.bnr.ro/nbrfxrates.xml"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=RON,USD"
TIMEOUT = 10
CENT = Decimal("0.01")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _from_bnr() -> dict:
    root = ET.fromstring(_get(BNR_URL))
    ns = {"b": root.tag.split("}")[0].strip("{")}
    cube = root.find(".//b:Cube", ns)
    ron_per = {"RON": Decimal(1)}
    for rate in cube.findall("b:Rate", ns):
        cur = rate.get("currency")
        if cur in CURRENCIES:
            ron_per[cur] = Decimal(rate.text) / Decimal(rate.get("multiplier", "1"))
    return {"ron_per": ron_per, "date": cube.get("date"), "source": "BNR (National Bank of Romania)"}


def _from_frankfurter() -> dict:
    data = json.loads(_get(FRANKFURTER_URL))
    ron_per_eur = Decimal(str(data["rates"]["RON"]))
    usd_per_eur = Decimal(str(data["rates"]["USD"]))
    ron_per = {"RON": Decimal(1), "EUR": ron_per_eur, "USD": ron_per_eur / usd_per_eur}
    return {"ron_per": ron_per, "date": data["date"], "source": "ECB via Frankfurter"}


def fetch_rates() -> dict | None:
    """Return {'ron_per': {cur: RON for 1 unit}, 'date', 'source'} or None."""
    for source in (_from_bnr, _from_frankfurter):
        try:
            rates = source()
            if all(c in rates["ron_per"] for c in CURRENCIES):
                return rates
        except Exception:
            continue
    return None


def convert(amount: Decimal, src: str, dst: str, rates: dict) -> Decimal:
    ron_per = rates["ron_per"]
    return (amount * ron_per[src] / ron_per[dst]).quantize(CENT, ROUND_HALF_UP)


def describe(rates: dict) -> str:
    r = rates["ron_per"]
    eur_usd = r["EUR"] / r["USD"]
    return (
        f"Rates from {rates['source']}, {rates['date']}: "
        f"1 EUR = {r['EUR']:.4f} RON, 1 USD = {r['USD']:.4f} RON, 1 EUR = {eur_usd:.4f} USD"
    )


if __name__ == "__main__":
    rates = fetch_rates()
    print(describe(rates) if rates else "Couldn't load exchange rates (are you online?).")

# Budget Splitter

A small command-line tool that splits an income amount across budget categories, based on the type of income (salary, freelancing, gift, …).

Requires Python 3.9+. No external dependencies.

## Usage

Interactive: pick the income type, then enter the amount:

```bash
python budget.py
```

Or pass both directly:

```bash
python budget.py salary 3600
```

```
Monthly salary: 3,600.00
+-----------------------+--------+----------+
| Category              |      % |   Amount |
+-----------------------+--------+----------+
| Rent                  |  25.0% |   900.00 |
| Utilities             |  11.1% |   400.00 |
| Car fuel              |  13.9% |   500.00 |
| Free spendings        |  16.7% |   600.00 |
| Wishlist              |  13.9% |   500.00 |
| Savings / Investments |  19.4% |   700.00 |
|   - Savings           |  15.3% |   550.00 |
|   - Investments       |   4.2% |   150.00 |
+-----------------------+--------+----------+
| Total                 | 100.0% | 3,600.00 |
+-----------------------+--------+----------+
```

Show help: usage plus every income type with the % each category gets:

```bash
python budget.py /help
```

`help`, `-h` and `--help` work too, and you can type `/help` at the interactive menu prompt. To see only the income types and their splits:

```bash
python budget.py --list
```

## Income types

| Type          | Split                                                              |
|---------------|--------------------------------------------------------------------|
| `salary`      | Rent 25%, Utilities 11.1%, Car fuel 13.9%, Free spendings 16.7%, Wishlist 13.9%, Savings / Investments 19.4% |
| `freelancing` | Taxes 20%, Savings / Investments 30%, Wishlist 30%, Free spendings 20% |
| `gift`        | Savings / Investments 50%, Wishlist 30%, Free spendings 20%        |
| `bonus`       | Savings / Investments 60% (half/half), Wishlist 20%, Free spendings 20% |
| `refund`      | Savings 70%, Free spendings 30%                                    |
| `other`       | Savings / Investments 40%, Wishlist 30%, Free spendings 30%        |

Unless noted, "Savings / Investments" is split 550 : 150 between savings and investments.

## ETF ideas

When the income type includes investments, a second table follows the budget. It lists 10 popular UCITS ETFs, ranked by their 1-month price change from best to worst, with UP/DOWN markers. This is meant as a quick look at what is doing well or poorly; it is not investment advice.

- Data comes from Yahoo Finance's public chart endpoint (Xetra listings, prices in EUR), so you need an internet connection. No API key is needed. If the data can't be loaded, the budget still prints.
- XTB discontinued its public API in March 2025, so the list is a curated watchlist of widely held ETFs rather than a live XTB feed. Check the XTB app to confirm an ETF is available for your account.
- Edit [`etfs.json`](etfs.json) to change the watchlist (Yahoo tickers, e.g. `SXR8.DE`).
- Add `--no-etfs` to skip the table, or run `python etf.py` to see only the ETFs.

## Customizing

All plans live in [`plans.json`](plans.json). Each category has a `weight`; weights are relative, so they can be percentages (`20`, `30`, …) or example amounts (`900`, `400`, …). A category can have an optional `split` list to break it into sub-categories the same way.

To add an income type, add a new entry under `plans`, and it shows up in the menu automatically.

Amounts are rounded to cents, and any rounding leftover goes to the last category so the total always matches the input.

## License

[MIT](LICENSE)

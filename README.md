# Budget Splitter

A small command-line tool that splits an income amount across budget categories, based on the type of income (salary, freelancing, gift, …).

Requires Python 3.9+. No external dependencies.

## Usage

Interactive: pick the income type and currency, then enter the amount:

```bash
python budget.py
```

Or pass them directly. The currency is optional and defaults to RON:

```bash
python budget.py salary 3600
python budget.py freelancing 500 eur
```

```
Monthly salary: 3,600.00 RON  (= 672.92 EUR / 743.37 USD)
+-----------------------+--------+----------+--------+--------+
| Category              |      % |      RON |    EUR |    USD |
+-----------------------+--------+----------+--------+--------+
| Rent                  |  25.0% |   900.00 | 168.23 | 185.84 |
| Utilities             |  11.1% |   400.00 |  74.77 |  82.60 |
| Subscriptions         |   4.2% |   150.00 |  28.04 |  30.97 |
| Car fuel              |  13.9% |   500.00 |  93.46 | 103.25 |
| Free spendings        |  12.5% |   450.00 |  84.12 |  92.92 |
| Wishlist              |  13.9% |   500.00 |  93.46 | 103.25 |
| Savings / Investments |  19.4% |   700.00 | 130.85 | 144.54 |
|   - Savings           |  15.3% |   550.00 | 102.81 | 113.57 |
|   - Investments       |   4.2% |   150.00 |  28.04 |  30.97 |
+-----------------------+--------+----------+--------+--------+
| Total                 | 100.0% | 3,600.00 | 672.92 | 743.37 |
+-----------------------+--------+----------+--------+--------+
Rates from BNR (National Bank of Romania), 2026-11-13: 1 EUR = 5.3498 RON, 1 USD = 4.8428 RON, 1 EUR = 1.1047 USD
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
| `salary`      | Rent 25%, Utilities 11.1%, Subscriptions 4.2%, Car fuel 13.9%, Free spendings 12.5%, Wishlist 13.9%, Savings / Investments 19.4% |
| `freelancing` | Taxes 20%, Savings / Investments 30%, Wishlist 30%, Free spendings 20% |
| `gift`        | Savings / Investments 50%, Wishlist 30%, Free spendings 20%        |
| `bonus`       | Savings / Investments 60% (half/half), Wishlist 20%, Free spendings 20% |
| `refund`      | Savings 70%, Free spendings 30%                                    |
| `other`       | Savings / Investments 40%, Wishlist 30%, Free spendings 30%        |

Unless noted, "Savings / Investments" is split 550 : 150 between savings and investments.

## Rent, utilities and subscriptions

For the monthly salary, the script first asks whether you pay for rent, utilities and subscriptions, and how much:

```
Do you pay for rent? [Y/n]: y
How much do you pay for rent? (RON, Enter = 900.00): 1200
Do you pay for utilities? [Y/n]: n
Do you pay for subscriptions? [Y/n]: y
How much do you pay for subscriptions? (RON, Enter = 150.00): 150
```

- What you enter is taken off the top as a fixed amount, since these are usually the most important bills.
- Car fuel keeps its normal share.
- Free spendings, Wishlist and Savings / Investments adjust to cover the difference: they get more if you don't pay a bill (or pay less than the plan), and less if you pay more.
- If the bills leave too little, those three get 0 and Car fuel shrinks to fit. The bills can't add up to more than the whole income.
- Answering `n`, or entering `0`, leaves the category out. Pressing Enter keeps the plan's normal amount.

To skip the questions, pass the amounts as options (`0` means you don't pay it):

```bash
python budget.py salary 3600 --rent 1200 --utilities 0 --subscriptions 150
```

### Warning for expenses that are too high

If rent + utilities + subscriptions + a minimum of 300 RON for food come to more than the income, the script prints a warning after the table. It says by how much the expenses are over, and suggests cheaper housing or utilities, cutting subscriptions, or a second job or extra income. The food minimum is `min_food` in [`plans.json`](plans.json), and it's converted when you budget in EUR or USD.

In [`plans.json`](plans.json), `"ask": true` marks a category the script asks about (and adds a `--<name>` option for), and `"flexible": true` marks the categories that absorb the difference.

## Currencies

Amounts can be in RON, EUR or USD. The table shows the amount in the currency you entered, plus columns converted into the other two.

- Rates come from the official daily reference rates of the National Bank of Romania ([BNR](https://curs.bnr.ro/nbrfxrates.xml)). If BNR can't be reached, the European Central Bank rates from [Frankfurter](https://frankfurter.dev) are used instead. Neither needs an API key.
- If no rates can be loaded (for example offline), the budget still prints in the original currency.
- The default currency is set by `default_currency` in [`plans.json`](plans.json). Run `python rates.py` to see today's rates.
- Plans are proportional, so the salary plan's weights (based on a 3600 RON salary) scale the same way whatever currency you enter.

## ETF ideas

When the income type includes investments, a second table follows the budget. It checks a watchlist of about 40 popular UCITS ETFs (world, US, Europe, emerging markets, Japan, sectors, dividends, bonds, gold), ranks them by their 1-month price change, and shows the top 10 and bottom 3 with UP/DOWN markers. Prices and ranking are fetched fresh on every run. This is meant as a quick look at what is doing well or poorly; it is not investment advice.

- Data comes from Yahoo Finance's public chart endpoint (Xetra listings, prices in EUR), so you need an internet connection. No API key is needed. If the data can't be loaded, the budget still prints.
- XTB discontinued its public API in March 2025, so the list is a curated watchlist of widely held ETFs rather than a live XTB feed. Check the XTB app to confirm an ETF is available for your account.
- Edit [`etfs.json`](etfs.json) to change the watchlist (Yahoo tickers, e.g. `SXR8.DE`) or how many to show (`show_top`, `show_bottom`; set `show_bottom` to `0` to hide the worst performers).
- Add `--no-etfs` to skip the table, or run `python etf.py` to see only the ETFs.

## Customizing

All plans live in [`plans.json`](plans.json). Each category has a `weight`; weights are relative, so they can be percentages (`20`, `30`, …) or example amounts (`900`, `400`, …). A category can have an optional `split` list to break it into sub-categories the same way.

To add an income type, add a new entry under `plans`, and it shows up in the menu automatically.

Amounts are rounded to cents, and any rounding leftover goes to the last category so the total always matches the input.

## License

[MIT](LICENSE)

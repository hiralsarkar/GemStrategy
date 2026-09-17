# GemStrategy

**An end-to-end trade intelligence platform for India's Pearl & Coloured Gemstone ecosystem**, built on real government trade data, automated data engineering, a SQL data model, and a Power BI analytics layer.

Covers **Natural Pearls, Cultured Pearls, Emerald, Ruby, and Sapphire** - India's monthly import/export activity by partner country, sourced directly from the Government of India's Department of Commerce (TRADESTAT), January 2018 through June 2026.

---

## Dashboard

### Overview
Headline KPIs, trade direction over time, commodity mix, partner concentration, and top emerging markets at a glance.

![Overview](docs/screenshots/01_overview.png)

### Commodity Intelligence
How Natural Pearls, Cultured Pearls, Emerald, Ruby, and Sapphire compare - rank evolution over time, YoY growth heatmap, and each commodity's contribution to India's trade balance.

![Commodity Intelligence](docs/screenshots/02_commodity_intelligence.png)

### Pearls
Natural vs. Cultured pearl trade, kept separate rather than merged into one generic "pearls" category - composition trend, supplier concentration (HHI), and top trading partners.

![Pearls](docs/screenshots/03_pearls.png)

### Colored Gemstones
Emerald, Ruby, and Sapphire trade composition, concentration, and key trading partners, plus a market-classification map (Emerging / Watchlist / Established / Declining) built from a size + growth + persistence rule, not growth percentage alone.

![Colored Gemstones](docs/screenshots/04_colored_gemstones.png)

---

## What the data shows

Numbers pulled directly off the dashboard's Overview page, Jan 2018 - Jun 2026:

- **Total trade**: $6.39bn across the five commodities (Imports $4.28bn, Exports $2.11bn, Trade Balance -$2.16bn)
- **Leading commodity**: Emerald ($3.2bn), ahead of Ruby ($2.4bn), Cultured Pearls ($0.4bn), Sapphire ($0.3bn), Natural Pearls ($0.1bn)
- **Partner concentration**: Hong Kong alone accounts for 49% of India's top-5-country trade share - Zambia (10%), Armenia (9%), Thailand (8%), and the USA (6%) make up most of the rest
- **Top import source and export destination are the same country**: Hong Kong, $2.09bn of imports and $1.03bn of exports - a strong signal that a lot of this trade is routing through a re-export/cutting hub rather than reflecting final consumption
- **Highest-scoring emerging markets**: Belgium (79), UAE (71), Thailand (65) and Hong Kong (65), ranked by the size + growth + persistence rule below, not by raw growth percentage

## Why this exists

Public commentary on India's gems and jewellery trade skews heavily toward diamonds and precious metals. Pearls and coloured gemstones get far less granular analysis, even though official trade statistics already contain everything needed to answer real business questions: where India sources from, where it sells to, which markets are genuinely growing versus just noisy, and how concentrated (and therefore risky) that trade is.

GemStrategy turns the raw government reports into a structured, queryable, and visual answer to those questions.

## Data

- **Source**: Government of India, Department of Commerce - TRADESTAT (commodity-wise, country-wise import/export reports)
- **Scope**: 10 HS codes across 5 commodities, both import and export flows, monthly, Jan 2018 - Jun 2026
- **Currency**: US $ Million throughout (not converted - reported directly by TRADESTAT in that unit)
- **Grain**: Commodity x Country x Month x Trade Flow
- Raw government reports are kept in full (`gemstrategy_data/raw/`) so every number in the dashboard can be traced back to its source. Nothing is estimated, interpolated, or filled in for missing observations.

## The wide-to-long problem

TRADESTAT's own reports store each month as a column header, not a row value - a report for one commodity looks like `Country | Jun-2025 (R) | Jun-2026 (F) | %Growth | Apr-Jun2025 (R) | Apr-Jun2026 (F)`. That's wide format, built for a human to scan across a page, and the header text isn't even stable: the revision-status suffix (`R` = Revised, `F` = Final, `P` = Provisional) changes from one report to the next.

For time-series analysis, month needs to be a value sitting in a column, not a column name - the standard "tidy data" shape (one variable per column, one observation per row). Since every report I fetched already corresponds to exactly one requested month, the fix wasn't a generic reshape - it was picking out, with certainty, the one column that actually matched the month being asked for. `value_column()` in `build_pearl_trade_csvs.py` does that with a regex match on the month-year text regardless of the `(R)/(F)/(P)` suffix, and raises an error if it finds zero or more than one match, rather than silently grabbing the wrong column. Every one of the 2,040 single-month extracts is then stacked into one long table with explicit `year`, `month_number`, and `period_date` columns - functionally the same operation as `pandas.melt()`, done via select-and-concatenate across files instead of a single call, because the source data arrived pre-split one month per report.

## Architecture

```
TRADESTAT (raw HTML reports)
        |
   scripts/  (Python: collect -> parse -> curate -> validate -> model)
        |
gemstrategy_data/  (curated fact tables + a SQL star schema)
        |
   GemStrategy Dashboard.pbix  (Power BI: DAX measures, 4-page report)
```

**Star schema**: `Dim_Date`, `Dim_Country` (131 countries, region-mapped), `Dim_HS_Code` (commodity name/group folded in - no separate junk dimensions), and `Fact_Trade` (37K+ rows). Available as a SQLite database and as plain CSVs.


## Rebuilding the data

```bat
.venv\Scripts\python.exe scripts\build_pearl_trade_csvs.py
.venv\Scripts\python.exe scripts\build_gemstrategy_trade_csvs.py
.venv\Scripts\python.exe scripts\validate_gemstrategy_data.py
.venv\Scripts\python.exe scripts\build_sql_model.py
```

Then refresh the `.pbix` in Power BI Desktop (Home -> Refresh) - it reads from `gemstrategy_data/powerbi_import/`.

## Data quality

Every rebuild runs through automated checks: expected report coverage (2,040/2,040 report-months present), currency consistency, duplicate/null detection on the fact tables, and country-label review. Full detail in [gemstrategy_data/validation_report.md](gemstrategy_data/validation_report.md).

**A real bug the validation caught**: one report-month (Cultured Pearls import, June 2026) had been fetched in Rupee Crore instead of US Dollar Million during an early pilot run, which would have shown up in the dashboard as roughly 9x its true value. The validator's currency-selector check flagged it; it was re-fetched with the correct selector and the affected curated files were rebuilt.

**Beyond internal consistency**, two further checks confirm the data itself, not just the pipeline's logic:
- Reconciled partner-country sums against the government's own published Total row across all 1,701 report-months in the dataset - only one exceeds a 0.05 USD-million tolerance, and that's within the government's own rounding.
- Re-fetched five report-months live from TRADESTAT during a later audit and compared them directly against the curated values already on disk - all five matched exactly, including a Total row and partner-country figures spanning 2020 to 2026.

## Methodology notes

**Concentration (HHI)**: the Herfindahl-Hirschman Index - sum of each country's squared market share, on a 0-10,000 scale - measures how dependent India's trade in a commodity is on a small number of partners. Below 1,500 is read as diversified, above 2,500 as highly concentrated. Applied separately to import suppliers and export destinations.

**Emerging-market classification**: a market isn't "emerging" just because it grew fast last month - a country going from $2,000 to $12,000 in trade is a 500% headline with no real weight behind it. The classification instead combines three signals: trailing 12-month trade size, trailing 12-month growth rate, and persistence (how many of the last 12 months actually had recorded trade). Only markets that clear a size floor and show sustained, not one-off, growth get labeled Emerging; small, sporadic activity is labeled a Watchlist candidate at best.

## Limitations

- **India's trade only, not global market share.** "Market share" here means share of India's own import/export activity - there's no UN Comtrade or global benchmark data in this project, so it can't say what fraction of *worldwide* pearl or gemstone trade India represents.
- **Country-level, not transaction-level.** A country ranking high, like Hong Kong or Belgium, is plausibly re-export or cutting-hub activity rather than final consumption - the data can't distinguish the two.
- **Value only, no quantity.** TRADESTAT reports quantity and value as separate selectors; only value (USD Million) was collected. That means a rise in trade value could be more volume, a higher price per unit, or both - the two aren't separated in this dataset.
- **HS classifications can shift over time.** TRADESTAT itself notes that HS codes can be dropped, reallocated, or have their reporting unit changed - the `hs_codes.csv` master list reflects the classification in effect for this collection window, not a permanently fixed scheme.

## Stack

Python (pandas, requests, BeautifulSoup, lxml) for collection and transformation - SQLite for the data model - Power BI and DAX for analysis and visualization.

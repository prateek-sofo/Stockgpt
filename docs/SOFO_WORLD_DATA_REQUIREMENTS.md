# What StockGPT needs from Sofo World

**Decision (Nov 19, 2026).** Sofo World is the one place that scrapes. StockGPT reads news,
company financials, SEC filings and earnings dates from Sofo World and stops collecting them
itself. This document is the full list of what StockGPT reads, what Sofo World already has, and
what is missing.

**Why it matters.** StockGPT ranks stocks every morning at 09:00 ET for the session that follows,
and it learns from several years of history. Every fact therefore needs the time the public could
first see it. A value without that time cannot be used in a backtest.

## Where things stand today

Read from the `sofo-world` repository on Nov 19, 2026 (schema, `crawl/sec/parse.py`, `sources/*.yaml`).

| Data | In Sofo World today | Gap for StockGPT |
|---|---|---|
| News articles and company mentions | `news_articles`, `article_mentions` (NW01, NW02, NW03, NW05, NW13) | Not reachable from StockGPT yet. Details in Part A. |
| Structured events | `events` (FN03: 8-K items as events, and others) | `event_date` is a date; `detected_at` exists. Enough. |
| Competitor graph | `relationships` | Needs `valid_from` on every link. |
| Financials | `observations` rows `metric.<name>` from FN01, with `published_at` = filing date | 5 metrics missing (B2). Quarterly and full-year durations and the filing date are already kept, which is what StockGPT needs (B3). |
| SEC filings | `observations` rows `filing` (form, filed, accession, items, url) | No acceptance time, so before-open vs after-close is unknown (B4). Collected per crawled company, not for every listed company every day (B5). |
| Listed tickers | `ID04.tickers` exists, shipped disabled | Needs to be on (B1). |
| Daily prices | none in the catalog | Part C. StockGPT keeps reading Yahoo until this exists. |
| Rates, volatility, policy risk | MK10 (FRED) is planned, not built | Part C. StockGPT keeps reading FRED until this exists. |

StockGPT already has the readers for all of this (`backend/stockgpt/sources/sofo_world.py` for
news, `sofo_finance.py` for financials and filings). Each table is switched to Sofo World
automatically once it covers at least 1,500 listed companies and is current to within 5 days; until
then StockGPT reads that one table from the public source directly and the Today page says so.

---

# Part A. News

Items marked **must** block the connection; the rest improve accuracy.

### A1. The one rule that matters most: true timestamps

A backtest is only honest if every article carries the time the public could first read it.

| Field | Requirement |
|---|---|
| `published_at` | **Must.** The publisher's own publication time, with timezone, to the minute. Not the crawl time. |
| `first_seen` | **Must.** When Sofo World first fetched it. Used when `published_at` is missing or date-only, and to measure crawl delay. |
| Updates | An edited or corrected article is a new row (or a new version) with its own time. Never overwrite the original time or headline. |
| Date-only items | Mark them (`time_precision = 'day'`). StockGPT treats these as known only from the next morning. |

Articles are assigned to a trading day like this: anything published from 16:00 ET on the prior
session to 09:00 ET on the scan day is "overnight news" for that scan. A wrong timestamp moves an
article across that line and leaks the future into the test.

### A2. Per article

| Field | Need | Notes |
|---|---|---|
| `article_id` | must | stable |
| `headline` | must | as published |
| `source` / publisher | must | name and domain |
| `url` | must | link only; StockGPT shows headline + link, never the article text |
| `published_at`, `first_seen` | must | A1 |
| `summary` or first paragraph | useful | 1 to 3 sentences is enough; full body is not needed and should stay in Sofo World's raw store |
| `language` | useful | English first |
| `cluster_id` | useful | one id for the same story syndicated across outlets (wire copy on 40 sites is one event, not 40) |
| `article_type` | useful | news, press release, analyst note, opinion, transcript, filing notice |
| `tone` | optional | GDELT tone or similar; StockGPT scores headlines itself |

### A3. Which company each article is about

| Field | Need | Notes |
|---|---|---|
| company mention rows | must | one row per (article, company) |
| `is_subject` | must | true when the article is about this company, false when it is only mentioned |
| `salience` | must | 0 to 1; StockGPT keeps subject rows and salience >= 0.5 |
| ticker and CIK | must | through `entity_identifiers`; CIK is the safest key, ticker as of the article date if tickers changed |
| `sofo_company_id` | must | so competitor links join |

A wrong company link is worse than a missing article. Precision target on the subject link: 95%+.

### A4. Types of news, in order of value

Tag each article or event with one of these. This order reflects how strongly each type has moved
stocks the next day in published research and in our own earnings and filing tests.

1. **Earnings results and guidance**: results, pre-announcements, guidance raised or cut, with the
   time of release (before open or after close).
2. **Analyst actions**: upgrades, downgrades, price-target changes, initiations.
3. **Mergers and acquisitions**: offers, rumours, approvals, deal breaks, activist stakes.
4. **Regulatory and legal**: FDA decisions and trial results, antitrust, investigations, lawsuits,
   settlements, recalls, sanctions.
5. **Capital actions**: share offerings, buybacks, dividends, debt raises, bankruptcy, delisting notices.
6. **Management**: CEO or CFO change, board changes, auditor change.
7. **Operations**: large contracts and customer wins or losses, product launches, price changes,
   layoffs and restructuring, plant openings or closures, cyber incidents, supply disruptions.
8. **Competitor and supply-chain news**: the same types, for companies linked in the relationship
   graph (A6). The request is explicitly to use news about a stock **and its competitors**.
9. **Macro and politics** (market-wide, not tied to one company): central bank decisions and
   speeches, inflation, jobs and growth data, tariffs and trade, budgets and shutdowns, elections,
   regulation by sector, wars and geopolitical events, oil supply. Tag by theme and, where it applies,
   by sector affected.

### A5. Structured events (Sofo World `events`)

For every event type in the list above that Sofo World already extracts
(`earnings_release`, `guidance_change`, `acquisition`, `leadership_change`, `layoff_warn`,
`contract_award`, `product_launch`, `price_change`, `regulatory_action`, `recall`,
`litigation_filed`, `bankruptcy`, `restructuring`, `funding_round`, `partnership`,
`customer_win_public`):

| Field | Need |
|---|---|
| `event_type`, company id | must |
| `event_date` | must: when it happened |
| `detected_at` | must: when Sofo World first knew. StockGPT uses the later of the two so nothing is known early. |
| `source_url`, `article_id` | must: provenance |
| `confidence` | must |
| direction or size where it exists | useful: guidance up or down, deal value, headcount affected |

### A6. Relationship graph

Competitor, customer, supplier, partner, parent and subsidiary links between companies, each with
`valid_from` (and `valid_to` if ended) and evidence. Without dates a link learned in 2026 would be
applied to 2020. Until this arrives StockGPT uses SEC industry codes and price co-movement instead.

### A7. Coverage and history

| | Minimum | Target |
|---|---|---|
| Companies | the 600 most traded US stocks | all 2,591 the model follows, then all ~7,700 exchange-listed |
| History | 2 years | from 2019-01-01 (the model's test period starts in 2020) |
| Sources | PR wires (NW02), company newsrooms (NW01), GDELT (NW03, NW13) | plus trade publications (NW05) and publisher feeds (NW14) |
| Latency | published to available: under 60 minutes | under 15 minutes for PR wires |
| Daily cut | everything published up to 08:30 ET available by 08:45 ET | |

Coverage must be reported honestly per day: if a source was down, StockGPT needs to know, because
"no news" and "news not collected" must not look the same. A daily row count per source is enough.

---

# Part B. Company financials and SEC filings

### B1. The list of listed companies (must)

One row per listed ticker: `ticker`, `name`, `exchange`, `cik`, `sic` (SEC industry code),
refreshed daily. Source: SEC `company_tickers_exchange.json` (about 10,400 rows) plus the SIC code
from each company's submissions record. In Sofo World this is `ID04.tickers`, currently disabled.
StockGPT uses it to map CIK to ticker, to assign sectors, and to know which tickers exist.

### B2. Financial metrics (must)

Sofo World's FN01 already stores revenue, net income, operating income, total assets, cash, SG&A,
sales and marketing, R&D, employees, shares outstanding and public float. StockGPT's model also uses
five that are not collected yet. Add them to `XBRL_METRICS` with these names:

| Metric name | XBRL concept (us-gaap), in order of preference | Used for |
|---|---|---|
| `gross_profit` | `GrossProfit` | gross margin |
| `eps_diluted` | `EarningsPerShareDiluted`, then `EarningsPerShareBasic` (unit USD/shares) | EPS growth, P/E, earnings surprise |
| `cash_from_operations` | `NetCashProvidedByUsedInOperatingActivities` | free-cash-flow margin |
| `capex` | `PaymentsToAcquirePropertyPlantAndEquipment` | free-cash-flow margin |
| `long_term_debt` | `LongTermDebt`, then `LongTermDebtNoncurrent` | net debt |

Also add two more revenue tags as fallbacks: `RevenueFromContractWithCustomerIncludingAssessedTax`
and `SalesRevenueGoodsNet`.

### B3. Point-in-time rules for metrics (must)

| Rule | Why |
|---|---|
| Keep `filed` (the filing date) on every metric point. It is there today in the value and in `published_at`. | The model may only use a number from the day it was filed. |
| Keep the **first** reported value for a period. A restatement is a new observation, never an overwrite. | Sofo World's append-only rule already does this. |
| Keep both quarterly (80 to 100 days) and full-year (350 to 380 days) durations, with `start` and `end`. | Most companies do not report Q4 separately. StockGPT derives Q4 as full year minus three quarters. |
| Keep `form` (10-Q, 10-K, and amendments). | Amendments are handled separately. |
| History from 2015. | Year-over-year growth for 2019 needs 2018; the test period starts in 2020. |

Do not serve these through `metric_points` for StockGPT: that table has the period end but not the
filing date. StockGPT reads the `observations` rows.

### B4. Filings (must)

For every filing: `form`, `filed`, `accession`, `items` (8-K item codes), `url`, `report_date`.
These exist today. One field is missing and matters a great deal:

| Field | Requirement |
|---|---|
| `accepted` | **Must.** EDGAR's `acceptanceDateTime` (to the second, in the submissions feed). An earnings 8-K accepted at 07:30 ET moves the stock that day; one accepted at 16:05 ET moves it the next day. Without the time StockGPT has to assume after-close for all of them. |

### B5. Coverage and freshness of filings (must)

| | Requirement |
|---|---|
| Companies | Every company in B1 (about 7,700 on exchanges, 10,400 with OTC), not only companies inside a watched territory. |
| Freshness | A filing accepted by EDGAR today is in Sofo World before 08:30 ET the next morning; for 8-Ks, within an hour is the target. |
| How | The cheapest complete source is EDGAR's daily form index (`/Archives/edgar/daily-index/<year>/QTR<n>/form.<yyyymmdd>.idx`): one file a day lists every filing by every registrant. Per-company submissions are then needed only for that day's 8-K filers (item codes and acceptance time), a few hundred requests. History comes from the bulk `submissions.zip` (one 1.5 GB file, refreshed nightly). StockGPT's `backend/stockgpt/sources/filings.py` does exactly this and can be ported as a Sofo World source. |
| Forms | All forms by listed companies. StockGPT filters and labels them itself. Insider forms 3, 4, 5 and 144, Schedules 13D and 13G, registration statements and prospectuses included. |

### B6. Earnings (useful)

| Field | Notes |
|---|---|
| Earnings release | 8-K item 2.02 with `accepted` covers the date and time. |
| Scheduled next earnings date | From company IR pages or press releases (FN08, NW01). Today StockGPT only knows past dates. |
| Consensus estimates | Not a public record. If Sofo World ever holds a licensed feed it must stay in the `vendor` schema; StockGPT does not need it to run. |

---

# Part C. Prices and macro (not scraped by Sofo World today)

"Scraping in one place" stops short here: Sofo World's catalog has no price source and its FRED
source (MK10) is planned. Until they exist StockGPT keeps reading these two directly.

| Data | What StockGPT uses | Source StockGPT reads now |
|---|---|---|
| Daily prices | open, high, low, close, volume, adjusted for splits and dividends, every listed ticker, from 2016; plus the last premarket trade before 09:00 ET for the 600 most traded | Yahoo Finance (public, no key) |
| Option prices | daily implied volatility and volume per ticker | none: estimated from price swings. Needs a licensed feed for a real options backtest. |
| Rates and risk | 10-year and 2-year Treasury yields, high-yield spread, VIX, oil, dollar index, policy-uncertainty index, geopolitical-risk index | FRED, Caldara-Iacoviello GPR |
| Delisted companies | price history of stocks that no longer trade | none: the backtest has survivorship bias until this exists |

Decision needed from Sofo World's side: add a price source and MK10 there, or leave these two in
StockGPT.

---

# Part D. Delivery and acceptance

### D1. Delivery

Either route works; the readers for both exist.

1. **Files on S3** (simplest): `s3://<bucket>/stockgpt/` with one folder per table, Parquet or CSV,
   written once a day before 08:45 ET. `infra/aws/export_news.sh` produces exactly this from the
   Sofo World database: `news_articles/`, `article_mentions/`, `entity_identifiers/`, `events/`,
   `relationships/`, `observations/` (financial metric points and filing records), and optionally
   `listed/` (B1).
2. **Read-only Postgres role** (`infra/aws/grants.sql`): `SELECT` on `news_articles`,
   `article_mentions`, `entity_identifiers`, `events`, `relationships`, and on the view
   `stockgpt_sec_observations`, which exposes only public SEC metric and filing rows.

Only public, owned data: nothing from the `vendor` schema and nothing from the people graph.

Then, where StockGPT runs: set `SOFO_NEWS_URI` or `SOFO_DATABASE_URL` and run `scripts/daily.sh`.

### D2. Acceptance checks StockGPT runs on the first delivery

News:

1. Share of articles with a minute-level `published_at`; share where `first_seen` is earlier than
   `published_at` (should be near zero).
2. Articles per day per source, 2019 to today: gaps and step changes.
3. Share of the 600 most traded stocks with at least one subject article in a typical week.
4. Earnings check: for known earnings dates, is there an `earnings_release` event or article within
   a day, on the right side of the open.
5. 200 random subject links checked by hand for the right company.
6. Duplicate rate before and after `cluster_id`.

Financials and filings (StockGPT holds its own copy from the SEC bulk files to compare against):

7. Companies with financials: Sofo World vs the SEC bulk file (2,591 modelled companies must match).
8. For 500 random company-quarters: revenue, operating income, EPS and the filing date equal the
   SEC bulk file exactly.
9. Filings per day for the last 30 days vs EDGAR's daily index: no missing days, counts within 1%.
10. Share of 8-K item 2.02 filings with an acceptance time: 100%.

After that the model is retrained on Sofo World data, and the Today page shows the source and date
of every input.

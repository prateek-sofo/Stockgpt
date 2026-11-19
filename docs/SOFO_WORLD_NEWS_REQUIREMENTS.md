# What StockGPT needs from Sofo World news

**Purpose.** StockGPT ranks stocks every morning at 09:00 ET for the session that follows. It needs to
know what was published about each company, its competitors, and the wider economy and politics
**before** that time, and it needs several years of the same data to learn from. Today the model runs
with no news at all (prices, SEC filings and earnings dates only).

This is the full list of what to collect and how to deliver it. Items marked **must** block the
connection; the rest improve accuracy.

## 1. The one rule that matters most: true timestamps

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

## 2. Per article

| Field | Need | Notes |
|---|---|---|
| `article_id` | must | stable |
| `headline` | must | as published |
| `source` / publisher | must | name and domain |
| `url` | must | link only; StockGPT shows headline + link, never the article text |
| `published_at`, `first_seen` | must | section 1 |
| `summary` or first paragraph | useful | 1 to 3 sentences is enough; full body is not needed and should stay in Sofo World's raw store |
| `language` | useful | English first |
| `cluster_id` | useful | one id for the same story syndicated across outlets (wire copy on 40 sites is one event, not 40) |
| `article_type` | useful | news, press release, analyst note, opinion, transcript, filing notice |
| `tone` | optional | GDELT tone or similar; StockGPT scores headlines itself |

## 3. Which company each article is about

| Field | Need | Notes |
|---|---|---|
| company mention rows | must | one row per (article, company) |
| `is_subject` | must | true when the article is about this company, false when it is only mentioned |
| `salience` | must | 0 to 1; StockGPT keeps subject rows and salience >= 0.5 |
| ticker and CIK | must | through `entity_identifiers`; CIK is the safest key, ticker as of the article date if tickers changed |
| `sofo_company_id` | must | so competitor links join |

A wrong company link is worse than a missing article. Precision target on the subject link: 95%+.

## 4. Types of news, in order of value

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
   graph (section 6). The request is explicitly to use news about a stock **and its competitors**.
9. **Macro and politics** (market-wide, not tied to one company): central bank decisions and
   speeches, inflation, jobs and growth data, tariffs and trade, budgets and shutdowns, elections,
   regulation by sector, wars and geopolitical events, oil supply. Tag by theme and, where it applies,
   by sector affected.

## 5. Structured events (Sofo World `events`)

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

## 6. Relationship graph

Competitor, customer, supplier, partner, parent and subsidiary links between companies, each with
`valid_from` (and `valid_to` if ended) and evidence. Without dates a link learned in 2026 would be
applied to 2020. Until this arrives StockGPT uses SEC industry codes and price co-movement instead.

## 7. Coverage and history

| | Minimum | Target |
|---|---|---|
| Companies | the 600 most traded US stocks | all 2,591 the model follows, then all ~7,700 exchange-listed |
| History | 2 years | from 2019-01-01 (the model's test period starts in 2020) |
| Sources | PR wires (NW02), company newsrooms (NW01), GDELT (NW03, NW13) | plus trade publications (NW05) and publisher feeds (NW14) |
| Latency | published to available: under 60 minutes | under 15 minutes for PR wires |
| Daily cut | everything published up to 08:30 ET available by 08:45 ET | |

Coverage must be reported honestly per day: if a source was down, StockGPT needs to know, because
"no news" and "news not collected" must not look the same. A daily row count per source is enough.

## 8. Delivery

Either route works; the loader for both already exists (`backend/stockgpt/sources/sofo_world.py`).

1. **Files on S3** (simplest): `s3://<bucket>/stockgpt/` with folders `news_articles/`,
   `article_mentions/`, `entity_identifiers/`, `events/`, `relationships/`, as Parquet (or CSV),
   partitioned by date, written once a day before 08:45 ET. `infra/aws/export_news.sh` produces
   exactly this from the Sofo World database.
2. **Read-only Postgres role**: `SELECT` on `news_articles`, `article_mentions`,
   `entity_identifiers`, `events`, `relationships` (`infra/aws/grants.sql`).

Only public, owned data: nothing from the `vendor` schema and nothing from the people graph.

## 9. Acceptance checks StockGPT will run on the first delivery

1. Share of articles with a minute-level `published_at`; share where `first_seen` is earlier than
   `published_at` (should be near zero).
2. Articles per day per source, 2019 to today: gaps and step changes.
3. Share of the 600 most traded stocks with at least one subject article in a typical week.
4. Earnings check: for known earnings dates (from SEC 8-K item 2.02), is there an `earnings_release`
   event or article within a day, with the right before-open or after-close side.
5. 200 random subject links checked by hand for the right company.
6. Duplicate rate before and after `cluster_id`.

After that the model is retrained with news, and the Today page will show, for every scan, the
article window used, the article count and the time of the latest article.

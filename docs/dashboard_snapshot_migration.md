# Dashboard snapshot migration

## Stage 1: fresh job code

`cloud_job.py` enters a standard-library bootstrap before analysis imports.
The bootstrap acquires data then git locks, pulls the existing main branch,
closes locks and execs the updated entrypoint in the same PID. A PID-bound,
immediately consumed environment marker prevents repeated pulls and does not
disable updates for child jobs. The existing task bodies and timer times are
unchanged. Failed pulls remain fatal as before; no guarantee is made against
network/provider outages.

## Stage 2: conditional snapshots and last-good fallback

Authenticated `/web-data` requests return an ETag from the opened, atomically
published file version. Matching requests return 304 without a payload.
The client serializes refreshes, closes responses, restores new snapshots only
after gzip and SQLite quick checks, and retains the last good file on failure.
Bootstrap data is used only when no runtime file exists. Resource TTL remains
120 seconds; existing table cache TTLs remain unchanged.

Web DB publication now uses same-filesystem atomic replacement, like its gzip
publication. No tables, rows, retention periods, tabs or calculations are removed
in these two stages. A sleeping Streamlit host still needs to start; this change
does not eliminate hosting cold starts or the first full snapshot transfer.

## Stage 3: incremental display-result migration

Completed without deleting source dependencies:

- `web_sector_trend_daily` stores all/rising daily sector ranks and members.
  It retains 39 dates so each of the 30 selectable dates has ten-day context.
- `web_stock_analysis_scores` stores the ordered result of the unchanged
  five-trading-day StockAnalyzer calculation. The dashboard reads this compact
  table and falls back to the legacy calculation only for older snapshots.
- `web_bottom_candidates` stores rule-model rows and legacy bottom rows with
  their display change rate and market cap already joined.
- `web_stock_catalog` stores the deduplicated, ordered watchlist search catalog.
  The web copy can therefore omit model features, model OHLCV and universe
  snapshots while the full Oracle analysis database retains them.
- `stock_data.recovery.db.gz` is a full Oracle-only recovery snapshot refreshed
  after the scheduled 16:00 analysis. Recovery prefers this file; it is never
  served by `/web-data` or committed to Git.

The rest of the display-only schema migration is not yet complete.

## Stage 4: intraday and overnight display evidence

The full `market_betting_runs.derived_evidence_json` remains in Oracle's
analysis database and full recovery snapshot. During web snapshot publication,
the copied JSON is reduced to the fields consumed by the dashboard: the saved
market-data timestamp, rolling 60-minute one-minute-bar path summaries, sector
breadth summaries and observed members, stock identity/current return, setup
prices, and position assessments. Judgments, quality issues and stock state
tables are retained unchanged.

This does not replace the rolling hour with a single hourly candle or five
recent bars. Collection and decisions still use the complete available
one-minute path in the rolling 60-minute clock window. Only unused intermediate
feature dictionaries are omitted from the web copy after the decision is
persisted.

Production comparison covered all 563 retained runs: session selection, market,
overnight and sector judgments, stock states, sector action/member rows, hourly
detail rows, setup/entry/invalidation/reference text and sector member groups
were equal before and after compaction. The evidence JSON decreased from about
88.4 MB to 16.5 MB in that production dataset.

## Stage 5: news and dashboard session catalog

`web_stock_news_summary` stores the exact per-stock score, sentiment counts and
keyword summary for each saved date/session. `web_stock_news_articles` stores
the same articles the expander can display, without the raw table's large
unique link index and collection-only columns. The app queries only the selected
date/session instead of reading all retained news into memory. Older bundled
snapshots fall back to the legacy raw calculation.

`web_dashboard_sessions` stores the distinct non-after-hours date/session list
and the existing HH:MM sort value. The sidebar reads this small catalog while
`daily_stocks` remains available unchanged for every dashboard calculation and
all three CSV downloads, including the full retained-data download.

Production comparison covered all 133 retained news date/session groups and
20,725 articles, including summary values, keyword order, per-stock article
order and all 251 sidebar date/session entries.

Required contracts before reducing any web snapshot data:

- Date/session catalog: every selectable saved run and its completion status.
- Selected-run tables: rankings, intersections, program flow and its historical
  comparison path, relative strength, closing scanners, technical-stage flags,
  market strength, news and detailed evidence used by expanders and downloads.
- Watchlist: retain the independent Oracle read/write path and symbol catalog.
- Overnight analysis: retain compact stored judgments, hourly evidence and
  sector member details; no change to one-minute collection or hourly rules.

Rollout gate: build new display results alongside old data; compare row keys,
values, ordering, histories and CSVs on production snapshots and empty/holiday
fixtures. Only switch reads after equality checks, then remove unused web data.
Never delete the Oracle analysis source database as part of web optimization.

## Local verification for stages 1 and 2

173 targeted regression tests passed, including bootstrap, conditional endpoint,
last-good corruption/timeout handling, existing analysis/hourly flow, watchlist,
rise ranking and schedule tests. Three gzip/restore tests passed separately.
Streamlit AppTest rendered 22 tabs with zero exceptions using the local snapshot.
These checks do not establish live market-data provider availability.

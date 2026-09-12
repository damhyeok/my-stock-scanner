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

## Stage 3: pending, do not delete dependencies yet

The display-only schema migration is NOT completed by stages 1 and 2.

Required contracts before reducing any web snapshot data:

- Date/session catalog: every selectable saved run and its completion status.
- Selected-run tables: rankings, intersections, program flow and its historical
  comparison path, relative strength, closing scanners, technical-stage flags,
  market strength, news and detailed evidence used by expanders and downloads.
- Ten-trading-day sector display: observed trading-date calendar, daily sector
  turnover/rank/member count/names; a separate rising-only summary retaining
  member change rates. Preserve all-sector ranks, ties, latest-day top-sector
  selection, and missing sessions. Retain enough earlier dates for every
  selectable historical date, not merely ten dates relative to today.
- Scoring: precompute the exact existing five-trading-day StockAnalyzer result;
  retain its universe exclusions and ordering.
- Bottom candidates: legacy fallback still directly reads `model_ohlcv_daily`
  for change rate and market cap. Materialize these display values before
  removing that table from web snapshots.
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

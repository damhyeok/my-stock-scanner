# Phase 5: Oracle runtime integration

Updated: 2026-08-01 KST

## Implemented

- `cloud_job.py market-betting` runs one read-only decision cycle and writes it to `stock_data.db`.
- Both the existing scheduled full-analysis job and the manual `/run` path run the market-betting cycle after the existing full analysis. This provides a deployment-safe fallback even before the dedicated timer is enabled on the VM.
- `market-betting.timer` runs throughout the regular session, with additional checks around the closing windows and at 16:05.
- The runtime uses only allow-listed KIS quotation endpoints. It has no order/account/position client.
- Results are written to the existing `market_betting_*` tables and included in `web_data.db` for the existing Streamlit tab.
- Raw normalized observations are retained for two trade dates on Oracle. They are excluded from the web deployment DB; compact decisions and derived evidence are retained for 30 trade dates.
- An active-stock sample is explicitly marked as an incomplete sector universe, so it cannot silently become full-sector breadth.
- `HOLD_EXISTING` remains `NOT_EVALUABLE` unless a position-specific thesis is supplied.

## Safe initial behavior

Live KIS fields are still `PARTIAL` until a market-session verification run confirms units, refresh behavior, trade-date provenance, and futures selection. The strict runtime therefore saves the evidence and data-quality blockers but does not promote the result to an actionable permission.

## Next development stage

1. `market-betting-verification.timer` captures sanitized KIS evidence at 09:45, 12:00, and 15:25 during each live exchange session. No field is automatically promoted.
2. The versioned registry `config/market_betting_field_verification.json` is wired into the runtime. A probe contract and each source field require explicit review time and evidence references before `VERIFIED` is accepted. The initial registry contains no approvals.
3. Add a complete sector constituent universe and turnover coverage source.
4. Add structural invalidation-price logic for breakout and pullback setups.
5. Add position-specific thesis input so `HOLD_EXISTING` can be evaluated per holding.
6. Observe Oracle runtime duration/API throttling for several sessions and adjust the operational schedule if necessary.

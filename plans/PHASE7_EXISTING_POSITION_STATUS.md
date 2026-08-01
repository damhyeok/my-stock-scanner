# Phase 7: Existing-position thesis input

Updated: 2026-08-01 KST

## Implemented

- The existing Streamlit market-betting tab contains a user position form.
- Inputs: ticker, name, average price, quantity, thesis status, optional thesis note, and optional structural invalidation price.
- Position records are stored persistently in Oracle `stock_data.db`, not only in Streamlit session state.
- Held tickers are collected in addition to the adaptive candidate universe and do not change the five-sector/thirty-stock discovery limits.
- Each position receives its own `HOLD`, `REDUCE`, `EXIT`, or `NOT_EVALUABLE` assessment.
- Profit cushion is displayed but cannot rescue a broken thesis or an invalidation-price breach.
- An unspecified thesis is `NOT_EVALUABLE`; the engine does not invent the user's investment rationale.

## Operational behavior

- Add/update/delete requests use the authenticated Oracle trigger endpoint.
- A saved position is assessed on the next scheduled or manually triggered market-betting run.
- Position changes are included in the bounded web database on the normal Oracle publication path.
- This is analysis-only; no brokerage order endpoint is connected.

## Remaining

- Verify live KIS fields before actionable permissions are enabled.
- Consider a future edit-from-row convenience control after the basic input workflow is observed in production.

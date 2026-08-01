# Phase 8: Live KIS field verification gate

Updated: 2026-08-01 KST

## Evidence captured

- A read-only weekend diagnostic succeeded for all executable KIS quotation endpoints.
- The diagnostic confirmed response containers and expected field presence.
- It was not accepted as live-session semantic evidence:
  - stock minute rows were returned with after-hours timestamps;
  - program rows have no source trade-date field;
  - index output includes special `999999` rows;
  - therefore the verification registry remains `PARTIAL`.

## Implemented safeguards

- The four market-betting probes now skip weekends unless explicitly forced for diagnostics.
- Every live probe records deterministic contract checks:
  - weekday/session request time;
  - non-empty output;
  - normal market-time range;
  - source trade date matching the request date where supplied;
  - OHLC invariants for stock, index, and futures bars;
  - numeric program-flow fields;
  - explicit request-context-only provenance for program dates.
- A single run can only become `REVIEW_READY`; it never changes the registry.
- Verification readiness requires all four probes to pass at OPEN, MID, and CLOSE checkpoints on the same trading day.
- The generated `contract_readiness.json` explicitly records `auto_promotes_registry: false`.

## Next live checkpoint

- Oracle captures at 09:45, 12:00, and 15:25 KST on weekdays.
- After all three checkpoints pass, inspect units and economic meaning against the official KIS contract before manually promoting individual fields.

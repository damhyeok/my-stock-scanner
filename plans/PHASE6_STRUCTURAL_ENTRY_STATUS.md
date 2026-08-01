# Phase 6: Structural entry and invalidation

Updated: 2026-08-01 KST

## Implemented

- Breakout setup: prior 20-bar resistance, buffered trigger, failed-breakout invalidation, and measured-range reward reference.
- Pullback setup: prior impulse, VWAP-zone retest, rebound confirmation, pullback-low invalidation, and prior-high reward reference.
- Explicit output: setup type, entry reference, invalidation price, reward reference, risk per share, reward per share, and reward/risk ratio.
- State linkage:
  - `SETUP`: valid structure, price trigger pending.
  - `TRIGGERED`: price trigger plus market/sector/relative-strength/activity gates and an explicit invalidation price.
  - `EXTENDED`: stop distance is too wide or structural reward/risk is below the configured minimum.
  - insufficient bars never manufacture an invalidation price.
- The existing Streamlit tab displays the structural values beside each stock state.

## Guardrails

- A one-minute VWAP cross is not used as a universal stop.
- Market and sector permissions remain above the stock setup gate.
- All thresholds are expert placeholders in the versioned config, not backtest-optimized constants.
- Reward reference is a structural comparison point, not a promised target price.
- The module is analysis-only and has no order client.

## Remaining

- Use live verified fields before actionable states are allowed.
- Add multi-bar failed-breakout/pullback invalidation monitoring after a trigger.
- Add position-specific thesis input for `HOLD_EXISTING`.

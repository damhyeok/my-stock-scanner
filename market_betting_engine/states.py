"""Permission gates and auditable stock-state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set, Tuple

from .contracts import (
    AxisSignal,
    AxisStatus,
    DataQualityReport,
    Evidence,
    Judgment,
    MarketPermission,
    StockState,
)


@dataclass(frozen=True)
class MarketGateConfig:
    # Two independent verified axes can support a reduced-confidence SELECTIVE
    # decision.  Requiring all three made a temporarily unavailable program
    # flow feed indistinguishable from unusable market data, even when price
    # action and futures confirmation were both valid.
    minimum_available_axes: int = 2
    hard_veto_independent_failures: int = 2


def assess_market_permission(
    signals: Iterable[AxisSignal],
    quality: DataQualityReport = DataQualityReport(),
    config: MarketGateConfig = MarketGateConfig(),
) -> Judgment:
    """Apply the agreed multi-axis rule; unavailable data is not a bearish call."""

    signal_list = tuple(signals)
    available = [item for item in signal_list if item.status != AxisStatus.UNAVAILABLE]
    available_axes = {item.axis for item in available}
    unique_fail_axes = {item.axis for item in available if item.status == AxisStatus.FAIL}
    warning_signals = [item for item in available if item.status == AxisStatus.WARNING]

    def evidence(item: AxisSignal) -> Evidence:
        return Evidence(item.reason_code, item.axis, item.message)

    blockers = tuple(evidence(item) for item in available if item.status == AxisStatus.FAIL)
    warnings = tuple(evidence(item) for item in warning_signals)
    supporting = tuple(evidence(item) for item in available if item.status == AxisStatus.PASS)
    unavailable = tuple(evidence(item) for item in signal_list if item.status == AxisStatus.UNAVAILABLE)

    if quality.blocking or len(available_axes) < config.minimum_available_axes:
        decision = MarketPermission.NOT_EVALUABLE
        blockers = blockers + unavailable
    elif len(unique_fail_axes) >= config.hard_veto_independent_failures:
        decision = MarketPermission.BLOCK
    elif unique_fail_axes or warning_signals or unavailable:
        decision = MarketPermission.SELECTIVE
    else:
        decision = MarketPermission.ALLOW

    return Judgment(
        decision=decision.value,
        evidence=supporting,
        counter_evidence=blockers,
        warnings=warnings,
        blockers=blockers if decision in (MarketPermission.BLOCK, MarketPermission.NOT_EVALUABLE) else (),
        quality=quality,
        confidence_label="STRUCTURAL_RULE",
    )


@dataclass(frozen=True)
class StockGateSignals:
    market_permits_entry: bool
    sector_permits_entry: bool
    setup_ready: bool = False
    trigger_confirmed: bool = False
    structural_invalidation_price_defined: bool = False
    extended_risk_reward: bool = False
    reaction_failed: bool = False
    thesis_invalidated: bool = False
    data_evaluable: bool = True


@dataclass(frozen=True)
class StateTransition:
    previous: StockState
    current: StockState
    reason_code: str
    allowed: bool


_ALLOWED_TRANSITIONS: Dict[StockState, Set[StockState]] = {
    StockState.WATCH: {
        StockState.WATCH,
        StockState.SETUP,
        StockState.EXTENDED,
        StockState.INVALIDATED,
        StockState.NOT_EVALUABLE,
    },
    StockState.SETUP: {
        StockState.WATCH,
        StockState.SETUP,
        StockState.TRIGGERED,
        StockState.EXTENDED,
        StockState.INVALIDATED,
        StockState.NOT_EVALUABLE,
    },
    StockState.TRIGGERED: {
        StockState.TRIGGERED,
        StockState.EXTENDED,
        StockState.FAILED,
        StockState.INVALIDATED,
        StockState.NOT_EVALUABLE,
    },
    StockState.EXTENDED: {
        StockState.EXTENDED,
        StockState.SETUP,
        StockState.FAILED,
        StockState.INVALIDATED,
        StockState.NOT_EVALUABLE,
    },
    StockState.FAILED: {StockState.FAILED, StockState.WATCH, StockState.INVALIDATED, StockState.NOT_EVALUABLE},
    StockState.INVALIDATED: {StockState.INVALIDATED},
    # Once missing data recovers, a name may already be too extended to enter
    # or its thesis may already be invalid.  Both are conservative/no-entry
    # destinations and must be reachable without forcing an artificial WATCH
    # cycle first.
    StockState.NOT_EVALUABLE: {
        StockState.NOT_EVALUABLE,
        StockState.WATCH,
        StockState.SETUP,
        StockState.EXTENDED,
        StockState.INVALIDATED,
    },
}


def validate_stock_transition(previous: StockState, current: StockState, *, rearm: bool = False) -> bool:
    if previous == StockState.INVALIDATED and rearm and current == StockState.WATCH:
        return True
    return current in _ALLOWED_TRANSITIONS[previous]


def resolve_stock_state(
    previous: StockState,
    signals: StockGateSignals,
    *,
    rearm: bool = False,
) -> StateTransition:
    """Resolve state with invalidation/failure precedence and no implicit entry."""

    if previous == StockState.INVALIDATED and not rearm:
        proposed, reason = StockState.INVALIDATED, "THESIS_REMAINS_INVALIDATED"
    elif not signals.data_evaluable:
        proposed, reason = StockState.NOT_EVALUABLE, "REQUIRED_DATA_NOT_EVALUABLE"
    elif signals.thesis_invalidated:
        proposed, reason = StockState.INVALIDATED, "STRUCTURAL_THESIS_INVALIDATED"
    elif signals.reaction_failed and previous in (StockState.TRIGGERED, StockState.EXTENDED):
        proposed, reason = StockState.FAILED, "POST_TRIGGER_REACTION_FAILED"
    elif signals.extended_risk_reward:
        proposed, reason = StockState.EXTENDED, "RISK_REWARD_EXTENDED"
    elif not (signals.market_permits_entry and signals.sector_permits_entry):
        proposed, reason = StockState.WATCH, "UPPER_GATE_NOT_OPEN"
    elif signals.trigger_confirmed and signals.structural_invalidation_price_defined and signals.setup_ready:
        proposed, reason = StockState.TRIGGERED, "ALL_TRIGGER_GATES_CONFIRMED"
    elif signals.setup_ready:
        proposed, reason = StockState.SETUP, "SETUP_READY_TRIGGER_PENDING"
    else:
        proposed, reason = StockState.WATCH, "WATCHING_FOR_SETUP"

    if previous == StockState.INVALIDATED and rearm and proposed == StockState.INVALIDATED:
        pass
    elif previous == StockState.INVALIDATED and rearm:
        proposed, reason = StockState.WATCH, "NEW_THESIS_REARMED"

    allowed = validate_stock_transition(previous, proposed, rearm=rearm)
    if not allowed:
        return StateTransition(previous, previous, "ILLEGAL_TRANSITION_REJECTED", False)
    return StateTransition(previous, proposed, reason, True)

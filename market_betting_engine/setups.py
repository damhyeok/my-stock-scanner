"""Structural breakout/pullback setup assessment with explicit invalidation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .features import BarFeatureSnapshot, NormalizedBar
from .contracts import StockState


class EntrySetupType(str, Enum):
    NONE = "NONE"
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"


@dataclass(frozen=True)
class EntrySetupConfig:
    breakout_lookback_bars: int = 20
    pullback_structure_bars: int = 8
    breakout_trigger_buffer_ratio: float = 0.001
    breakout_failure_buffer_ratio: float = 0.003
    setup_proximity_ratio: float = 0.005
    pullback_vwap_tolerance_ratio: float = 0.006
    minimum_impulse_return_ratio: float = 0.015
    minimum_reward_risk_ratio: float = 1.5
    maximum_risk_fraction: float = 0.03
    placeholder: bool = True


@dataclass(frozen=True)
class EntrySetupAssessment:
    setup_type: EntrySetupType
    state_hint: StockState
    evaluable: bool
    entry_reference: float | None = None
    invalidation_price: float | None = None
    reward_reference: float | None = None
    risk_per_share: float | None = None
    reward_per_share: float | None = None
    reward_risk_ratio: float | None = None
    reference_level: float | None = None
    reasons: tuple[str, ...] = ()
    placeholder: bool = True


def _empty(reason: str, placeholder: bool) -> EntrySetupAssessment:
    return EntrySetupAssessment(
        EntrySetupType.NONE,
        StockState.WATCH,
        False,
        reasons=(reason,),
        placeholder=placeholder,
    )


def _finalize(
    *,
    setup_type: EntrySetupType,
    triggered: bool,
    entry: float,
    invalidation: float,
    target: float,
    reference: float,
    reasons: tuple[str, ...],
    config: EntrySetupConfig,
) -> EntrySetupAssessment:
    risk = entry - invalidation
    reward = target - entry
    if entry <= 0 or risk <= 0:
        return _empty("STRUCTURAL_INVALIDATION_NOT_BELOW_ENTRY", config.placeholder)
    ratio = reward / risk if reward > 0 else 0.0
    extended = (
        risk / entry > config.maximum_risk_fraction
        or ratio < config.minimum_reward_risk_ratio
    )
    if extended:
        state = StockState.EXTENDED
        reasons = reasons + ("RISK_REWARD_EXTENDED",)
    elif triggered:
        state = StockState.TRIGGERED
        reasons = reasons + ("PRICE_TRIGGER_CONFIRMED",)
    else:
        state = StockState.SETUP
        reasons = reasons + ("PRICE_TRIGGER_PENDING",)
    return EntrySetupAssessment(
        setup_type=setup_type,
        state_hint=state,
        evaluable=True,
        entry_reference=entry,
        invalidation_price=invalidation,
        reward_reference=target,
        risk_per_share=risk,
        reward_per_share=max(0.0, reward),
        reward_risk_ratio=ratio,
        reference_level=reference,
        reasons=reasons,
        placeholder=config.placeholder,
    )


def assess_entry_setup(
    bars: Sequence[NormalizedBar],
    features: BarFeatureSnapshot,
    config: EntrySetupConfig = EntrySetupConfig(),
) -> EntrySetupAssessment:
    """Assess price structure only; upper market/sector gates remain separate."""

    required = max(config.breakout_lookback_bars + 1, config.pullback_structure_bars + 2)
    if len(bars) < required:
        return _empty("INSUFFICIENT_STRUCTURE_BARS", config.placeholder)
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    current = ordered[-1]
    previous = ordered[-2]
    prior = ordered[-config.breakout_lookback_bars - 1 : -1]
    resistance = max(bar.high for bar in prior)
    support = min(bar.low for bar in prior)
    range_width = resistance - support
    if resistance <= 0 or range_width <= 0:
        return _empty("INVALID_STRUCTURE_RANGE", config.placeholder)

    breakout_trigger = resistance * (1 + config.breakout_trigger_buffer_ratio)
    distance_to_resistance = current.close / resistance - 1
    breakout_candidate = distance_to_resistance >= -config.setup_proximity_ratio
    breakout_confirmed = current.close >= breakout_trigger
    if breakout_candidate:
        invalidation = resistance * (1 - config.breakout_failure_buffer_ratio)
        target = resistance + range_width
        return _finalize(
            setup_type=EntrySetupType.BREAKOUT,
            triggered=breakout_confirmed,
            entry=current.close,
            invalidation=invalidation,
            target=target,
            reference=resistance,
            reasons=(
                f"RESISTANCE={resistance:.4f}",
                f"DISTANCE_TO_RESISTANCE={distance_to_resistance:.6f}",
                "INVALIDATION_IS_FAILED_BREAKOUT_LEVEL",
            ),
            config=config,
        )

    vwap = float(features.session_vwap.value) if features.session_vwap.available else None
    if not vwap or vwap <= 0:
        return _empty("VWAP_UNAVAILABLE_FOR_PULLBACK", config.placeholder)
    structure = ordered[-config.pullback_structure_bars :]
    pullback_low = min(bar.low for bar in structure)
    prior_start = prior[0].close
    impulse_return = resistance / prior_start - 1 if prior_start > 0 else 0.0
    touched_vwap_zone = (
        pullback_low <= vwap * (1 + config.pullback_vwap_tolerance_ratio)
        and current.close >= vwap * (1 - config.pullback_vwap_tolerance_ratio)
    )
    below_prior_high = current.close < resistance
    pullback_candidate = (
        impulse_return >= config.minimum_impulse_return_ratio
        and touched_vwap_zone
        and below_prior_high
    )
    if not pullback_candidate:
        return EntrySetupAssessment(
            EntrySetupType.NONE,
            StockState.WATCH,
            True,
            reasons=("NO_BREAKOUT_OR_PULLBACK_STRUCTURE",),
            placeholder=config.placeholder,
        )
    rebound_confirmed = current.close > previous.close and current.close > current.open
    invalidation = pullback_low * (1 - config.breakout_failure_buffer_ratio)
    return _finalize(
        setup_type=EntrySetupType.PULLBACK,
        triggered=rebound_confirmed,
        entry=current.close,
        invalidation=invalidation,
        target=resistance,
        reference=vwap,
        reasons=(
            f"SESSION_VWAP={vwap:.4f}",
            f"PULLBACK_LOW={pullback_low:.4f}",
            f"IMPULSE_RETURN={impulse_return:.6f}",
            "INVALIDATION_IS_PULLBACK_STRUCTURE_LOW",
        ),
        config=config,
    )

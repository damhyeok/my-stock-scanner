"""Wire normalized observations through market, sector, and stock gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .adapters import AdapterResult
from .contracts import (
    AxisSignal,
    DataQualityReport,
    Judgment,
    MarketPermission,
    Observation,
    StockState,
)
from .engines import SectorCoverage, assess_sector_state
from .quality import combine_quality, evaluate_observations
from .session import SessionContext
from .states import StateTransition, StockGateSignals, assess_market_permission, resolve_stock_state


@dataclass(frozen=True)
class SectorDecisionInput:
    name: str
    signals: Tuple[AxisSignal, ...]
    coverage: SectorCoverage
    persistence_confirmed: bool


@dataclass(frozen=True)
class StockDecisionInput:
    symbol: str
    previous_state: StockState
    signals: StockGateSignals
    sector_name: str


@dataclass(frozen=True)
class DecisionCycleResult:
    quality: DataQualityReport
    market: Judgment
    sectors: Mapping[str, Judgment]
    stocks: Mapping[str, StateTransition]
    observation_count: int


def run_intraday_decision_cycle(
    *,
    context: SessionContext,
    adapter_results: Sequence[AdapterResult],
    market_signals: Iterable[AxisSignal],
    sector_inputs: Sequence[SectorDecisionInput],
    stock_inputs: Sequence[StockDecisionInput],
    expected_metrics: Optional[Iterable[str]] = None,
    require_verified_inputs: bool = True,
) -> DecisionCycleResult:
    """Run gates in dependency order without manufacturing missing signals."""

    observations = tuple(
        observation
        for result in adapter_results
        for observation in result.observations
    )
    adapter_issues = tuple(issue for result in adapter_results for issue in result.issues)
    observed_quality = evaluate_observations(
        observations,
        target_trade_date=context.target_trade_date,
        now=context.evaluated_at,
        expected_metrics=expected_metrics,
        require_verified_semantics=require_verified_inputs,
        require_source_trade_date=require_verified_inputs,
    )
    quality = combine_quality(
        observed_quality,
        DataQualityReport(
            issues=adapter_issues,
            observed_count=0,
            expected_count=None,
        ),
    )
    market = assess_market_permission(tuple(market_signals), quality)
    sectors: Dict[str, Judgment] = {}
    for sector in sector_inputs:
        sectors[sector.name] = assess_sector_state(
            sector.signals,
            sector.coverage,
            persistence_confirmed=sector.persistence_confirmed,
            quality=quality,
        )

    market_open_for_new_entries = market.decision in {
        MarketPermission.ALLOW.value,
        MarketPermission.SELECTIVE.value,
    }
    stocks: Dict[str, StateTransition] = {}
    for stock in stock_inputs:
        sector_judgment = sectors.get(stock.sector_name)
        sector_open = sector_judgment is not None and sector_judgment.decision in {"LEADING", "EMERGING"}
        source = stock.signals
        is_new_entry_state = stock.previous_state in {StockState.WATCH, StockState.SETUP, StockState.EXTENDED}
        gated = StockGateSignals(
            market_permits_entry=(source.market_permits_entry and market_open_for_new_entries)
            if is_new_entry_state else source.market_permits_entry,
            sector_permits_entry=(source.sector_permits_entry and sector_open)
            if is_new_entry_state else source.sector_permits_entry,
            setup_ready=source.setup_ready,
            trigger_confirmed=source.trigger_confirmed,
            structural_invalidation_price_defined=source.structural_invalidation_price_defined,
            extended_risk_reward=source.extended_risk_reward,
            reaction_failed=source.reaction_failed,
            thesis_invalidated=source.thesis_invalidated,
            data_evaluable=source.data_evaluable and not quality.blocking,
        )
        stocks[stock.symbol] = resolve_stock_state(stock.previous_state, gated)

    return DecisionCycleResult(quality, market, sectors, stocks, len(observations))

"""Oracle-friendly read-only runtime for one market-betting decision cycle."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .collector import collect_probe_observations
from .config import load_analysis_config
from .contracts import AxisSignal, AxisStatus, StockState
from .engines import SectorCoverage, assess_overnight_permissions
from .orchestrator import (
    SectorDecisionInput,
    StockDecisionInput,
    run_intraday_decision_cycle,
)
from .pipeline import derive_evidence_bundle
from .features import extract_bar_series
from .positions import assess_position, list_positions
from .session import KST, SessionContext
from .setups import (
    EntrySetupAssessment,
    TriggerLifecycleAssessment,
    assess_entry_setup,
    assess_trigger_lifecycle,
)
from .states import StockGateSignals
from .storage import PersistenceReceipt, prune_decision_history, save_decision_cycle
from .verification_registry import load_verification_registry
from .universe import AdaptiveUniverseSelection, build_adaptive_universe


ENGINE_VERSION = "oracle-runtime-v5-position-thesis"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "market_betting_engine.placeholder.json"
DEFAULT_VERIFICATION_REGISTRY = (
    Path(__file__).resolve().parents[1] / "config" / "market_betting_field_verification.json"
)


def _latest_trade_date(db_path: str | Path, as_of: date) -> date:
    try:
        with sqlite3.connect(db_path) as conn:
            values = conn.execute(
                "SELECT DISTINCT date FROM daily_stocks WHERE category='VOLUME_TOP_60'"
            ).fetchall()
    except sqlite3.OperationalError:
        return as_of
    candidates = []
    for (raw_value,) in values:
        text = str(raw_value or "").replace("-", "")
        try:
            parsed = datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            continue
        if parsed <= as_of:
            candidates.append(parsed)
    return max(candidates, default=as_of)


def _adaptive_universe(
    db_path: str | Path,
    trade_date: date,
    config,
) -> AdaptiveUniverseSelection:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        session = conn.execute(
            """
            SELECT session
            FROM daily_stocks
            WHERE date=? AND category='VOLUME_TOP_60'
            GROUP BY session
            ORDER BY MAX(collected_at_kst) DESC
            LIMIT 1
            """,
            (trade_date.strftime("%Y%m%d"),),
        ).fetchone()
        if session is None:
            return AdaptiveUniverseSelection((), (), 0)
        seed_rows = conn.execute(
            """
            SELECT ticker, MAX(name) AS name, MAX(sector) AS sector,
                   MAX(trading_value) AS trading_value,
                   MAX(fluctuation_rate) AS fluctuation_rate
            FROM daily_stocks
            WHERE date=? AND session=? AND category='VOLUME_TOP_60'
            GROUP BY ticker
            ORDER BY trading_value DESC
            """,
            (trade_date.strftime("%Y%m%d"), session["session"]),
        ).fetchall()
        discovery_rows = conn.execute(
            """
            SELECT ticker, MAX(name) AS name, MAX(sector) AS sector,
                   MAX(trading_value) AS trading_value,
                   MAX(fluctuation_rate) AS fluctuation_rate
            FROM daily_stocks
            WHERE date=? AND category IN ('VOLUME_TOP_60','FOREIGN_TOP_30','INST_TOP_30')
            GROUP BY ticker
            """,
            (trade_date.strftime("%Y%m%d"),),
        ).fetchall()
    return build_adaptive_universe(
        [dict(row) for row in seed_rows],
        [dict(row) for row in discovery_rows],
        config,
    )


def _previous_states(db_path: str | Path, symbols: Iterable[str]) -> dict[str, StockState]:
    wanted = set(symbols)
    if not wanted:
        return {}
    result: dict[str, StockState] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT s.symbol, s.current_state
                FROM market_betting_stock_states s
                JOIN market_betting_runs r ON r.run_id=s.run_id
                ORDER BY r.evaluated_at_kst DESC
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    for symbol, state in rows:
        if symbol in wanted and symbol not in result:
            try:
                result[symbol] = StockState(state)
            except ValueError:
                result[symbol] = StockState.WATCH
    return result


def _previous_setups(db_path: str | Path, symbols: Iterable[str]) -> dict[str, Mapping]:
    """Load the most recent persisted structural setup for each requested symbol."""

    wanted = set(symbols)
    if not wanted:
        return {}
    result: dict[str, Mapping] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT r.derived_evidence_json
                FROM market_betting_runs r
                ORDER BY r.evaluated_at_kst DESC
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    for (raw_json,) in rows:
        try:
            derived = json.loads(raw_json or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        setups = derived.get("stock_setups", {}) if isinstance(derived, dict) else {}
        if not isinstance(setups, dict):
            continue
        for symbol, setup in setups.items():
            if symbol in wanted and symbol not in result and isinstance(setup, dict):
                result[symbol] = setup
        if wanted.issubset(result):
            break
    return result


def _previous_sector_decisions(db_path: str | Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT j.scope_id, j.decision
                FROM market_betting_judgments j
                JOIN market_betting_runs r ON r.run_id=j.run_id
                WHERE j.scope_type='SECTOR'
                ORDER BY r.evaluated_at_kst DESC
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    for name, decision in rows:
        result.setdefault(name, decision)
    return result


def _unavailable_market_signals() -> tuple[AxisSignal, ...]:
    return tuple(
        AxisSignal(axis, AxisStatus.UNAVAILABLE, code, message)
        for axis, code, message in (
            ("price_action", "MARKET_PRICE_UNAVAILABLE", "KOSPI minute bars are unavailable"),
            ("actual_flow", "PROGRAM_FLOW_UNAVAILABLE", "program flow observations are unavailable"),
            ("futures", "FUTURES_CONFIRMATION_UNAVAILABLE", "futures minute bars are unavailable"),
        )
    )


def _stock_gate(
    signals: Sequence[AxisSignal],
    setup: EntrySetupAssessment,
    lifecycle: TriggerLifecycleAssessment,
) -> StockGateSignals:
    available = [signal for signal in signals if signal.status != AxisStatus.UNAVAILABLE]
    setup_ready = lifecycle.active or setup.state_hint in {StockState.SETUP, StockState.TRIGGERED}
    trigger_confirmed = lifecycle.active or (
        setup.state_hint == StockState.TRIGGERED
        and bool(available)
        and all(signal.status == AxisStatus.PASS for signal in available)
    )
    return StockGateSignals(
        market_permits_entry=True,
        sector_permits_entry=True,
        setup_ready=setup_ready,
        trigger_confirmed=trigger_confirmed,
        structural_invalidation_price_defined=(
            lifecycle.invalidation_price is not None or setup.invalidation_price is not None
        ),
        extended_risk_reward=not lifecycle.active and setup.state_hint == StockState.EXTENDED,
        reaction_failed=lifecycle.reaction_failed,
        thesis_invalidated=lifecycle.thesis_invalidated,
        data_evaluable=(setup.evaluable or lifecycle.active) and bool(available) and not any(
            signal.status == AxisStatus.UNAVAILABLE for signal in signals
        ),
    )


def _sector_members(universe: Sequence[Mapping]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in universe:
        sector = str(row.get("sector") or "기타").strip() or "기타"
        result.setdefault(sector, []).append(str(row["ticker"]).zfill(6))
    return result


def run_market_betting_analysis(
    db_path: str | Path = "stock_data.db",
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    verification_registry_path: str | Path = DEFAULT_VERIFICATION_REGISTRY,
    evaluated_at: datetime | None = None,
) -> PersistenceReceipt:
    """Collect allow-listed quotation data, evaluate it, and save one cycle.

    Provider fields remain PARTIAL until the separate live-session verification
    checklist promotes them. Consequently this runtime records evidence but
    deliberately returns NOT_EVALUABLE rather than manufacturing permission.
    """

    now = evaluated_at or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    now = now.astimezone(KST)
    target = _latest_trade_date(db_path, now.date())
    provisional = SessionContext(
        target,
        now,
        now.date() == target,
        "KIS_RESPONSE_CONFIRMATION_PENDING",
    )
    config = load_analysis_config(config_path)
    maximum = max(1, min(60, int(os.environ.get("MARKET_BETTING_MAX_STOCKS", str(config.universe.total_stock_limit)))))
    runtime_universe_config = type(config.universe)(
        candidate_sector_limit=config.universe.candidate_sector_limit,
        stocks_per_sector=config.universe.stocks_per_sector,
        total_stock_limit=maximum,
        placeholder=config.universe.placeholder,
    )
    selection = _adaptive_universe(db_path, target, runtime_universe_config)
    universe = list(selection.stocks)
    candidate_symbols = [str(row["ticker"]).zfill(6) for row in universe]
    positions = list_positions(db_path)
    symbols = list(dict.fromkeys(candidate_symbols + [position.ticker for position in positions]))
    verification_registry = load_verification_registry(verification_registry_path)

    def collect(probe_id: str, *, instrument: str, ticker: str = "005930"):
        return collect_probe_observations(
            probe_id,
            context=provisional,
            instrument=instrument,
            ticker=ticker,
            field_verification_statuses={
                field_name: status.value
                for field_name, status in verification_registry.statuses_for_probe(probe_id).items()
            },
        )

    collections = [
        collect("kis_index_minute_kospi", instrument="KOSPI"),
        collect("kis_program_summary_kospi", instrument="KOSPI"),
        collect("kis_futures_minute_active", instrument="ACTIVE"),
    ]
    collections.extend(
        collect("kis_stock_minute", instrument=symbol, ticker=symbol)
        for symbol in symbols
    )

    index_probe = collections[0].probe
    exchange_session_confirmed = target.strftime("%Y%m%d") in index_probe.source_trade_dates
    context = SessionContext(
        target,
        now,
        exchange_session_confirmed,
        "KIS_INDEX_RESPONSE" if exchange_session_confirmed else "KIS_INDEX_RESPONSE_MISSING",
    )
    adapter_results = [item.adapted for item in collections]
    observations = tuple(
        observation for result in adapter_results for observation in result.observations
    )
    members = _sector_members(universe)
    bundle = None
    try:
        bundle = derive_evidence_bundle(
            observations,
            stock_symbols=symbols,
            sector_members=members,
            feature_config=config.feature,
            signal_thresholds=config.signals,
        )
        market_signals = bundle.market_signals
    except ValueError:
        market_signals = _unavailable_market_signals()

    sector_inputs = []
    stock_inputs = []
    stock_setups = {}
    stock_lifecycles = {}
    if bundle is not None:
        turnover = {
            str(row["ticker"]).zfill(6): float(row.get("trading_value") or 0)
            for row in universe
        }
        previous_sectors = _previous_sector_decisions(db_path)
        for name, evidence in bundle.sectors.items():
            requested_turnover = sum(turnover.get(symbol, 0) for symbol in evidence.requested_members)
            observed_values = [turnover.get(symbol, 0) for symbol in evidence.observed_members]
            sector_inputs.append(
                SectorDecisionInput(
                    name=name,
                    signals=evidence.signals,
                    coverage=SectorCoverage(
                        total_members=len(evidence.requested_members),
                        observed_members=len(evidence.observed_members),
                        total_universe_turnover=requested_turnover,
                        observed_turnover=sum(observed_values),
                        leader_turnover=max(observed_values, default=0),
                        universe_complete=False,
                    ),
                    persistence_confirmed=previous_sectors.get(name) in {"LEADING", "EMERGING"},
                )
            )
        previous = _previous_states(db_path, candidate_symbols)
        previous_setups = _previous_setups(db_path, candidate_symbols)
        symbol_to_sector = {
            symbol: sector for sector, sector_symbols in members.items() for symbol in sector_symbols
        }
        for symbol in candidate_symbols:
            evidence = bundle.stocks.get(symbol)
            if evidence is None:
                continue
            bars = extract_bar_series(observations, f"stock.{symbol}")
            setup = assess_entry_setup(bars, evidence.features, config.setup)
            previous_state = previous.get(symbol, StockState.WATCH)
            lifecycle = assess_trigger_lifecycle(
                bars, previous_state, previous_setups.get(symbol), config.setup
            )
            stock_setups[symbol] = setup
            stock_lifecycles[symbol] = lifecycle
            stock_inputs.append(
                StockDecisionInput(
                    symbol=symbol,
                    previous_state=previous_state,
                    signals=_stock_gate(evidence.signals, setup, lifecycle),
                    sector_name=symbol_to_sector.get(symbol, "기타"),
                )
            )

    result = run_intraday_decision_cycle(
        context=context,
        adapter_results=adapter_results,
        market_signals=market_signals,
        sector_inputs=sector_inputs,
        stock_inputs=stock_inputs,
        require_verified_inputs=True,
    )
    position_assessments = {}
    for position in positions:
        bars = extract_bar_series(observations, f"stock.{position.ticker}")
        current_price = bars[-1].close if bars else None
        position_assessments[position.ticker] = assess_position(
            position,
            current_price,
            market_signals,
            result.quality,
        )
    thesis_values = [assessment.thesis_valid for assessment in position_assessments.values()]
    if any(value is False for value in thesis_values):
        aggregate_thesis_valid = False
    elif thesis_values and all(value is True for value in thesis_values):
        aggregate_thesis_valid = True
    else:
        aggregate_thesis_valid = None
    overnight = assess_overnight_permissions(
        market_signals,
        existing_thesis_valid=aggregate_thesis_valid,
        quality=result.quality,
    )
    derived = {
        "bundle": asdict(bundle) if bundle is not None else None,
        "tracked_universe_count": len(universe),
        "tracked_universe_is_complete_market_breadth": False,
        "adaptive_universe": asdict(selection),
        "stock_setups": {symbol: asdict(setup) for symbol, setup in stock_setups.items()},
        "stock_lifecycles": {
            symbol: asdict(lifecycle) for symbol, lifecycle in stock_lifecycles.items()
        },
        "position_assessments": {
            ticker: asdict(assessment)
            for ticker, assessment in position_assessments.items()
        },
        "verification_registry_version": verification_registry.registry_version,
        "probe_statuses": [
            {
                "probe_id": item.probe.probe_id,
                "execution_status": item.probe.execution_status,
                "verification_status": item.probe.verification_status,
                "row_count": item.probe.output_row_count,
            }
            for item in collections
        ],
    }
    receipt = save_decision_cycle(
        db_path,
        context=context,
        result=result,
        config_version=config.config_version,
        engine_version=ENGINE_VERSION,
        observations=observations,
        derived_evidence=derived,
        overnight=overnight,
    )
    prune_decision_history(
        db_path,
        keep_run_trade_dates=int(os.environ.get("MARKET_BETTING_KEEP_RUN_DATES", "30")),
        keep_raw_observation_trade_dates=int(
            os.environ.get("MARKET_BETTING_KEEP_RAW_DATES", "2")
        ),
    )
    print(
        f"[Market Betting] run={receipt.run_id} market={result.market.decision} "
        f"observations={receipt.observation_count} quality_blocking={result.quality.blocking}"
    )
    return receipt

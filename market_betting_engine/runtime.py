"""Oracle-friendly read-only runtime for one market-betting decision cycle."""

from __future__ import annotations

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
from .session import KST, SessionContext
from .states import StockGateSignals
from .storage import PersistenceReceipt, prune_decision_history, save_decision_cycle


ENGINE_VERSION = "oracle-runtime-v1"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "market_betting_engine.placeholder.json"


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


def _latest_universe(db_path: str | Path, trade_date: date, limit: int) -> list[dict]:
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
            return []
        rows = conn.execute(
            """
            SELECT ticker, MAX(name) AS name, MAX(sector) AS sector,
                   MAX(trading_value) AS trading_value
            FROM daily_stocks
            WHERE date=? AND session=? AND category='VOLUME_TOP_60'
            GROUP BY ticker
            ORDER BY trading_value DESC
            LIMIT ?
            """,
            (trade_date.strftime("%Y%m%d"), session["session"], limit),
        ).fetchall()
    return [dict(row) for row in rows]


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


def _stock_gate(signals: Sequence[AxisSignal]) -> StockGateSignals:
    available = [signal for signal in signals if signal.status != AxisStatus.UNAVAILABLE]
    statuses = {signal.axis: signal.status for signal in available}
    setup_ready = (
        statuses.get("relative_strength") == AxisStatus.PASS
        and statuses.get("activity") != AxisStatus.FAIL
        and any(signal.axis == "price_action" and signal.status == AxisStatus.PASS for signal in available)
    )
    trigger_confirmed = setup_ready and all(signal.status == AxisStatus.PASS for signal in available)
    return StockGateSignals(
        market_permits_entry=True,
        sector_permits_entry=True,
        setup_ready=setup_ready,
        trigger_confirmed=trigger_confirmed,
        # A structural stop is not inferred from a generic minute-bar pattern.
        structural_invalidation_price_defined=False,
        data_evaluable=bool(available) and not any(
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
    maximum = max(1, min(60, int(os.environ.get("MARKET_BETTING_MAX_STOCKS", "60"))))
    universe = _latest_universe(db_path, target, maximum)
    symbols = [str(row["ticker"]).zfill(6) for row in universe]

    collections = [
        collect_probe_observations(
            "kis_index_minute_kospi", context=provisional, instrument="KOSPI"
        ),
        collect_probe_observations(
            "kis_program_summary_kospi", context=provisional, instrument="KOSPI"
        ),
        collect_probe_observations(
            "kis_futures_minute_active", context=provisional, instrument="ACTIVE"
        ),
    ]
    collections.extend(
        collect_probe_observations(
            "kis_stock_minute", context=provisional, instrument=symbol, ticker=symbol
        )
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
    config = load_analysis_config(config_path)
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
        previous = _previous_states(db_path, bundle.stocks)
        symbol_to_sector = {
            symbol: sector for sector, sector_symbols in members.items() for symbol in sector_symbols
        }
        for symbol, evidence in bundle.stocks.items():
            stock_inputs.append(
                StockDecisionInput(
                    symbol=symbol,
                    previous_state=previous.get(symbol, StockState.WATCH),
                    signals=_stock_gate(evidence.signals),
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
    overnight = assess_overnight_permissions(
        market_signals,
        existing_thesis_valid=None,
        quality=result.quality,
    )
    derived = {
        "bundle": asdict(bundle) if bundle is not None else None,
        "tracked_universe_count": len(universe),
        "tracked_universe_is_complete_market_breadth": False,
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

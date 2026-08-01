"""Bounded two-stage sector and stock universe selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class AdaptiveUniverseConfig:
    candidate_sector_limit: int = 5
    stocks_per_sector: int = 8
    total_stock_limit: int = 30
    placeholder: bool = True


@dataclass(frozen=True)
class CandidateSector:
    name: str
    seed_member_count: int
    turnover_activity: float
    turnover_share: float
    advancing_ratio: float
    equal_weight_return: float
    leader_concentration: float


@dataclass(frozen=True)
class AdaptiveUniverseSelection:
    candidates: tuple[CandidateSector, ...]
    stocks: tuple[dict, ...]
    discovery_stock_count: int
    selection_method: str = "TOP60_ACTIVITY_THEN_CANDIDATE_SECTOR_EXPANSION"


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _sector(value) -> str:
    return str(value or "").strip()


def _deduplicate(rows: Sequence[Mapping]) -> list[dict]:
    selected: dict[str, dict] = {}
    for source in rows:
        ticker = str(source.get("ticker") or "").strip().zfill(6)
        if len(ticker) != 6 or not ticker.isdigit():
            continue
        row = dict(source)
        row["ticker"] = ticker
        row["sector"] = _sector(row.get("sector"))
        row["trading_value"] = _number(row.get("trading_value"))
        row["fluctuation_rate"] = _number(row.get("fluctuation_rate"))
        current = selected.get(ticker)
        if current is None or row["trading_value"] > current["trading_value"]:
            selected[ticker] = row
    return list(selected.values())


def select_candidate_sectors(
    top_turnover_rows: Sequence[Mapping],
    config: AdaptiveUniverseConfig = AdaptiveUniverseConfig(),
) -> tuple[CandidateSector, ...]:
    """Rank activity candidates without calling trading value actual money inflow."""

    rows = [
        row for row in _deduplicate(top_turnover_rows)
        if _sector(row.get("sector")) not in {"", "기타"}
    ]
    total_turnover = sum(row["trading_value"] for row in rows)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["sector"], []).append(row)

    candidates = []
    for name, members in grouped.items():
        turnover = sum(row["trading_value"] for row in members)
        returns = [row["fluctuation_rate"] for row in members]
        leader = max((row["trading_value"] for row in members), default=0.0)
        candidates.append(
            CandidateSector(
                name=name,
                seed_member_count=len(members),
                turnover_activity=turnover,
                turnover_share=turnover / total_turnover if total_turnover else 0.0,
                advancing_ratio=sum(value > 0 for value in returns) / len(returns),
                equal_weight_return=sum(returns) / len(returns),
                leader_concentration=leader / turnover if turnover else 0.0,
            )
        )
    # This is an activity shortlist, not a claim of actual capital inflow.
    candidates.sort(
        key=lambda item: (
            item.turnover_activity,
            item.seed_member_count,
            item.advancing_ratio,
            item.equal_weight_return,
        ),
        reverse=True,
    )
    return tuple(candidates[: config.candidate_sector_limit])


def build_adaptive_universe(
    top_turnover_rows: Sequence[Mapping],
    discovery_rows: Sequence[Mapping],
    config: AdaptiveUniverseConfig = AdaptiveUniverseConfig(),
) -> AdaptiveUniverseSelection:
    """Expand only candidate sectors and cap deep minute-bar collection."""

    candidates = select_candidate_sectors(top_turnover_rows, config)
    candidate_names = {item.name for item in candidates}
    discovery = [
        row for row in _deduplicate((*top_turnover_rows, *discovery_rows))
        if row.get("sector") in candidate_names
    ]
    by_sector: dict[str, list[dict]] = {item.name: [] for item in candidates}
    for row in discovery:
        by_sector[row["sector"]].append(row)
    for members in by_sector.values():
        members.sort(key=lambda row: row["trading_value"], reverse=True)

    selected = []
    # Round-robin prevents the first high-turnover sector from consuming the cap.
    for rank in range(config.stocks_per_sector):
        for candidate in candidates:
            members = by_sector[candidate.name]
            if rank < len(members):
                selected.append(members[rank])
                if len(selected) >= config.total_stock_limit:
                    return AdaptiveUniverseSelection(candidates, tuple(selected), len(discovery))
    return AdaptiveUniverseSelection(candidates, tuple(selected), len(discovery))

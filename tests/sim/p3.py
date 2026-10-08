"""P3 시뮬레이션 공용 — 장중 슬롯의 기대 데이터 키·행 수, 창 전 데이터 심기(docs/p3_design.md §8.3).

기대값은 **등록부·설정·가짜 서버 상수에서 계산한다**
(하드코딩 금지 — §8.3 '기대 키 수는 등록부에서').
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay
from kbj.data.catalog import keys_for
from kbj.data.spec import DataKey
from kbj.services.scheduler.registry import Registry
from tests.fakes.kis_server import RANK_ROWS, SECTORS_PER_MARKET
from tests.sim.harness import KST, SimDay

INTRADAY_JOBS: Final = ("flows.intraday", "market.intraday")
N_SEED_ETFS: Final = 60  # 감시 상위 N(markets.yaml etf.watch_top_n — 50)보다 많이
SEED_ETF_PREFIX: Final = "985"  # 합성 ETF 코드 대역(가짜 KRX 995xxx·운용사 99xxx0 과 겹치지 않게)


def slot_label(t: datetime) -> str:
    return t.astimezone(KST).strftime("%Y-%m-%dT%H:%M")


def slots(first: datetime, last: datetime) -> list[str]:
    """first..last(양 끝 포함) 10분 슬롯의 as_of."""
    out: list[str] = []
    t = first
    while t <= last:
        out.append(slot_label(t))
        t += timedelta(minutes=10)
    return out


def intraday_keys(reg: Registry, sim: SimDay, as_of: str) -> dict[DataKey, str]:
    """그 슬롯에 두 장중 작업이 잡아야 할 데이터 키 → 작업(등록부 collects 그대로 펼친다)."""
    out: dict[DataKey, str] = {}
    for name in INTRADAY_JOBS:
        for c in reg.by_name(name).collects:
            for k in keys_for(sim.catalog[c.dataset_id], as_of, c.venues or None):
                out[k] = name
    return out


@dataclass(frozen=True)
class SlotRows:
    index: int
    sector: int
    rank: int
    etf_quote: int


def expected_rows(sim: SimDay, n_watch_candidates: int) -> SlotRows:
    """슬롯마다 이력 표에 쌓여야 할 행 수(가짜 서버 상수 × 설정)."""
    cfg = sim.markets()
    n_venues = len(cfg.kis.venues)
    return SlotRows(
        index=len(cfg.market.intraday_indices),
        sector=SECTORS_PER_MARKET * 2,  # 시장 2회 호출(코스피·코스닥)
        rank=RANK_ROWS * 2 * n_venues,  # 시장 2 × 거래소
        etf_quote=min(cfg.etf.watch_top_n, n_watch_candidates),
    )


def seed_watch_etfs(day: date, n: int = N_SEED_ETFS) -> Callable[[SimDay], None]:
    """창 전 거래일의 KRX ETF 일별(순자산) — `flows.intraday` 감시 목록(순자산 상위 N)의 근거."""

    def seed(sim: SimDay) -> None:
        rows = [
            EtfDay(
                f"{SEED_ETF_PREFIX}{i:03d}",
                day,
                f"합성감시ETF{i:03d}",
                10_000.0,
                10_000.0,
                1_000_000,
                (n - i) * 10_000_000_000,
                1,
                1,
                1,
                "코스피 200",
                "krx",
                "KRX",
                Quality.OK,
            )
            for i in range(n)
        ]
        sim.repos.etf.upsert_etf_days(rows, loaded_by="sim.seed")

    return seed


def rows_at(values: Iterable[datetime], ts: datetime) -> int:
    return sum(1 for v in values if v == ts)

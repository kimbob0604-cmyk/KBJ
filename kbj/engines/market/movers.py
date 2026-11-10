"""급등·급락 — 시총 하한 이상 보통주의 1일 등락률 상위·하위(docs/metrics.md §6.8).

- 입력: 원장(`kbj.engines.flows.ledger`) **한 날**의 행 — 등락률·시총은 그날 마감 스냅 값,
  이름·시장·종류는 원장이 정한 신원(스냅·유니버스, 없으면 이름·코드 `board.kinds.of`). 화면마다
  따로 받은 숫자를 섞지 않는다(metrics §0).
- 대상: 코스피·코스닥(`MAIN_MARKETS`) 보통주(`common`) — 우선주·스팩·리츠·ETF·ETN·코넥스는 밖.
  관리종목·거래정지·정리매매(`EXCLUDE_FLAGS`)는 뺀다. 상태를 모르면 뺄 수 없어 남기고 센다.
- 시총 ≥ `min_mktcap`(원 — 경계 포함). 시총·등락률을 모르거나 invalid 인 행은 빼고 센다(0 으로
  보지 않는다 — 절대 규칙 2).
- 급등 = 등락률 > 0 내림차순, 급락 = 등락률 < 0 오름차순, 각 `n` 개. 같으면 시총 큰 순 → 코드.
  보합(0)은 어느 쪽에도 없다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import MAIN_MARKETS, LedgerRow, source_label, worst_quality

__all__ = ["MIN_MKTCAP_WON", "MOVERS_N", "MOVER_KINDS", "Mover", "Movers", "top_movers"]

# 3,000억 — Beom's Dashboard 첫 화면과 같은 하한 [확인 필요]
MIN_MKTCAP_WON: Final = 300_000_000_000
MOVERS_N: Final = 10
MOVER_KINDS: Final[tuple[str, ...]] = ("common",)  # 보통주만 [확인 필요 — 우선주를 넣을지]


@dataclass(frozen=True)
class Mover:
    code: str
    name: str | None
    market: str | None
    close: float | None
    chg_pct: float
    mktcap: int  # 원
    source: str
    quality: Quality


@dataclass(frozen=True)
class Movers:
    date: date
    min_mktcap: int
    n_eligible: int  # 하한·종류·상태를 통과하고 등락률을 아는 종목 수(순위를 매긴 모집단)
    n_no_mktcap: int  # 시총을 몰라 뺀 종목
    n_no_chg: int  # 등락률을 모르거나 invalid 라 뺀 종목
    n_status_unknown: int  # 관리·정지 상태를 몰라 빼지 못한 종목(n_eligible 안)
    up: tuple[Mover, ...]
    down: tuple[Mover, ...]
    source: str  # 모집단 행의 원천 묶음(예 'KRX', 'KRX+KIS') — 모집단이 비면 ''
    quality: Quality | None  # 모집단 행 중 가장 나쁜 것(없으면 None)


def _raw_sources(r: LedgerRow) -> list[str]:
    """등락률·시총(스냅)과 종가의 원천. 둘 다 없으면 행의 원천 전부('+' 로 이은 것은 나눈다)."""
    own = [s for k in ("snap", "close") if (s := r.sources.get(k))]
    return own or [p for s in r.sources.values() for p in s.split("+") if p]


def top_movers(
    rows: Iterable[LedgerRow],
    day: date,
    *,
    min_mktcap: int = MIN_MKTCAP_WON,
    n: int = MOVERS_N,
    markets: Collection[str] = MAIN_MARKETS,
    kinds: Collection[str] = MOVER_KINDS,
) -> Movers:
    """그날 원장 행 → 급등·급락 상위 n 개씩(metrics §6.8). `day` 가 아닌 행은 무시한다."""
    if min_mktcap < 0:
        raise ValueError("min_mktcap 은 0 이상")
    if n < 1:
        raise ValueError("n 은 1 이상")
    pool: list[tuple[float, int, LedgerRow]] = []  # (등락률, 시총, 행)
    no_cap = no_chg = unknown = 0
    for r in rows:
        if r.date != day or r.market not in markets or r.kind not in kinds or r.flagged:
            continue
        if not r.usable or r.chg_pct is None:
            no_chg += 1
            continue
        if r.mktcap is None:
            no_cap += 1
            continue
        if r.mktcap < min_mktcap:
            continue
        if r.flagged is None:
            unknown += 1
        pool.append((r.chg_pct, r.mktcap, r))

    def mover(chg: float, cap: int, r: LedgerRow) -> Mover:
        return Mover(
            code=r.code, name=r.name, market=r.market, close=r.close, chg_pct=chg, mktcap=cap,
            source=source_label(_raw_sources(r)) or "KBJ", quality=r.quality,
        )  # fmt: skip

    up = sorted((x for x in pool if x[0] > 0), key=lambda x: (-x[0], -x[1], x[2].code))
    down = sorted((x for x in pool if x[0] < 0), key=lambda x: (x[0], -x[1], x[2].code))
    return Movers(
        date=day,
        min_mktcap=min_mktcap,
        n_eligible=len(pool),
        n_no_mktcap=no_cap,
        n_no_chg=no_chg,
        n_status_unknown=unknown,
        up=tuple(mover(*x) for x in up[:n]),
        down=tuple(mover(*x) for x in down[:n]),
        source=source_label(s for _c, _m, r in pool for s in _raw_sources(r)),
        quality=worst_quality(r.quality for _c, _m, r in pool),
    )

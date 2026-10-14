"""수집 대상 문맥 (docs/phase1_design.md §5·§7, PLAN §2.3·§4.4).

- 시리즈 = (시장분류, 6자리 만기). 시장분류는 월물리스트·전광판 `FID_COND_MRKT_CLS_CODE`
  ('' 월물, WKM 위클리(월), WKI 위클리(목)) — 같은 `261001` 이 WKM·WKI 둘 다 있다
- 최종거래일 원천은 KIS 단건 `futs_last_tr_date`(만기 목록은 월물리스트, PLAN §2.3). 캘린더 계산은
  교차검증용이고, KIS 값을 못 얻었을 때만 대신 쓴다
- 대상 만기: 살아 있는(만기일 15:20 KST 전) 시리즈를 최종거래일 순으로 세워 최근접·차기, 첫 월물.
  만기일 15:20 이 지나면 그 시리즈는 빠지고 다음이 최근접이 된다
- 선물도 같다: 최종거래일 15:20 이 지난 종목(분기 만기일의 근월물)은 건너뛴다
  (`ChainContext.live_futures_codes`). 선물 최종거래일은 마스터 결제월(`F YYYYMM`)과 같은 달 월물
  옵션의 최종거래일(월물리스트·KIS 값), 없으면 캘린더 계산(둘째 목요일). 마스터에 코스피200 선물이
  있는데 없는 종목(만기 지나 새 마스터에서 빠진 근월물)도 건너뛴다 — 선물 전광판 순서엔 남아 있다
- ATM 은 선물 근월물 가격으로 `core.chain` 이 고른다 — 전광판 `atm_cls_name` 은 쓰지 않는다(#11a).
  기준가(`ChainContext.reference`)는 살아 있는 종목의 시세만이다
- 보강 1 = 월물 ATM±n(마스터 행사가), 보강 2 = 추적 만기의 나머지 행사가(전광판·보강 1 제외),
  ATM 가까운 순(`core.chain.by_distance`)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from kbj.core.calendar import session_tag  # noqa: F401 — 다시 내보내기(KBJ P2)

# KBJ P7(FND-B): 체인 문맥 모델(시리즈·대상 만기·시리즈 체인·가변 문맥)은 정본
# `kbj.services.runtime.chain_feed` 로 옮겼다 — 다시 내보낸다. 아래 남은 것은 poller 수집 계획만.
from kbj.services.runtime.chain_feed import (  # noqa: F401
    CPS,
    BoardSeen,
    ChainContext,
    ExpiryInfo,
    FuturesQuote,
    Series,
    SeriesChain,
    Tag,
    Targets,
    build_chain,
    estimate_last_trade_date,
    kospi200_futures,
    select_targets,
)

from core.chain import atm_window, by_distance, covers
from data.kis.models import FuturesBoardRow
from services.poller.records import CallPut


# KBJ P2(설계 §1.3): session_tag 는 kbj.core.calendar 로 승격했다 — 위 import 로 다시 내보낸다.


def resolve_candidates(listed: Mapping[str, Sequence[str]], monthly: int) -> list[Series]:
    """최종거래일을 조회할 시리즈: 위클리 전부 + 월물 목록 앞 `monthly` 개(목록은 결제월 순)."""
    out = [Series("", m) for m in list(listed.get("", ()))[:monthly]]
    out += [Series(cls, m) for cls in ("WKM", "WKI") for m in listed.get(cls, ())]
    return out


def near_month(rows: Sequence[FuturesBoardRow]) -> FuturesBoardRow | None:
    """선물 전광판에서 근월물 — 가격이 있는 행 중 잔존일수가 가장 짧은 것(같으면 앞 행)."""
    priced = [r for r in rows if r.futs_prpr is not None and r.futs_prpr > 0]
    if not priced:
        return None
    return min(priced, key=lambda r: 10**6 if r.hts_rmnn_dynu is None else r.hts_rmnn_dynu)


@dataclass(frozen=True)
class FillTarget:
    series: Series
    strike: Decimal
    cp: CallPut
    code: str

    @property
    def key(self) -> str:
        return f"{self.series.label}:{self.strike:.2f}:{self.cp}"


def fill1_targets(chain: SeriesChain, ref: Decimal, n: int) -> tuple[FillTarget, ...]:
    """ATM±n 행사가 × 콜·풋, ATM 가까운 순 (보강 1)."""
    window = atm_window(chain.strikes, ref, n)
    out: list[FillTarget] = []
    for k in by_distance(window, ref):
        for cp in CPS:
            code = chain.code(k, cp)
            if code is not None:
                out.append(FillTarget(chain.series, k, cp, code))
    return tuple(out)


def fill2_targets(
    chains: Sequence[SeriesChain],
    ref: Decimal,
    exclude: Mapping[Series, frozenset[tuple[Decimal, str]]],
) -> tuple[FillTarget, ...]:
    """추적 만기 전 행사가 − 제외(전광판이 준 것·보강 1), ATM 가까운 순 → 만기 순 → 콜·풋."""
    strikes = {k for ch in chains for k in ch.strikes}
    if not strikes:
        return ()
    out: list[FillTarget] = []
    for k in by_distance(strikes, ref):
        for ch in chains:
            ex = exclude.get(ch.series, frozenset())
            for cp in CPS:
                code = ch.code(k, cp)
                if code is not None and (k, cp) not in ex:
                    out.append(FillTarget(ch.series, k, cp, code))
    return tuple(out)


def board_covers(
    series: Series, board: Iterable[Decimal], ref: Decimal, fill1: Iterable[Decimal] = ()
) -> bool:
    """전광판 커버리지 (설계 §5): 위클리는 전광판이 ATM 을 담는가, 월물은 전광판 또는 보강 1 범위가.

    `covers` 는 이어진 구간을 가정하므로 두 구간을 따로 본다(합치면 가운데 빈 곳을 못 본다).
    """
    ks = list(board)
    if ks and covers(ks, ref):
        return True
    if series.weekly:
        return False
    f1 = list(fill1)
    return bool(f1) and covers(f1, ref)



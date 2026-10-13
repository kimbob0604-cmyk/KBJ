"""내부자 — 임원·주요주주 소유보고의 정정 대체와 30·90·180일 창.

docs/p4_design.md §4.4, docs/metrics.md §13.

KR 내부자 구현은 legacy 에 없다(SD 는 미국 yfinance 비중뿐 — 설계 §1.3). 새로 만든다.

정의(metrics §13 — 정본)
- 입력: 보고 1건 = 보고자 1명의 보고 후 소유·증감(`InsiderReport`).
- 정정: 같은 보고자·같은 원 보고(제목 정정 짝)면 **늦은 접수번호만**. 짝을 명시(`base_of` —
  정정 접수번호 → 원 접수번호)하지 않으면 정정 공시(`corrections`)가 같은 회사·같은 보고자의 바로
  앞 보고를 대체한다 [추정 — 정정은 보통 직전 보고를 고친다].
- 창: t 기준 최근 W 일(W = 30·90·180 달력일) — 공시일 rcept_dt ∈ [t − W, t](닫힌 구간). 접수일 > t
  인 보고·정정은 쓰지 않는다(PIT).
- 순증감(주) = Σ 증감, 매수·매도 합은 부호로 나눈다(순증감 = 매수 − 매도). 증감 칸이 없는 보고는
  합에서 빠지고 행은 estimated.
- **화면 이름 "보고 기준 순증감(사유 미구분 포함)"** — 증여·상속·신규선임 등 비거래 증감이 섞인다.
- 비율(%) = 순증감 ÷ 분모 × 100(분모는 §4.3 오버행과 같은 DART 주식총수 — 없으면 None).
- 사유 파악률(%) = 사유를 아는 증감 주식 ÷ 전체 증감 주식(절댓값) × 100, 증감 0 이면 None.
- 5% 대량보유는 목록만(`dedupe_major`) — 창 합계를 내지 않는다(임원 보고와 같은 지분을 이중으로
  센다).
- 금액(로그인 — 읽을 때): Σ 증감 × 보고일 종가(`insider_amount` — 추정, 실거래가가 아님).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Protocol

from kbj.core.fin_rows import INSIDER_WINDOWS, InsiderReport, InsiderWindow, MajorHolderReport
from kbj.core.quality import Quality

__all__ = [
    "dedupe",
    "dedupe_major",
    "insider_amount",
    "window",
    "windows",
]


class _Reported(Protocol):
    @property
    def rcept_no(self) -> str: ...

    @property
    def corp_code(self) -> str: ...

    @property
    def reporter(self) -> str: ...

    @property
    def quality(self) -> Quality: ...


def _dedupe[R: _Reported](
    rows: Iterable[R], corrections: Collection[str], base_of: Mapping[str, str] | None
) -> list[R]:
    ordered = sorted(
        (r for r in rows if r.quality is not Quality.INVALID), key=lambda r: r.rcept_no
    )
    present = {r.rcept_no for r in ordered}
    replaced: set[str] = set()
    paired: set[str] = set()
    for fix, base in (base_of or {}).items():
        if fix in present:
            replaced.add(base)
            paired.add(fix)
    fixes = set(corrections)
    for r in ordered:
        if r.rcept_no not in fixes or r.rcept_no in paired:
            continue
        prior = [
            p
            for p in ordered
            if p.rcept_no < r.rcept_no
            and p.corp_code == r.corp_code
            and p.reporter == r.reporter
            and p.rcept_no not in replaced
        ]
        if prior:
            replaced.add(max(prior, key=lambda p: p.rcept_no).rcept_no)
    return [r for r in ordered if r.rcept_no not in replaced]


def dedupe(
    reports: Iterable[InsiderReport],
    corrections: Collection[str] = (),
    base_of: Mapping[str, str] | None = None,
) -> list[InsiderReport]:
    """정정 대체(늦은 접수번호만) — 접수번호 순. invalid 행은 뺀다."""
    return _dedupe(reports, corrections, base_of)


def dedupe_major(
    reports: Iterable[MajorHolderReport],
    corrections: Collection[str] = (),
    base_of: Mapping[str, str] | None = None,
) -> list[MajorHolderReport]:
    """5% 대량보유 목록의 정정 대체(같은 규칙) — 접수번호 순."""
    return _dedupe(reports, corrections, base_of)


def window(
    reports: Sequence[InsiderReport],
    t: date,
    days: int,
    *,
    corp_code: str,
    denominator: int | None,
) -> InsiderWindow:
    """정정 대체를 마친 보고(`dedupe` 결과)로 t 기준 최근 days 일 창 하나."""
    if days not in INSIDER_WINDOWS:
        raise ValueError(f"창은 {INSIDER_WINDOWS} 중 하나: {days!r}")
    lo = t - timedelta(days=days)
    inside = [r for r in reports if r.corp_code == corp_code and lo <= r.rcept_dt <= t]
    deltas = [r.shares_delta for r in inside if r.shares_delta is not None]
    unknown = len(inside) - len(deltas)
    buy = sum(d for d in deltas if d > 0)
    sell = -sum(d for d in deltas if d < 0)
    net = buy - sell
    total_abs = buy + sell
    known_abs = sum(
        abs(r.shares_delta)
        for r in inside
        if r.shares_delta is not None and r.reason is not None and r.reason.strip()
    )
    den = denominator if denominator is not None and denominator > 0 else None
    estimated = unknown > 0 or any(r.quality is Quality.ESTIMATED for r in inside)
    return InsiderWindow(
        corp_code=corp_code,
        as_of=t,
        days=days,
        net_shares=net,
        buy_shares=buy,
        sell_shares=sell,
        n_reports=len(inside),
        n_reporters=len({r.reporter for r in inside}),
        ratio_pct=None if den is None else net / den * 100,
        reason_known_pct=None if total_abs == 0 else known_abs / total_abs * 100,
        quality=Quality.ESTIMATED if estimated else Quality.OK,
    )


def windows(
    reports: Iterable[InsiderReport],
    t: date,
    *,
    corp_code: str,
    denominator: int | None,
    corrections: Collection[str] = (),
    base_of: Mapping[str, str] | None = None,
    days: Sequence[int] = INSIDER_WINDOWS,
) -> list[InsiderWindow]:
    """회사 하나의 30·90·180일 창 — 접수일 ≤ t 인 보고만으로 정정 대체(PIT)한 뒤 계산한다."""
    known = [r for r in reports if r.corp_code == corp_code and r.rcept_dt <= t]
    rows = dedupe(known, corrections, base_of)
    return [window(rows, t, d, corp_code=corp_code, denominator=denominator) for d in days]


def insider_amount(
    reports: Iterable[InsiderReport], close_on: Callable[[str, date], float | None], code: str
) -> tuple[int | None, int]:
    """Σ 증감 × 보고일 종가(원 — 로그인, 추정) → (금액, 종가를 몰라 뺀 보고 수).

    종가를 하나도 못 구하면 금액 None.
    """
    total = 0.0
    used = missing = 0
    for r in reports:
        if r.shares_delta is None:
            continue
        close = close_on(code, r.rcept_dt)
        if close is None:
            missing += 1
            continue
        total += r.shares_delta * close
        used += 1
    return (round(total) if used else None), missing

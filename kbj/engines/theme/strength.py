"""테마 강도·밸류체인 묶음(로그인 — docs/metrics.md §31, ADR 0039 §4).

순수 함수(계약 ⑨). 입력: 대테마(`ThemeRow`)·대테마↔네이버 테마 연결·편입 줄·종목 마스터(로그인 표)와
보드 엔진이 마감 뒤 쓴 종목 하루(`StockDay` — `prv_board.stock_day`: 당일 등락·5일·21거래일 수익률·
시총·신고가 라벨, 수정주가 이상 구간을 뺀 값 — KRX/KIS 정본 일봉에서 보드가 계산).

- 대테마의 종목 = 그 대테마에 걸린 네이버 테마들의 편입 종목 합집합(종목 마스터 안 — 중복 한 번).
- 집계는 보드 테마 집계와 같은 롤업(`kbj.engines.board.aggregate.rollup`): 등락·5일·20일(=보드
  `ret_21d`, 21거래일 전 종가 대비)은 **동일가중 평균**(metrics §31 — 시총 가중이면 여러 테마에 든
  대형주 하나가 테마를 덮었다, 2026-10-20), 상승·보합·하락·모름 종목 수, 신고가 종목 수(보드
  라벨 — 종가 기준 우선순위),
  근접 수, 거래대금 배수(20일 평균 대비 — 아는 종목만).
- 상승 비율 = 상승 / (등락을 아는 종목). 아는 종목이 없으면 None(0 으로 채우지 않는다 — 규칙 2).
- 밸류체인 = 대테마의 `valuechain` 값이 같은 대테마들. 체인 집계는 그 대테마들 종목의 합집합.
- 정렬: 고른 기간 값 내림차순, 값 없음은 뒤, 같으면 원천 순서(`ord`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from kbj.core.rows import StockDay
from kbj.core.theme_rows import MemberRow, ThemeLinkRow, ThemeRow
from kbj.engines.board.aggregate import rollup

__all__ = [
    "EOK",
    "PERIOD_KEY",
    "ChainStrength",
    "Period",
    "Strength",
    "chain_strengths",
    "codes_by_theme",
    "naver_by_theme",
    "rank",
    "stock_items",
    "theme_strengths",
]

Period = Literal[1, 5, 20]
# 기간 → 집계 칸(보드 롤업 키). 20일 = 보드 ret_21d(21거래일 전 종가 대비 — metrics §31)
PERIOD_KEY: Final[Mapping[int, str]] = {1: "chg_pct", 5: "ret_5d", 20: "ret_21d"}
EOK: Final = 100_000_000  # 억원 → 원(보드 extra 의 turnover_avg20 은 억원 — StockDay 머리말)


@dataclass(frozen=True)
class Strength:
    """대테마(또는 체인) 하나의 강도. 금액은 원(시총 합 — 아는 종목만)."""

    n_members: int
    n_priced: int
    chg_pct: float | None
    ret_5d: float | None
    ret_20d: float | None
    weighted: bool
    up: int
    flat: int
    down: int
    unknown: int
    up_ratio: float | None
    n_newhigh: int
    n_near: int
    turnover_mult: float | None
    mktcap: int | None
    leader_code: str | None
    leader_name: str | None
    leader_chg_pct: float | None

    def value(self, period: int) -> float | None:
        return {1: self.chg_pct, 5: self.ret_5d, 20: self.ret_20d}[period]


@dataclass(frozen=True)
class ChainStrength:
    key: str
    themes: tuple[str, ...]
    strength: Strength


def naver_by_theme(links: Iterable[ThemeLinkRow]) -> dict[str, list[int]]:
    """대테마 id → 네이버 테마 번호(원천 순서, 중복 없음)."""
    out: dict[str, list[int]] = {}
    for lk in sorted(links, key=lambda x: (x.theme_id, x.ord)):
        lst = out.setdefault(lk.theme_id, [])
        if lk.naver_no not in lst:
            lst.append(lk.naver_no)
    return out


def codes_by_theme(
    links: Iterable[ThemeLinkRow],
    members: Iterable[MemberRow],
    universe: Collection[str] | None = None,
) -> dict[str, list[str]]:
    """대테마 id → 종목코드(정렬·중복 없음). `universe` 를 주면 그 안 종목만(종목 마스터)."""
    by_no: dict[int, set[str]] = {}
    for m in members:
        if universe is None or m.code in universe:
            by_no.setdefault(m.naver_no, set()).add(m.code)
    out: dict[str, list[str]] = {}
    for tid, nos in naver_by_theme(links).items():
        got: set[str] = set()
        for no in nos:
            got |= by_no.get(no, set())
        out[tid] = sorted(got)
    return out


def stock_items(days: Mapping[str, StockDay]) -> dict[str, dict[str, Any]]:
    """보드 종목 하루 → 롤업 입력(보드 universe 행과 같은 키). 금액은 원으로 맞춘다."""
    out: dict[str, dict[str, Any]] = {}
    for code, s in days.items():
        if not s.quality.usable:
            continue
        avg20 = s.extra.get("turnover_avg20")
        out[code] = {
            "code": code,
            "name": s.name or code,
            "chg_pct": s.chg_pct,
            "ret_5d": s.ret_5d,
            "ret_21d": s.ret_21d,
            "mktcap": s.mktcap,
            "turnover": s.turnover,
            "turnover_avg20": float(avg20) * EOK if isinstance(avg20, int | float) else None,
            "label": s.label if s.label in ("hist", "w52", "d120") else None,
            "near_kind": s.near_kind,
        }
    return out


def _strength(codes: Sequence[str], items: Mapping[str, dict[str, Any]]) -> Strength:
    got = [items[c] for c in codes if c in items]
    r = rollup("", got, "theme")
    # 등락은 동일가중(metrics §31) — 시총 가중이면 여러 테마에 든 대형주 하나가 테마를 덮는다
    eq = rollup("", [{**x, "mktcap": None} for x in got], "theme")
    b = r["breadth"]
    lead = r["leader"]
    known_cap = [x["mktcap"] for x in got if x.get("mktcap") is not None]
    return Strength(
        n_members=len(codes),
        n_priced=len(got),
        chg_pct=eq["chg_pct"],
        ret_5d=eq["ret_5d"],
        ret_20d=eq["ret_21d"],
        weighted=False,
        up=b["up"],
        flat=b["flat"],
        down=b["down"],
        unknown=b["unknown"] + (len(codes) - len(got)),
        up_ratio=round(b["up"] / b["total"], 4) if b["total"] else None,
        n_newhigh=r["n_newhigh"],
        n_near=r["n_near"],
        turnover_mult=r["turnover_mult"],
        mktcap=int(sum(known_cap)) if known_cap else None,
        leader_code=lead["code"] if lead else None,
        leader_name=lead["name"] if lead else None,
        leader_chg_pct=lead["chg_pct"] if lead else None,
    )


def theme_strengths(
    themes: Sequence[ThemeRow],
    codes: Mapping[str, Sequence[str]],
    items: Mapping[str, dict[str, Any]],
) -> dict[str, Strength]:
    """대테마 id → 강도(종목이 없어도 줄은 만든다 — 값은 None)."""
    return {t.id: _strength(codes.get(t.id, ()), items) for t in themes}


def chain_strengths(
    themes: Sequence[ThemeRow],
    codes: Mapping[str, Sequence[str]],
    items: Mapping[str, dict[str, Any]],
) -> list[ChainStrength]:
    """밸류체인(대테마 valuechain 값)별 묶음 — 체인 이름순."""
    by_chain: dict[str, list[ThemeRow]] = {}
    for t in sorted(themes, key=lambda x: x.ord):
        if t.valuechain is not None:
            by_chain.setdefault(t.valuechain, []).append(t)
    out: list[ChainStrength] = []
    for key in sorted(by_chain):
        ts = by_chain[key]
        union = sorted({c for t in ts for c in codes.get(t.id, ())})
        out.append(ChainStrength(key, tuple(t.id for t in ts), _strength(union, items)))
    return out


def rank(
    themes: Sequence[ThemeRow], strengths: Mapping[str, Strength], period: int
) -> list[ThemeRow]:
    """고른 기간 값 내림차순(값 없음은 뒤, 같으면 원천 순서)."""
    if period not in PERIOD_KEY:
        raise ValueError(f"기간은 1·5·20: {period!r}")

    def key(t: ThemeRow) -> tuple[bool, float, int]:
        v = strengths[t.id].value(period)
        return (v is None, -(v or 0.0), t.ord)

    return sorted(themes, key=key)

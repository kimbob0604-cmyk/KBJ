"""행 자료형 — 엔진(순수 계산)과 저장소(SQL)가 같이 쓰는 frozen dataclass.

근거: docs/p3_design.md §1.1·D-P3-5.

엔진은 저장소를 모르고(계약 ⑦·⑨), 저장소는 엔진을 모른다. 둘이 주고받는 행 모양을 바닥 계층인
`kbj.core` 에 두어 두 쪽이 같은 말을 쓰게 한다. 이 모듈은 I/O 를 하지 않는다(계약 ③).

규칙(CLAUDE.md 절대 규칙 1, docs/metrics.md §0)
- 값 행은 `source`·`quality` 를 갖고, as_of 는 행의 `date`(거래일) 또는 `ts`(시각 — 시간대 필수)다.
- KR 금액(거래대금·순매수·시가총액·순자산)은 **원 단위 정수**(`int`)만 받는다. float 를 넣으면
  `TypeError` — 단위 변환(KIS 백만원 → 원 등)은 파서가 끝낸 뒤 넘긴다. 수량(거래량·좌수)도 정수.
- 가격·지수·NAV·비율은 `float`(KRX 비수정 가격, NAV 소수 둘째 자리).
- naive 시각은 받지 않는다(`ValueError`). 날짜 칸에 `datetime` 을 넣어도 거부한다.
- `venue` 는 거래소 구분 `'' | KRX | NXT | TOTAL`(kbj/data/spec.py `Venue` 와 같은 값 — core 는
  kbj.data 를 import 하지 않아 문자열로 둔다). 거래소를 나누지 않는 행은 ''.
- 저장 표는 docs/p3_design.md §3.4(0007~0009)·0003. 열 이름과 필드 이름을 맞췄다.

원장 우선순위(D-P3-7): 한 (종목, 날짜, 투자자/지표) 의 값은 **krx(ok) > kis(ok, 마감 확정) >
kis(estimated, 장중)** 로 하나만 고른다 — `ledger_rank`·`pick_best`. invalid 는 맨 뒤(다른 행이 없을
때만 남아 집계에서 빠지고 수가 세어진다).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final, Literal

from kbj.core.quality import Quality

__all__ = [
    "BASES",
    "BOARD_ARTIFACTS",
    "CHANGE_KINDS",
    "CHECK_IDS",
    "EXCLUDE_FLAGS",
    "GROUP4",
    "INST7",
    "KINDS",
    "LABEL_KINDS",
    "SOURCE_RANK",
    "VENUES",
    "AllTime",
    "Bar",
    "BoardArtifact",
    "BoardDayRecord",
    "Change",
    "EtfDay",
    "EtfMeta",
    "EtfQuote",
    "EtfType",
    "Fund",
    "HoldingRow",
    "IndexBar",
    "IndexQuote",
    "IntradayInvestor",
    "Investor",
    "InvestorDay",
    "Label",
    "LedgerCheck",
    "RankRow",
    "ReconcileRow",
    "Revision",
    "SectorQuote",
    "Snap",
    "SplitCheck",
    "SplitEvent",
    "StockDay",
    "UniverseRow",
    "ledger_rank",
    "pick_best",
    "row_rank",
    "venue_rank",
]

VENUES: Final[tuple[str, ...]] = ("", "KRX", "NXT", "TOTAL")
# 종목 종류 — ET board/engine/kinds.py 이름 + ETF·ETN(0003 prv_market.universe.kind 와 같다)
KINDS: Final[tuple[str, ...]] = ("common", "pref", "spac", "reit", "etf", "etn")
# 스크리닝이 기본으로 빼는 상태(metrics §3 — 관리종목·거래정지·정리매매)
EXCLUDE_FLAGS: Final[frozenset[str]] = frozenset({"managed", "halted", "liquidation"})
BASES: Final[tuple[str, ...]] = ("close", "high")  # 신고가 기준(ET newhigh.BASES)
LABEL_KINDS: Final[tuple[str, ...]] = ("hist", "w52", "d60", "w52_low")  # w52_low 는 US 보드용
CHANGE_KINDS: Final[tuple[str, ...]] = ("NEW", "DROP", "IN10", "OUT10", "ADD", "CUT")
CHECK_IDS: Final[tuple[str, ...]] = ("c1", "c2", "c3")  # 검산 ①②③(metrics §2·§4)

# 원천 순위(작을수록 앞) — D-P3-7. 이름은 소문자로 비교한다(KRX·krx 같은 출처).
SOURCE_RANK: Final[Mapping[str, int]] = {"krx": 0, "kis": 1, "kis.prelim": 2}
_UNKNOWN_SOURCE_RANK: Final = 5
_QUALITY_RANK: Final[Mapping[Quality, int]] = {
    Quality.OK: 0,
    Quality.STALE: 1,
    Quality.ESTIMATED: 2,
    Quality.INVALID: 9,
}

_VENUE_RANK: Final[Mapping[str, int]] = {"KRX": 0, "": 1, "TOTAL": 2, "NXT": 3}
_CODE = re.compile(r"\S+")
_FLAG = re.compile(r"[a-z][a-z0-9_]*")


class Investor(StrEnum):
    """투자자 구분 — 0003 `prv_flows.stock_investor_daily.investor` 주석의 12개 이름 그대로.

    외국인(metrics §2)은 FOREIGN(+ FOREIGN_OTHER — 응답이 나눠 줄 때만 따로 행)이고, 기관은
    INSTITUTION(합계) 과 7구분(`INST7`)이다. KIS 가 주지 않는 구분은 행을 만들지 않는다(0 으로
    채우지 않는다 — 검산 불가는 `None`).
    """

    FOREIGN = "foreign"  # 외국인
    FOREIGN_OTHER = "foreign_other"  # 기타외국인
    INSTITUTION = "institution"  # 기관 합계
    FIN_INVEST = "fin_invest"  # 금융투자
    TRUST = "trust"  # 투신
    PRIVATE_FUND = "private_fund"  # 사모
    INSURANCE = "insurance"  # 보험
    BANK = "bank"  # 은행
    PENSION = "pension"  # 연기금
    OTHER_FIN = "other_fin"  # 기타금융
    OTHER_CORP = "other_corp"  # 기타법인
    INDIVIDUAL = "individual"  # 개인


INST7: Final[tuple[Investor, ...]] = (
    Investor.FIN_INVEST,
    Investor.TRUST,
    Investor.PRIVATE_FUND,
    Investor.INSURANCE,
    Investor.BANK,
    Investor.PENSION,
    Investor.OTHER_FIN,
)
# 검산 ①(metrics §2)의 4구분 — 외국인(기타외국인은 같은 구분에 더한다)·기관·기타법인·개인
GROUP4: Final[tuple[Investor, ...]] = (
    Investor.FOREIGN,
    Investor.INSTITUTION,
    Investor.OTHER_CORP,
    Investor.INDIVIDUAL,
)


class EtfType(StrEnum):
    """ETF 유형(docs/metrics.md §8 정본 — 규칙은 `kbj.engines.etf.types.etf_type`).

    값은 DB `prv_etf.meta.etf_type` 에 그대로 들어간다. 화면 이름은 `label`.
    """

    KR_INDEX = "kr_index"  # 국내 대표지수
    KR_THEME = "kr_theme"  # 국내 테마
    OVERSEAS = "overseas"  # 해외주식
    LEVERAGED_INVERSE = "leveraged_inverse"  # 레버리지·인버스
    BOND_CASH = "bond_cash"  # 채권·현금
    COMMODITY = "commodity"  # 원자재
    OTHER = "other"  # 분류 불가(기타)

    @property
    def label(self) -> str:
        return _ETF_TYPE_LABELS[self]


_ETF_TYPE_LABELS: Final[Mapping[EtfType, str]] = {
    EtfType.KR_INDEX: "국내 대표지수",
    EtfType.KR_THEME: "국내 테마",
    EtfType.OVERSEAS: "해외주식",
    EtfType.LEVERAGED_INVERSE: "레버리지·인버스",
    EtfType.BOND_CASH: "채권·현금",
    EtfType.COMMODITY: "원자재",
    EtfType.OTHER: "기타",
}


# ── 검증 도우미 ─────────────────────────────────────────────────────────────────────────


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _code(what: str, value: str) -> None:
    if not isinstance(value, str) or not _CODE.fullmatch(value):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError(f"{what} 는 공백 없는 비지 않은 문자열: {value!r}")


def _text(what: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError(f"{what} 가 비었다")


def _day(what: str, value: date | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{what} 는 date 여야 한다(datetime·문자열 아님): {value!r}")


def _aware(what: str, value: datetime | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, datetime):
        raise TypeError(f"{what} 는 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


def _int(what: str, value: int | None, *, nonneg: bool = False) -> None:
    """원 단위 금액·수량 — 정수만(float·bool 은 거부)."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(f"{what} 는 정수(원·주 단위)여야 한다: {value!r}")
    if nonneg and value < 0:
        raise ValueError(f"{what} 는 0 이상: {value!r}")


def _num(what: str, value: float | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(f"{what} 는 수여야 한다: {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{what} 는 유한한 수여야 한다: {value!r}")


def _quality(obj: object, name: str = "quality") -> None:
    q = getattr(obj, name)
    try:
        _set(obj, name, Quality(q))
    except ValueError:
        raise ValueError(f"{name} 는 ok·stale·estimated·invalid 중 하나: {q!r}") from None


def _venue(value: str) -> None:
    if value not in VENUES:
        raise ValueError(f"venue 는 {VENUES} 중 하나: {value!r}")


def _choice(
    what: str, value: str | None, allowed: Iterable[str], *, optional: bool = False
) -> None:
    if value is None and optional:
        return
    allowed_t = tuple(allowed)
    if value not in allowed_t:
        raise ValueError(f"{what} 는 {allowed_t} 중 하나: {value!r}")


def _source(value: str) -> None:
    _text("source", value)


# ── 원장 우선순위 (D-P3-7) ───────────────────────────────────────────────────────────────


def ledger_rank(source: str, quality: Quality | str) -> tuple[int, int, int]:
    """작을수록 앞. (invalid 여부, 원천 순위, 품질 순위) — krx(ok) > kis(ok) > kis(estimated)
    > kis.prelim(estimated) > 모르는 원천. invalid 는 원천과 무관하게 맨 뒤."""
    q = Quality(quality)
    return (
        1 if q is Quality.INVALID else 0,
        SOURCE_RANK.get(source.lower(), _UNKNOWN_SOURCE_RANK),
        _QUALITY_RANK[q],
    )


def venue_rank(venue: str) -> int:
    """같은 원천·품질이면 KRX > ''(구분 없음·이관분) > TOTAL > NXT(D-P3-9 — 실측 전 기본 KRX)."""
    return _VENUE_RANK.get(venue, len(_VENUE_RANK))


def row_rank(source: str, quality: Quality | str, venue: str = "") -> tuple[int, int, int, int]:
    """`ledger_rank` + 거래소 순위 — 저장소·원장이 (종목, 날짜) 마다 한 행을 고를 때 쓰는 키."""
    return (*ledger_rank(source, quality), venue_rank(venue))


def pick_best[R, K: Hashable](
    rows: Iterable[R],
    key: Callable[[R], K],
    rank: Callable[[R], tuple[int, ...]],
) -> dict[K, R]:
    """키마다 순위가 가장 앞선 행 하나. 같은 순위면 먼저 온 행(입력 순서 — 결정적)."""
    out: dict[K, R] = {}
    best: dict[K, tuple[int, ...]] = {}
    for r in rows:
        k = key(r)
        rk = rank(r)
        if k not in best or rk < best[k]:
            out[k] = r
            best[k] = rk
    return out


# ── 시세 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Bar:
    """일봉 한 줄(`prv_market.daily_bar` — asset 은 저장소 인자). 가격은 비수정(KRX)."""

    code: str
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    turnover: int | None  # 원
    source: str
    venue: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        for n in ("open", "high", "low", "close"):
            _num(n, getattr(self, n))
        _int("volume", self.volume, nonneg=True)
        _int("turnover", self.turnover, nonneg=True)
        _source(self.source)
        _venue(self.venue)
        _quality(self)


@dataclass(frozen=True)
class Snap:
    """종목 일자 스냅(`prv_market.stock_snapshot`). `market` 은 KOSPI·KOSDAQ·KONEX(표의 segment).

    `status_flags`: 관리종목·거래정지 등(`managed`·`halted`·`liquidation` …) — **모르면 None**
    (빈 튜플은 '아무 상태도 없음'을 확인한 것). 원천 KIS `iscd_stat_cls_code` [실측 필요].
    """

    code: str
    date: date
    name: str | None
    market: str | None
    kind: str | None
    close: float | None
    chg_pct: float | None
    volume: int | None
    turnover: int | None  # 원
    turnover_is_estimate: bool
    mktcap: int | None  # 원
    shares: int | None
    status_flags: tuple[str, ...] | None
    source: str
    venue: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        _choice("kind", self.kind, KINDS, optional=True)
        _num("close", self.close)
        _num("chg_pct", self.chg_pct)
        _int("volume", self.volume, nonneg=True)
        _int("turnover", self.turnover, nonneg=True)
        _int("mktcap", self.mktcap, nonneg=True)
        _int("shares", self.shares, nonneg=True)
        if self.status_flags is not None:
            flags = tuple(self.status_flags)
            for f in flags:
                if not _FLAG.fullmatch(f):
                    raise ValueError(f"status_flags 는 소문자 식별자: {f!r}")
            _set(self, "status_flags", flags)
        _source(self.source)
        _venue(self.venue)
        _quality(self)

    @property
    def flagged(self) -> bool | None:
        """스크리닝 기본 제외 대상인가. 상태를 모르면 None(metrics §3 `n_status_unknown`)."""
        if self.status_flags is None:
            return None
        return bool(EXCLUDE_FLAGS & set(self.status_flags))


@dataclass(frozen=True)
class UniverseRow:
    """유니버스 한 줄(`prv_market.universe`). `flags` 는 출처에만 있는 칸."""

    code: str
    as_of: date
    name: str | None
    market: str | None
    kind: str | None
    listed_on: date | None
    source: str
    quality: Quality
    flags: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("as_of", self.as_of)
        _choice("kind", self.kind, KINDS, optional=True)
        _day("listed_on", self.listed_on, optional=True)
        _source(self.source)
        _quality(self)
        _set(self, "flags", dict(self.flags))


@dataclass(frozen=True)
class IndexBar:
    """지수 일봉(`prv_market.daily_bar` asset=index). 거래대금은 지수 구성 종목 합(원)."""

    code: str
    date: date
    name: str | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    turnover: int | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        for n in ("open", "high", "low", "close"):
            _num(n, getattr(self, n))
        _int("volume", self.volume, nonneg=True)
        _int("turnover", self.turnover, nonneg=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class IndexQuote:
    """장중 지수 현재가(`prv_market.index_intraday`). ts = 슬롯 시작(aware)."""

    code: str
    ts: datetime
    name: str | None
    value: float | None
    chg_pct: float | None
    turnover: int | None  # 누적 거래대금(원)
    volume: int | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _aware("ts", self.ts)
        _num("value", self.value)
        _num("chg_pct", self.chg_pct)
        _int("turnover", self.turnover, nonneg=True)
        _int("volume", self.volume, nonneg=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class SectorQuote:
    """장중 업종 지수(`prv_market.sector_intraday`). market = KOSPI·KOSDAQ."""

    market: str
    code: str
    ts: datetime
    name: str | None
    value: float | None
    chg_pct: float | None
    turnover: int | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("market", self.market)
        _code("code", self.code)
        _aware("ts", self.ts)
        _num("value", self.value)
        _num("chg_pct", self.chg_pct)
        _int("turnover", self.turnover, nonneg=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class RankRow:
    """장중 거래대금 순위 한 줄(`prv_market.turnover_rank_intraday`)."""

    market: str
    ts: datetime
    rank: int
    venue: str
    code: str
    name: str | None
    turnover: int | None
    chg_pct: float | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("market", self.market)
        _aware("ts", self.ts)
        _int("rank", self.rank)
        if self.rank < 1:
            raise ValueError("rank 는 1 이상")
        _venue(self.venue)
        _code("code", self.code)
        _int("turnover", self.turnover, nonneg=True)
        _num("chg_pct", self.chg_pct)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class ReconcileRow:
    """KIS 마감값 대 KRX 확정값 대조 한 줄(`prv_market.eod_reconcile`, D-P3-8)."""

    trade_date: date
    code: str
    field: Literal["close", "turnover", "mktcap"]
    kis_value: float | None
    krx_value: float | None
    diff_pct: float | None
    verdict: Literal["ok", "mismatch", "missing_kis", "missing_krx"]
    checked_at: datetime

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _code("code", self.code)
        _choice("field", self.field, ("close", "turnover", "mktcap"))
        _num("kis_value", self.kis_value)
        _num("krx_value", self.krx_value)
        _num("diff_pct", self.diff_pct)
        _choice("verdict", self.verdict, ("ok", "mismatch", "missing_kis", "missing_krx"))
        _aware("checked_at", self.checked_at)


# ── 수급 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InvestorDay:
    """종목(또는 시장 — code 에 KIS 시장 코드) 투자자별 일별 순매수(원·주)."""

    code: str
    date: date
    investor: Investor
    net_value: int | None
    net_qty: int | None
    source: str
    venue: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        _set(self, "investor", Investor(self.investor))
        _int("net_value", self.net_value)
        _int("net_qty", self.net_qty)
        if self.net_value is None and self.net_qty is None:
            raise ValueError("net_value·net_qty 가 둘 다 없다 — 행을 만들지 않는다")
        _source(self.source)
        _venue(self.venue)
        _quality(self)


@dataclass(frozen=True)
class IntradayInvestor:
    """장중 가집계 한 줄(`prv_flows.investor_intraday` — 증권사 추정치, quality=estimated)."""

    code: str
    ts: datetime
    investor: Investor
    venue: str
    net_value: int | None
    net_qty: int | None
    rank: int | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _aware("ts", self.ts)
        _set(self, "investor", Investor(self.investor))
        _venue(self.venue)
        _int("net_value", self.net_value)
        _int("net_qty", self.net_qty)
        _int("rank", self.rank)
        if self.net_value is None and self.net_qty is None:
            raise ValueError("net_value·net_qty 가 둘 다 없다")
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class Revision:
    """잠정 → 확정 덮어쓰기 차이(`prv_flows.investor_revision`, D-P3-7)."""

    code: str
    trade_date: date
    investor: Investor
    venue: str
    est_value: int | None
    est_ts: datetime | None
    final_value: int | None
    final_source: str
    diff: int | None  # final − est (둘 다 있을 때)
    revised_at: datetime

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("trade_date", self.trade_date)
        _set(self, "investor", Investor(self.investor))
        _venue(self.venue)
        _int("est_value", self.est_value)
        _aware("est_ts", self.est_ts, optional=True)
        _int("final_value", self.final_value)
        _source(self.final_source)
        _int("diff", self.diff)
        _aware("revised_at", self.revised_at)


@dataclass(frozen=True)
class LedgerCheck:
    """검산 실패 기록(`prv_flows.ledger_check`) — c1 4구분 합 0, c2 7구분 = 기관, c3 ETF 순자산."""

    domain: Literal["stock", "etf"]
    trade_date: date
    code: str
    check_id: Literal["c1", "c2", "c3"]
    residual: float | None
    checked_at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        _choice("domain", self.domain, ("stock", "etf"))
        _day("trade_date", self.trade_date)
        _code("code", self.code)
        _choice("check_id", self.check_id, CHECK_IDS)
        _num("residual", self.residual)
        _aware("checked_at", self.checked_at)
        _set(self, "detail", dict(self.detail))


# ── ETF ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EtfDay:
    """ETF 일별(`prv_etf.etf_daily` — KRX `etp/etf_bydd_trd`). 순자산·거래대금·시총은 원."""

    code: str
    date: date
    name: str | None
    close: float | None
    nav: float | None
    list_shrs: int | None  # 상장좌수
    net_asset: int | None  # 보고 순자산(원) — 공표 단위 [실측 필요]
    turnover: int | None
    volume: int | None
    mktcap: int | None
    base_index: str | None
    source: str
    venue: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        _num("close", self.close)
        _num("nav", self.nav)
        _int("list_shrs", self.list_shrs, nonneg=True)
        _int("net_asset", self.net_asset, nonneg=True)
        _int("turnover", self.turnover, nonneg=True)
        _int("volume", self.volume, nonneg=True)
        _int("mktcap", self.mktcap, nonneg=True)
        _source(self.source)
        _venue(self.venue)
        _quality(self)


@dataclass(frozen=True)
class EtfQuote:
    """장중 ETF 현재가·iNAV(`prv_etf.quote_intraday`) — 괴리율 경고에만(metrics §4-4)."""

    code: str
    ts: datetime
    price: float | None
    inav: float | None
    premium_pct: float | None
    turnover: int | None
    volume: int | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _aware("ts", self.ts)
        _num("price", self.price)
        _num("inav", self.inav)
        _num("premium_pct", self.premium_pct)
        _int("turnover", self.turnover, nonneg=True)
        _int("volume", self.volume, nonneg=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class EtfMeta:
    """ETF 메타(`prv_etf.meta`). theme = `classify`(ADR 0001 Q8), etf_type = metrics §8."""

    code: str
    name: str | None
    issuer: str | None
    brand: str | None
    theme: str | None
    etf_type: EtfType | None
    leverage: float | None  # 2.0·−1.0·−2.0 … 모르면 None
    base_index: str | None
    listed_on: date | None
    delisted_on: date | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        if self.etf_type is not None:
            _set(self, "etf_type", EtfType(self.etf_type))
        _num("leverage", self.leverage)
        _day("listed_on", self.listed_on, optional=True)
        _day("delisted_on", self.delisted_on, optional=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class SplitEvent:
    """분할·병합(`prv_etf.split_event`). ratio = 새 좌수 ÷ 옛 좌수(1:10 분할 10, 5:1 병합 0.2).

    수동 표(origin=manual)가 감지(detected)보다 우선한다 — 저장소가 manual 을 덮지 않는다.
    """

    code: str
    effective_date: date
    ratio: float
    origin: Literal["detected", "manual"]
    note: str
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("effective_date", self.effective_date)
        _source(self.source)
        _num("ratio", self.ratio)
        if self.ratio <= 0:
            raise ValueError("ratio 는 0 보다 커야 한다")
        _choice("origin", self.origin, ("detected", "manual"))
        _quality(self)


@dataclass(frozen=True)
class Fund:
    """운용사 PDF 추적 대상 펀드(`prv_etf.fund` — ET tracker.py DDL:71 열)."""

    fund_id: str
    issuer: str
    fund_key: str
    ticker: str | None
    name: str | None
    theme: str | None
    is_active: bool
    depth: Literal["full", "top10"]
    track: bool
    empty_streak: int
    source: str

    def __post_init__(self) -> None:
        _code("fund_id", self.fund_id)
        _text("issuer", self.issuer)
        _text("fund_key", self.fund_key)
        _choice("depth", self.depth, ("full", "top10"))
        _int("empty_streak", self.empty_streak, nonneg=True)
        _source(self.source)


@dataclass(frozen=True)
class HoldingRow:
    """구성종목 한 줄(`prv_etf.holding`) — ET 어댑터 `{code: {name, qty, val, wt}}` 의 값."""

    code: str
    name: str | None
    qty: float | None
    wt: float | None  # 비중 %
    val: float | None  # 평가금액(원)
    source: str  # 예 ETF_ISSUERS:kodex
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        for n in ("qty", "wt", "val"):
            _num(n, getattr(self, n))
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class Change:
    """구성종목 변동 한 줄(`prv_etf.change_log` — ET tracker.py:analyze 출력 열)."""

    run_date: date
    fund_id: str
    code: str
    kind: Literal["NEW", "DROP", "IN10", "OUT10", "ADD", "CUT"]
    name: str | None
    asof: date
    prev_asof: date
    gap_days: int
    prev_qty: float | None
    cur_qty: float | None
    prev_wt: float | None
    cur_wt: float | None
    qty_pct: float | None
    qty_pct_adj: float | None

    def __post_init__(self) -> None:
        _day("run_date", self.run_date)
        _code("fund_id", self.fund_id)
        _code("code", self.code)
        _choice("kind", self.kind, CHANGE_KINDS)
        _day("asof", self.asof)
        _day("prev_asof", self.prev_asof)
        _int("gap_days", self.gap_days, nonneg=True)
        for n in ("prev_qty", "cur_qty", "prev_wt", "cur_wt", "qty_pct", "qty_pct_adj"):
            _num(n, getattr(self, n))


# ── 신고가 보드 ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AllTime:
    """역사적 최고가 스칼라(`prv_board.alltime` — ET board/engine/db.py DDL 열 그대로 + 2).

    `history_from` = 받은 일봉 이력의 첫 날. 상장일이 그보다 앞이면 hist 를 계산하지 않는다
    (D-P3-11 — 지어내지 않는다).
    """

    code: str
    hi: float | None
    hi_date: date | None
    cl: float | None
    cl_date: date | None
    prev_hi: float | None
    prev_cl: float | None
    first_date: date | None
    last_date: date | None
    n_days: int
    suspect: bool
    suspect_date: date | None
    suspect_note: str | None
    history_from: date | None
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        for n in ("hi", "cl", "prev_hi", "prev_cl"):
            _num(n, getattr(self, n))
        for n in ("hi_date", "cl_date", "first_date", "last_date", "suspect_date", "history_from"):
            _day(n, getattr(self, n), optional=True)
        _int("n_days", self.n_days, nonneg=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class Label:
    """일자별 신고가 라벨(`prv_board.label`). rank 0=hist 1=w52 2=d60(ET 그대로)."""

    code: str
    date: date
    basis: Literal["close", "high"]
    kind: Literal["hist", "w52", "d60", "w52_low"]
    rank: int
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        _choice("basis", self.basis, BASES)
        _choice("kind", self.kind, LABEL_KINDS)
        _int("rank", self.rank, nonneg=True)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class SplitCheck:
    """분할 의심 종목의 공시 대조 결과(`prv_board.split_check` — ET D-056)."""

    code: str
    jump_date: date | None
    verdict: Literal["action", "none", "unknown"]
    note: str
    source: str

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("jump_date", self.jump_date, optional=True)
        _choice("verdict", self.verdict, ("action", "none", "unknown"))
        _source(self.source)


@dataclass(frozen=True)
class StockDay:
    """보드의 종목 하루(`prv_board.stock_day`) — ET universe 행의 핵심 열 + extra(나머지 키).

    금액(turnover·mktcap)은 **원**이다(보드 엔진 안의 억원 값은 저장할 때 원으로 바꾼다).
    """

    code: str
    date: date
    name: str | None
    close: float | None
    chg_pct: float | None
    turnover: int | None
    turnover_is_estimate: bool
    mktcap: int | None
    label: str | None
    near_kind: str | None
    near_gap: float | None
    status: str | None
    suspect: bool
    sector: str | None
    theme: str | None
    ret_5d: float | None
    ret_21d: float | None
    vol_mult: float | None
    source: str
    quality: Quality
    extra: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        _code("code", self.code)
        _day("date", self.date)
        for n in ("close", "chg_pct", "near_gap", "ret_5d", "ret_21d", "vol_mult"):
            _num(n, getattr(self, n))
        _int("turnover", self.turnover, nonneg=True)
        _int("mktcap", self.mktcap, nonneg=True)
        _choice("label", self.label, LABEL_KINDS, optional=True)
        _source(self.source)
        _quality(self)
        _set(self, "extra", dict(self.extra))


BOARD_ARTIFACTS: Final[tuple[str, ...]] = (
    "universe_meta",
    "newhigh",
    "sectors",
    "events",
    "rankings",
)


@dataclass(frozen=True)
class BoardArtifact:
    """보드 산출 JSON 하나(`prv_board.artifact` — ET state/<날짜>/*.json 과 같은 키)."""

    trade_date: date
    name: Literal["universe_meta", "newhigh", "sectors", "events", "rankings"]
    payload: Mapping[str, Any]
    engine_version: str
    input_digest: str
    as_of: datetime
    generated_at: datetime
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _choice("name", self.name, BOARD_ARTIFACTS)
        _text("engine_version", self.engine_version)
        _text("input_digest", self.input_digest)
        _aware("as_of", self.as_of)
        _aware("generated_at", self.generated_at)
        _source(self.source)
        _quality(self)
        _set(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class BoardDayRecord:
    """한 거래일 보드 저장 묶음 — `BoardRepo.put_day` 가 그날 행을 통째로 바꾼다(재실행 멱등).

    엔진의 `BoardDay` 는 kbj.engines 에 있어 저장소가 모른다(계약 ⑦) — 서비스
    (kbj.services.engine.board)가 이 모양으로 옮겨 넘긴다.
    """

    trade_date: date
    labels: tuple[Label, ...]
    stock_days: tuple[StockDay, ...]
    artifacts: tuple[BoardArtifact, ...]
    source: str
    quality: Quality

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _set(self, "labels", tuple(self.labels))
        _set(self, "stock_days", tuple(self.stock_days))
        _set(self, "artifacts", tuple(self.artifacts))
        for item in (*self.labels, *self.stock_days, *self.artifacts):
            d = item.trade_date if isinstance(item, BoardArtifact) else item.date
            if d != self.trade_date:
                raise ValueError(f"묶음 날짜 {self.trade_date} 와 다른 행: {d}")
        names = [a.name for a in self.artifacts]
        if len(set(names)) != len(names):
            raise ValueError("같은 이름의 artifact 가 두 번 있다")
        _source(self.source)
        _quality(self)

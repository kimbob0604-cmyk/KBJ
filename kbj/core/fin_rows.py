"""P4 행 자료형 — 공시·재무·컨센서스·밸류에이션·관심종목(엔진·저장소·수집·API 가 같이 쓴다).

근거: docs/p4_design.md §1.1(M)·§3.4(마이그레이션 0010~0013 열)·§4(엔진 입출력), docs/metrics.md
§9~§15.

P3 `kbj.core.rows` 와 같은 자리·같은 규칙이다(그 파일은 고치지 않는다 — 설계 §1.1). 엔진은 저장소를
모르고(계약 ⑦·⑨) 저장소는 엔진을 모른다. 둘이 주고받는 행 모양을 바닥 계층 `kbj.core` 에 둔다.
이 모듈은 I/O 를 하지 않는다(계약 ③).

규칙(CLAUDE.md 절대 규칙 1, docs/metrics.md §0·§9)
- 값 행은 `source`·`quality` 를 갖는다. as_of 는 행의 날짜(rcept_dt·trade_date·as_of) 또는
  시각(aware).
- **금액은 원 단위 정수**(`int`)만 받는다 — DART 원 정수. float·bool 을 넣으면 `TypeError`(단위
  변환은
  파서가 끝낸 뒤 넘긴다). 주식 수도 정수. 외국 통화 회사는 `currency` 열에 통화를 적고 환산하지
  않는다.
- 가격·배수·비율(%)·EPS(원/주)는 `float`(유한값만 — NaN·inf 는 `ValueError`).
- naive 시각은 받지 않는다(`ValueError`). 날짜 칸에 `datetime` 을 넣어도 거부한다.
- 종목코드 `^[0-9A-Z]{6}$`(영숫자 단축코드 포함), corp_code `^[0-9]{8}$`, 접수번호 `^[0-9]{14}$`.
- 없는 값은 `None` 이다 — 0 으로 채우지 않는다(절대 규칙 2). 적용 불가는 `NaReason`.

`REPRT`(보고서 코드 → (분기 라벨, 누적 개월))는 P2 `kbj/data/public/dart/client.py` 에서 옮겼다 —
엔진(`kbj.engines.financials`)이 쓰는데 엔진은 `kbj.data` 를 import 하지 못한다(계약 ⑨). client 는
이 객체를 다시 내보낸다(두 벌 금지 — 같은 객체).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final, Literal, cast

from kbj.core.quality import Quality

__all__ = [
    "BAND_METRICS",
    "BUYBACK_KINDS",
    "CORP_CLS",
    "DART_SOURCE",
    "INSIDER_WINDOWS",
    "PARSE_STATES",
    "REPRT",
    "REVISION_SIGNALS",
    "SCENARIO_CASES",
    "SCENARIO_METHODS",
    "WATCH_SOURCES",
    "BandRow",
    "BuybackEvent",
    "ConsensusRow",
    "CorpProfile",
    "DisclosureRow",
    "FiscalPeriod",
    "InsiderReport",
    "InsiderWindow",
    "Instrument",
    "MajorHolderReport",
    "NaReason",
    "OpinionRow",
    "OverhangAdjust",
    "OverhangEvent",
    "OverhangState",
    "OverhangSummary",
    "PrelimRow",
    "PrelimScope",
    "QuarterRow",
    "RatioRow",
    "ReportRow",
    "RevisionRow",
    "ScenarioRow",
    "ScheduleRow",
    "SegmentRow",
    "ShareClass",
    "ShareCount",
    "StatementRaw",
    "SurpriseRow",
    "TargetConsensus",
    "ValuationDay",
    "WatchItem",
    "is_corp_code",
    "is_rcept_no",
    "is_stock_code",
    "quarter_of_reprt",
    "reprt_of_quarter",
]

DART_SOURCE: Final = "DART"

# 보고서 코드 → (분기 라벨, 누적 개월 수) — DR client.REPRT:24(P2 승격본 client.py:57 에서 옮김)
REPRT: Final[Mapping[str, tuple[str, int]]] = {
    "11013": ("1Q", 3),
    "11012": ("2Q", 6),
    "11014": ("3Q", 9),
    "11011": ("4Q", 12),
}

CORP_CLS: Final[tuple[str, ...]] = ("Y", "K", "N", "E")  # 유가·코스닥·코넥스·기타(DART corp_cls)
PARSE_STATES: Final[tuple[str, ...]] = ("none", "pending", "done", "failed", "na")
BUYBACK_KINDS: Final[tuple[str, ...]] = ("acquire", "dispose", "trust_in", "trust_out")
INSIDER_WINDOWS: Final[tuple[int, ...]] = (30, 90, 180)  # 달력일(§4.4)
BAND_METRICS: Final[tuple[str, ...]] = ("per", "pbr", "psr", "ev_ebitda")
SCENARIO_CASES: Final[tuple[str, ...]] = ("bear", "base", "bull")
SCENARIO_METHODS: Final[tuple[str, ...]] = ("PER", "PBR", "INSUFFICIENT")
# 리비전 신호(SD revision_calculator.classify_signal:76 의 다섯 단계 + 부호 전환 — §4.7)
REVISION_SIGNALS: Final[tuple[str, ...]] = (
    "STRONG_UP",
    "UP",
    "NEUTRAL",
    "DOWN",
    "STRONG_DOWN",
    "TURN",
)
WATCH_SOURCES: Final[tuple[str, ...]] = ("import:sd", "import:csv", "cli", "api")

_STOCK = re.compile(r"[0-9A-Z]{6}")
_CORP = re.compile(r"[0-9]{8}")
_RCEPT = re.compile(r"[0-9]{14}")
_REPRT_CODE = re.compile(r"1101[1-4]")
_FS_DIV = ("CFS", "OFS")
_FISCAL_YM = re.compile(r"[0-9]{4}(0[1-9]|1[0-2])")
_IDENT = re.compile(r"[a-z][a-z0-9_]*")
_CURRENCY = re.compile(r"[A-Z]{3}")
XLSX_MAX_BYTES: Final = 2 * 1024 * 1024  # pub_fin.report.xlsx 상한(0011 CHECK 와 같다)


class Instrument(StrEnum):
    """오버행 상품(§4.3). 값이 DB `pub_filings.overhang_event.instrument` 에 그대로 들어간다."""

    CB = "cb"  # 전환사채
    BW = "bw"  # 신주인수권부사채
    EB = "eb"  # 교환사채 — 교환 대상은 이미 있는 주식(신주 아님)
    PAID_IN = "paid_in"  # 유상증자
    BONUS = "bonus"  # 무상증자


class PrelimScope(StrEnum):
    """잠정실적 범위(SD earnings_parser.EARNINGS_TYPES:50 — 자회사가 가장 구체적이다)."""

    CONSOLIDATED = "consolidated"  # 연결재무제표기준영업(잠정)실적
    STANDALONE = "standalone"  # 영업(잠정)실적(별도)
    SUBSIDIARY = "subsidiary"  # (자회사의 주요경영사항) — 알림 대상 아님


class NaReason(StrEnum):
    """적용 불가 사유(설계 §5.2) — 값 칸은 None 이고 0 으로 채우지 않는다.

    `currency_mismatch`(§8.4 #19 외국기업 — 재무 통화 ≠ KRW 시세)와 `missing_input`(D&A 없는
    EV/EBITDA 처럼 필요한 입력이 공시에 없음)은 이 묶음(M)이 더한 두 값이다 [확인 필요 — A·V 와
    약속].
    """

    LOSS = "loss"  # 적자 — PER 산출 안 함
    CAPITAL_IMPAIRMENT = "capital_impairment"  # 자본잠식 — PBR
    NO_REVENUE = "no_revenue"  # 매출 ≤ 0 — PSR
    FINANCIAL_SECTOR = "financial_sector"  # 금융업 — 매출총이익·EBITDA·EV/EBITDA
    SHORT_HISTORY = "short_history"  # 유효 일 < 250 — 5년 밴드 없음(신규상장)
    NOT_DETAIL_TIER = "not_detail_tier"  # 전체 재무제표 수집 대상 아님(주요계정 층)
    NOT_IN_CONSENSUS_UNIVERSE = "not_in_consensus_universe"
    NO_FILINGS = "no_filings"  # 정기보고서 없음
    SPAC = "spac"  # 스팩 — 실적 지표 해당 없음
    KONEX_NO_CONSENSUS = "konex_no_consensus"
    CURRENCY_MISMATCH = "currency_mismatch"  # 재무 통화 ≠ 시세 통화(KRW) — 환산하지 않는다
    MISSING_INPUT = "missing_input"  # 계산에 필요한 공시 항목이 없다(대체값으로 바꾸지 않음)


# ── 형식 검사 ────────────────────────────────────────────────────────────────────────────


def is_stock_code(value: object) -> bool:
    """`^[0-9A-Z]{6}$` — 영숫자 단축코드(0009K0 꼴) 포함."""
    return isinstance(value, str) and _STOCK.fullmatch(value) is not None


def is_corp_code(value: object) -> bool:
    return isinstance(value, str) and _CORP.fullmatch(value) is not None


def is_rcept_no(value: object) -> bool:
    return isinstance(value, str) and _RCEPT.fullmatch(value) is not None


def quarter_of_reprt(reprt_code: str) -> int:
    """보고서 코드 → 분기 번호(1~4). 모르는 코드는 `ValueError`."""
    try:
        label, _ = REPRT[reprt_code]
    except KeyError:
        raise ValueError(f"보고서 코드는 {', '.join(REPRT)} 중 하나: {reprt_code!r}") from None
    return int(label[0])


def reprt_of_quarter(q: int) -> str:
    """분기 번호(1~4) → 보고서 코드."""
    for code, (label, _) in REPRT.items():
        if int(label[0]) == q:
            return code
    raise ValueError(f"분기는 1~4: {q!r}")


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _stock(what: str, value: str | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not is_stock_code(value):
        raise ValueError(f"{what} 는 종목코드(^[0-9A-Z]{{6}}$): {value!r}")


def _corp(what: str, value: str | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not is_corp_code(value):
        raise ValueError(f"{what} 는 corp_code(8자리 숫자): {value!r}")


def _rcept(what: str, value: str | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not is_rcept_no(value):
        raise ValueError(f"{what} 는 접수번호(14자리 숫자): {value!r}")


def _text(what: str, value: str | None, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip():
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


def _won(what: str, value: object, *, nonneg: bool = False) -> None:
    """원 단위 금액·주식 수 — 정수만(float·bool 은 거부)."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} 는 정수(원·주 단위)여야 한다: {value!r}")
    if nonneg and value < 0:
        raise ValueError(f"{what} 는 0 이상: {value!r}")


def _num(what: str, value: object, *, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{what} 는 수여야 한다: {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{what} 는 유한한 수여야 한다: {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{what} 는 0 보다 커야 한다: {value!r}")


def _quality(obj: object, name: str = "quality") -> None:
    q: object = getattr(obj, name)
    try:
        _set(obj, name, Quality(str(q)))
    except ValueError:
        raise ValueError(f"quality 는 ok·stale·estimated·invalid 중 하나: {q!r}") from None


def _source(value: str) -> None:
    _text("source", value)


def _dart_source(value: str) -> None:
    """공개 표(pub_*) 행 — 원천은 DART 하나(0010·0011 CHECK, ADR 0002 §2.1)."""
    if value != DART_SOURCE:
        raise ValueError(f"공개 재무·공시 행의 source 는 'DART' 만: {value!r}")


def _choice(what: str, value: object, allowed: Iterable[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    allowed_t = tuple(allowed)
    if value not in allowed_t:
        raise ValueError(f"{what} 는 {allowed_t} 중 하나: {value!r}")


def _ident(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise ValueError(f"{what} 는 소문자 식별자: {value!r}")


def _currency(value: object) -> None:
    if value is not None and not (isinstance(value, str) and _CURRENCY.fullmatch(value)):
        raise ValueError(f"currency 는 ISO 4217 세 글자: {value!r}")


def _fiscal_q(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4:
        raise ValueError(f"fiscal_q 는 1~4: {value!r}")


def _year(what: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1990 <= value <= 2100:
        raise ValueError(f"{what} 는 1990~2100 연도: {value!r}")


def _notes(obj: object, name: str = "notes") -> None:
    raw: object = getattr(obj, name)
    if isinstance(raw, str):
        raise TypeError(f"{name} 는 문자열 튜플이다(문자열 하나가 아님)")
    if not isinstance(raw, Iterable):
        raise TypeError(f"{name} 는 문자열 튜플이다")
    items = tuple(cast(Iterable[object], raw))
    for n in items:
        if not isinstance(n, str) or not n.strip():
            raise ValueError(f"{name} 의 항목은 비지 않은 문자열")
    _set(obj, name, items)


def _frozen_map(obj: object, name: str) -> None:
    raw: object = getattr(obj, name)
    if not isinstance(raw, Mapping):
        raise TypeError(f"{name} 는 mapping 이어야 한다")
    m = cast(Mapping[object, object], raw)
    _set(obj, name, {str(k): v for k, v in m.items()})


def _na(obj: object, name: str = "na_reason") -> None:
    v: object = getattr(obj, name)
    if v is not None:
        _set(obj, name, NaReason(str(v)))


# ── 회계 기간 ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FiscalPeriod:
    """회계 분기 하나 — `fiscal_period(bsns_year, reprt_code, acc_mt)`(엔진 R)의 결과.

    `year` 는 회계연도(DART bsns_year — 비12월 결산의 뜻은 [확인 필요 #34]), `q` 는 회계 분기(1~4),
    `start`·`end` 는 그 3개월의 첫날·마지막 날(달력). 비12월 결산 라벨 `FY25 1Q (25.04~25.06)`.
    """

    year: int
    q: int
    start: date
    end: date

    def __post_init__(self) -> None:
        _year("year", self.year)
        _fiscal_q(self.q)
        _day("start", self.start)
        _day("end", self.end)
        if not self.start < self.end:
            raise ValueError("start 는 end 보다 앞이어야 한다")
        days = (self.end - self.start).days
        if not 80 <= days <= 95:
            raise ValueError(f"분기는 약 3개월(80~95일): {days}일")

    @property
    def calendar_year_end(self) -> bool:
        """12월 결산(4분기 끝이 12월)인가."""
        return (self.end.month + (4 - self.q) * 3) % 12 == 0

    @property
    def label(self) -> str:
        """화면 라벨. 12월 결산은 `2025 1Q`, 비12월 결산은 `FY25 1Q (25.04~25.06)`."""
        if self.calendar_year_end:
            return f"{self.year} {self.q}Q"
        s, e = self.start, self.end
        return (
            f"FY{self.year % 100:02d} {self.q}Q "
            f"({s.year % 100:02d}.{s.month:02d}~{e.year % 100:02d}.{e.month:02d})"
        )

    @property
    def key(self) -> str:
        """`2025Q1` 꼴(실행 기록·알림 subject 의 기간 부분)."""
        return f"{self.year}Q{self.q}"


# ── 공시(0010 pub_filings — 공개, 원천 DART) ───────────────────────────────────────────────


@dataclass(frozen=True)
class DisclosureRow:
    """공시 한 건(`pub_filings.disclosure` — list.json 한 행 + 우리가 붙인 칸).

    `first_seen_at` = 우리가 처음 본 시각(DART 응답에는 접수 시각이 없다 — 날짜뿐, §4.1).
    `kind` 는 P2 `kind_of` + F 의 새 종류(소문자 식별자 — 닫힌 목록이 아니다).
    `kw_score`·`kw_matched` 는 `filings.derive` 가 채운다(F 는 None·() 로 넣는다).
    `parse_state`: none(파싱 대상 아님)·pending·done·failed·na(3회 실패 — 사유 parse_note).
    """

    rcept_no: str
    corp_code: str
    stock_code: str | None
    corp_name: str
    corp_cls: str | None
    title: str
    base_title: str
    filer: str | None
    rcept_dt: date
    remark: str | None
    kind: str
    is_correction: bool
    first_seen_at: datetime
    kw_score: int | None = None
    kw_matched: tuple[str, ...] = ()
    parse_state: str = "none"
    parse_tries: int = 0
    parse_note: str | None = None
    fin_loaded_at: datetime | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _stock("stock_code", self.stock_code, optional=True)
        _text("corp_name", self.corp_name)
        _choice("corp_cls", self.corp_cls, CORP_CLS, optional=True)
        _text("title", self.title)
        _text("base_title", self.base_title)
        _day("rcept_dt", self.rcept_dt)
        _ident("kind", self.kind)
        _aware("first_seen_at", self.first_seen_at)
        _won("kw_score", self.kw_score, nonneg=True)
        _set(self, "kw_matched", tuple(self.kw_matched))
        _choice("parse_state", self.parse_state, PARSE_STATES)
        _won("parse_tries", self.parse_tries, nonneg=True)
        _aware("fin_loaded_at", self.fin_loaded_at, optional=True)
        _dart_source(self.source)
        _quality(self)

    @property
    def link(self) -> str:
        """DART 뷰어 링크(공개 — 키 없음)."""
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"


@dataclass(frozen=True)
class CorpProfile:
    """기업개황(`pub_filings.corp_profile` — company.json). 대표자 이름 등 개인 칸은 받지 않는다.

    `acc_mt` = 결산월(1~12, DART 문자열 '12' → 12 [확인 필요 #34: 형식]). 모르면 None.
    """

    corp_code: str
    stock_code: str | None
    acc_mt: int | None
    induty_code: str | None
    corp_cls: str | None
    est_dt: date | None
    corp_name: str | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _stock("stock_code", self.stock_code, optional=True)
        _won("acc_mt", self.acc_mt)
        if self.acc_mt is not None and not 1 <= self.acc_mt <= 12:
            raise ValueError(f"acc_mt 는 1~12: {self.acc_mt!r}")
        if self.induty_code is not None and not re.fullmatch(r"[0-9]{1,6}", self.induty_code):
            raise ValueError(f"induty_code 는 숫자(KSIC): {self.induty_code!r}")
        _choice("corp_cls", self.corp_cls, CORP_CLS, optional=True)
        _day("est_dt", self.est_dt, optional=True)
        _dart_source(self.source)
        _quality(self)

    def is_financial(self, prefixes: Iterable[str]) -> bool | None:
        """금융업인가 — KSIC 앞 두 자리가 `prefixes` 중 하나(목록의 정본은 `config/fin.yaml
        valuation.financial_ksic_prefixes` 하나 — 여기에 두 벌로 적지 않는다). 업종 모름은 None."""
        if not self.induty_code or len(self.induty_code) < 2:
            return None
        return self.induty_code[:2] in tuple(prefixes)


_PRELIM_METRICS: Final = ("revenue", "op", "pretax", "ni", "ni_owner")


@dataclass(frozen=True)
class PrelimRow:
    """잠정실적 한 건(`pub_filings.earnings_prelim`) — 원 단위(본문 단위 × `unit_mult`).

    세 열을 다 남긴다: `_cur`(당기), `_prev_q`(전기 — 직전 분기), `_prev_y`(전년동기). 2열 폼은
    `_prev_y` 가 None. `fiscal_q`=4 + `is_annual`=True 는 연간 잠정. `period_inferred`=True 면
    본문에
    기간 표기가 없어 결산월로 추정(quality estimated — §4.2). `yoy_reported` = 본문 '전년동기대비%'
    {지표: %}(우리 계산과 0.1%p 넘게 다르면 `notes`). 정정은 새 접수번호의 새 행(`is_correction` 은
    공시 행에 있다).
    """

    rcept_no: str
    corp_code: str
    rcept_dt: date
    scope: PrelimScope
    fiscal_year: int
    fiscal_q: int
    is_annual: bool
    period_end: date
    period_inferred: bool
    unit_mult: int
    revenue_cur: int | None = None
    revenue_prev_q: int | None = None
    revenue_prev_y: int | None = None
    op_cur: int | None = None
    op_prev_q: int | None = None
    op_prev_y: int | None = None
    pretax_cur: int | None = None
    pretax_prev_q: int | None = None
    pretax_prev_y: int | None = None
    ni_cur: int | None = None
    ni_prev_q: int | None = None
    ni_prev_y: int | None = None
    ni_owner_cur: int | None = None
    ni_owner_prev_q: int | None = None
    ni_owner_prev_y: int | None = None
    eps_cur: float | None = None  # 원/주
    eps_prev_y: float | None = None
    yoy_reported: Mapping[str, float | None] = field(default_factory=dict[str, float | None])
    currency: str = "KRW"
    notes: tuple[str, ...] = ()
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _day("rcept_dt", self.rcept_dt)
        _set(self, "scope", PrelimScope(self.scope))
        _year("fiscal_year", self.fiscal_year)
        _fiscal_q(self.fiscal_q)
        if self.is_annual and self.fiscal_q != 4:
            raise ValueError("연간 잠정(is_annual)은 fiscal_q=4")
        _day("period_end", self.period_end)
        if self.period_end > self.rcept_dt:
            raise ValueError("period_end 가 접수일보다 뒤다(미래 기간의 잠정실적은 없다)")
        if self.unit_mult not in (1, 1_000, 1_000_000, 100_000_000):
            raise ValueError(f"unit_mult 는 1·1천·1백만·1억: {self.unit_mult!r}")
        for m in _PRELIM_METRICS:
            for tail in ("cur", "prev_q", "prev_y"):
                _won(f"{m}_{tail}", getattr(self, f"{m}_{tail}"))
        _num("eps_cur", self.eps_cur)
        _num("eps_prev_y", self.eps_prev_y)
        _frozen_map(self, "yoy_reported")
        for k, v in self.yoy_reported.items():
            _choice("yoy_reported 키", k, (*_PRELIM_METRICS, "eps"))
            _num(f"yoy_reported[{k}]", v)
        _currency(self.currency)
        _notes(self)
        _dart_source(self.source)
        _quality(self)

    @property
    def period(self) -> str:
        """`2026Q3`(연간 잠정은 `2026FY`) — 알림 subject 의 기간 부분(§D-P4-14)."""
        return f"{self.fiscal_year}FY" if self.is_annual else f"{self.fiscal_year}Q{self.fiscal_q}"


@dataclass(frozen=True)
class ScheduleRow:
    """실적 일정(`pub_filings.earnings_schedule`) — `filings.derive` 가 쓴다.

    kind: `pre_announce`(결산실적공시예고 본문의 예정일 — ok) · `statutory`(법정 제출기한 —
    estimated,
    분기·반기 = 분기말 + 45일, 사업 = 결산일 + 90일 [확인 필요: 휴일 처리]).
    """

    corp_code: str
    period_end: date
    kind: Literal["pre_announce", "statutory"]
    planned_date: date
    fiscal_year: int
    fiscal_q: int
    rcept_no: str | None = None
    note: str | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _day("period_end", self.period_end)
        _choice("kind", self.kind, ("pre_announce", "statutory"))
        _day("planned_date", self.planned_date)
        _year("fiscal_year", self.fiscal_year)
        _fiscal_q(self.fiscal_q)
        _rcept("rcept_no", self.rcept_no, optional=True)
        _dart_source(self.source)
        _quality(self)
        if self.kind == "statutory" and self.quality is Quality.OK:
            raise ValueError("법정 기한 행(statutory)은 추정이다 — quality estimated")


# ── 오버행 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OverhangEvent:
    """오버행 결정 공시 한 건(`pub_filings.overhang_event`).

    `face_value` = 권면총액(CB·BW·EB, 원) — 유상·무상증자는 None. `price` = 전환·행사·교환가액
    (유상증자는 발행가 — 구조화 응답에 없으면 원문 폴백, 둘 다 없으면 None). `shares` = 발행 결정 시
    잠재·신주 수(CB 전환 주식 수·BW 행사 주식 수·EB 교환 대상 주식 수·유증 신주 수).
    `refix_floor` = 리픽싱 하한(표시만). `alloc_method` = 배정 방식 원문(제3자배정 등 — 보호예수
    추정).
    `parse_source`: api(구조화) · document(원문 폴백).
    """

    rcept_no: str
    corp_code: str
    rcept_dt: date
    instrument: Instrument
    face_value: int | None
    price: float | None
    shares: int | None
    refix_floor: float | None
    exercise_start: date | None
    exercise_end: date | None
    maturity: date | None
    listing_date: date | None
    alloc_method: str | None
    parse_source: Literal["api", "document"]
    seq: str | None = None  # 회차(예: '3')
    notes: tuple[str, ...] = ()
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _day("rcept_dt", self.rcept_dt)
        _set(self, "instrument", Instrument(self.instrument))
        _won("face_value", self.face_value, nonneg=True)
        _num("price", self.price, positive=True)
        _won("shares", self.shares, nonneg=True)
        _num("refix_floor", self.refix_floor, positive=True)
        for n in ("exercise_start", "exercise_end", "maturity", "listing_date"):
            _day(n, getattr(self, n), optional=True)
        if (
            self.exercise_start is not None
            and self.exercise_end is not None
            and self.exercise_start > self.exercise_end
        ):
            raise ValueError("청구기간 시작이 끝보다 뒤다")
        _choice("parse_source", self.parse_source, ("api", "document"))
        _notes(self)
        _dart_source(self.source)
        _quality(self)


@dataclass(frozen=True)
class OverhangAdjust:
    """오버행 조정 공시(`pub_filings.overhang_adjust`) — 전환가액 조정·청구 행사·만기 전 상환.

    `target_rcept_no` = 연결된 결정 공시. 연결 실패면 None + `quality=invalid` + `note`(사유 —
    §4.3).
    exercise: `amount`(청구 권면 금액, 원)·`shares`(발행 주식 수). price_adjust: `new_price`.
    redemption: `amount`(상환 권면 금액).
    """

    rcept_no: str
    corp_code: str
    rcept_dt: date
    target_rcept_no: str | None
    kind: Literal["price_adjust", "exercise", "redemption"]
    new_price: float | None
    amount: int | None
    shares: int | None
    effective_date: date | None
    note: str | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _day("rcept_dt", self.rcept_dt)
        _rcept("target_rcept_no", self.target_rcept_no, optional=True)
        _choice("kind", self.kind, ("price_adjust", "exercise", "redemption"))
        _num("new_price", self.new_price, positive=True)
        _won("amount", self.amount, nonneg=True)
        _won("shares", self.shares, nonneg=True)
        _day("effective_date", self.effective_date, optional=True)
        _dart_source(self.source)
        _quality(self)
        if self.target_rcept_no is None and self.quality is not Quality.INVALID:
            raise ValueError("대상 결정 공시를 잇지 못한 조정은 quality invalid + note(사유)")
        if self.quality is Quality.INVALID:
            _text("note", self.note)


@dataclass(frozen=True)
class OverhangState:
    """t 기준 결정 공시 하나의 상태(`pub_filings.overhang_state` — 파생, §4.3).

    `remaining_face` = 권면총액 − Σ청구·상환(≤ t). 청구 공시를 파싱 못 하면 권면총액 그대로(상한) +
    estimated. `potential_shares` = 잔여 ÷ 현재 가격(버림) — EB 는 교환 대상(신주 아님).
    `lockup_end_est` = 추정 보호예수 해제일(D-P4-18 — estimated).
    """

    corp_code: str
    as_of: date
    rcept_no: str
    instrument: Instrument
    remaining_face: int | None
    price: float | None
    potential_shares: int | None
    active: bool
    exercisable: bool
    lockup_end_est: date | None = None
    notes: tuple[str, ...] = ()
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _day("as_of", self.as_of)
        _rcept("rcept_no", self.rcept_no)
        _set(self, "instrument", Instrument(self.instrument))
        _won("remaining_face", self.remaining_face, nonneg=True)
        _num("price", self.price, positive=True)
        _won("potential_shares", self.potential_shares, nonneg=True)
        if self.exercisable and not self.active:
            raise ValueError("행사 가능(exercisable)은 활성(active)일 때만")
        _day("lockup_end_est", self.lockup_end_est, optional=True)
        _notes(self)
        _dart_source(self.source)
        _quality(self)


@dataclass(frozen=True)
class OverhangSummary:
    """회사의 t 기준 오버행 요약(`pub_filings.overhang_summary` — 파생, 공개 칸만).

    분모(공개) = 최근 `share_count.common_issued`(`denom_source='dart_share_count'`). 분모가 없으면
    비율 None. 로그인 분모(KRX 상장주식수)·금액·ITM 은 읽을 때 계산하고 여기 쓰지 않는다(§2 혼합
    값).
    """

    corp_code: str
    as_of: date
    potential_new_shares: int
    exercisable_shares: int
    exchange_shares: int
    denominator: int | None
    denom_source: str | None
    ratio_pct: float | None
    exercisable_ratio_pct: float | None
    notes: tuple[str, ...] = ()
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _day("as_of", self.as_of)
        for n in ("potential_new_shares", "exercisable_shares", "exchange_shares"):
            _won(n, getattr(self, n), nonneg=True)
        _won("denominator", self.denominator, nonneg=True)
        if self.denominator == 0:
            raise ValueError("분모 0 은 None 으로(0 으로 나누지 않는다)")
        _num("ratio_pct", self.ratio_pct)
        _num("exercisable_ratio_pct", self.exercisable_ratio_pct)
        if self.denominator is None and (
            self.ratio_pct is not None or self.exercisable_ratio_pct is not None
        ):
            raise ValueError("분모가 없으면 비율도 None")
        if (self.denominator is None) != (self.denom_source is None):
            raise ValueError("분모와 분모 출처(denom_source)는 함께 있다")
        _notes(self)
        _dart_source(self.source)
        _quality(self)


# ── 내부자·대량보유·자기주식·주식총수 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class InsiderReport:
    """임원·주요주주 소유보고 한 건(`pub_filings.insider_report` — elestock.json 한 행).

    보고자 이름은 저장하되 로그·알림에는 싣지 않는다 [제안]. `reason`(변동 사유)은 원문 파싱에
    성공할
    때만 [확인 필요 #32].
    """

    rcept_no: str
    corp_code: str
    rcept_dt: date
    reporter: str
    position: str | None
    registered: bool | None
    major_holder: bool | None
    shares_after: int | None
    shares_delta: int | None
    ratio_after: float | None
    ratio_delta: float | None
    reason: str | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _day("rcept_dt", self.rcept_dt)
        _text("reporter", self.reporter)
        _won("shares_after", self.shares_after, nonneg=True)
        _won("shares_delta", self.shares_delta)
        _num("ratio_after", self.ratio_after)
        _num("ratio_delta", self.ratio_delta)
        _dart_source(self.source)
        _quality(self)


@dataclass(frozen=True)
class MajorHolderReport:
    """주식등의 대량보유상황보고(5%) 한 건(`pub_filings.major_holder_report` — majorstock.json).

    목록만 보인다 — 창 합계는 내지 않는다(임원 보고와 같은 지분을 이중으로 센다, §4.4).
    """

    rcept_no: str
    corp_code: str
    rcept_dt: date
    reporter: str
    report_type: str | None
    shares: int | None
    shares_delta: int | None
    ratio: float | None
    ratio_delta: float | None
    reason: str | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _day("rcept_dt", self.rcept_dt)
        _text("reporter", self.reporter)
        _won("shares", self.shares, nonneg=True)
        _won("shares_delta", self.shares_delta)
        _num("ratio", self.ratio)
        _num("ratio_delta", self.ratio_delta)
        _dart_source(self.source)
        _quality(self)


@dataclass(frozen=True)
class InsiderWindow:
    """t 기준 최근 W 일(30·90·180 달력일) 보고 기준 순증감(`pub_filings.insider_window` — 파생).

    화면 이름은 "보고 기준 순증감(사유 미구분 포함)" — 장내 순매수가 아니다(§4.4). `ratio_pct`
    분모는
    §4.3 과 같다(없으면 None). `reason_known_pct` = 사유를 아는 증감 주식 ÷ 전체 증감 주식(절댓값).
    """

    corp_code: str
    as_of: date
    days: int
    net_shares: int
    buy_shares: int
    sell_shares: int
    n_reports: int
    n_reporters: int
    ratio_pct: float | None
    reason_known_pct: float | None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _day("as_of", self.as_of)
        if self.days not in INSIDER_WINDOWS:
            raise ValueError(f"days 는 {INSIDER_WINDOWS} 중 하나: {self.days!r}")
        _won("net_shares", self.net_shares)
        _won("buy_shares", self.buy_shares, nonneg=True)
        _won("sell_shares", self.sell_shares, nonneg=True)
        if self.net_shares != self.buy_shares - self.sell_shares:
            raise ValueError("net_shares = buy_shares − sell_shares")
        _won("n_reports", self.n_reports, nonneg=True)
        _won("n_reporters", self.n_reporters, nonneg=True)
        _num("ratio_pct", self.ratio_pct)
        _num("reason_known_pct", self.reason_known_pct)
        if self.reason_known_pct is not None and not 0 <= self.reason_known_pct <= 100:
            raise ValueError("reason_known_pct 는 0~100")
        _dart_source(self.source)
        _quality(self)


@dataclass(frozen=True)
class BuybackEvent:
    """자기주식 취득·처분(·신탁) 결정(`pub_filings.buyback_event`)."""

    rcept_no: str
    corp_code: str
    rcept_dt: date
    kind: Literal["acquire", "dispose", "trust_in", "trust_out"]
    shares_planned: int | None
    amount_planned: int | None
    start: date | None
    end: date | None
    method: str | None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _corp("corp_code", self.corp_code)
        _day("rcept_dt", self.rcept_dt)
        _choice("kind", self.kind, BUYBACK_KINDS)
        _won("shares_planned", self.shares_planned, nonneg=True)
        _won("amount_planned", self.amount_planned, nonneg=True)
        _day("start", self.start, optional=True)
        _day("end", self.end, optional=True)
        _dart_source(self.source)
        _quality(self)


@dataclass(frozen=True)
class ShareCount:
    """정기보고서 주식의 총수 현황(`pub_filings.share_count` — stockTotqySttus.json [확인 필요
    #33]).

    `common_issued` = 보통주 발행주식의 총수(자기주식 포함) — 오버행·내부자 비율 공개 분모(§4.3).
    """

    corp_code: str
    period_end: date
    rcept_no: str
    common_issued: int | None
    pref_issued: int | None
    treasury_common: int | None
    treasury_pref: int | None
    rcept_dt: date | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _day("period_end", self.period_end)
        _rcept("rcept_no", self.rcept_no)
        for n in ("common_issued", "pref_issued", "treasury_common", "treasury_pref"):
            _won(n, getattr(self, n), nonneg=True)
        _day("rcept_dt", self.rcept_dt, optional=True)
        _dart_source(self.source)
        _quality(self)

    @property
    def common_outstanding(self) -> int | None:
        """보통주 발행 − 보통주 자기주식(BPS 분모 — §4.5). 하나라도 없으면 None."""
        if self.common_issued is None or self.treasury_common is None:
            return None
        return self.common_issued - self.treasury_common


# ── 재무(0011 pub_fin — 공개, 원천 DART) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class StatementRaw:
    """단일회사 전체 재무제표 응답 원시 행(`pub_fin.statement_raw` — 상세 층만).

    `rows` = 응답 list 그대로(dict 목록 — 값 해석은 엔진 R). 같은 키를 다시 받으면 덮어쓴다(정정
    보고서는 rcept_no 가 큰 쪽).
    """

    corp_code: str
    bsns_year: int
    reprt_code: str
    fs_div: Literal["CFS", "OFS"]
    rcept_no: str | None
    rows: tuple[Mapping[str, Any], ...]
    currency: str | None = None
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _year("bsns_year", self.bsns_year)
        if not _REPRT_CODE.fullmatch(self.reprt_code):
            raise ValueError(f"reprt_code 는 {', '.join(REPRT)} 중 하나: {self.reprt_code!r}")
        _choice("fs_div", self.fs_div, _FS_DIV)
        _rcept("rcept_no", self.rcept_no, optional=True)
        rows: list[Mapping[str, Any]] = []
        for r in self.rows:
            if not isinstance(r, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError("rows 의 항목은 mapping")
            rows.append({str(k): v for k, v in r.items()})
        _set(self, "rows", tuple(rows))
        _currency(self.currency)
        _dart_source(self.source)
        _quality(self)

    @property
    def n_rows(self) -> int:
        return len(self.rows)


_QUARTER_MONEY: Final = (
    "revenue",
    "cogs",
    "gross_profit",
    "sgna",
    "op",
    "pretax",
    "tax",
    "ni",
    "ni_owner",
    "d_and_a",
    "ebitda",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "equity_owner",
    "cash",
    "short_inv",
    "total_debt",
    "net_debt",
    "minority",
)


@dataclass(frozen=True)
class QuarterRow:
    """분기 재무 한 줄(`pub_fin.quarterly`) — 손익은 **분기 단독**(누적 차분 — Q4 = 연간 − 3분기
    누적), 상태표는 분기말(§4.5).

    `rcept_dt` = 그 분기를 담은 정기보고서 접수일 — **PIT 기준**(t 에 공시된 분기만 쓴다, D-P4-11).
    모르면 None(PIT 계산에서 빠진다). `tier`: main(주요계정 — fnlttMultiAcnt)·full(전체 재무제표).
    `derived`=True 면 직전 누적이 없어 차분을 못 한 값(손익 칸 None — 지어내지 않는다).
    `reported_3m` =
    보고서의 3개월값 {지표: 원}(값으로 쓰지 않는 대조 열), `diff3m_max_pct` = 차분값과의 최대
    차이(%) —
    1% 넘으면 quality estimated + 사유(notes).
    """

    corp_code: str
    fiscal_year: int
    fiscal_q: int
    fs_div: Literal["CFS", "OFS"]
    period_start: date
    period_end: date
    rcept_dt: date | None
    tier: Literal["main", "full"]
    revenue: int | None = None
    cogs: int | None = None
    gross_profit: int | None = None
    sgna: int | None = None
    op: int | None = None
    pretax: int | None = None
    tax: int | None = None
    ni: int | None = None
    ni_owner: int | None = None
    d_and_a: int | None = None
    ebitda: int | None = None
    total_assets: int | None = None
    total_liabilities: int | None = None
    total_equity: int | None = None
    equity_owner: int | None = None
    cash: int | None = None
    short_inv: int | None = None
    total_debt: int | None = None
    net_debt: int | None = None
    minority: int | None = None
    eps_basic: float | None = None  # 원/주 — 분기 단독
    derived: bool = False
    reported_3m: Mapping[str, int | None] = field(default_factory=dict[str, int | None])
    diff3m_max_pct: float | None = None
    currency: str = "KRW"
    notes: tuple[str, ...] = ()
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _year("fiscal_year", self.fiscal_year)
        _fiscal_q(self.fiscal_q)
        _choice("fs_div", self.fs_div, _FS_DIV)
        _day("period_start", self.period_start)
        _day("period_end", self.period_end)
        if not self.period_start < self.period_end:
            raise ValueError("period_start 는 period_end 보다 앞")
        _day("rcept_dt", self.rcept_dt, optional=True)
        if self.rcept_dt is not None and self.rcept_dt < self.period_end:
            raise ValueError("정기보고서 접수일이 분기말보다 앞이다")
        _choice("tier", self.tier, ("main", "full"))
        for n in _QUARTER_MONEY:
            _won(n, getattr(self, n))
        _num("eps_basic", self.eps_basic)
        _frozen_map(self, "reported_3m")
        for k, v in self.reported_3m.items():
            _choice("reported_3m 키", k, _QUARTER_MONEY)
            _won(f"reported_3m[{k}]", v)
        _num("diff3m_max_pct", self.diff3m_max_pct)
        _currency(self.currency)
        _notes(self)
        _dart_source(self.source)
        _quality(self)

    @property
    def period(self) -> str:
        return f"{self.fiscal_year}Q{self.fiscal_q}"

    def known_by(self, t: date) -> bool:
        """t 에 공시돼 있었는가(PIT — 정기보고서 접수일 ≤ t). 접수일 모름은 False."""
        return self.rcept_dt is not None and self.rcept_dt <= t


@dataclass(frozen=True)
class RatioRow:
    """DART 만으로 만든 비율(`pub_fin.ratio` — 공개, §4.5). 분모 ≤ 0 이면 None + `na_reasons`.

    `fs_div` 는 근거 기준(CFS 우선 — 분기마다 바뀌면 notes). `shares_basis` = BPS 분모(보통주 발행 −
    보통주 자기주식, DART 주식총수).
    """

    corp_code: str
    fiscal_year: int
    fiscal_q: int
    fs_div: Literal["CFS", "OFS"]
    ttm_revenue: int | None = None
    ttm_op: int | None = None
    ttm_ni_owner: int | None = None
    opm: float | None = None
    npm: float | None = None
    roe: float | None = None
    roa: float | None = None
    debt_ratio: float | None = None
    net_debt_ratio: float | None = None
    rev_yoy: float | None = None
    op_yoy: float | None = None
    eps_ttm: float | None = None
    bps: float | None = None
    shares_basis: int | None = None
    na_reasons: Mapping[str, str] = field(default_factory=dict[str, str])
    notes: tuple[str, ...] = ()
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _year("fiscal_year", self.fiscal_year)
        _fiscal_q(self.fiscal_q)
        _choice("fs_div", self.fs_div, _FS_DIV)
        for n in ("ttm_revenue", "ttm_op", "ttm_ni_owner"):
            _won(n, getattr(self, n))
        for n in ("opm", "npm", "roe", "roa", "debt_ratio", "net_debt_ratio", "rev_yoy", "op_yoy",
                  "eps_ttm", "bps"):  # fmt: skip
            _num(n, getattr(self, n))
        _won("shares_basis", self.shares_basis, nonneg=True)
        _frozen_map(self, "na_reasons")
        for k, v in self.na_reasons.items():
            if getattr(self, k, None) is not None:
                raise ValueError(f"na_reasons[{k}] 가 있는데 값도 있다")
            _ = NaReason(str(v))
        _notes(self)
        _dart_source(self.source)
        _quality(self)

    @property
    def period(self) -> str:
        return f"{self.fiscal_year}Q{self.fiscal_q}"


@dataclass(frozen=True)
class ReportRow:
    """dart-report 산출(`pub_fin.report`) — 화면 블록 payload 와 엑셀이 **같은 입력**에서(§4.6).

    `xlsx` ≤ 2 MiB(0011 CHECK). `engine_version`·`input_digest` 로 같은 입력이면 다시 만들지 않는다.
    종목마다 최근 4개만 남긴다(`ops.nightly` — 저장소 `prune_reports`).
    """

    stock_code: str
    built_on: date
    corp_code: str
    year_from: int
    year_to: int
    fs_div_used: Literal["CFS", "OFS"]
    payload: Mapping[str, Any]
    xlsx: bytes | None
    warnings: tuple[str, ...]
    engine_version: str
    input_digest: str
    generated_at: datetime
    source: str = DART_SOURCE
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("stock_code", self.stock_code)
        _day("built_on", self.built_on)
        _corp("corp_code", self.corp_code)
        _year("year_from", self.year_from)
        _year("year_to", self.year_to)
        if self.year_from > self.year_to:
            raise ValueError("year_from ≤ year_to")
        _choice("fs_div_used", self.fs_div_used, _FS_DIV)
        _frozen_map(self, "payload")
        if self.xlsx is not None:
            if not isinstance(self.xlsx, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError("xlsx 는 bytes")
            if len(self.xlsx) > XLSX_MAX_BYTES:
                raise ValueError(f"xlsx 는 {XLSX_MAX_BYTES}B 이하(0011 CHECK)")
        _notes(self, "warnings")
        _text("engine_version", self.engine_version)
        _text("input_digest", self.input_digest)
        _aware("generated_at", self.generated_at)
        _dart_source(self.source)
        _quality(self)

    @property
    def xlsx_sha256(self) -> str | None:
        if self.xlsx is None:
            return None
        return hashlib.sha256(self.xlsx).hexdigest()


@dataclass(frozen=True)
class SegmentRow:
    """부문 매출(`pub_fin.segment` — 사업보고서 원문 표 best-effort, quality estimated)."""

    corp_code: str
    bsns_year: int
    segment: str
    amounts: Mapping[str, int | None]
    rcept_no: str | None
    source: str = DART_SOURCE
    quality: Quality = Quality.ESTIMATED

    def __post_init__(self) -> None:
        _corp("corp_code", self.corp_code)
        _year("bsns_year", self.bsns_year)
        _text("segment", self.segment)
        _frozen_map(self, "amounts")
        for k, v in self.amounts.items():
            _won(f"amounts[{k}]", v)
        _rcept("rcept_no", self.rcept_no, optional=True)
        _dart_source(self.source)
        _quality(self)


# ── 컨센서스·리비전(0012 prv_fin — 로그인, 원천 KIS) ──────────────────────────────────────


def _fiscal_ym(value: str) -> None:
    if not isinstance(value, str) or not _FISCAL_YM.fullmatch(value):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError(f"fiscal_ym 은 YYYYMM: {value!r}")


@dataclass(frozen=True)
class ConsensusRow:
    """추정실적 스냅 한 줄(`prv_fin.consensus_snapshot` — KIS 종목추정실적, D-P4-9).

    `fiscal_ym` = 결산년월 YYYYMM, `is_estimate` = 추정(E)/실적(A) [확인 필요 #29]. 금액은 원
    (KIS 단위가 억원이면 파서가 원으로). `extra` = KIS 가 준 비율 등(계산에 쓰지 않음).
    """

    code: str
    snap_date: date
    fiscal_ym: str
    is_estimate: bool
    revenue: int | None
    op: int | None
    ni: int | None
    ni_owner: int | None
    eps: float | None
    ebitda: int | None = None
    extra: Mapping[str, Any] = field(default_factory=dict[str, Any])
    source: str = "KIS"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _day("snap_date", self.snap_date)
        _fiscal_ym(self.fiscal_ym)
        for n in ("revenue", "op", "ni", "ni_owner", "ebitda"):
            _won(n, getattr(self, n))
        _num("eps", self.eps)
        _frozen_map(self, "extra")
        _source(self.source)
        _quality(self)

    @property
    def fiscal_end(self) -> date:
        """결산월 마지막 날."""
        y, m = int(self.fiscal_ym[:4]), int(self.fiscal_ym[4:])
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1)


@dataclass(frozen=True)
class OpinionRow:
    """증권사 투자의견 한 줄(`prv_fin.invest_opinion`). 증권사 이름은 원문 그대로.

    `target_price` ≤ 0·의견 '미제시/Not Rated' 는 목표가 컨센서스에서 뺀다(엔진 K — §4.7).
    """

    code: str
    broker: str
    opinion_date: date
    opinion: str | None
    opinion_prev: str | None
    target_price: float | None
    source: str = "KIS"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _text("broker", self.broker)
        _day("opinion_date", self.opinion_date)
        _num("target_price", self.target_price)
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class TargetConsensus:
    """목표가 컨센서스(`prv_fin.target_consensus`) — 창 안 증권사별 마지막 행 하나씩(§4.7).

    n ≥ 3 → ok, 1~2 → estimated, 0 → 행을 만들지 않는다(None).
    """

    code: str
    as_of: date
    mean: float
    median: float
    high: float
    low: float
    n_brokers: int
    window_days: int
    source: str = "KIS"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _day("as_of", self.as_of)
        for n in ("mean", "median", "high", "low"):
            _num(n, getattr(self, n), positive=True)
        if not self.low <= self.median <= self.high or not self.low <= self.mean <= self.high:
            raise ValueError("low ≤ mean·median ≤ high")
        _won("n_brokers", self.n_brokers, nonneg=True)
        if self.n_brokers < 1:
            raise ValueError("증권사 0 이면 행을 만들지 않는다(None)")
        _won("window_days", self.window_days, nonneg=True)
        _source(self.source)
        _quality(self)
        if self.n_brokers < 3 and self.quality is Quality.OK:
            raise ValueError("증권사 1~2 곳이면 quality estimated(§4.7)")


@dataclass(frozen=True)
class RevisionRow:
    """리비전 한 줄(`prv_fin.revision`) — 지표 × 결산년월 × 창(7·30일).

    기준선 = snap_date ≤ t − W 중 가장 늦은 스냅. 기준 0 → pct None, 부호가 바뀌면 signal TURN.
    `metric`: revenue·op·ni·eps·target_mean(목표가 평균 — fiscal_ym 은 '000000').
    """

    code: str
    as_of: date
    metric: str
    fiscal_ym: str
    window_days: int
    current: float | None
    baseline: float | None
    baseline_date: date | None
    pct: float | None
    signal: str | None
    source: str = "KIS"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _day("as_of", self.as_of)
        _choice("metric", self.metric, ("revenue", "op", "ni", "eps", "target_mean"))
        if self.metric == "target_mean":
            if self.fiscal_ym != "000000":
                raise ValueError("목표가 리비전의 fiscal_ym 은 '000000'")
        else:
            _fiscal_ym(self.fiscal_ym)
        if self.window_days not in (7, 30):
            raise ValueError("window_days 는 7·30")
        _num("current", self.current)
        _num("baseline", self.baseline)
        _day("baseline_date", self.baseline_date, optional=True)
        _num("pct", self.pct)
        _choice("signal", self.signal, REVISION_SIGNALS, optional=True)
        if self.signal == "TURN" and self.pct is not None:
            raise ValueError("부호 전환(TURN)은 비율 대신 — pct None")
        _source(self.source)
        _quality(self)


# ── 밸류에이션·시나리오·서프라이즈(0012 prv_fin — 로그인) ──────────────────────────────────


_MULTIPLES: Final = ("per", "pbr", "psr", "ev_ebitda", "per_fwd", "psr_fwd", "ev_ebitda_fwd")


@dataclass(frozen=True)
class ValuationDay:
    """하루 PIT 배수(`prv_fin.valuation_daily` — hypertable, §4.8).

    `mktcap_company` = 회사 시총(보통 + 우선, 원). `ttm_ref` = 그날 쓴 마지막 분기(`2026Q2:CFS`).
    값이 거름값(`valuation.caps`) 밖이면 저장은 하되 `flags['out_of_cap']` 에 지표 목록. 적용 불가
    칸은 None + `flags['na'][지표] = NaReason`.
    """

    code: str
    trade_date: date
    mktcap_company: int | None
    price: float | None
    ttm_ref: str | None
    per: float | None = None
    pbr: float | None = None
    psr: float | None = None
    ev: int | None = None
    ev_ebitda: float | None = None
    per_fwd: float | None = None
    psr_fwd: float | None = None
    ev_ebitda_fwd: float | None = None
    flags: Mapping[str, Any] = field(default_factory=dict[str, Any])
    source: str = "krx+DART"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _day("trade_date", self.trade_date)
        _won("mktcap_company", self.mktcap_company, nonneg=True)
        _num("price", self.price)
        for n in _MULTIPLES:
            _num(n, getattr(self, n))
        _won("ev", self.ev)
        _frozen_map(self, "flags")
        na: object = self.flags.get("na", {})
        if not isinstance(na, Mapping):
            raise TypeError("flags['na'] 는 {지표: NaReason} mapping")
        for k, v in cast(Mapping[object, object], na).items():
            _choice("flags.na 지표", k, _MULTIPLES)
            _ = NaReason(str(v))
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class BandRow:
    """배수 밴드(`prv_fin.valuation_band`) — 최근 1,240거래일 유효 값의 선형 백분위(§4.8).

    유효 일 < 250 이면 백분위·최소·최대 None + `na_reason=short_history`. `current_rank_pct` =
    100 × #{x ≤ 현재} ÷ n(경험적 — SD 조각 선형과 다르다, ADR 0016).
    """

    code: str
    as_of: date
    metric: str
    p10: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    p90: float | None
    min: float | None
    max: float | None
    n_days: int
    n_excluded: int
    current: float | None
    current_rank_pct: float | None
    window_from: date | None
    na_reason: NaReason | None = None
    source: str = "krx+DART"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _day("as_of", self.as_of)
        _choice("metric", self.metric, BAND_METRICS)
        ps = (self.p10, self.p25, self.p50, self.p75, self.p90)
        for n, v in zip(("p10", "p25", "p50", "p75", "p90"), ps, strict=True):
            _num(n, v)
        _num("min", self.min)
        _num("max", self.max)
        vals = [v for v in ps if v is not None]
        if vals and (len(vals) != 5 or vals != sorted(vals)):
            raise ValueError("백분위는 다섯 개 모두 있고 p10 ≤ … ≤ p90")
        _won("n_days", self.n_days, nonneg=True)
        _won("n_excluded", self.n_excluded, nonneg=True)
        _num("current", self.current)
        _num("current_rank_pct", self.current_rank_pct)
        if self.current_rank_pct is not None and not 0 <= self.current_rank_pct <= 100:
            raise ValueError("current_rank_pct 는 0~100")
        _day("window_from", self.window_from, optional=True)
        _na(self)
        if not vals and self.na_reason is None:
            raise ValueError("밴드가 없으면 na_reason 이 있어야 한다")
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class ScenarioRow:
    """시나리오 적정가 한 칸(`prv_fin.scenario` — 구 TAM, D-P4-12)(§4.9).

    `eps_source`·`multiple_source` 는 SD 와 같은 문자열 체계(`CONSENSUS`·`TTM_EPS`·`BASE_×0.7`·
    `5Y_P25` …). method INSUFFICIENT 면 eps·multiple·tp None + `na_reason`.
    """

    code: str
    as_of: date
    case: Literal["bear", "base", "bull"]
    method: Literal["PER", "PBR", "INSUFFICIENT"]
    eps: float | None
    eps_source: str | None
    multiple: float | None
    multiple_source: str | None
    tp: float | None
    upside_pct: float | None
    price: float | None = None
    na_reason: NaReason | None = None
    source: str = "krx+DART+KIS"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _day("as_of", self.as_of)
        _choice("case", self.case, SCENARIO_CASES)
        _choice("method", self.method, SCENARIO_METHODS)
        for n in ("eps", "multiple", "tp", "upside_pct", "price"):
            _num(n, getattr(self, n))
        _na(self)
        if self.method == "INSUFFICIENT":
            if self.tp is not None:
                raise ValueError("INSUFFICIENT 는 적정가 None")
            if self.na_reason is None:
                raise ValueError("INSUFFICIENT 는 na_reason(사유)이 있어야 한다")
        elif self.tp is not None and (self.eps_source is None or self.multiple_source is None):
            raise ValueError("적정가에는 근거 문자열(eps_source·multiple_source)이 필수")
        _source(self.source)
        _quality(self)


@dataclass(frozen=True)
class SurpriseRow:
    """잠정실적 분류·알림 기록(`prv_fin.earnings_surprise` — 로그인, §4.2).

    `signal`·`priority` = YoY 분류(공개 규칙) 또는 서프라이즈 분류(분기 컨센서스가 있을 때만).
    `alerted_at`·`notify_ticket` = 관심종목 알림을 넣은 시각·발송 키. 성과는 알림 직전 마감 종가
    대비 3·5·7거래일 뒤 종가(정지로 없으면 None).
    """

    rcept_no: str
    code: str | None
    corp_code: str
    period: str
    scope: PrelimScope
    signal: str
    priority: int
    rev_yoy: float | None = None
    op_yoy: float | None = None
    rev_surprise: float | None = None
    op_surprise: float | None = None
    consensus_ref: str | None = None
    alerted_at: datetime | None = None
    notify_ticket: str | None = None
    price_at_alert: float | None = None
    perf_3d: float | None = None
    perf_5d: float | None = None
    perf_7d: float | None = None
    perf_done: bool = False
    notes: tuple[str, ...] = ()
    source: str = "DART"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _rcept("rcept_no", self.rcept_no)
        _stock("code", self.code, optional=True)
        _corp("corp_code", self.corp_code)
        if not re.fullmatch(r"[0-9]{4}(Q[1-4]|FY)", self.period):
            raise ValueError(f"period 는 YYYYQn 또는 YYYYFY: {self.period!r}")
        _set(self, "scope", PrelimScope(self.scope))
        if not re.fullmatch(r"[A-Z][A-Z_]*", self.signal):
            raise ValueError(f"signal 은 대문자 식별자(SD 분류 이름): {self.signal!r}")
        if isinstance(self.priority, bool) or not 1 <= self.priority <= 9:
            raise ValueError("priority 는 1~9(작을수록 중요)")
        for n in ("rev_yoy", "op_yoy", "rev_surprise", "op_surprise", "price_at_alert",
                  "perf_3d", "perf_5d", "perf_7d"):  # fmt: skip
            _num(n, getattr(self, n))
        _aware("alerted_at", self.alerted_at, optional=True)
        _notes(self)
        _source(self.source)
        _quality(self)


# ── 관심종목·종류주식(0012·0013 — 로그인·개인) ──────────────────────────────────────────


@dataclass(frozen=True)
class WatchItem:
    """관심종목 한 줄(`prv_journal.watchlist` — 개인 데이터, D-P4-13). 공개판은 관심종목을
    모른다."""

    list_name: str
    code: str
    name_at_add: str | None
    added_at: datetime | None
    sort: int
    note: str | None = None
    source: str = "cli"

    def __post_init__(self) -> None:
        _ident("list_name", self.list_name)
        _stock("code", self.code)
        _aware("added_at", self.added_at, optional=True)
        _won("sort", self.sort, nonneg=True)
        if self.sort >= 32768:
            raise ValueError(f"sort 는 0~32767(smallint): {self.sort!r}")
        _choice("source", self.source, WATCH_SOURCES)


@dataclass(frozen=True)
class ShareClass:
    """종류주식 → 회사(`prv_market.share_class` — KRX 기본정보 + 이름 규칙, 로그인 원천).

    우선주는 DART corp_code 가 없다 → `common_code` 의 회사 재무를 쓴다(§4.5). 코드·이름 규칙이
    **둘 다** 맞을 때만 만든다(R13 — 못 맞추면 그 종목은 na_reason).
    """

    code: str
    corp_code: str | None
    share_class: Literal["common", "pref"]
    common_code: str
    source: str = "krx"
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        _stock("code", self.code)
        _corp("corp_code", self.corp_code, optional=True)
        _choice("share_class", self.share_class, ("common", "pref"))
        _stock("common_code", self.common_code)
        if self.share_class == "common" and self.common_code != self.code:
            raise ValueError("보통주의 common_code 는 자기 코드")
        if self.share_class == "pref" and self.common_code == self.code:
            raise ValueError("우선주의 common_code 는 다른(보통주) 코드")
        _source(self.source)
        _quality(self)

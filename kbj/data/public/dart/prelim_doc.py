"""잠정실적·결산실적공시예고 원문 해석(공개 등급).

승격 원본: SD `earnings_parser.py`: `EARNINGS_TYPES`:50, `classify_disclosure_type`:58,
`_parse_num`:111, `_strip_html`:124, `_detect_unit_multiplier`:133, `detect_scope_from_body`:155,
`parse_preliminary_earnings`:181, `_infer_quarter`:262, `save_pre_announce_to_queue`:351(예정일
정규식). docs/p4_design.md §1.3·§4.2, docs/metrics.md §14.

SD 와 다른 점
- 세 열을 다 남긴다: 당기·전기(직전 분기 — SD 는 버렸다)·전년동기(5번째 열). 2열 폼(전년동기 없음)은
  당기·전기만.
- 단위는 **원**으로(SD 는 백만원). 단위 표기(억원·백만원·천원·원)가 없으면 SD 처럼 백만원으로 보되
  notes·quality estimated 로 드러낸다(조용히 가정하지 않는다).
- 음수 표기 `-`·`(1,234)`·`△1,234` 를 읽는다. 지표에 법인세비용차감전계속사업이익·지배기업
  소유주지분 순이익을 더했다(라벨 [확인 필요 #30]).
- 기간: 본문 기간 표기('YYYY.MM.DD ~ YYYY.MM.DD')가 먼저, 없으면 **결산월 기준** 접수일 직전에 끝난
  회계 분기(SD `_infer_quarter` 는 달력 분기 고정 — 비12월 결산에서 틀린다). 결산월을 모르면 달력
  분기 + estimated. 회계연도 이름은 '회계연도가 시작한 해'([확인 필요 #34] — 합성 세계·엔진 R 의
  `fiscal_period` 와 같은 뜻).
- 범위: 제목의 자회사 > 본문의 연결/별도 > 제목(SD `save_earnings_actual` 와 같은 우선순위).
- 결산실적공시예고: 예정일만 쓰던 SD 를 넘어 공시 시각·대상도 읽는다. 결과는
  `pub_filings.disclosure. parse_note` 에 `encode_pre_announce()` 글자로 남긴다(실적 일정 표는
  `filings.derive` — 묶음 E — 가 `decode_pre_announce()` 로 읽어 만든다. 피드는 그 표를 쓰지 않는다
  — 두 작업이 한 표를 쓰지 않게).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final, Literal, cast

from kbj.core.fin_rows import FiscalPeriod, PrelimRow, PrelimScope
from kbj.core.quality import Quality
from kbj.data.public.dart.doctext import (
    INT_MAX,
    ParseError,
    cell_after,
    html_tables,
    parse_date,
    parse_float,
    strip_html,
)

__all__ = [
    "METRIC_LABELS",
    "UNIT_MULT",
    "PreAnnounce",
    "PrelimMetric",
    "PrelimParse",
    "classify",
    "decode_pre_announce",
    "encode_pre_announce",
    "fiscal_period_of_end",
    "infer_period",
    "parse",
    "parse_pre_announce",
    "parse_pre_announce_detail",
    "to_row",
]

PRE_ANNOUNCE: Final = "PRE_ANNOUNCE"
# 본문 단위 표기 → 원 배수(SD _detect_unit_multiplier 는 백만원 배율이었다). 긴 이름부터 본다
UNIT_MULT: Final[tuple[tuple[str, int], ...]] = (
    ("백만원", 1_000_000),
    ("억원", 100_000_000),
    ("천원", 1_000),
    ("원", 1),
)
DEFAULT_UNIT: Final = ("백만원", 1_000_000)  # 표기 없음 — SD 기본값(estimated)
# 지표 → 본문 라벨(앞의 것부터) [확인 필요 #30]. eps 는 원/주(단위 배수를 곱하지 않는다)
METRIC_LABELS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("revenue", ("매출액", "영업수익", "매출")),
    ("op", ("영업이익", "영업손익")),
    ("pretax", ("법인세비용차감전계속사업이익", "법인세비용차감전순이익", "법인세차감전순이익")),
    ("ni", ("당기순이익", "당기순손익")),
    ("ni_owner", ("지배기업 소유주지분 순이익", "지배기업소유주지분순이익", "지배주주순이익")),
    ("eps", ("주당순이익", "기본주당순이익")),
)
_MONEY: Final = ("revenue", "op", "pretax", "ni", "ni_owner")
_NUM = r"(\(?[△\-]?[\d,]+(?:\.\d+)?\)?|-)"
_PCT = r"(\(?-?[\d,]+(?:\.\d+)?\)?|-)"
# 흑자적자전환여부 칸 — 글자('흑자전환' 등) 또는 '-'. 숫자로 시작하는 토큰은 받지 않는다: SD 의
# `(\S+)` 는 이 칸이 비어 있으면(태그를 지우면 사라진다) 다음 열(전년동기 숫자)을 전환 칸으로 먹어
# 전년동기·증감율이 한 칸씩 밀려 읽혔다(예: '100 50 100.0 80 25.0 -' → 전년동기 25.0)
_TURN = r"([^\s\d(△\-.,+][^\s]*|-)"
_YOY_TURN = r"(?:\s+(흑자전환|적자전환|적자지속|흑자지속|적자확대|적자축소|-))?"
_UNIT = re.compile(r"단위\s*[:：]\s*([가-힣%원,\s]+)")
_PERIOD = re.compile(
    r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\s*[~∼]\s*"
    r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"
)
_CONSOL: Final = ("연결재무제표", "연결기준", "연결 기준")
_SEPARATE: Final = ("별도재무제표", "별도기준", "별도 기준")
_NOTE_KEYS: Final = ("planned", "time", "target")
# SD save_pre_announce_to_queue:351 의 정규식(표가 없을 때 본문에서)
_PRE_PATTERNS: Final = (
    re.compile(r"결산실적\s*공시예정일\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"),
    re.compile(
        r"(?:발표\s*예정일|예상\s*발표일|실적발표\s*예정일|공시\s*예정일)\s*[:：]?\s*"
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    ),
)


@dataclass(frozen=True)
class PrelimMetric:
    """한 지표의 세 열(원 — eps 는 원/주). `yoy_pct`·`qoq_pct` 는 본문 표기 %(없으면 None)."""

    cur: int | float | None
    prev_q: int | float | None
    prev_y: int | float | None = None
    yoy_pct: float | None = None
    qoq_pct: float | None = None
    qoq_turn: str | None = None  # 전기 대비 흑자·적자 전환 표기(본문)
    yoy_turn: str | None = None  # 전년동기 대비 흑자·적자 전환 표기(본문)
    six_col: bool = False  # 6열 폼(전년동기 열이 있는 행)에서 읽었다


@dataclass(frozen=True)
class PrelimParse:
    """잠정실적 원문 해석 결과(기간·범위는 to_row 가 정한다)."""

    unit_label: str
    unit_mult: int
    unit_assumed: bool
    metrics: Mapping[str, PrelimMetric]
    two_col: bool
    scope: PrelimScope | None  # 본문 표기(연결/별도) — 없으면 None
    period: tuple[date, date] | None  # 본문 기간 표기
    notes: tuple[str, ...] = field(default=())


def classify(title: str | None) -> PrelimScope | Literal["PRE_ANNOUNCE"] | None:
    """제목 → 잠정실적 범위(SD classify_disclosure_type:58 순서: 자회사 > 결산예고 > 연결 > 별도).

    '실적등에대한전망'(가이던스)은 None(제외).
    """
    t = (title or "").replace(" ", "")
    if not t or "실적등에대한전망" in t:
        return None
    if "자회사" in t and "잠정" in t:
        return PrelimScope.SUBSIDIARY
    if "결산실적공시예고" in t:
        return PRE_ANNOUNCE
    if "연결재무제표기준영업(잠정)" in t:
        return PrelimScope.CONSOLIDATED
    if "영업(잠정)실적" in t:
        return PrelimScope.STANDALONE
    return None


def _unit(text: str) -> tuple[str, int, bool]:
    m = _UNIT.search(text[:3000])
    if m:
        unit_str = m.group(1)
        for label, mult in UNIT_MULT:
            if label in unit_str:
                return label, mult, False
    return DEFAULT_UNIT[0], DEFAULT_UNIT[1], True


def _scope_of_body(text: str) -> PrelimScope | None:
    """본문의 연결/별도 표기 — 먼저 나온 쪽(SD detect_scope_from_body:155)."""
    c = min((p for p in (text.find(k) for k in _CONSOL) if p >= 0), default=-1)
    s = min((p for p in (text.find(k) for k in _SEPARATE) if p >= 0), default=-1)
    if c >= 0 and (s < 0 or c < s):
        return PrelimScope.CONSOLIDATED
    if s >= 0:
        return PrelimScope.STANDALONE
    return None


def _dec(s: str | None) -> Decimal | None:
    """표 숫자 → Decimal(빈 값 '-' 은 None). 괄호·△ 는 음수."""
    if s is None:
        return None
    t = s.strip()
    if t in ("", "-"):
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.startswith("△"):
        neg, t = True, t[1:]
    if t.startswith("-"):
        neg, t = not neg, t[1:]
    try:
        d = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None
    return -d if neg else d


def _won(d: Decimal | None, mult: int) -> int | None:
    """표시 단위 → 원(반올림). bigint 밖·정밀도 초과(`InvalidOperation` — ValueError 가 아니다)는
    `ParseError` 로 — 그 공시 한 건의 실패로 처리되게."""
    if d is None:
        return None
    try:
        v = int((d * mult).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ParseError("금액이 너무 크다(정밀도 초과)") from None
    if abs(v) > INT_MAX:
        raise ParseError("금액이 bigint 범위 밖이다")
    return v


def _label_re(label: str) -> str:
    """라벨 글자 사이 공백을 허용한다('지배기업 소유주지분 순이익')."""
    return r"\s*".join(re.escape(ch) for ch in label if not ch.isspace())


def parse(html: str) -> PrelimParse:
    """잠정실적 원문 → 세 열(당기·전기·전년동기) 원 단위. 지표를 하나도 못 읽으면 `ParseError`.

    폼(SD parse_preliminary_earnings:181): `<라벨> 당해실적 당기 전기 전기대비% 흑자적자전환
    전년동기 전년동기대비% 흑자적자전환` — 6열. 전기 쪽 흑자적자전환 칸이 비어 태그와 함께 사라진
    행(`당기 전기 전기대비% 전년동기 전년동기대비%`)도 6열 폼으로 읽는다(SD 는 이 행을 2열로 읽어
    전년동기를 버렸다). 전년동기 열이 없는 단순 폼은 `당해실적 당기 전기` 2열.
    """
    text = strip_html(html)
    if not text:
        raise ParseError("원문이 비었다")
    unit_label, mult, assumed = _unit(text)
    metrics: dict[str, PrelimMetric] = {}
    two_col_seen = False
    six_col_seen = False
    for key, labels in METRIC_LABELS:
        for label in labels:
            lab = _label_re(label)
            six = re.search(
                rf"{lab}\s*당해실적\s+{_NUM}\s+{_NUM}\s+{_PCT}\s+{_TURN}\s+{_NUM}\s+{_PCT}{_YOY_TURN}",
                text,
            )
            # 전기 쪽 흑자적자전환 칸이 빈 행(5개 값)
            # 그룹: 당기·전기·전기대비%·전년동기·전년동기대비%
            five = None if six else re.search(
                rf"{lab}\s*당해실적\s+{_NUM}\s+{_NUM}\s+{_PCT}\s+{_NUM}\s+{_PCT}{_YOY_TURN}", text
            )  # fmt: skip
            factor = 1 if key == "eps" else mult
            if six is not None or five is not None:
                if six is not None:
                    g_cur, g_prev_q, g_qoq, g_turn = (six.group(i) for i in (1, 2, 3, 4))
                    g_prev_y, g_yoy, g_yturn = six.group(5), six.group(6), six.group(7)
                else:
                    m5 = cast(re.Match[str], five)
                    g_cur, g_prev_q, g_qoq = m5.group(1), m5.group(2), m5.group(3)
                    g_turn = None
                    g_prev_y, g_yoy, g_yturn = m5.group(4), m5.group(5), m5.group(6)
                cur, prev_q, prev_y = _dec(g_cur), _dec(g_prev_q), _dec(g_prev_y)
                metrics[key] = PrelimMetric(
                    cur=_won(cur, factor) if key != "eps" else _float(cur),
                    prev_q=_won(prev_q, factor) if key != "eps" else _float(prev_q),
                    prev_y=_won(prev_y, factor) if key != "eps" else _float(prev_y),
                    yoy_pct=parse_float(g_yoy),
                    qoq_pct=parse_float(g_qoq),
                    qoq_turn=None if g_turn in (None, "-") else g_turn,
                    yoy_turn=None if g_yturn in (None, "-") else g_yturn,
                    six_col=True,
                )
                six_col_seen = True
                break
            simple = re.search(rf"{lab}\s*당해실적\s+{_NUM}\s+{_NUM}", text)
            if simple:
                cur, prev_q = _dec(simple.group(1)), _dec(simple.group(2))
                metrics[key] = PrelimMetric(
                    cur=_won(cur, factor) if key != "eps" else _float(cur),
                    prev_q=_won(prev_q, factor) if key != "eps" else _float(prev_q),
                )
                two_col_seen = True
                break
    if not any(k in metrics for k in _MONEY):
        raise ParseError("잠정실적 표(매출액·영업이익 … 당해실적)를 찾지 못했다")
    notes: list[str] = []
    if assumed:
        notes.append("단위 표기 없음 — 백만원으로 보았다(SD 기본값)")
    if two_col_seen and six_col_seen:
        notes.append("6열·2열 행이 섞였다 — 지표마다 읽은 열만 남겼다")
    period: tuple[date, date] | None = None
    m = _PERIOD.search(text)
    if m:
        try:
            a = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            b = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            a = b = None
        if a is not None and b is not None and a < b:
            period = (a, b)
    return PrelimParse(
        unit_label=unit_label,
        unit_mult=mult,
        unit_assumed=assumed,
        metrics=metrics,
        two_col=two_col_seen and not six_col_seen,
        scope=_scope_of_body(text),
        period=period,
        notes=tuple(notes),
    )


def _float(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def fiscal_period_of_end(end: date, acc_mt: int) -> FiscalPeriod:
    """분기 끝 날짜 + 결산월 → 회계 분기(회계연도 = 시작한 해 [확인 필요 #34]).

    예: 결산월 3, 2026-09-30 → FY2026 2Q(2026-07-01~09-30). 12월 결산이면 달력 분기.
    """
    if not 1 <= acc_mt <= 12:
        raise ValueError(f"결산월은 1~12: {acc_mt!r}")
    start_month = acc_mt % 12 + 1
    months = (end.month - start_month) % 12
    if months % 3 != 2:
        raise ValueError(f"{end} 는 결산월 {acc_mt} 회사의 분기말이 아니다")
    q = months // 3 + 1
    year = end.year if end.month >= start_month else end.year - 1
    q_start = _add_months(_month_start(end), -2)
    return FiscalPeriod(year, q, q_start, end)


def _last_quarter_end_before(day: date, acc_mt: int) -> date:
    """`day` 앞(같은 날 제외)에 끝난 마지막 회계 분기말."""
    end_months = {(acc_mt + 3 * k - 1) % 12 + 1 for k in range(4)}
    d = day.replace(day=1) - timedelta(days=1)  # 지난달 말일
    while d.month not in end_months:
        d = d.replace(day=1) - timedelta(days=1)
    return d


def infer_period(
    rcept_dt: date, acc_mt: int | None, parsed: PrelimParse | None = None
) -> tuple[FiscalPeriod, bool, bool, tuple[str, ...]]:
    """잠정실적의 회계 기간 → (분기, 추정했는가, 연간인가, notes).

    ① 본문 기간 표기(정답 — 단, 끝이 접수일 뒤면 버린다) ② 결산월 기준 접수일 직전에 끝난 분기
      (`period_inferred=True`) ③ 결산월 모름 → 달력 분기 + notes. 본문 기간이 300일 이상이면 연간.
    """
    notes: list[str] = []
    acc = acc_mt
    if acc is None:
        acc = 12
        notes.append("결산월 모름 — 12월 결산(달력 분기)으로 보았다")
    if parsed is not None and parsed.period is not None:
        start, end = parsed.period
        if end <= rcept_dt:
            annual = (end - start).days >= 300
            try:
                return fiscal_period_of_end(end, acc), False, annual, tuple(notes)
            except ValueError:
                notes.append(f"본문 기간 끝({end})이 결산월 {acc} 의 분기말이 아니다 — 추정")
        else:
            notes.append(f"본문 기간 끝({end})이 접수일 뒤 — 버리고 추정")
    # 본문 기간이 없으면 결산월 기준 직전 분기(period_inferred=True·quality estimated 가 그 뜻을
    # 싣는다)
    end = _last_quarter_end_before(rcept_dt, acc)
    return fiscal_period_of_end(end, acc), True, False, tuple(notes)


def _scope(title: str, parsed: PrelimParse) -> PrelimScope:
    by_title = classify(title)
    if by_title is PrelimScope.SUBSIDIARY:
        return PrelimScope.SUBSIDIARY
    if parsed.scope is not None:
        return parsed.scope
    if isinstance(by_title, PrelimScope):
        return by_title
    raise ParseError("잠정실적 범위(연결·별도)를 정하지 못했다")


def _int_or_none(v: int | float | None) -> int | None:
    return v if isinstance(v, int) else None


def to_row(
    parsed: PrelimParse,
    *,
    rcept_no: str,
    corp_code: str,
    rcept_dt: date,
    title: str,
    acc_mt: int | None,
    yoy_tol_pp: float = 0.1,
) -> PrelimRow:
    """해석 결과 → `PrelimRow`(원 단위). 기간 추정·단위 가정·결산월 모름이면 quality estimated.

    본문 '전년동기대비%' 와 우리 계산이 `yoy_tol_pp`(%p) 넘게 다르면 notes(본문 값은 반올림·정정
    포함일 수 있다 — 값은 바꾸지 않는다, §4.2).
    """
    period, inferred, annual, period_notes = infer_period(rcept_dt, acc_mt, parsed)
    notes = [*parsed.notes, *period_notes]
    m = parsed.metrics
    yoy: dict[str, float | None] = {}
    for key in (*_MONEY, "eps"):
        got = m.get(key)
        if got is None or not got.six_col:
            continue
        yoy[key] = got.yoy_pct
        cur, base = got.cur, got.prev_y
        if got.yoy_pct is not None and cur is not None and base:
            ours = (cur - base) / abs(base) * 100
            if abs(ours - got.yoy_pct) > yoy_tol_pp:
                notes.append(f"{key} 전년동기대비 본문 {got.yoy_pct}% ≠ 계산 {ours:.2f}%")
    eps = m.get("eps")

    def col(key: str, tail: Literal["cur", "prev_q", "prev_y"]) -> int | None:
        got = m.get(key)
        return None if got is None else _int_or_none(getattr(got, tail))

    estimated = inferred or parsed.unit_assumed or acc_mt is None
    return PrelimRow(
        rcept_no=rcept_no,
        corp_code=corp_code,
        rcept_dt=rcept_dt,
        scope=_scope(title, parsed),
        fiscal_year=period.year,
        fiscal_q=period.q,
        is_annual=annual,
        period_end=period.end,
        period_inferred=inferred,
        unit_mult=parsed.unit_mult,
        revenue_cur=col("revenue", "cur"),
        revenue_prev_q=col("revenue", "prev_q"),
        revenue_prev_y=col("revenue", "prev_y"),
        op_cur=col("op", "cur"),
        op_prev_q=col("op", "prev_q"),
        op_prev_y=col("op", "prev_y"),
        pretax_cur=col("pretax", "cur"),
        pretax_prev_q=col("pretax", "prev_q"),
        pretax_prev_y=col("pretax", "prev_y"),
        ni_cur=col("ni", "cur"),
        ni_prev_q=col("ni", "prev_q"),
        ni_prev_y=col("ni", "prev_y"),
        ni_owner_cur=col("ni_owner", "cur"),
        ni_owner_prev_q=col("ni_owner", "prev_q"),
        ni_owner_prev_y=col("ni_owner", "prev_y"),
        eps_cur=None if eps is None or eps.cur is None else float(eps.cur),
        eps_prev_y=None if eps is None or eps.prev_y is None else float(eps.prev_y),
        yoy_reported=yoy,
        notes=tuple(notes),
        quality=Quality.ESTIMATED if estimated else Quality.OK,
    )


# ── 결산실적공시예고 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreAnnounce:
    """결산실적공시예고 본문 — 공시 예정일(필수)·시각·대상(예: '2026년 연간 및 4분기 잠정실적')."""

    planned_date: date
    planned_time: str | None = None
    target: str | None = None


def parse_pre_announce_detail(html: str) -> PreAnnounce | None:
    """예정일을 못 읽으면 None. 표('공시예정일 | 2027-01-28')가 먼저, 없으면 SD 본문 정규식."""
    tables = html_tables(html)
    planned = parse_date(
        cell_after(tables, ("공시예정일", "결산실적공시예정일", "발표예정일", "공시 예정일"))
    )
    if planned is None:
        text = strip_html(html)
        for pat in _PRE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    planned = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    planned = None
                break
    if planned is None:
        return None
    raw_time = cell_after(tables, ("공시예정시간", "발표예정시간", "공시 예정시간"))
    hhmm = re.search(r"([01]?\d|2[0-3])\s*[:시]\s*([0-5]\d)", raw_time or "")
    return PreAnnounce(
        planned_date=planned,
        planned_time=f"{int(hhmm.group(1)):02d}:{hhmm.group(2)}" if hhmm else None,
        target=cell_after(tables, ("공시대상", "공시 대상", "대상")),
    )


def parse_pre_announce(html: str) -> date | None:
    """결산실적공시예고 → 공시 예정일(설계 §1.3 시그니처). 못 읽으면 None."""
    got = parse_pre_announce_detail(html)
    return None if got is None else got.planned_date


def encode_pre_announce(p: PreAnnounce) -> str:
    """`disclosure.parse_note` 에 남길 글자 — `planned=2027-01-28;time=16:00;target=…`."""
    parts = [f"planned={p.planned_date.isoformat()}"]
    if p.planned_time:
        parts.append(f"time={p.planned_time}")
    if p.target:
        parts.append("target=" + p.target.replace(";", ",").replace("=", " "))
    return ";".join(parts)


def decode_pre_announce(note: str | None) -> PreAnnounce | None:
    """`encode_pre_announce` 의 역. 형식이 아니면 None(실패 사유 글자 등)."""
    if not note or not note.startswith("planned="):
        return None
    got: dict[str, str] = {}
    for part in note.split(";"):
        k, sep, v = part.partition("=")
        if sep and k in _NOTE_KEYS:
            got[k] = v
    planned = parse_date(got.get("planned"))
    if planned is None:
        return None
    return PreAnnounce(planned, got.get("time") or None, got.get("target") or None)

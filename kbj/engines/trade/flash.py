"""수출 속보 엔진(순수 — 계약 ⑨, docs/p6_design.md §4.1, metrics §25).

관세청 10일 단위 잠정치(15157908·941·901·909 — `pub_trade.ten_day`)는 그 달 1일부터 구간 끝 날까지
**누계**다(seg 1 = 1~10일, 2 = 1~20일, 3 = 1~말일). 이 모듈은 저장된 행(`TenDayRow`)만으로:

- 구간 표: 종류(kind)마다 (ym, seg) 구간 하나 = 열 00~10 칸(00 = 전체). 최신이 먼저.
- YoY = `v(kind, col, ym, seg) / v(kind, col, ym−12, seg) − 1`. 전년 값이 없거나 0 이면 None + 사유,
  **열 이름이 전년과 다르면 None**(10대 품목·국가 구성이 바뀌면 비교 불가 — 사유 '열 이름 바뀜').
- 전월 같은 구간 대비(`mom`) = `v(ym, seg) / v(ym−1, seg) − 1`(같은 규칙 — 화면 보조).
- 10대 기여도 = `Δvᵢ / Δv₀₀`(Δ = 올해 − 전년 같은 구간, 분모 0 → None). 열 00 은 1.0(분모가 있으면).
- '발표 당시'(first_usd) 대 '최신'(usd — 정정 반영, D-P6-12). 표는 최신 기준, 칩·마감 요약 줄은
  발표 당시 값의 YoY(전년 값은 최신 — 이미 굳은 값).
- 칩 = 수출 품목(`exp_item`) 열 00 의 가장 최근 구간 YoY(`1~20일 +12.3%` 꼴, quality estimated).

금액은 미화 달러 정수(원화 환산 없음 — D-P6-11). 값을 지어내지 않는다 — 없으면 None 과 사유.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.core.trade_rows import TenDayRow, prev_ym

__all__ = [
    "CHIP_KIND",
    "CHIP_LABEL",
    "REASON_COLUMN_CHANGED",
    "REASON_NO_PREV",
    "REASON_PREV_ZERO",
    "Basis",
    "FlashCell",
    "FlashChip",
    "FlashSegment",
    "flash_chip",
    "monotonic_breaks",
    "new_segments",
    "ratio",
    "segments",
    "span_label",
]

Basis = Literal["latest", "first"]

CHIP_KIND: Final = "exp_item"
CHIP_LABEL: Final = "수출 속보"
REASON_NO_PREV: Final = "전년 같은 구간 값 없음"
REASON_PREV_ZERO: Final = "전년 같은 구간 값 0"
REASON_COLUMN_CHANGED: Final = "열 이름 바뀜"
_Q_ORDER: Final = (Quality.OK, Quality.STALE, Quality.ESTIMATED, Quality.INVALID)


def _worst(qs: Iterable[Quality]) -> Quality:
    got = list(qs)
    return max(got, key=_Q_ORDER.index) if got else Quality.INVALID


def _int(v: Decimal | None) -> int | None:
    return None if v is None else int(v)


def ratio(cur: Decimal | int | None, prev: Decimal | int | None) -> tuple[float | None, str | None]:
    """`cur / prev − 1` 과 사유. 어느 하나 None → (None, 전년 값 없음), prev 0 → (None, 0)."""
    if prev is None:
        return None, REASON_NO_PREV
    if cur is None:
        return None, "값 없음"
    if prev == 0:
        return None, REASON_PREV_ZERO
    return float(Decimal(cur) / Decimal(prev)) - 1.0, None


def span_label(span_text: str) -> str:
    """`01~20` → `1~20일`."""
    a, b = span_text.split("~", 1)
    return f"{int(a)}~{int(b)}일"


@dataclass(frozen=True)
class FlashCell:
    col_no: int
    name: str
    usd: int | None
    first_usd: int | None
    yoy: float | None
    yoy_reason: str | None
    contribution: float | None
    mom: float | None
    quality: Quality


@dataclass(frozen=True)
class FlashSegment:
    kind: str
    ym: str
    seg: int
    span_text: str
    first_seen_at: datetime
    revised_at: datetime | None
    quality: Quality
    source: str
    as_of: datetime
    cells: tuple[FlashCell, ...]

    @property
    def total(self) -> FlashCell | None:
        return next((c for c in self.cells if c.col_no == 0), None)


_Key = tuple[str, str, int, int]  # (kind, ym, seg, col_no)


def _index(rows: Iterable[TenDayRow]) -> dict[_Key, TenDayRow]:
    out: dict[_Key, TenDayRow] = {}
    for r in rows:
        k = (r.kind, r.ym, r.seg, r.col_no)
        if k in out:
            raise ValueError(f"10일 잠정치 같은 칸이 두 번 있다: {k}")
        out[k] = r
    return out


def _value(r: TenDayRow | None, basis: Basis) -> Decimal | None:
    if r is None or r.quality is Quality.INVALID:
        return None
    return r.first_usd if basis == "first" else r.usd


def _yoy(
    idx: Mapping[_Key, TenDayRow], r: TenDayRow, basis: Basis
) -> tuple[float | None, str | None]:
    p = idx.get((r.kind, prev_ym(r.ym, 12), r.seg, r.col_no))
    if p is not None and p.name != r.name:
        return None, f"{REASON_COLUMN_CHANGED}(전년 '{p.name}' → '{r.name}')"
    # 전년 값은 최신(이미 굳은 값) — 발표 당시 기준이어도 분모는 최신
    return ratio(_value(r, basis), _value(p, "latest"))


def _delta(idx: Mapping[_Key, TenDayRow], r: TenDayRow, basis: Basis) -> Decimal | None:
    p = idx.get((r.kind, prev_ym(r.ym, 12), r.seg, r.col_no))
    cur, prev = _value(r, basis), _value(p, "latest")
    if cur is None or prev is None or (p is not None and p.name != r.name):
        return None
    return cur - prev


def _cells(
    idx: Mapping[_Key, TenDayRow], rows: Sequence[TenDayRow], basis: Basis
) -> tuple[FlashCell, ...]:
    by_col = sorted(rows, key=lambda r: r.col_no)
    total = next((r for r in by_col if r.col_no == 0), None)
    d0 = _delta(idx, total, basis) if total is not None else None
    out: list[FlashCell] = []
    for r in by_col:
        yoy, why = _yoy(idx, r, basis)
        di = _delta(idx, r, basis)
        contrib = None if di is None or d0 is None or d0 == 0 else float(di / d0)
        m = idx.get((r.kind, prev_ym(r.ym, 1), r.seg, r.col_no))
        mom = (
            None
            if m is not None and m.name != r.name
            else ratio(_value(r, basis), _value(m, "latest"))[0]
        )
        out.append(
            FlashCell(
                col_no=r.col_no,
                name=r.name,
                usd=_int(r.usd),
                first_usd=_int(r.first_usd),
                yoy=yoy,
                yoy_reason=why,
                contribution=contrib,
                mom=mom,
                quality=r.quality,
            )
        )
    return tuple(out)


def _segment(
    idx: Mapping[_Key, TenDayRow], rows: Sequence[TenDayRow], basis: Basis
) -> FlashSegment:
    r0 = rows[0]
    revised = [r.revised_at for r in rows if r.revised_at is not None]
    return FlashSegment(
        kind=r0.kind,
        ym=r0.ym,
        seg=r0.seg,
        span_text=r0.span_text,
        first_seen_at=min(r.first_seen_at for r in rows),
        revised_at=max(revised) if revised else None,
        quality=_worst(r.quality for r in rows),
        source="+".join(sorted({r.source for r in rows})),
        as_of=max(r.as_of for r in rows),
        cells=_cells(idx, rows, basis),
    )


def _groups(rows: Iterable[TenDayRow], kind: str) -> dict[tuple[str, int], list[TenDayRow]]:
    out: dict[tuple[str, int], list[TenDayRow]] = {}
    for r in rows:
        if r.kind == kind:
            out.setdefault((r.ym, r.seg), []).append(r)
    return out


def segments(
    rows: Sequence[TenDayRow], *, kind: str, months: int, basis: Basis = "latest"
) -> list[FlashSegment]:
    """그 종류의 구간 표 — 가장 최근 기준 월부터 `months` 개월(그 안의 구간 전부), 최신이 먼저.

    rows 에는 YoY 를 위해 전년 같은 구간(12개월 더 앞)이 있어야 한다(없으면 YoY None + 사유)."""
    if months < 1:
        raise ValueError(f"months 는 1 이상: {months}")
    idx = _index(rows)
    groups = _groups(rows, kind)
    if not groups:
        return []
    last = max(ym for ym, _ in groups)
    first = prev_ym(last, months - 1)
    keys = sorted((k for k in groups if k[0] >= first), reverse=True)
    return [_segment(idx, groups[k], basis) for k in keys]


@dataclass(frozen=True)
class FlashChip:
    """띠 칩·마감 요약 줄 값 — 수출 품목 전체(열 00)의 한 구간, 발표 당시 기준 YoY."""

    ym: str
    seg: int
    span_text: str
    usd: int
    yoy: float | None
    yoy_reason: str | None
    first_seen_at: datetime
    source: str
    quality: Quality

    @property
    def label(self) -> str:
        return span_label(self.span_text)

    @property
    def text(self) -> str:
        """`2026-12 1~10일 +12.3%`(YoY 없으면 사유)."""
        v = f"{self.yoy * 100:+.1f}%" if self.yoy is not None else (self.yoy_reason or "YoY 없음")
        return f"{self.ym} {self.label} {v}"


def _chip_of(idx: Mapping[_Key, TenDayRow], r: TenDayRow) -> FlashChip | None:
    v = _value(r, "first")
    if v is None:
        return None
    yoy, why = _yoy(idx, r, "first")
    q = r.quality if r.quality is not Quality.OK else Quality.ESTIMATED  # 잠정치 — 단정하지 않는다
    return FlashChip(
        ym=r.ym,
        seg=r.seg,
        span_text=r.span_text,
        usd=int(v),
        yoy=yoy,
        yoy_reason=why,
        first_seen_at=r.first_seen_at,
        source=r.source,
        quality=q,
    )


def _totals(rows: Iterable[TenDayRow]) -> list[TenDayRow]:
    return [
        r
        for r in rows
        if r.kind == CHIP_KIND and r.col_no == 0 and r.quality is not Quality.INVALID
    ]


def flash_chip(rows: Sequence[TenDayRow]) -> FlashChip | None:
    """가장 최근 구간(ym, seg 최대 — 발표 당시 값이 있는 것)의 칩. 없으면 None."""
    idx = _index(rows)
    cands = [r for r in _totals(rows) if r.first_usd is not None]
    if not cands:
        return None
    r = max(cands, key=lambda x: (x.ym, x.seg))
    return _chip_of(idx, r)


def new_segments(rows: Sequence[TenDayRow], *, since: datetime, until: datetime) -> list[FlashChip]:
    """`since < first_seen_at ≤ until` 에 처음 본 수출 품목 전체 구간(오래된 것부터) — 마감 요약 줄
    입력(직전 마감 요약 이후 처음 본 구간만 — D-P6-8)."""
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("since·until 은 시간대가 있는 시각이어야 한다")
    idx = _index(rows)
    hits = sorted(
        (r for r in _totals(rows) if since < r.first_seen_at <= until),
        key=lambda x: (x.ym, x.seg),
    )
    out = [_chip_of(idx, r) for r in hits]
    return [c for c in out if c is not None]


def monotonic_breaks(rows: Sequence[TenDayRow]) -> list[tuple[str, str, int, int]]:
    """누계가 줄어든 칸 `(kind, ym, seg, col_no)` — seg 2 < seg 1 또는 seg 3 < seg 2(최신
    값, 같은 열
    이름일 때만). 잠정치라 정정으로 생길 수 있어 오류가 아니라 경고(notes) 입력이다."""
    idx = _index(rows)
    out: list[tuple[str, str, int, int]] = []
    for (kind, ym, seg, col), r in sorted(idx.items()):
        if seg == 1:
            continue
        p = idx.get((kind, ym, seg - 1, col))
        if p is None or p.name != r.name or p.usd is None or r.usd is None:
            continue
        if r.usd < p.usd:
            out.append((kind, ym, seg, col))
    return out

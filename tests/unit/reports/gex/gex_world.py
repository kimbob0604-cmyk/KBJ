"""OUT 시험 입력 — 엔진 레벨·진단 행을 손으로 만든다.

합성 — 실데이터 아님(CLAUDE.md 공개 레포 규칙).

값은 손계산하기 쉽게 고른 합성 수(코스피200 선물 근처 pt 가 아니어도 된다). 레벨 이름·detail 키는
엔진(`kbj.engines.gex.evaluate._levels`)이 쓰는 모양 그대로: flip detail `f_ref`·`multi_cross`,
expected_move_calendar detail `F`·`lower`·`upper`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from kbj.core.gex.records import LevelRecord
from kbj.core.p7_rows import FearGreedRow, VerdictRow, fear_greed_band
from kbj.core.quality import Quality
from kbj.core.time import KST

BY = "test.out"
DEFAULT_PARTS: dict[str, int] = {"fear_greed": 1, "breadth_ma20": 1, "gex": 1, "credit_spread": 0}
SRC = "SYNTHETIC:test.out"


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)


def levels(
    ts: datetime,
    *,
    trade_date: date | None = None,
    session: str = "day",
    scope: str = "all",
    f: float | None = 351.2,
    flip: float | None = 350.0,
    call_wall: float | None = 360.0,
    put_wall: float | None = 340.0,
    sigma: float | None = 4.0,
    quality: str = "ok",
    flags: dict[str, str] | None = None,
    drop: tuple[str, ...] = (),
) -> list[LevelRecord]:
    """한 사이클의 레벨 행. f 는 Flip detail f_ref(현재가), sigma 는 ±1σ(달력, F 가운데)."""
    flags = flags or {}
    td = trade_date or ts.astimezone(KST).date()
    dist = None if (f is None or flip is None) else round((f - flip) / f * 100, 4)
    move_detail: dict[str, Any] = {}
    if sigma is not None and f is not None:
        move_detail = {"F": f, "lower": f - sigma, "upper": f + sigma}
    spec: list[tuple[str, float | None, dict[str, Any]]] = [
        ("call_wall", call_wall, {}),
        ("put_wall", put_wall, {}),
        ("abs_gamma", 350.0, {}),
        ("flip", flip, {"f_ref": f, "multi_cross": False} if f is not None else {}),
        ("flip_distance", dist, {"f_ref": f, "flip": flip}),
        ("expected_move_calendar", sigma, move_detail),
        ("expected_move_trading", sigma, move_detail),
        ("top_levels", 3.0, {}),
    ]
    flag_of = {"flip_distance": "flip_distance", "expected_move_calendar": "expected_move",
               "expected_move_trading": "expected_move"}  # fmt: skip
    out: list[LevelRecord] = []
    for name, value, detail in spec:
        if name in drop:
            continue
        out.append(LevelRecord.model_validate({
            "ts": ts, "trade_date": td, "session": session, "scope": scope, "name": name,
            "value": value, "detail": detail, "quality": quality,
            "flag": flags.get(name, flags.get(flag_of.get(name, name), "visible")),
        }))  # fmt: skip
    return out


def fear_greed(d: date, score: str | None, *, q: Quality = Quality.OK) -> FearGreedRow:
    s = None if score is None else Decimal(score)
    return FearGreedRow(
        trade_date=d,
        score=s,
        band=None if s is None else fear_greed_band(s),
        components={"vol": s, "breadth": s, "credit": s, "foreign": s, "spread": s},
        source=SRC,
        as_of=at(d, 16, 0),
        quality=q if s is not None else Quality.INVALID,
        reasons=() if s is not None else ("성분 부족",),
    )


def verdict(
    d: date,
    label: str = "risk_on",
    score: int | None = 3,
    *,
    parts: dict[str, int] | None = None,
    events: tuple[str, ...] = (),
    q: Quality = Quality.ESTIMATED,
) -> VerdictRow:
    return VerdictRow(
        trade_date=d,
        label=label,  # type: ignore[arg-type]
        score=score,
        parts=parts if parts is not None else DEFAULT_PARTS,
        event_flags=events,
        source=SRC,
        as_of=at(d, 16, 0),
        quality=q,
    )

"""공개 진단 JSON — `diag/summary.json`·`diag/history.json`·`diag/events.json`(P7 묶음 DIAG 본문).

근거: docs/p7_design.md §2·§7, metrics §D1~§D5·§D9.

- `pub_diag.indicator`·`gauge`·`event_marker` 의 SQL 만 읽는다(공개 내보내기 역할 — 계약 ⑧, 스키마는
  `pub_` 만). 로그인 판정(국장 공포탐욕·종합 판정·GEX)은 키조차 싣지
  않는다(`manifest.diag_problems`).
- summary: 오늘 이하 마지막 날의 공공지표 다섯 줄 + 매크로 위험 게이지 + 오늘부터 `HORIZON_DAYS`
  이벤트.
  지표의 상태 점수(`score`)는 게이지 성분(`gauge.parts`)에서 옮긴다(공식을 두 벌 두지 않는다).
- history: 지표마다 최근 `HISTORY_POINTS` 거래일 점(값·구간·백분위·품질)과 게이지 판정 이력.
- events: 오늘 −7일 ~ +`HORIZON_DAYS` 일 이벤트.
- 행이 하나도 없으면(작업 전) 파일 0·이유 0 — 빈 파일·가짜 값을 만들지 않는다. 숫자는 반올림하지
  않는다(화면에서만).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.core.time import KST
from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, Row, canon, day, num

__all__ = [
    "DIAG_DIR",
    "HISTORY_POINTS",
    "HORIZON_DAYS",
    "Q_DIAG_EVENTS",
    "Q_DIAG_GAUGE",
    "Q_DIAG_HISTORY",
    "Q_DIAG_LAST",
    "build_diag",
]

DIAG_DIR: Final = "diag"
HORIZON_DAYS: Final = 30
EVENTS_BACK_DAYS: Final = 7
HISTORY_POINTS: Final = 250
SOURCE: Final = "KBJ:diag.public"
_IND_COLS: Final = "trade_date, key, value, unit, state, pct_rank, flags, quality, reasons, as_of"

Q_DIAG_LAST: Final = Query(
    "diag_last",
    "SELECT max(trade_date) AS trade_date FROM pub_diag.indicator WHERE trade_date <= %(upto)s",
)
Q_DIAG_HISTORY: Final = Query(
    "diag_history",
    "SELECT trade_date, key, value, unit, state, pct_rank, flags, quality, reasons, as_of "
    "FROM pub_diag.indicator WHERE trade_date BETWEEN %(start)s AND %(end)s "
    "ORDER BY key, trade_date",
)
Q_DIAG_GAUGE: Final = Query(
    "diag_gauge",
    "SELECT trade_date, label, score, parts, quality, reasons, as_of FROM pub_diag.gauge "
    "WHERE trade_date BETWEEN %(start)s AND %(end)s ORDER BY trade_date",
)
Q_DIAG_EVENTS: Final = Query(
    "diag_events",
    "SELECT date, kind, title, detail, quality, as_of FROM pub_diag.event_marker "
    "WHERE date BETWEEN %(start)s AND %(end)s ORDER BY date, kind, title",
)


def _q(v: object) -> Quality:
    q = str(v)
    if q not in ("ok", "stale", "estimated", "invalid"):
        raise ValueError(f"모르는 quality: {q[:12]!r}")
    return q  # pyright: ignore[reportReturnType]


def _list(v: object) -> list[Any]:
    got = canon(v)
    return list(got) if isinstance(got, list) else []  # pyright: ignore[reportUnknownArgumentType]


def _card(r: Row, parts: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": r["key"],
        "tier": "public",
        "value": num(r["value"]),
        "unit": r["unit"],
        "state": r["state"],
        "pct_rank": num(r["pct_rank"]),
        "score": parts.get(r["key"]),
        "flags": _list(r["flags"]),
        "quality": _q(r["quality"]),
        "reasons": _list(r["reasons"]),
    }


def _gauge(g: Row) -> dict[str, Any]:
    return {
        "label": g["label"],
        "score": g["score"],
        "parts": canon(g["parts"]),
        "quality": _q(g["quality"]),
        "reasons": _list(g["reasons"]),
    }


def _event(e: Row, today: date) -> dict[str, Any]:
    left = (e["date"] - today).days
    return {
        "date": day(e["date"]),
        "kind": e["kind"],
        "title": e["title"],
        "days_left": left if left >= 0 else None,
        "quality": _q(e["quality"]),
    }


def _file(name: str, rows: Sequence[Row], data: dict[str, Any]) -> PublicFile:
    return PublicFile(
        name=f"{DIAG_DIR}/{name}",
        source=SOURCE,
        as_of=max(r["as_of"] for r in rows),
        quality=worst_quality(_q(r["quality"]) for r in rows),
        data=data,
        compact=True,
    )


def build_diag(read: Reader, *, now: datetime) -> BuildOut:
    """공개 진단 파일 셋(행이 없으면 파일 0)."""
    today = now.astimezone(KST).date()
    last = read(Q_DIAG_LAST, {"upto": today})
    d: date | None = last[0]["trade_date"] if last else None
    if d is None:
        return BuildOut()
    start = d - timedelta(days=HISTORY_POINTS * 2)
    hist = read(Q_DIAG_HISTORY, {"start": start, "end": d})
    gauges = read(Q_DIAG_GAUGE, {"start": start, "end": d})
    ev_start = today - timedelta(days=EVENTS_BACK_DAYS)
    ev_end = today + timedelta(days=HORIZON_DAYS)
    events = read(Q_DIAG_EVENTS, {"start": ev_start, "end": ev_end})
    rows = [r for r in hist if r["trade_date"] == d]
    gauge = next((g for g in gauges if g["trade_date"] == d), None)
    parts: dict[str, Any] = dict(canon(gauge["parts"])) if gauge is not None else {}
    upcoming = [e for e in events if e["date"] >= today]
    summary = {
        "trade_date": day(d),
        "gauge": _gauge(gauge) if gauge is not None else None,
        "indicators": [_card(r, parts) for r in sorted(rows, key=lambda r: str(r["key"]))],
        "events": [_event(e, today) for e in upcoming],
    }
    series: dict[str, dict[str, Any]] = {}
    for r in hist:
        s = series.setdefault(str(r["key"]), {"unit": r["unit"], "points": []})
        s["points"].append({
            "trade_date": day(r["trade_date"]), "value": num(r["value"]), "state": r["state"],
            "pct_rank": num(r["pct_rank"]), "quality": _q(r["quality"]),
        })  # fmt: skip
    for s in series.values():
        s["points"] = s["points"][-HISTORY_POINTS:]
    history = {
        "series": series,
        "gauge": [
            {"trade_date": day(g["trade_date"]), "label": g["label"], "score": g["score"]}
            for g in gauges[-HISTORY_POINTS:]
        ],
    }
    files = [
        _file("summary.json", [*rows, *([gauge] if gauge is not None else []), *upcoming], summary),
        _file("history.json", [*hist, *gauges], history),
    ]
    if events:
        files.append(
            _file(
                "events.json",
                events,
                {
                    "start": day(ev_start),
                    "end": day(ev_end),
                    "events": [_event(e, today) for e in events],
                },
            )
        )
    return BuildOut(files=tuple(files))

"""공개 매크로 파일 — `macro/tabs.json`·`macro/<탭>.json`·`macro/calendar.json`(docs/p5_design.md
§7, MAC 본문).

- `pub_macro.series_meta`·`series`·`calendar` 만 읽는다(SQL 은 `pub_` 스키마 — `test_sql_schemas`,
  계약 ⑧). 공개 탭(`config/macro.yaml tabs[].public`)·공개 시리즈만 싣는다 — 로그인 탭
  '시장'·예상치는
  없다(공개 캘린더 표에 예상치 칸이 없다).
- 파일 모양은 로그인 API 응답 모델과 같다(`MacroTabs`·`MacroSeriesSet`·`EconCalendar` 의 data) —
  화면이
  같은 코드로 그린다. 파일마다 봉투(source·as_of·quality — 하위 폴더 규칙).
- 기간: 일별 2년, 월·분기 10년(§7 — 합계 3 MB 상한 `DIR_CAPS["macro"]`). 캘린더는 오늘 −past ~
  +future(`calendar.window_days`).
- 파생(metrics §16): 이 패키지는 엔진을 import 하지 않는다(계약 ⑧). 공개 파생 중
  차(spread)·전년동월비
  (yoy)는 같은 정의를 SQL 로 계산한다(`Q_SPREAD`·`Q_YOY` — 같은 기간끼리만, 한쪽이 없으면 NULL).
  실질 정책금리(real_rate)는 로그인 화면에서만(사유를 싣는다 — 값을 지어내지 않는다).
- 최신값 변화 = 값이 있는 마지막 두 기간의 차(엔진 `latest_change` 와 같은 정의).
- 공개 메타가 비었으면(수집 전) 파일 0·이유 0. 메타는 있는데 값이 없으면 파일 없이 이유만(가짜 값
  없음).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.config.macro import DerivedCfg, MacroConfig, SeriesCfg, load_macro
from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, Row, day, num, stamp

__all__ = ["MACRO_DIR", "build_macro"]

MACRO_DIR: Final = "macro"
SPAN_DAYS: Final[Mapping[str, int]] = {
    "D": 2 * 366,
    "W": 2 * 366,
    "M": 10 * 366,
    "Q": 10 * 366,
    "A": 10 * 366,
}

Q_META: Final = Query(
    "macro_meta",
    "SELECT series_id, tab, title, unit, freq, source, dataset FROM pub_macro.series_meta "
    "ORDER BY tab, series_id",
)
Q_SERIES: Final = Query(
    "macro_series",
    "SELECT period, value, quality, as_of, released_at FROM pub_macro.series "
    "WHERE series_id = %(sid)s AND period >= %(start)s ORDER BY period",
)
Q_SPREAD: Final = Query(
    "macro_spread",
    "SELECT a.period, a.value - b.value AS value, a.quality AS qa, b.quality AS qb, "
    "GREATEST(a.as_of, b.as_of) AS as_of FROM pub_macro.series a "
    "JOIN pub_macro.series b ON b.series_id = %(b)s AND b.period = a.period "
    "WHERE a.series_id = %(a)s AND a.period >= %(start)s ORDER BY a.period",
)
Q_YOY: Final = Query(
    "macro_yoy",
    "SELECT a.period, CASE WHEN b.value IS NULL OR b.value = 0 OR a.value IS NULL THEN NULL "
    "ELSE (a.value / b.value - 1) * 100 END AS value, a.quality AS qa, b.quality AS qb, "
    "GREATEST(a.as_of, b.as_of) AS as_of FROM pub_macro.series a "
    "JOIN pub_macro.series b ON b.series_id = a.series_id AND b.period = "
    "to_char(to_date(a.period, 'YYYY-MM') - interval '12 months', 'YYYY-MM') "
    "WHERE a.series_id = %(a)s AND a.period >= %(start)s AND length(a.period) = 7 "
    "ORDER BY a.period",
)
Q_CALENDAR: Final = Query(
    "macro_calendar",
    "SELECT event_id, kind, event_date, time_kst, title, country, importance, actual, "
    "actual_series, unit, source, quality, received_at FROM pub_macro.calendar "
    "WHERE event_date BETWEEN %(start)s AND %(end)s ORDER BY event_date, time_kst, event_id",
)
NA_REAL_RATE: Final = "공개판 미제공 — 로그인 화면에서 계산(공개 내보내기는 엔진을 쓰지 않는다)"


def _start(freq: str, today: date) -> str:
    d = today - timedelta(days=SPAN_DAYS[freq])
    if freq in ("D", "W"):
        return d.isoformat()
    if freq == "M":
        return f"{d:%Y-%m}"
    if freq == "Q":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year}"


def _q(v: object) -> Quality:
    q = str(v)
    if q not in ("ok", "stale", "estimated", "invalid"):
        raise ValueError(f"모르는 quality: {q[:12]!r}")
    return q  # pyright: ignore[reportReturnType]


def _latest(
    points: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, float | None, float | None]:
    have = [p for p in points if p["value"] is not None]
    if not have:
        return None, None, None
    last = have[-1]
    if len(have) == 1:
        return dict(last), None, None
    prev = float(have[-2]["value"])
    v = float(last["value"])
    return dict(last), v - prev, (v / prev - 1.0) * 100.0 if prev != 0 else None


def _series_dict(
    *,
    sid: str,
    title: str,
    unit: str,
    freq: str,
    source: str,
    rows: Sequence[Row],
    derived: bool,
    na: str | None,
    two: bool = False,
) -> tuple[dict[str, Any], list[datetime], list[Quality]]:
    pts = [
        {
            "period": r["period"],
            "value": num(r["value"]),
            "released_at": None if two else stamp(r.get("released_at")),
        }
        for r in rows
    ]
    quals: list[Quality] = [
        worst_quality([_q(r["qa"]), _q(r["qb"])]) if two else _q(r["quality"]) for r in rows
    ]
    stamps = [r["as_of"] for r in rows if r.get("as_of") is not None]
    last, change, pct = _latest(pts)
    idx = max((i for i, p in enumerate(pts) if p["value"] is not None), default=None)
    qual: Quality | None = quals[idx] if idx is not None else ("invalid" if rows else None)
    data = {
        "series_id": sid,
        "title": title,
        "unit": unit,
        "freq": freq,
        "tier": "public",
        "source": source,
        "as_of": stamp(max(stamps)) if stamps else None,
        "quality": qual,
        "latest": last,
        "change": change,
        "change_pct": pct,
        "points": pts,
        "derived": derived,
        "na_reason": na if na else (None if rows else "아직 없음 — macro.morning·monthly·evening"),
    }
    return data, stamps, ([qual] if qual else [])


def _src(s: SeriesCfg) -> str:
    return s.dataset if s.source == "ECOS" else s.source


def _info(s: SeriesCfg) -> dict[str, Any]:
    return {
        "series_id": s.id,
        "title": s.title,
        "unit": s.unit,
        "freq": s.freq,
        "tier": "public",
        "source": _src(s),
        "dataset": s.dataset,
        "ready": s.ready,
        "probe": s.probe,
    }


def _derived_info(d: DerivedCfg, by: Mapping[str, SeriesCfg]) -> dict[str, Any]:
    ins = [by[i] for i in d.inputs]
    return {
        "series_id": d.id,
        "title": d.title,
        "unit": d.unit,
        "freq": ins[0].freq if d.kind == "spread" else "M",
        "tier": "public",
        "source": "+".join(dict.fromkeys(_src(s) for s in ins)),
        "dataset": "DERIVED",
        "ready": all(s.ready for s in ins),
        "probe": next((s.probe for s in ins if s.probe), None),
    }


def _derived(
    read: Reader, d: DerivedCfg, by: Mapping[str, SeriesCfg], today: date
) -> tuple[dict[str, Any], list[datetime], list[Quality]]:
    info = _derived_info(d, by)
    common = {
        "sid": d.id,
        "title": d.title,
        "unit": d.unit,
        "freq": info["freq"],
        "source": info["source"],
        "derived": True,
    }
    if d.kind == "spread":
        rows = read(
            Q_SPREAD, {"a": d.inputs[0], "b": d.inputs[1], "start": _start(info["freq"], today)}
        )
        return _series_dict(**common, rows=rows, na=None, two=True)
    if d.kind == "yoy" and by[d.inputs[0]].freq == "M":
        rows = read(Q_YOY, {"a": d.inputs[0], "start": _start("M", today)})
        return _series_dict(**common, rows=rows, na=None, two=True)
    return _series_dict(**common, rows=[], na=NA_REAL_RATE)


def _file(
    name: str,
    data: Mapping[str, Any],
    stamps: Sequence[datetime],
    quals: Sequence[Quality],
    source: str,
    now: datetime,
    notes: Sequence[str] = (),
) -> PublicFile:
    return PublicFile(
        name=f"{MACRO_DIR}/{name}.json",
        source=source or "KBJ",
        as_of=max(stamps) if stamps else now,
        quality=worst_quality(quals) if quals else "invalid",
        data=data,
        notes=tuple(notes),
        compact=True,
    )


def build_macro(read: Reader, *, now: datetime, cfg: MacroConfig | None = None) -> BuildOut:
    """공개 매크로 파일들. 공개 메타가 없으면 파일 0 + 이유."""
    cfg = cfg or load_macro()
    meta = read(Q_META, {})
    if not meta:
        return BuildOut()  # 수집 전(작업 꺼짐 — 실측 #43~#45) — 파일도 이유도 없다(P4 출력 그대로)
    today = now.date()
    by = {s.id: s for s in cfg.series}
    stored = {str(m["series_id"]) for m in meta}
    tabs = [t for t in cfg.tabs if t.public]
    files: list[PublicFile] = []
    all_stamps: list[datetime] = []
    tab_infos: list[dict[str, Any]] = []
    for t in tabs:
        series = [s for s in cfg.public_series() if s.tab == t.id]
        derived = [d for d in cfg.derived if d.tab == t.id and d.tier == "public"]
        out: list[dict[str, Any]] = []
        stamps: list[datetime] = []
        quals: list[Quality] = []
        sources: set[str] = set()
        for s in series:
            rows = (
                read(Q_SERIES, {"sid": s.id, "start": _start(s.freq, today)})
                if s.id in stored
                else []
            )
            na = f"확인 전({s.probe})" if not s.ready and not rows else None
            data, st, q = _series_dict(
                sid=s.id,
                title=s.title,
                unit=s.unit,
                freq=s.freq,
                source=_src(s),
                rows=rows,
                derived=False,
                na=na,
            )
            out.append(data)
            stamps += st
            quals += q
            if rows:
                sources.add(s.source)
        for d in derived:
            data, st, q = _derived(read, d, by, today)
            out.append(data)
            stamps += st
            quals += q
        tab_infos.append(
            {
                "id": t.id,
                "title": t.title,
                "public": True,
                "series": [_info(s) for s in series] + [_derived_info(d, by) for d in derived],
            }
        )
        if not stamps:
            continue  # 이 탭에 값이 하나도 없다 — 파일을 만들지 않는다
        notes = [f"{x['title']}: {x['na_reason']}" for x in out if x["na_reason"]][:12]
        files.append(
            _file(
                t.id,
                {"tab": t.id, "series": out},
                stamps,
                quals,
                "+".join(sorted(sources)) or "KBJ",
                now,
                notes,
            )
        )
        all_stamps += stamps
    skipped: list[str] = []
    if files:
        files.insert(
            0,
            _file(
                "tabs",
                {"tabs": tab_infos},
                all_stamps,
                ["ok"],
                "+".join(sorted({str(m["source"]) for m in meta})),
                now,
            ),
        )
    else:
        skipped.append(f"{MACRO_DIR}/: 공개 시리즈 값이 하나도 없다 — 만들지 않았다")
    cal = _calendar(read, cfg, today, now)
    if cal is None:
        skipped.append(f"{MACRO_DIR}/calendar.json: 공개 캘린더가 비었다 — 만들지 않았다")
    else:
        files.append(cal)
    return BuildOut(files=tuple(files), skipped=tuple(skipped))


def _calendar(read: Reader, cfg: MacroConfig, today: date, now: datetime) -> PublicFile | None:
    w = cfg.calendar.window_days
    a, b = today - timedelta(days=w.past), today + timedelta(days=w.future)
    rows = read(Q_CALENDAR, {"start": a, "end": b})
    if not rows:
        return None
    out = [
        {
            "event_id": r["event_id"],
            "date": day(r["event_date"]),
            "time_kst": None if r["time_kst"] is None else r["time_kst"].strftime("%H:%M"),
            "kind": r["kind"],
            "title": r["title"],
            "country": r["country"],
            "importance": int(r["importance"]),
            "source": r["source"],
            "quality": r["quality"],
            "actual": num(r["actual"]),
            "actual_series": r["actual_series"],
            "unit": r["unit"],
            "forecast": None,
        }
        for r in rows
    ]
    stamps = [r["received_at"] for r in rows if r.get("received_at") is not None]
    return _file(
        "calendar",
        {"start": a.isoformat(), "end": b.isoformat(), "rows": out},
        stamps,
        [_q(r["quality"]) for r in rows],
        "+".join(sorted({str(r["source"]).upper() for r in rows})),
        now,
    )

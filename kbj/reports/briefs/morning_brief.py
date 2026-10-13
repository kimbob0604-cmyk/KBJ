"""아침 브리핑(08:10) 섹션 — ① 간밤 미국 ② 금리·달러·원자재 ③ 한국 매크로(새 공표)
⑤ 일정·결과·실적·금통위.

④ 야간선물·GEX(P7)·⑥ 구루(P8)는 싣지 않는다(config/briefs.yaml `omitted`). ⑦ 품질 줄은 `quality.py`.
재료는 저장소에서만 읽는다(계약 ⑯): US 묶음이 쓴 `prv_board.artifact(US, us_brief)`·미국 일봉
(`prv_market.daily_bar` market='US'), 매크로 `pub_macro`·`prv_macro`(시리즈·캘린더), 실적 일정
`pub_filings.earnings_schedule`, 금통위 `config/calendar_events.yaml`.

① 미국 지수 줄 규칙(SD `scripts/check_futures_us_index.py` 1~3 승격 — `tests/unit/reports/briefs/
test_sd_rules.py`): 줄마다 **미국 거래일**을 붙이고(한국 날짜로 신선도를 재지 않는다),
뉴욕 장중 봉은
'종가'라 적지 않고 '현재', 못 받은 지수는 옛 값을 내지 않고 '미국 지수 수신 실패 — 이름들' 한 줄.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from kbj.core.quality import Quality

from .sections import (
    WEEKDAY_KO,
    BriefCtx,
    Material,
    SectionOut,
    esc,
    fmt_num,
    mmdd,
    worst,
)

__all__ = [
    "US_INDEXES",
    "calendar_section",
    "kr_macro",
    "rates_table",
    "us_index_lines",
    "us_overnight",
]

NY: Final = ZoneInfo("America/New_York")
US_CLOSE: Final = time(16, 0)

# 간밤 미국 지수(야후 심볼 → 이름) — 정본은 config/briefs.yaml us_indexes(WRAP 이 옮겼다). 이 값은
# 설정을 쓰지 않는 시험·호출의 기본값으로만 남긴다(설정과 같다 — 시험)
US_INDEXES: Final[tuple[tuple[str, str], ...]] = (
    ("^GSPC", "S&P 500"),
    ("^IXIC", "NASDAQ"),
    ("^DJI", "다우"),
)

KR_MACRO_SOURCES: Final = ("ECOS", "KOSIS")


# ── ① 간밤 미국 ─────────────────────────────────────────────────────────────────────────


def us_index_lines(
    bars: Mapping[str, Sequence[Any]],
    names: Sequence[tuple[str, str]],
    now: datetime,
) -> list[str]:
    """미국 지수 줄(SD 규칙 1~3). `bars` = {심볼: 날짜 오름차순 일봉(close·date)}.

    - 줄 끝에 **미국 거래일**: 마감 뒤 봉은 '(MM/DD 종가)', 그날 뉴욕 16:00 전 봉은 '(MM/DD 현재)'.
    - 못 받은 지수(종가 있는 봉이 둘 미만 — 등락을 못 잰다)는 옛 값을 내지 않고 이름만 모아
      '미국 지수 수신 실패' 한 줄.
    """
    ny_now = now.astimezone(NY)
    out: list[str] = []
    failed: list[str] = []
    for sym, name in names:
        rows = [b for b in bars.get(sym) or () if getattr(b, "close", None) is not None]
        if len(rows) < 2:
            # 전일 종가가 없으면 등락을 못 잰다 — 반쪽 값을 내지 않고 못 받은 것으로 센다(SD 규칙 3)
            failed.append(name)
            continue
        last, prev = rows[-1], rows[-2]
        live = last.date == ny_now.date() and ny_now.time() < US_CLOSE
        tag = f"{mmdd(last.date)} {'현재' if live else '종가'}"
        chg = f" {(float(last.close) / float(prev.close) - 1) * 100:+.2f}%" if prev.close else ""
        out.append(f"{esc(name)} {float(last.close):,.2f}{chg} <i>({tag})</i>")
    if failed:
        out.append(f"미국 지수 수신 실패 — {esc(', '.join(failed))}")
    return out


def _brief_html(payload: Mapping[str, Any]) -> list[str]:
    """US `us_brief` 산출의 본문. html(이미 막은 텔레그램 HTML)·parts·text·lines 를 받는다."""
    if isinstance(payload.get("html"), str):
        return [payload["html"]]
    parts = payload.get("parts")
    if isinstance(parts, list | tuple):
        return [str(p) for p in parts if p]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(payload.get("text"), str):
        return [esc(payload["text"])]
    lines = payload.get("lines")
    if isinstance(lines, list | tuple):
        return [esc(x) for x in lines]  # pyright: ignore[reportUnknownVariableType]
    return []


def us_overnight(bc: BriefCtx) -> SectionOut:
    """① 간밤 미국 — 지수 줄 + US 묶음의 `us_brief` 산출(그대로 싣는다)."""
    indexes = bc.us_indexes or bc.cfg.us_index_pairs() or US_INDEXES
    expected = bc.prev_us_day()
    repos = bc.repos
    bars = repos.us.daily([s for s, _ in indexes], bc.day, 2)
    lines = us_index_lines(bars, indexes, bc.now)
    got_index = any(bars.get(s) for s, _ in indexes)
    idx_days = [b.date for s, _ in indexes for b in (bars.get(s) or ())[-1:]]
    idx_q = worst([b.quality for s, _ in indexes for b in (bars.get(s) or ())[-1:]])
    mats: list[Material] = []
    if got_index:
        basis = max(idx_days)
        mats.append(Material(
            "미국 지수", "ok" if basis >= expected else "late", idx_q, basis, expected, "us.eod",
            "06:10",
        ))  # fmt: skip
    else:
        mats.append(Material("미국 지수", "not_ready", job="us.eod", due="06:10"))

    day = repos.board.us_last_day(bc.day)
    art = repos.board.us_artifact(day, "us_brief") if day is not None else None
    body = _brief_html(art.payload) if art is not None else []
    if art is not None and body:
        late = art.trade_date < expected
        mats.append(Material(
            "간밤 미국 브리프", "late" if late else "ok", art.quality, art.trade_date, expected,
            "us.eod", "06:10",
        ))  # fmt: skip
        if late:
            lines.append(f"<i>{esc(bc.cfg.texts.stale_basis.format(as_of=art.trade_date))}</i>")
        lines += body
    else:
        mats.append(Material("간밤 미국 브리프", "not_ready", job="us.eod", due="06:10"))
    if not got_index and art is None:
        return SectionOut(materials=tuple(mats), not_ready=True)
    as_of = art.as_of if art is not None else bc.now
    src = "+".join(sorted({*(["yahoo"] if got_index else []), *([art.source] if art else [])}))
    return SectionOut(
        tuple(lines), src or "us.eod", as_of, worst([m.quality for m in mats]), tuple(mats)
    )


# ── ② 금리·달러·원자재 한 표 ─────────────────────────────────────────────────────────────


def _latest(bc: BriefCtx, ids: Sequence[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(최신 행, 메타) — 공개·로그인 저장소를 같이 본다(시리즈 id 는 겹치지 않는다)."""
    rows: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    for repo in (bc.repos.macro, bc.repos.macro_prv):
        rows.update(repo.latest(ids))
        meta.update({m.series_id: m for m in repo.meta()})
    return rows, meta


def _w(s: str) -> int:
    """고정폭 글꼴의 표시 폭(한글 2칸) — <pre> 표 줄맞춤(ET board/us brief `_w`)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def _previous(bc: BriefCtx, sid: str, period: str) -> Any:
    """같은 시리즈의 바로 앞 기간 값(값 있는 것). 없으면 None.

    개정 전 값(`prev_value`)과 다르다 — 그것은 같은 기간의 고치기 전 값이다."""
    for repo in (bc.repos.macro, bc.repos.macro_prv):
        rows = repo.series([sid], None, period).get(sid) or []
        vals = [r for r in rows if r.period < period and r.value is not None]
        if vals:
            return vals[-1].value
    return None


def _basis(period: str) -> date | None:
    try:
        return date.fromisoformat(period[:10])
    except ValueError:
        return None


def rates_table(bc: BriefCtx) -> SectionOut:
    """② `config/briefs.yaml table_series` 순서의 한 표(美10년은 DGS10 하나). 없는 칸은 '—'."""
    ids = list(bc.cfg.table_series)
    rows, meta = _latest(bc, ids)
    prev_kr, prev_us = bc.prev_kr_day(), bc.prev_us_day()
    body: list[str] = []
    mats: list[Material] = []
    width = max(_w(getattr(meta.get(s), "title", s)) for s in ids)
    for sid in ids:
        m = meta.get(sid)
        title = getattr(m, "title", sid)
        r = rows.get(sid)
        if r is None or r.value is None or r.quality is Quality.INVALID:
            body.append(f"{_pad(title, width)}  —")
            mats.append(Material(title, "not_ready", r.quality if r else None, note="값 없음"))
            continue
        unit = r.unit if r.unit in ("%", "%p") else ""
        chg = ""
        before = _previous(bc, sid, r.period)
        if before is not None:
            chg = f"  {float(r.value - before):+,.2f}"
        basis = _basis(r.period)
        expected = (
            prev_us if sid.startswith(("FRED:", "YAHOO:", "TREASURY:", "NYFED:")) else prev_kr
        )
        late = basis is not None and getattr(m, "freq", "D") == "D" and basis < expected
        tail = f"  ({basis.strftime('%m/%d')})" if basis else f"  ({r.period})"
        body.append(f"{_pad(title, width)}  {fmt_num(r.value)}{unit}{chg}{tail}")
        mats.append(Material(
            title, "late" if late else "ok", r.quality, basis, expected if late else None,
            "macro.morning", "06:20",
        ))  # fmt: skip
    if all(m.status == "not_ready" for m in mats):
        # 표 전체가 비었다 — 칸마다가 아니라 재료 하나로 적는다
        return SectionOut(
            materials=(
                Material("금리·달러·원자재", "not_ready", job="macro.morning", due="06:20"),
            ),
            not_ready=True,
        )
    as_of = max(r.as_of for r in rows.values())
    src = "+".join(sorted({r.source for r in rows.values()}))
    return SectionOut(
        ("<pre>" + esc("\n".join(body)) + "</pre>",),
        src,
        as_of,
        worst([m.quality for m in mats]),
        tuple(mats),
    )


# ── ③ 한국 매크로(새 공표) ───────────────────────────────────────────────────────────────


def kr_macro(bc: BriefCtx) -> SectionOut:
    """③ 전일 08:10 이후 새로 공표된 한국 월·분기 시리즈만. 없으면 빈 섹션(config: 생략)."""
    hh, mm = (int(x) for x in bc.cfg.morning.send_at.split(":"))
    start = datetime.combine(bc.day - timedelta(days=1), time(hh, mm), tzinfo=bc.now_kst.tzinfo)
    meta = {m.series_id: m for m in bc.repos.macro.meta()}
    rows = [
        r
        for r in bc.repos.macro.released_between(start, bc.now)
        if r.source in KR_MACRO_SOURCES
        and getattr(meta.get(r.series_id), "freq", "D") != "D"
        and r.value is not None
    ]
    if not rows:
        return SectionOut(source="pub_macro", as_of=bc.now, quality=Quality.OK)
    lines: list[str] = []
    for r in sorted(rows, key=lambda x: (x.released_at or x.as_of, x.series_id)):
        title = getattr(meta.get(r.series_id), "title", r.series_id)
        before = _previous(bc, r.series_id, r.period)
        prev = f" (전기 {fmt_num(before)})" if before is not None else ""
        unit = r.unit if r.unit in ("%", "%p") else f" {r.unit}"
        lines.append(esc(f"{title} {r.period}: {fmt_num(r.value)}{unit}{prev}"))
    return SectionOut(
        tuple(lines),
        "+".join(sorted({r.source for r in rows})),
        max(r.as_of for r in rows),
        worst([r.quality for r in rows]),
    )


# ── ⑤ 일정·발표 결과·실적·금통위 ─────────────────────────────────────────────────────────


def _flag(country: str) -> str:
    return {"KR": "🇰🇷", "US": "🇺🇸"}.get(country, country)


def _ev_line(e: Any, *, with_day: bool) -> str:
    when = e.time_kst.strftime("%H:%M") if e.time_kst else "시각 미정"
    if with_day:
        when = f"{mmdd(e.date)}({WEEKDAY_KO[e.date.weekday()]}) {when}"
    bits = [f"{when} {_flag(e.country)} {e.title}"]
    if e.actual is not None:
        bits.append(f"결과 {fmt_num(e.actual)}{e.unit or ''}")
    if e.forecast is not None:
        bits.append(f"예상 {fmt_num(e.forecast)}{e.unit or ''}")
    return esc(" · ".join(bits))


def calendar_section(bc: BriefCtx) -> SectionOut:
    """⑤ 오늘·이번 주 일정(+ 발표 결과·예상치), 어제 발표 결과, 오늘 실적 일정, 금통위 D-n."""
    start, end = bc.day - timedelta(days=1), bc.day + timedelta(days=6)
    seen: dict[str, Any] = {}
    for repo in (bc.repos.macro, bc.repos.macro_prv):
        for e in repo.calendar(start, end):
            seen.setdefault(e.event_id, e)
    events = sorted(seen.values(), key=lambda e: (e.date, e.time_kst or time.max, e.event_id))
    lines: list[str] = []
    today = [e for e in events if e.date == bc.day]
    week = [e for e in events if e.date > bc.day]
    done = [e for e in events if e.date < bc.day and e.actual is not None]
    if today:
        lines.append("<b>오늘</b>")
        lines += [_ev_line(e, with_day=False) for e in today]
    if done:
        lines.append("<b>어제 발표 결과</b>")
        lines += [_ev_line(e, with_day=False) for e in done]
    if week:
        lines.append("<b>이번 주</b>")
        lines += [_ev_line(e, with_day=True) for e in week]

    sched = bc.repos.filings.schedule(start=bc.day, end=bc.day)
    if sched:
        names = {cc: nm for _, (cc, nm) in bc.repos.filings.corp_index().items()}
        corps = sorted({names.get(s.corp_code, s.corp_code) for s in sched})
        lines.append(esc(f"오늘 실적 일정 {len(corps)}곳: " + ", ".join(corps[:10])
                         + (f" 외 {len(corps) - 10}" if len(corps) > 10 else "")))  # fmt: skip
    nxt = [(d, t) for d, t in bc.bok_meetings if d >= bc.day]
    if nxt:
        d, title = nxt[0]
        dn = (d - bc.day).days
        lines.append(esc(f"{title} {'D-day' if dn == 0 else f'D-{dn}'} ({mmdd(d)})"))
    qs = [e.quality for e in events] + [s.quality for s in sched]
    src = "+".join(sorted({*(e.source for e in events), *(["dart"] if sched else [])})) or "config"
    return SectionOut(tuple(lines), src, bc.now, worst(qs) or Quality.OK)

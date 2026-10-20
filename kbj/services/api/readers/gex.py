"""GEX 리더 — `/api/gex/*`(docs/p7_design.md §5, metrics §G0~§G8 — 로그인). P7 묶음 ENG 본문.

`ctx.repos.gex`(DB 만 — 계약 ⑩)를 읽어 `models.gex` 로. 계산은 하지 않는다 — 엔진(`gex.engine`)이
저장한 산출을 싣기만 한다(단위 GX 그대로: pt·원/1%·연율 소수, 투자자별 대금만 백만원 → 원).

규칙
- **기능 플래그(D-P7-11·§10 Q3)**: 행의 `flag`(계산 당시 플래그) 그대로 — `visible` 만 값을 싣고,
  `shadow` 는 값·detail 을 비우고 자리만(화면 '섀도 검증 중'), `off` 는 싣지 않는다
- **품질**: `invalid` 행은 빼고 봉투 notes 에 수를 적는다. 봉투 품질은 실은 행 중 가장 나쁜 것
- **세션·지연(§6.2)**: 지금이 장(DAY·NIGHT)이 아니면 `session_open = false`(화면 '마지막 세션 —
  <시각>'). 장 중인데 마지막 사이클이 `STALE_AFTER` 보다 오래되면 `stale` + 사유 [확인 필요 — GX
  엔진 사이클은 chain.ready 마다(≈30초), 따라잡기 10초]
- **현재가**: Flip 레벨 detail 의 `f_ref`(엔진이 쓴 근월물 기준 F — GX §3.4·§3.5). 없으면 그 사이클
  선물 전광판 근월물(F·board, 잔존일 가장 짧은 값 있는 행)을 `estimated` 로 [확인 필요]
- **범위(scope)별 행사가 막대**: 그 범위 순GEX(`net_gex`) 행 payload 의 `expiries`(시리즈 라벨)에 든
  시리즈만. 라벨이 없으면 막대를 다 싣고 notes 에 적는다
- 투자자별(§6.2)은 `prv_gex.investor_flow`(쓰기 주인 `flows.market_minute` — ADR 0019) 그 세션 최신
  행. 야간은 수집하지 않는다(D-P7-6 — `night_investor_collected = false`)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any, Final, cast

from kbj.core.calendar import State, state_at
from kbj.core.gex.records import FuturesRecord, LevelRecord, MetricRecord
from kbj.core.quality import Quality
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex import (
    GexFlagT,
    GexFlow,
    GexFlowPoint,
    GexInvestorRow,
    GexLevel,
    GexLevels,
    GexMetricPoint,
    GexMetricSeries,
    GexProfile,
    GexScopeT,
    GexStrikeBar,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = [
    "METRICS_WINDOW",
    "NO_GEX",
    "NO_GEX_FLOW",
    "SOURCE",
    "STALE_AFTER",
    "gex_flow",
    "gex_levels",
    "gex_metrics",
    "gex_profile",
]

NO_GEX: Final = "아직 없음 — gex.engine(장중 사이클 — 켜기 전 [WRAP])"
NO_GEX_FLOW: Final = "아직 없음 — gex.engine·flows.market_minute(정규장 매분)"
SOURCE: Final = "KBJ:gex.engine(KIS 옵션 체인·웹소켓)"
FLOW_SOURCE: Final = "KBJ:gex.engine(웹소켓 체결)+KIS(투자자별 — flows.market_minute)"
STALE_AFTER: Final = timedelta(minutes=2)  # 장 중 마지막 사이클이 이보다 오래면 '지연' [확인 필요]
METRICS_WINDOW: Final = timedelta(days=7)  # from·to 를 안 주면 끝(지금)에서 이만큼 앞 [확인 필요]
FLOW_WINDOW: Final = timedelta(hours=16)  # HIRO 를 찾는 창 — 세션 하나(야간 12시간)를 덮는다
MILLION: Final = 1_000_000  # investor_flow 대금 백만원 → 원
SHADOW_NOTE: Final = "섀도 검증 중 — 값은 저장만(1주 섀도 운영 뒤 visible, §10 Q3)"
NIGHT_INVESTOR_NOTE: Final = "투자자별 — 야간은 수집 안 함(D-P7-6)"
_ORDER: Final = (Quality.OK, Quality.STALE, Quality.ESTIMATED, Quality.INVALID)


def _q(v: str) -> Quality:
    return Quality(v)


def _worst(qs: Iterable[Quality]) -> Quality | None:
    got = list(qs)
    return max(got, key=_ORDER.index) if got else None


def _flag(v: str) -> GexFlagT:
    return cast(GexFlagT, v)


def _invalid_note(n: int, what: str) -> str:
    return f"품질 invalid {n}건 뺌({what})" if n else ""


def _session_open(ctx: ReadContext) -> bool:
    return state_at(ctx.now_kst(), ctx.cal).state in (State.DAY, State.NIGHT)


def _latest_levels(ctx: ReadContext, scope: GexScopeT) -> list[LevelRecord]:
    rows = ctx.repos.gex.levels_latest(scope, upto=ctx.now())
    if not rows:
        raise NoData(NO_GEX)
    return rows


def _visible_value(r: LevelRecord | MetricRecord) -> float | None:
    return r.value if r.flag == "visible" else None


def _level_of(rows: Sequence[LevelRecord], name: str) -> LevelRecord | None:
    return next((r for r in rows if r.name == name and r.quality != "invalid"), None)


def _near_futures(rows: Sequence[FuturesRecord]) -> FuturesRecord | None:
    # 주간 = 전광판(market F·source board), 야간 = 단건 시세(market CM·source single) — 야간은
    # F·board 가 없어 '현재가 없음'이었다(2026-10-20)
    priced = [
        r
        for r in rows
        if r.price is not None and r.market in ("F", "CM") and r.source in ("board", "single")
    ]
    priced.sort(key=lambda r: (r.remaining_days if r.remaining_days is not None else 10**6, r.code))
    return priced[0] if priced else None


def _price(
    ctx: ReadContext, rows: Sequence[LevelRecord]
) -> tuple[float | None, Quality | None, list[str]]:
    """현재가(pt) — Flip detail 의 f_ref, 없으면 그 사이클 선물 전광판 근월물(estimated)."""
    for name in ("flip", "flip_distance"):
        lv = _level_of(rows, name)
        if lv is not None and lv.flag == "visible":
            f_ref = lv.detail.get("f_ref")
            if isinstance(f_ref, int | float) and not isinstance(f_ref, bool):
                return float(f_ref), _q(lv.quality), []
    head = rows[0]
    fut = _near_futures(ctx.repos.gex.futures_latest(head.trade_date, head.session, head.ts))
    if fut is None or fut.price is None:
        return None, None, ["현재가 없음 — 엔진 F_ref·선물 전광판 행이 없다"]
    note = f"현재가: 선물 전광판 근월물 {fut.code}(엔진 F_ref 없음 — estimated)"
    return float(fut.price), Quality.ESTIMATED, [note]


def _stale(ctx: ReadContext, cycle_at: datetime, session_open: bool) -> tuple[bool, str | None]:
    if not session_open:
        return False, None
    now = ctx.now_kst()
    age = now - cycle_at
    if age <= STALE_AFTER:
        return False, None
    at = cycle_at.astimezone(now.tzinfo).strftime("%H:%M")
    return True, f"엔진 지연 — 마지막 사이클 {at} KST({int(age.total_seconds() // 60)}분 전)"


CORE_LEVELS: Final = ("call_wall", "put_wall", "flip")
NIGHT_FALLBACK_NOTE: Final = (
    "야간: 옵션 호가가 얇아 야간 레벨을 낼 수 없다 — 주간 마지막 사이클({at}) 레벨(벽·Flip)을 "
    "보인다(stale). 현재가·기대변동폭은 야간 값"
)


def gex_levels(ctx: ReadContext, *, scope: GexScopeT) -> Envelope[GexLevels]:
    rows = _latest_levels(ctx, scope)
    head = rows[0]
    kept = [r for r in rows if r.flag != "off"]
    invalid = sum(1 for r in kept if r.quality == "invalid")
    shown = [r for r in kept if r.quality != "invalid"]
    fallback_note: str | None = None
    price_rows: Sequence[LevelRecord] = shown or rows
    if head.session == "night" and not any(r.name in CORE_LEVELS for r in shown):
        # 야간 사이클은 벽·Flip 이 모두 invalid(옵션 전부 제외) — 주간 마지막 사이클 값을 stale 로
        day = [
            r
            for r in ctx.repos.gex.levels_latest(scope, upto=ctx.now(), session="day")
            if r.flag != "off" and r.quality != "invalid" and r.name not in {x.name for x in shown}
        ]  # fmt: skip
        if day:
            shown = [*shown, *(r.model_copy(update={"quality": "stale"}) for r in day)]
            at = day[0].ts.astimezone(ctx.now_kst().tzinfo).strftime("%m/%d %H:%M")
            fallback_note = NIGHT_FALLBACK_NOTE.format(at=at)
            invalid = 0
    levels = [
        GexLevel(
            name=r.name,
            value=_visible_value(r),
            flag=_flag(r.flag),
            quality=_q(r.quality),
            reasons=list(r.reasons),
            detail=dict(r.detail) if r.flag == "visible" else {},
        )
        for r in sorted(shown, key=lambda r: r.name)
    ]
    price, price_q, price_notes = _price(ctx, price_rows)
    open_ = _session_open(ctx)
    stale, reason = _stale(ctx, head.ts, open_)
    notes = [_invalid_note(invalid, "레벨"), *price_notes]
    if fallback_note:
        notes.append(fallback_note)
    if any(r.flag == "shadow" for r in shown):
        notes.append(SHADOW_NOTE)
    if stale and reason:
        notes.append(reason)
    data = GexLevels(
        scope=scope,
        trade_date=head.trade_date,
        session=head.session,
        cycle_at=head.ts,
        session_open=open_,
        price=price,
        price_quality=price_q,
        stale=stale,
        stale_reason=reason,
        levels=levels,
    )
    quality = _worst(_q(r.quality) for r in shown)
    if stale and quality is not None:
        quality = _worst((quality, Quality.STALE))
    return envelope(
        data, source=SOURCE, as_of=head.ts, quality=quality, notes=notes,
        generated_at=ctx.now(),
    )  # fmt: skip


def _scope_labels(ctx: ReadContext, scope: GexScopeT, ts: datetime) -> set[str] | None:
    """그 범위 순GEX 행 payload 의 시리즈 라벨(`M:202612`·`WKM:261202`). 없으면 None."""
    for m in ctx.repos.gex.metrics_series("net_gex", ts, ts, scope=scope):
        labels = m.payload.get("expiries")
        if isinstance(labels, list) and labels:
            return {str(x) for x in cast(list[Any], labels)}
    return None


def gex_profile(ctx: ReadContext, *, scope: GexScopeT) -> Envelope[GexProfile]:
    rows = _latest_levels(ctx, scope)
    head = rows[0]
    bars_raw = ctx.repos.gex.strike_profile(head.ts)
    labels = _scope_labels(ctx, scope, head.ts)
    notes: list[str] = []
    if labels is None:
        notes.append("범위별 시리즈 표시 없음 — 그 사이클 행사가 막대를 모두 실었다")
        chosen = bars_raw
    else:
        chosen = [b for b in bars_raw if f"{b.mrkt_cls or 'M'}:{b.expiry}" in labels]
    invalid = sum(1 for b in chosen if b.quality == "invalid")
    bars = [
        GexStrikeBar(
            mrkt_cls=b.mrkt_cls, expiry=b.expiry, strike=float(b.strike), gex_call=b.gex_call,
            gex_put=b.gex_put, gex=b.gex, quality=_q(b.quality),
        )
        for b in chosen
        if b.quality != "invalid"
    ]  # fmt: skip
    notes.append(_invalid_note(invalid, "행사가 막대"))

    def lv(name: str) -> float | None:
        r = _level_of(rows, name)
        return None if r is None else _visible_value(r)

    price, price_q, price_notes = _price(ctx, rows)
    notes.extend(price_notes)
    data = GexProfile(
        scope=scope,
        cycle_at=head.ts,
        price=price,
        flip=lv("flip"),
        call_wall=lv("call_wall"),
        put_wall=lv("put_wall"),
        expected_move=lv("expected_move_calendar"),
        bars=bars,
    )
    used = [_q(r.quality) for r in rows if r.quality != "invalid" and r.flag == "visible"]
    quality = _worst([*used, *(b.quality for b in bars), *([price_q] if price_q else [])])
    return envelope(
        data, source=SOURCE, as_of=head.ts, quality=quality, notes=notes,
        generated_at=ctx.now(),
    )  # fmt: skip


def _pick_series(rows: Sequence[MetricRecord]) -> list[MetricRecord]:
    """한 지표의 (범위, 키) 하나 — 범위 all·빈 키가 있으면 그것, 없으면 정렬 첫 묶음."""
    groups: dict[tuple[str, str], list[MetricRecord]] = {}
    for r in rows:
        groups.setdefault((r.scope, r.key), []).append(r)
    key = ("all", "") if ("all", "") in groups else min(groups)
    return sorted(groups[key], key=lambda r: r.ts)


def gex_metrics(
    ctx: ReadContext, *, name: str, start: datetime | None, end: datetime | None
) -> Envelope[GexMetricSeries]:
    to = end if end is not None else ctx.now()
    frm = start if start is not None else to - METRICS_WINDOW
    if frm > to:
        raise NoData(f"아직 없음 — from 이 to 보다 늦다(gex.engine — {name})")
    rows = [r for r in ctx.repos.gex.metrics_series(name, frm, to) if r.flag != "off"]
    if not rows:
        raise NoData(f"{NO_GEX} — 지표 {name}")
    picked = _pick_series(rows)
    invalid = sum(1 for r in picked if r.quality == "invalid")
    kept = [r for r in picked if r.quality != "invalid"]
    if not kept:
        raise NoData(f"{NO_GEX} — 지표 {name}(값 있는 행 없음)")
    last = kept[-1]
    flag = _flag(last.flag)
    data = GexMetricSeries(
        name=name,
        scope=last.scope,
        key=last.key,
        flag=flag,
        points=[
            GexMetricPoint(ts=r.ts, value=_visible_value(r), quality=_q(r.quality)) for r in kept
        ],
    )
    notes = [_invalid_note(invalid, name)]
    if flag == "shadow":
        notes.append(SHADOW_NOTE)
    return envelope(
        data, source=SOURCE, as_of=last.ts, quality=_worst(_q(r.quality) for r in kept),
        notes=notes, generated_at=ctx.now(),
    )  # fmt: skip


def gex_flow(ctx: ReadContext) -> Envelope[GexFlow]:
    now = ctx.now()
    hiro_rows = [
        r for r in ctx.repos.gex.metrics_series("hiro", now - FLOW_WINDOW, now) if r.flag != "off"
    ]
    levels = ctx.repos.gex.levels_latest("all", upto=now)
    if hiro_rows:
        tag = (hiro_rows[-1].trade_date, hiro_rows[-1].session)
    elif levels:
        tag = (levels[0].trade_date, levels[0].session)
    else:
        raise NoData(NO_GEX_FLOW)
    trade_date, session = tag
    same = [r for r in hiro_rows if (r.trade_date, r.session) == tag]
    hiro = [r for r in same if r.quality != "invalid"]
    hiro_flag: GexFlagT = _flag(hiro[-1].flag) if hiro else "shadow"
    inv_rows = ctx.repos.gex.investor_latest(trade_date, session, now)
    investors = [
        GexInvestorRow(
            market_code=r.market_code, sector_code=r.sector_code, investor=r.investor,
            net_qty=r.net_qty,
            net_value_won=None if r.net_value is None else r.net_value * MILLION,
            ts=r.ts, quality=_q(r.quality),
        )
        for r in sorted(inv_rows, key=lambda r: (r.market_code, r.sector_code, r.investor))
        if r.quality != "invalid"
    ]  # fmt: skip
    if not hiro and not investors:
        raise NoData(NO_GEX_FLOW)
    notes = [
        _invalid_note(len(same) - len(hiro), "HIRO"),
        "투자자별 대금은 원(investor_flow 백만원 × 10⁶)",
        NIGHT_INVESTOR_NOTE if session == "night" else "",
    ]
    if hiro_flag == "shadow":
        notes.append(SHADOW_NOTE)
    data = GexFlow(
        hiro_flag=hiro_flag,
        hiro=[GexFlowPoint(ts=r.ts, value=_visible_value(r), quality=_q(r.quality)) for r in hiro],
        investors=investors,
        night_investor_collected=False,
    )
    stamps = [r.ts for r in hiro] + [r.ts for r in investors]
    quality = _worst([*(_q(r.quality) for r in hiro), *(r.quality for r in investors)])
    return envelope(
        data, source=FLOW_SOURCE, as_of=max(stamps), quality=quality, notes=notes,
        generated_at=now,
    )  # fmt: skip

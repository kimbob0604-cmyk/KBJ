"""마감 요약(16:00 — ADR 0018) 섹션 — 지수·환율·시장 수급 / 업종·특징주 / 신고가 3축 / 수급 동향 /
주요 공시.

프로그램매매 줄(F2)·시그널·초안·추천(P8)은 싣지 않는다. ⑥ GEX·진단(P7 OUT — D-P7-16)은 주간 마지막
엔진 산출(`prv_gex.levels` 범위 all, visible 만)과 `diag.daily`(16:00 — 무른 의존 wait_min 10)가 쓴
종합 판정·공포탐욕(`prv_diag`)을 싣는다(문장 `kbj.reports.gex.lines`).
재료는 저장소에서만 읽는다(계약 ⑯):
장중 지수 `prv_market.index_quote`(KIS — 마지막 값), 환율 `prv_macro`/`pub_macro` 최신, 시장 투자자
`prv_flows.market_investor_daily`, 보드 산출 `prv_board.artifact`(KR — sectors·events·newhigh)·
`stock_day`(이름), 종목 투자자 `prv_flows.stock_investor_daily`, 공시 `pub_filings.disclosure`
(키워드 점수), 수출 속보 `pub_trade.ten_day`(관세청 10일 잠정치 — ① 섹션 끝 한 줄, 직전
마감 요약 이후
처음 본 구간만 — P6 D-P6-8).

16:00 의 수급은 KIS 마감 수집 값이라 잠정일 수 있다 — 줄마다 **기준일·원천·quality** 를 적고, 기대
기준일(실행일)과 다르면 실제 기준일을 적는다(ADR 0018 §2-5, metrics §20 ②).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import Investor
from kbj.core.trade_rows import prev_ym
from kbj.engines.trade.flash import FlashChip, new_segments
from kbj.reports.gex.lines import cycle_label, diag_lines, gex_lines, snapshot

from .sections import BriefCtx, Material, SectionOut, eok, esc, fmt_num, mmdd, worst

__all__ = [
    "FX_SERIES",
    "export_flash_line",
    "filings_section",
    "flows_trend",
    "gex_diag_section",
    "market_section",
    "newhigh_section",
    "sectors_section",
]

FX_SERIES: Final[tuple[str, ...]] = ("YAHOO:KRW=X", "ECOS:731Y003:usdkrw")  # 앞의 것부터
QUALITY_KO: Final = {"ok": "확정", "estimated": "잠정", "stale": "지연", "invalid": "무효"}
MAIN_INVESTORS: Final = ((Investor.INDIVIDUAL, "개인"), (Investor.FOREIGN, "외국인"),
                         (Investor.INSTITUTION, "기관"))  # fmt: skip
NEWHIGH_AXES: Final = (("hist", "🏔", "역사적"), ("w52", "📈", "52주"), ("d120", "📊", "120일"))
NO_FILINGS: Final = "오늘 중요 공시 없음"
FLASH_LOOKBACK_MONTHS: Final = 14  # 최신 구간 + 전년 같은 구간 + 공표 지연
TOP_SECTORS, BOTTOM_SECTORS, TOP_MOVERS, TOP_FLOWS, NAMES_PER_AXIS = 5, 3, 5, 5, 5


def _qko(q: Any) -> str:
    v = getattr(q, "value", q)
    return QUALITY_KO.get(str(v), str(v))


def _open(bc: BriefCtx) -> datetime:
    return datetime.combine(bc.day, time(8, 0), tzinfo=bc.now_kst.tzinfo)


# ── ① 지수·환율·시장 수급 ─────────────────────────────────────────────────────────────


def market_section(bc: BriefCtx) -> SectionOut:
    repos = bc.repos
    lines: list[str] = []
    mats: list[Material] = []
    qs: list[Quality | None] = []
    srcs: set[str] = set()
    last: dict[str, Any] = {}
    for q in repos.market.index_quotes(_open(bc), bc.now):
        if q.code in bc.indices and q.value is not None:
            last[q.code] = q  # ts 순 — 마지막이 남는다
    for code, name in bc.indices.items():
        q = last.get(code)
        if q is None:
            continue
        hhmm = q.ts.astimezone(bc.now_kst.tzinfo).strftime("%H:%M")
        pct = f" {q.chg_pct:+.2f}%" if q.chg_pct is not None else ""
        lines.append(esc(f"{name} {fmt_num(q.value)}{pct} ({hhmm} 기준 · {q.source})"))
        qs.append(q.quality)
        srcs.add(q.source)
    if last:
        mats.append(Material("장중 지수", "ok", worst([q.quality for q in last.values()]),
                             bc.day, bc.day, "market.intraday", "15:30"))  # fmt: skip
    else:
        mats.append(Material("장중 지수", "not_ready", job="market.intraday", due="15:30"))

    fx = None
    for repo in (repos.macro_prv, repos.macro):
        got = repo.latest(list(FX_SERIES))
        for sid in FX_SERIES:
            if fx is None and got.get(sid) is not None and got[sid].value is not None:
                fx = got[sid]
    if fx is not None:
        lines.append(esc(f"원/달러 {fmt_num(fx.value)}원 (기준 {fx.period} · {fx.source})"))
        qs.append(fx.quality)
        srcs.add(fx.source)

    mk = repos.flows.market_days(bc.day, 1)
    for code, name in bc.indices.items():
        rows = mk.get(code) or []
        if not rows:
            continue
        d = max(r.date for r in rows)
        by = {Investor(r.investor): r for r in rows if r.date == d}
        bits = [f"{ko} {eok(by[i].net_value)}" for i, ko in MAIN_INVESTORS if i in by]
        q = worst([r.quality for r in by.values()])
        src = "+".join(sorted({r.source for r in by.values()}))
        lines.append(esc(f"{name} 수급 {' · '.join(bits)} (기준일 {mmdd(d)} · {src} · {_qko(q)})"))
        qs.append(q)
        srcs.add(src)
        mats.append(Material(f"{name} 시장 수급", "ok" if d >= bc.day else "late", q, d, bc.day,
                             "market.close_collect", "15:35"))  # fmt: skip
    if not mk:
        mats.append(Material("시장 수급", "not_ready", job="market.close_collect", due="15:35"))
    fl = export_flash_line(bc)
    if fl is not None:
        line, fc = fl
        lines.append(line)
        qs.append(fc.quality)
        srcs.add(fc.source)
    if not lines:
        return SectionOut(materials=tuple(mats), not_ready=True)
    return SectionOut(tuple(lines), "+".join(sorted(srcs)), bc.now, worst(qs), tuple(mats))


# ── 수출 속보 한 줄(P6 FLASH — D-P6-8, metrics §25) ──────────────────────────────────


def _usd_eok(usd: int) -> str:
    return f"{usd / 100_000_000:,.1f}억 달러"


def export_flash_line(bc: BriefCtx) -> tuple[str, FlashChip] | None:
    """직전 마감 요약(전 거래일 `closing.send_at`) 이후 **처음 본** 관세청 10일 잠정치
    수출 구간 한 줄
    (가장 최근 구간 — 발표 당시 값 기준 YoY). 없으면 None(줄 없음 — 재료가 아니라 덧붙임이라
    not_ready 가 아니다). 공표일이 휴장이면 다음 거래일 마감 요약에 실린다(창이 전 거래일부터)."""
    hh, mm = (int(x) for x in bc.cfg.closing.send_at.split(":", 1))
    tz = bc.now_kst.tzinfo
    since = datetime.combine(bc.prev_kr_day(), time(hh, mm), tzinfo=tz)
    ym = f"{bc.day.year:04d}-{bc.day.month:02d}"
    rows = bc.repos.trade.ten_day(kind="exp_item", ym_from=prev_ym(ym, FLASH_LOOKBACK_MONTHS))
    hits = new_segments(rows, since=since, until=bc.now)
    if not hits:
        return None
    fc = hits[-1]
    yoy = f"전년 대비 {fc.yoy * 100:+.1f}%" if fc.yoy is not None else (fc.yoy_reason or "YoY 없음")
    m = int(fc.ym[5:])
    text = f"수출 속보 {m}월 {fc.label} {_usd_eok(fc.usd)} · {yoy} (관세청 잠정 · 발표 당시)"
    return esc(text), fc


# ── 보드 산출 ────────────────────────────────────────────────────────────────────────


def _board(bc: BriefCtx, name: str) -> tuple[Any, Material]:
    day = bc.repos.board.last_day(bc.day)
    art = bc.repos.board.artifact(day, name) if day is not None else None
    if art is None:
        return None, Material(f"보드 {name}", "not_ready", job="board.daily", due="16:00")
    late = art.trade_date < bc.day
    return art, Material(f"보드 {name}", "late" if late else "ok", art.quality, art.trade_date,
                         bc.day, "board.daily", "16:00")  # fmt: skip


def _names(bc: BriefCtx, day: Any) -> dict[str, str]:
    """종목 이름 — 보드 종목 하루(stock_day), 없으면 보드 신고가 목록의 이름."""
    out: dict[str, str] = {}
    art, _ = _board(bc, "newhigh")
    for r in (art.payload.get("achieved") if art else None) or []:
        if r.get("code") and r.get("name"):
            out[r["code"]] = r["name"]
    out.update({c: sd.name for c, sd in bc.repos.board.stock_days(day).items() if sd.name})
    return out


def _stale_line(bc: BriefCtx, mat: Material) -> list[str]:
    if mat.status == "late" and mat.basis is not None:
        return [f"<i>{esc(bc.cfg.texts.stale_basis.format(as_of=mat.basis))}</i>"]
    return []


def sectors_section(bc: BriefCtx) -> SectionOut:
    """② 업종 상위 5·하위 3(보드 sectors — 등락률 가중) + 특징주(보드 events 거래량 이상 상위)."""
    art, mat = _board(bc, "sectors")
    ev_art, ev_mat = _board(bc, "events")
    if art is None and ev_art is None:
        return SectionOut(materials=(mat, ev_mat), not_ready=True)
    lines = _stale_line(bc, mat)
    secs = [s for s in (art.payload.get("sectors") if art else None) or []
            if s.get("chg_pct") is not None]  # fmt: skip
    if secs:
        top = secs[:TOP_SECTORS]
        bottom = [s for s in secs[-BOTTOM_SECTORS:] if s not in top]
        lines.append(
            "<b>상위</b> " + esc(" · ".join(f"{s['name']} {s['chg_pct']:+.2f}%" for s in top))
        )
        if bottom:
            lines.append("<b>하위</b> " + esc(" · ".join(
                f"{s['name']} {s['chg_pct']:+.2f}%" for s in reversed(bottom))))  # fmt: skip
    names = _names(bc, bc.day)
    evs = [e for e in (ev_art.payload.get("events") if ev_art else None) or []
           if e.get("type") == "volume_anomaly"]  # fmt: skip
    evs.sort(key=lambda e: -(e.get("severity") or 0))
    if evs:
        bits = [f"{names.get(e['tickers'][0], e['tickers'][0])} 거래량 {e['severity']:.1f}배"
                for e in evs[:TOP_MOVERS]]  # fmt: skip
        lines.append("<b>특징주</b> " + esc(" · ".join(bits)))
    arts = [a for a in (art, ev_art) if a is not None]
    return SectionOut(tuple(lines), "+".join(sorted({a.source for a in arts})),
                      max(a.as_of for a in arts), worst([a.quality for a in arts]),
                      (mat, ev_mat))  # fmt: skip


def newhigh_section(bc: BriefCtx) -> SectionOut:
    """③ 신고가 3축(역사적·52주·120일 — ADR 0017), **종가 기준**. 기준 표기는 한 줄만(SD 규칙)."""
    art, mat = _board(bc, "newhigh")
    if art is None:
        return SectionOut(materials=(mat,), not_ready=True)
    p = art.payload
    counts = p.get("counts_close") or p.get("counts") or {}
    rows = p.get("achieved") or []
    lines = _stale_line(bc, mat)
    lines.append(f"<i>종가 기준 · {esc(str(p.get('as_of') or art.trade_date)[:10])} 보드</i>")
    for kind, icon, label in NEWHIGH_AXES:
        hits = [r for r in rows if ((r.get("close_basis") or {}).get("label")) == kind]
        lines.append(f"{icon} {label} 신고가 {int(counts.get(kind, len(hits)))}종목")
        for r in hits[:NAMES_PER_AXIS]:
            chg = f" {r['chg_pct']:+.1f}%" if r.get("chg_pct") is not None else ""
            sec = f" — {r['sector']}" if r.get("sector") else ""
            lines.append("  " + esc(f"{r.get('name') or r.get('code')}{chg}{sec}"))
        if len(hits) > NAMES_PER_AXIS:
            lines.append(f"  … 외 {len(hits) - NAMES_PER_AXIS}종목")
    return SectionOut(tuple(lines), art.source, art.as_of, art.quality, (mat,))


# ── ④ 수급 동향 ──────────────────────────────────────────────────────────────────────


def flows_trend(bc: BriefCtx, *, window: int = 5) -> SectionOut:
    """④ 외국인·기관 순매수 상위(마지막 기준일 금액)와 연속 순매수 일수(최근 `window` 거래일)."""
    rows = bc.repos.flows.window(None, bc.day, window)
    if not rows:
        return SectionOut(
            materials=(
                Material("종목 수급", "not_ready", job="market.close_collect", due="15:35"),
            ),
            not_ready=True,
        )
    by: dict[Investor, dict[str, list[Any]]] = defaultdict(dict)
    for code, items in rows.items():
        for r in items:
            inv = Investor(r.investor)
            if inv in (Investor.FOREIGN, Investor.INSTITUTION) and r.net_value is not None:
                by[inv].setdefault(code, []).append(r)
    last = max(r.date for items in rows.values() for r in items)
    names = _names(bc, last)
    lines: list[str] = []
    qs: list[Quality] = []
    for inv, ko in ((Investor.FOREIGN, "외국인"), (Investor.INSTITUTION, "기관")):
        today = [(c, rs) for c, rs in by[inv].items() if any(r.date == last for r in rs)]
        ranked = sorted(today, key=lambda t: -next(r.net_value for r in t[1] if r.date == last))
        bits: list[str] = []
        for code, rs in ranked[:TOP_FLOWS]:
            val = next(r.net_value for r in rs if r.date == last)
            if val <= 0:
                break
            streak = 0
            for r in sorted(rs, key=lambda x: x.date, reverse=True):
                if (r.net_value or 0) <= 0:
                    break
                streak += 1
            qs += [r.quality for r in rs]
            tail = f"·{streak}일 연속" if streak >= 2 else ""
            bits.append(f"{names.get(code, code)} {eok(val)}{tail}")
        if bits:
            lines.append(f"<b>{ko}</b> " + esc(" · ".join(bits)))
    q = worst(qs) or Quality.OK
    src = "+".join(sorted({r.source for items in rows.values() for r in items}))
    if lines:
        lines.append(f"<i>{esc(f'기준일 {mmdd(last)} · {src} · {_qko(q)}')}</i>")
    mat = Material("종목 수급", "ok" if last >= bc.day else "late", q, last, bc.day,
                   "market.close_collect", "15:35")  # fmt: skip
    as_of = datetime.combine(last, time(15, 30), tzinfo=bc.now_kst.tzinfo)
    return SectionOut(tuple(lines), src, as_of, q, (mat,))


# ── ⑤ 주요 공시 ──────────────────────────────────────────────────────────────────────


def filings_section(bc: BriefCtx, *, limit: int = 10) -> SectionOut:
    """⑤ 오늘 접수 공시 중 키워드 중요도 '높음' 이상(중요도 ≤ 2 — P4 `importance_levels.high`)."""
    rows = bc.repos.filings.disclosures(start=bc.day, end=bc.day)
    hot = [d for d in rows if (d.kw_score or 0) >= bc.importance_high]
    hot.sort(key=lambda d: (-(d.kw_score or 0), d.rcept_no))
    if not hot:
        # 섹션별 '없음' 문구는 config/briefs.yaml 의 none_text(WRAP) — 없으면 SD 문구 그대로
        sc = next((s for s in bc.cfg.closing.sections if s.key == "filings"), None)
        none = sc.none_text if sc is not None and sc.none_text else NO_FILINGS
        return SectionOut((esc(none),), "dart", bc.now, Quality.OK)
    lines = [esc(f"{d.corp_name} — {d.title}") for d in hot[:limit]]
    if len(hot) > limit:
        lines.append(f"… 외 {len(hot) - limit}건")
    return SectionOut(tuple(lines), "dart", bc.now, Quality.OK)


# ── ⑥ GEX·진단(P7 — D-P7-16) ─────────────────────────────────────────────────────────────

GEX_DAY_JOB: Final = ("gex.engine", "15:45")  # 재료 주인(서비스)·주간 세션 끝 — 품질 줄용
DIAG_JOB: Final = ("diag.daily", "16:00")


def gex_diag_section(bc: BriefCtx) -> SectionOut:
    """⑥ 오늘 주간 마지막 GEX 사이클 + 진단(종합 판정·공포탐욕). 둘 다 없으면 미준비."""
    lines: list[str] = []
    mats: list[Material] = []
    qs: list[Quality | None] = []
    stamps: list[datetime] = []
    srcs: list[str] = []

    snap = snapshot(bc.repos.gex.levels_latest("all", upto=bc.now))
    job, due = GEX_DAY_JOB
    if snap is None:
        mats.append(Material("GEX 마감", "not_ready", job=job, due=due))
    else:
        fresh = snap.trade_date == bc.day and snap.session == "day"
        lines.append(f"📐 <b>GEX</b> <i>{esc(cycle_label(snap))} 마지막 사이클</i>")
        if not fresh:
            lines.append("<i>오늘 주간 산출 없음 — 마지막 산출을 싣는다</i>")
        lines += gex_lines(snap)
        mats.append(Material("GEX 마감", "ok" if fresh else "late", snap.quality, snap.trade_date,
                             bc.day, job, due))  # fmt: skip
        qs.append(snap.quality)
        stamps.append(snap.ts)
        srcs.append("KBJ:gex.engine")

    job, due = DIAG_JOB
    verdict = bc.repos.diag.verdict_on(bc.day)
    prev_day = bc.prev_kr_day()
    fg = {r.trade_date: r for r in bc.repos.diag.fear_greed_history(prev_day, bc.day)}
    body = diag_lines(verdict, fg.get(bc.day), fg.get(prev_day))
    if verdict is None and bc.day not in fg:
        mats.append(Material("진단", "not_ready", job=job, due=due))
        if lines:
            lines.append(esc(bc.cfg.texts.not_ready.format(job=job, due=due)))
    else:
        lines.append("🧭 <b>진단</b>")
        lines += body
        got = [r for r in (verdict, fg.get(bc.day)) if r is not None]
        q = worst([r.quality for r in got])
        mats.append(Material("진단", "ok", q, bc.day, bc.day, job, due))
        qs.append(q)
        stamps += [r.as_of for r in got]
        srcs += [r.source for r in got]
    if not lines:
        return SectionOut(materials=tuple(mats), not_ready=True)
    src = "+".join(dict.fromkeys(srcs)) or "KBJ"
    return SectionOut(tuple(lines), src, max(stamps), worst(qs), tuple(mats))

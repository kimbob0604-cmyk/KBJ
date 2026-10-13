"""마감 요약(16:00 — ADR 0018) 섹션 — 지수·환율·시장 수급 / 업종·특징주 / 신고가 3축 / 수급 동향 /
주요 공시.

프로그램매매 줄(F2)·GEX·시그널·초안·추천(P7·P8)은 싣지 않는다. 재료는 저장소에서만 읽는다(계약 ⑯):
장중 지수 `prv_market.index_quote`(KIS — 마지막 값), 환율 `prv_macro`/`pub_macro` 최신, 시장 투자자
`prv_flows.market_investor_daily`, 보드 산출 `prv_board.artifact`(KR — sectors·events·newhigh)·
`stock_day`(이름), 종목 투자자 `prv_flows.stock_investor_daily`, 공시 `pub_filings.disclosure`
(키워드 점수).

16:00 의 수급은 KIS 마감 수집 값이라 잠정일 수 있다 — 줄마다 **기준일·원천·quality** 를 적고, 기대
기준일(실행일)과 다르면 실제 기준일을 적는다(ADR 0018 §2-5, metrics §20 ②).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import Investor

from .sections import BriefCtx, Material, SectionOut, eok, esc, fmt_num, mmdd, worst

__all__ = [
    "FX_SERIES",
    "filings_section",
    "flows_trend",
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
    if not lines:
        return SectionOut(materials=tuple(mats), not_ready=True)
    return SectionOut(tuple(lines), "+".join(sorted(srcs)), bc.now, worst(qs), tuple(mats))


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

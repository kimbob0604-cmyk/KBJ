"""HTML 리포트 + 일간 코멘트용 ~함 체 문단.

문체는 명사형·개조식. 작업 과정 워딩은 넣지 않는다.
수치는 표본 수·한계와 함께 적는다.
"""
from __future__ import annotations

import html
import json

from . import config as C

CSS = """
:root{--bg:#fff;--fg:#16181d;--mute:#6b7280;--line:#e5e7eb;--up:#d1453b;--dn:#1e63c8;
--pos:#e8f0fe;--card:#fafafa}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14161a;--fg:#e8eaed;
--mute:#9aa0a6;--line:#2a2e35;--pos:#1b2942;--card:#1a1d22}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 18px 64px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:30px 0 10px}
.sub{color:var(--mute);font-size:12px;margin-bottom:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0 4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card .k{color:var(--mute);font-size:11px} .card .v{font-size:19px;font-weight:600;margin-top:2px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:12.5px;white-space:nowrap}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right}
th{background:var(--card);position:sticky;top:0;font-weight:600;color:var(--mute)}
td.l,th.l{text-align:left} tr:last-child td{border-bottom:0}
.up{color:var(--up)} .dn{color:var(--dn)} .mute{color:var(--mute)}
.bars{display:grid;gap:5px;margin:8px 0}
.bar{display:grid;grid-template-columns:150px 1fr 92px;gap:8px;align-items:center;font-size:12.5px}
.bar .t{background:var(--pos);height:16px;border-radius:3px;min-width:2px}
.tag{display:inline-block;padding:1px 6px;border-radius:10px;background:var(--pos);font-size:11px}
ul.n{color:var(--mute);font-size:12px;padding-left:18px}
"""


def relink_board(root, as_of: str, log=print) -> int:
    """리포트를 쓴 뒤 같은 폴더의 보드(index.html · d/{날짜}.html) 헤더 링크만 갱신.

    엔진 코드(board.web.site.relink)를 부르기만 한다. 엔진이 없거나 실패해도
    리포트 생성은 영향받지 않는다 — 그 사실만 남긴다.
    """
    try:
        from board.web import site
    except Exception as e:                         # 엔진 소스가 이 폴더에 없는 배치
        log(f"  보드 링크 갱신 생략 — board.web.site 없음 ({type(e).__name__})")
        return 0
    try:
        return site.relink(str(root), as_of, log=log)
    except Exception as e:
        log(f"  보드 링크 갱신 실패: {type(e).__name__}: {e}")
        return 0


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _n(x, d=1, dash="—") -> str:
    if x is None or x != x:
        return dash
    return f"{float(x):,.{d}f}"


def _signed(x, d=1) -> str:
    if x is None or x != x:
        return '<span class="mute">—</span>'
    cls = "up" if x > 0 else ("dn" if x < 0 else "mute")
    return f'<span class="{cls}">{float(x):+,.{d}f}</span>'


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{CSS}</style></head>"
            f"<body><div class=wrap>{body}</div></body></html>")


def _cards(items) -> str:
    return ('<div class=cards>' + "".join(
        f'<div class=card><div class=k>{_e(k)}</div><div class=v>{v}</div></div>'
        for k, v in items) + '</div>')


def _bars(pairs, unit="") -> str:
    """가로 막대. 길이는 **최대값 대비 비율**이라 최대값이 0 이면 비율이 없다.

    그날 어느 등급에도 종목이 안 잡히면 값이 전부 0 으로 들어온다. 예전에는
    `max()` 결과를 그대로 분모에 넣어 ZeroDivisionError 로 리포트 생성이
    통째로 죽었고, 그 뒤 단계(검증)까지 건너뛰어졌다 — 2026-09-16 flowlab
    run #10 이 그랬다. 값이 0 인 것은 사실이므로 **막대만 비우고 숫자는 그대로
    적는다.** 0 을 못 그린다고 0 이 없는 일이 되지는 않는다.
    """
    vals = [abs(v) for _, v in pairs if v is not None]
    mx = max(vals) if vals else 0
    out = ['<div class=bars>']
    for label, v in pairs:
        w = 0 if (v is None or not mx) else min(100, abs(v) / mx * 100)
        out.append(f'<div class=bar><div class=l>{_e(label)}</div>'
                   f'<div><div class=t style="width:{w:.1f}%"></div></div>'
                   f'<div>{_n(v)}{_e(unit)}</div></div>')
    out.append('</div>')
    return "".join(out)


def _notes(notes) -> str:
    if not notes:
        return ""
    return "<ul class=n>" + "".join(f"<li>{_e(x)}</li>" for x in notes) + "</ul>"


# ── 모듈 A 리포트 ─────────────────────────────────────────────
FLOW_COLS = [
    ("name", "종목", "l"), ("code", "코드", "l"), ("group", "구분", "l"),
    ("label", "라벨", "l"), ("chg_pct", "등락%", ""), ("mktcap", "시총(억)", ""),
    ("turnover", "거래대금(억)", ""), ("vol_mult", "거래량배수", ""),
    ("inst_5d_eok", "기관5일(억)", ""), ("frgn_5d_eok", "외인5일(억)", ""),
    ("net_5d_eok", "합계5일(억)", ""), ("intensity_5d_bp", "강도(bp)", ""),
    ("concentration_1d_pct", "당일집중%", ""), ("frgn_rate_chg_5d_pp", "외인율Δ5일", ""),
    ("flow_grade", "등급", "l"), ("prior_win_20d_pct", "과거20일승률%", ""),
]


def flows_html(p: dict) -> str:
    agg = p["aggregate"]
    rows = [r for r in p["rows"] if r.get("flow_grade")]
    rows.sort(key=lambda r: (r.get("net_5d_eok") is None, -(r.get("net_5d_eok") or 0)))

    head = (f"<h1>수급 레이어 — {_e(p['as_of'])}</h1>"
            f"<div class=sub>source={_e(p['source'])} · 생성 {_e(p['generated_at'])} · "
            f"신고가 달성 {agg['n_achieved']} · 근접 {agg['n_proximity']} · "
            f"수급 결합 {agg['n_with_flows']}/{agg['n_rows']}종목"
            f"({_n(agg['coverage_pct'])}%)</div>")

    cards = _cards([
        ("쌍끌이", f"{agg['by_grade']['쌍끌이']}<span class=mute style='font-size:12px'> "
                 f"/ {_n(agg['by_grade_pct']['쌍끌이'])}%</span>"),
        ("기관주도", agg["by_grade"]["기관주도"]),
        ("외인주도", agg["by_grade"]["외인주도"]),
        ("개인주도", agg["by_grade"]["개인주도"]),
        (f"supported (≥{_n(C.SUPPORT_INTENSITY_BP,0)}bp)",
         f"{agg['supported_n']}<span class=mute style='font-size:12px'> "
         f"/ {_n(agg['supported_pct'])}%</span>"),
        ("강도 중앙값", f"{_n(agg['intensity_5d_bp_median'])}bp"),
    ])

    grade_bars = _bars([(g, agg["by_grade"][g]) for g in
                        ("쌍끌이", "기관주도", "외인주도", "개인주도")], "종목")
    top_bars = _bars([(f"{t['name']} ({t['flow_grade']})", t["net_5d_eok"])
                      for t in agg["top_net_5d"]], "억")

    th = "".join(f'<th class="{c}">{_e(t)}</th>' for _, t, c in FLOW_COLS)
    trs = []
    for r in rows:
        tds = []
        for key, _, cls in FLOW_COLS:
            v = r.get(key)
            if key in ("inst_5d_eok", "frgn_5d_eok", "net_5d_eok", "intensity_5d_bp",
                       "concentration_1d_pct", "frgn_rate_chg_5d_pp", "chg_pct"):
                cell = _signed(v)
            elif key in ("mktcap", "turnover"):
                cell = _n(v, 0)
            elif key == "flow_grade":
                mark = " ✓" if r.get("supported") else ""
                cell = f'<span class=tag>{_e(v)}{mark}</span>'
            elif key in ("vol_mult", "prior_win_20d_pct"):
                cell = _n(v)
            else:
                cell = _e(v)
            tds.append(f'<td class="{cls}">{cell}</td>')
        trs.append("<tr>" + "".join(tds) + "</tr>")

    miss = ""
    if p.get("missing"):
        li = "".join(f"<li>{_e(m['name'])}({_e(m['code'])}) — {_e(m['reason'])}</li>"
                     for m in p["missing"][:30])
        miss = f"<h2>수급 결합 실패 {len(p['missing'])}종목</h2><ul class=n>{li}</ul>"

    prior = p.get("prior") or {}
    prior_line = (f"<div class=sub>과거 통계 — {_e(prior.get('note'))}</div>"
                  if prior else "")

    body = (head + cards
            + "<h2>수급 등급 분포 (5일 기준)</h2>" + grade_bars
            + "<h2>5일 순매수 상위</h2>" + top_bars
            + "<h2>종목별</h2><div class=scroll><table><thead><tr>" + th
            + "</tr></thead><tbody>" + "".join(trs) + "</tbody></table></div>"
            + prior_line + miss
            + "<h2>한계</h2>" + _notes(p.get("notes")))
    return _page(f"수급 레이어 {p['as_of']}", body)


def flows_md(p: dict) -> str:
    """일간 코멘트에 그대로 붙이는 ~함 체 문단."""
    agg = p["aggregate"]
    g, gp = agg["by_grade"], agg["by_grade_pct"]
    L = []
    if p["source"] == C.SOURCE_DEMO:
        L.append("> 합성(demo) 소스 산출물임. 아래 수치는 시장 사실이 아니며 배관 점검용임.\n")
    L.append(f"### 수급 레이어 — {p['as_of']}\n")
    L.append(f"- 신고가 달성 {agg['n_achieved']}종목·근접 {agg['n_proximity']}종목 중 "
             f"{agg['n_with_flows']}종목에 투자자별 순매매 결합함 "
             f"(결합률 {_n(agg['coverage_pct'])}%)")
    L.append(f"- 5일 기준 등급 분포는 쌍끌이 {g['쌍끌이']}종목({_n(gp['쌍끌이'])}%)·"
             f"기관주도 {g['기관주도']}·외인주도 {g['외인주도']}·개인주도 {g['개인주도']}종목임")
    L.append(f"- 개인주도가 아니면서 5일 순매수 강도 {_n(C.SUPPORT_INTENSITY_BP, 0)}bp 이상인 "
             f"종목은 {agg['supported_n']}종목({_n(agg['supported_pct'])}%)이고, "
             f"강도 중앙값은 {_n(agg['intensity_5d_bp_median'])}bp 임")
    top = agg["top_net_5d"][:5]
    if top:
        s = " · ".join(f"{t['name']} {_n(t['net_5d_eok'])}억({t['flow_grade']})" for t in top)
        L.append(f"- 5일 순매수 상위는 {s} 순임")
    prior = p.get("prior") or {}
    if prior.get("available"):
        L.append(f"- 거래량 배수 구간별 과거 20일 승률을 표에 부착함 "
                 f"(표본 {prior.get('n_events')}건, {prior.get('years')}년 창)")
    else:
        L.append("- 과거 승률 부착은 이벤트 스터디 미실행으로 비어 있음")
    if p.get("missing"):
        L.append(f"- 수급 결합 실패 {len(p['missing'])}종목은 표에서 제외함")
    L.append("")
    L.append("표본 수와 산출 기준은 리포트 하단에 함께 둠. "
             "순매매 금액은 일별 순매매량에 그날 종가를 곱해 합산한 환산값임.")
    return "\n".join(L) + "\n"


# ── 모듈 B 리포트 ─────────────────────────────────────────────
def _axis_table(title: str, rows: list[dict], key: str, keylabel: str) -> str:
    if not rows:
        return ""
    th = (f"<th class=l>{_e(keylabel)}</th><th>n</th><th>5d 승률%</th><th>5d 중앙%</th>"
          f"<th>20d 중앙%</th><th>20d 평균%</th><th>20d 승률%</th>")
    trs = "".join(
        "<tr>" + f"<td class=l>{_e(r[key])}</td><td>{r['n']:,}</td>"
        + f"<td>{_n(r.get('win_5d_pct'))}</td><td>{_signed(r.get('med_5d_pct'))}</td>"
        + f"<td>{_signed(r.get('med_20d_pct'))}</td><td>{_signed(r.get('avg_20d_pct'))}</td>"
        + f"<td>{_n(r.get('win_20d_pct'))}</td></tr>" for r in rows)
    return (f"<h2>{_e(title)}</h2><div class=scroll><table><thead><tr>{th}</tr></thead>"
            f"<tbody>{trs}</tbody></table></div>")


def eventstudy_html(p: dict) -> str:
    o = p["overall"]
    head = (f"<h1>신고가 이벤트 스터디 — {_e(p['years'])}년</h1>"
            f"<div class=sub>source={_e(p['source'])} · 기준일 {_e(p['as_of'])} · "
            f"유니버스 {p['universe_n']:,}종목 · 신고가 {o['n']:,}건 · "
            f"근접 {p['proximity']['n']:,}건 · 지수 대비 초과수익</div>")
    cards = _cards([
        ("신고가 표본", f"{o['n']:,}건"),
        ("20일 중앙", _signed(o["med_20d_pct"]) + "%"),
        ("20일 평균", _signed(o["avg_20d_pct"]) + "%"),
        ("20일 승률", f"{_n(o['win_20d_pct'])}%"),
        ("5일 중앙", _signed(o["med_5d_pct"]) + "%"),
        ("5일 승률", f"{_n(o['win_5d_pct'])}%"),
    ])
    lead = ("<div class=sub>초과수익 = 종목 후행수익률 − 소속지수 같은 구간 수익률. "
            "평균이 중앙값보다 높으면 소수 상단 종목이 끌어올린 것으로 읽음.</div>")

    vb = _bars([(r["bucket"], r["med_20d_pct"]) for r in p["by_volmult"]], "%")
    conv = p["proximity"].get("by_gap") or []
    conv_rows = "".join(
        f"<tr><td class=l>{_e(r['bucket'])}</td><td>{r['n']:,}</td>"
        f"<td>{_n(r['conv_pct'])}</td></tr>" for r in conv)
    conv_tbl = (f"<h2>근접 → {C.BREAKOUT_WINDOW}일 내 돌파 전환율</h2><div class=scroll><table>"
                f"<thead><tr><th class=l>갭</th><th>n</th><th>전환율%</th></tr></thead>"
                f"<tbody>{conv_rows}</tbody></table></div>") if conv else ""

    body = (head + cards + lead
            + _axis_table("거래량 배수 (20일 평균 대비)", p["by_volmult"], "bucket", "배수")
            + "<h2>거래량 배수별 20일 중앙 초과수익</h2>" + vb
            + _axis_table("신선도", p["by_freshness"], "bucket", "신규/연속")
            + _axis_table("연속 일수", p["by_streak"], "bucket", "연속")
            + _axis_table("라벨", p["by_kind"], "bucket", "라벨")
            + _axis_table("거래량 배수 × 신선도", p["by_volmult_freshness"], "bucket", "구간")
            + conv_tbl
            + "<h2>한계</h2>" + _notes(p.get("limits")))
    return _page("신고가 이벤트 스터디", body)


# ── 누적(history) 리포트 ──────────────────────────────────────
GRADE_COLORS = {"쌍끌이": "#d1453b", "기관주도": "#e08a3c", "외인주도": "#1e63c8", "개인주도": "#9aa0a6"}


def _stacked_svg(by_date: list[dict], grades) -> str:
    """날짜별 등급 구성 100% 누적 막대 + supported% 선. 외부 의존 없이 인라인 SVG."""
    if not by_date:
        return ""
    W, H, PL, PB, PT = 1100, 260, 40, 34, 10
    n = len(by_date)
    bw = (W - PL - 10) / n
    ih = H - PB - PT
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;display:block">']
    for i, d in enumerate(by_date):
        tot = sum(d.get(g) or 0 for g in grades) or 1
        y = PT + ih
        x = PL + i * bw
        for g in grades:
            v = (d.get(g) or 0) / tot * ih
            y -= v
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bw - 1.5, 0.8):.1f}" '
                         f'height="{v:.1f}" fill="{GRADE_COLORS[g]}" opacity=".85">'
                         f'<title>{_e(d["as_of"])} {_e(g)} {d.get(g) or 0}종목</title></rect>')
    # supported% 선 (0~100 → ih)
    pts = []
    for i, d in enumerate(by_date):
        sp = d.get("supported_pct")
        if sp is None:
            continue
        pts.append(f"{PL + i * bw + bw / 2:.1f},{PT + ih - sp / 100 * ih:.1f}")
    if pts:
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="currentColor" '
                     f'stroke-width="1.6" stroke-dasharray="4 3" opacity=".8"/>')
    for pct in (0, 25, 50, 75, 100):
        y = PT + ih - pct / 100 * ih
        parts.append(f'<text x="{PL - 6}" y="{y + 4:.1f}" font-size="10" text-anchor="end" '
                     f'fill="#6b7280">{pct}%</text>')
    step = max(1, n // 8)
    for i in range(0, n, step):
        parts.append(f'<text x="{PL + i * bw + bw / 2:.1f}" y="{H - 14}" font-size="10" '
                     f'text-anchor="middle" fill="#6b7280">{_e(by_date[i]["as_of"][5:])}</text>')
    parts.append("</svg>")
    legend = " ".join(
        f'<span class=tag style="background:{GRADE_COLORS[g]};color:#fff">{_e(g)}</span>'
        for g in grades) + ' <span class=tag>- - supported%</span>'
    return f'<div style="margin:8px 0 4px">{legend}</div>' + "".join(parts)


def _join_table(title: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    th = ("<th class=l>구간</th><th>n</th><th>5d 승률%</th><th>5d 중앙%</th>"
          "<th>20d 중앙%</th><th>20d 평균%</th><th>20d 승률%</th>")
    trs = "".join(
        "<tr>"
        f"<td class=l>{_e(r['bucket'])}"
        + (' <span class=tag>표본 부족</span>' if r.get("small_sample") else "") + "</td>"
        f"<td>{r['n']:,}</td><td>{_n(r.get('win_5d_pct'))}</td>"
        f"<td>{_signed(r.get('med_5d_pct'))}</td><td>{_signed(r.get('med_20d_pct'))}</td>"
        f"<td>{_signed(r.get('avg_20d_pct'))}</td><td>{_n(r.get('win_20d_pct'))}</td></tr>"
        for r in rows)
    return (f"<h2>{_e(title)}</h2><div class=scroll><table><thead><tr>{th}</tr></thead>"
            f"<tbody>{trs}</tbody></table></div>")


def history_html(s: dict) -> str:
    grades = ("쌍끌이", "기관주도", "외인주도", "개인주도")
    bd = s.get("by_date") or []
    j = s.get("eventstudy_join") or {}
    lo, hi = s.get("date_range") or ("", "")

    head = (f"<h1>수급 누적 — {_e(lo)} ~ {_e(hi)}</h1>"
            f"<div class=sub>source={_e(s.get('source'))} · 생성 {_e(s.get('generated_at'))} · "
            f"{s['n_dates']}일 · {s['n_rows']:,}행 (신고가 달성+근접, 수급 결합분)</div>")

    tot = {g: sum(d.get(g) or 0 for d in bd) for g in grades}
    n_all = sum(tot.values()) or 1
    sup_all = sum(d.get("supported_n") or 0 for d in bd)
    cards = _cards([
        ("누적 일수", f"{s['n_dates']}"),
        ("누적 행", f"{s['n_rows']:,}"),
        ("쌍끌이 비중", f"{tot['쌍끌이'] / n_all * 100:.1f}%"),
        ("개인주도 비중", f"{tot['개인주도'] / n_all * 100:.1f}%"),
        ("supported 비중", f"{sup_all / n_all * 100:.1f}%"),
        ("이벤트 조인", f"{j.get('n_joined', 0):,}"
                    f"<span class=mute style='font-size:12px'> / {j.get('n_flows_achieved', 0):,}</span>"),
    ])

    trend = "<h2>날짜별 수급 등급 구성 (5일 기준)</h2>" + _stacked_svg(bd, grades)

    th = ("<th class=l>기준일</th><th>대상</th><th>달성</th>" +
          "".join(f"<th>{_e(g)}</th>" for g in grades) +
          "<th>supported</th><th>supported%</th><th>강도 중앙(bp)</th>")
    trs = "".join(
        f"<tr><td class=l>{_e(d['as_of'])}</td><td>{d.get('n', 0)}</td>"
        f"<td>{d.get('n_achieved', 0)}</td>"
        + "".join(f"<td>{d.get(g) or 0}</td>" for g in grades)
        + f"<td>{d.get('supported_n', 0)}</td><td>{_n(d.get('supported_pct'))}</td>"
        f"<td>{_n(d.get('intensity_med'))}</td></tr>"
        for d in reversed(bd))
    table = (f"<h2>날짜별</h2><div class=scroll><table><thead><tr>{th}</tr></thead>"
             f"<tbody>{trs}</tbody></table></div>")

    if j.get("n_joined"):
        join = ("<h2>수급 등급별 신고가 후행 성과</h2>"
                f"<div class=sub>{_e(j.get('note'))}</div>"
                + _join_table("등급별 (5일 기준 등급)", j.get("by_flow_grade") or [])
                + _join_table(f"supported (개인주도 아님 · 강도 ≥ {_n(C.SUPPORT_INTENSITY_BP, 0)}bp)",
                              j.get("by_supported") or []))
    else:
        join = ("<h2>수급 등급별 신고가 후행 성과</h2>"
                "<div class=sub>조인 0건 — 이벤트 스터디 미실행이거나 후행 20일이 지난 날짜가 없음</div>")

    body = head + cards + trend + table + join + "<h2>한계</h2>" + _notes(s.get("notes"))
    return _page("수급 누적", body)

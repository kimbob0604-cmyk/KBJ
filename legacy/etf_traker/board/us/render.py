#!/usr/bin/env python3
"""
미국장 보드 화면 — board.json 하나를 읽어 **외부 의존 없는 단일 HTML** 을 만든다.

국장 화면(board/web/render.py)과 같은 규칙이다. CSS 는 board.css 를 그대로
인라인해 두 보드가 같은 모양으로 보인다. 색 방향도 국장과 같은 적색=상승이다 —
사용자가 쓰던 미국장 시트가 적색=상승이라(config/settings.yaml rankings 주석)
두 화면을 나란히 놓고 읽을 수 있다.

화면은 계산하지 않는다. board.json 에 없는 값은 화면에도 없다.
"""
import html
import os

from . import brief as BR

CSS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'web', 'board.css')

EXTRA_CSS = """
.usgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:14px 16px}
.card h3{margin:0 0 8px;font-size:12.5px;font-weight:600;color:var(--ink-2)}
.card .big{font-family:var(--mono);font-size:22px;font-weight:600}
.brief{white-space:pre-wrap;font-family:var(--mono);font-size:12.5px;line-height:1.7;
  background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:16px;
  overflow-x:auto}
.tick{font-family:var(--mono);font-weight:600}
.sector-line{margin:0 0 6px;font-size:13px}
.sector-line .nm{font-weight:600}
"""


def e(s):
    return html.escape(str(s if s is not None else ''))


def cls(v):
    return 'up' if (v or 0) > 0 else ('down' if (v or 0) < 0 else 'mut')


def pct(v, digits=1):
    if v is None:
        return '<span class="mut">–</span>'
    return f'<span class="num {cls(v)}">{v:+.{digits}f}%</span>'


def card(title, big, note=''):
    return (f'<div class="card"><h3>{e(title)}</h3><div class="big">{big}</div>'
            f'<div class="mut" style="font-size:12px">{note}</div></div>')


def strip(b):
    s = b['summary']
    m = s.get('mega') or {}
    ratio = '–' if s.get('up_ratio') is None else f'{s["up_ratio"]:.0f}%'
    d = s.get('newhigh_delta')
    delta = '' if d is None else f'전일 {d:+d}'
    return '<div class="usgrid">' + ''.join([
        card('신고가', f'{s["newhigh"]}', delta),
        card('52주 신고가 / 신저가', f'{s["w52_high"]} / {s["w52_low"]}', '종가 기준'),
        card('상승 / 하락', f'{s["up"]} / {s["down"]}', f'상승 비율 {ratio}'),
        card('중앙 등락', BR.pct(s.get('median'), 1), f'±5% {s["surge"]} / {s["plunge"]}'),
        card('대형주', f'{m.get("n", 0)}개', f'상승 {m.get("up_count", 0)}'),
    ]) + '</div>'


def trend_table(b):
    tr = b.get('trend') or []
    if not tr:
        return ''
    head = ''.join(f'<th class="num">{BR.short(t["asof"])}</th>' for t in tr)
    rows = [('신고가', [str(t['newhigh']) for t in tr]),
            ('52주 신고가', [str(t['w52_high']) for t in tr]),
            ('52주 신저가', [str(t['w52_low']) for t in tr]),
            ('상승 비율', ['–' if t['up_ratio'] is None else f'{t["up_ratio"]:.0f}%' for t in tr]),
            ('중앙 등락', [BR.pct(t['median'], 1, zero='0.0', unit='') for t in tr])]
    body = ''.join(
        f'<tr><td>{e(k)}</td>' + ''.join(f'<td class="num">{e(v)}</td>' for v in vals) + '</tr>'
        for k, vals in rows)
    return (f'<table><thead><tr><th>지표</th>{head}</tr></thead><tbody>{body}</tbody></table>')


def sector_table(b):
    rows = []
    for s in (b.get('sectors') or [])[:20]:
        tr = '·'.join(str(x) for x in (s.get('trend') or []))
        rows.append(f'<tr><td class="nm">{e(s["sector"])}</td>'
                    f'<td class="num">{s["newhigh"]}</td>'
                    f'<td class="num">{s["w52"]}</td>'
                    f'<td class="num">{s["low52"]}</td>'
                    f'<td class="num">{pct(s.get("median"))}</td>'
                    f'<td class="num">{pct(s.get("median_5d"))}</td>'
                    f'<td class="num mut">{e(tr)}</td>'
                    f'<td class="num mut">{s["n"]}</td></tr>')
    return ('<table><thead><tr><th>섹터</th><th>신고가</th><th>52주</th><th>신저가</th>'
            '<th>당일 중앙</th><th>5일 중앙</th><th>신고가 추이</th><th>종목수</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def stock_table(rows, limit):
    out = []
    for r in rows[:limit]:
        out.append(
            f'<tr><td class="tick">{e(r["ticker"])}</td>'
            f'<td>{e(r.get("name"))}</td>'
            f'<td>{e(r.get("sector"))}</td>'
            f'<td>{e(r.get("industry"))}</td>'
            f'<td class="num">{pct(r.get("chg_pct"))}</td>'
            f'<td class="num">{pct(r.get("ret_5d"))}</td>'
            f'<td class="num">{e(BR.money(r.get("turnover")))}</td>'
            f'<td class="num">{e(BR.mult(r.get("turnover_mult")) or "–")}</td>'
            f'<td class="num">{e(BR.money(r.get("mktcap")))}</td>'
            f'<td class="num">{r.get("streak") or "–"}</td></tr>')
    return ('<table><thead><tr><th>티커</th><th>이름</th><th>섹터</th><th>산업</th>'
            '<th>등락</th><th>5일</th><th>거래대금</th><th>배수</th><th>시총</th>'
            f'<th>연속</th></tr></thead><tbody>{"".join(out)}</tbody></table>')


def section(title, inner, note=''):
    return (f'<section class="sec"><div class="sec-h"><h2>{e(title)}</h2>'
            f'<span class="note">{e(note)}</span></div>{inner}</section>')


def render(board, rows, cfg):
    with open(CSS_PATH, encoding='utf-8') as f:
        css = f.read()
    b = board
    achieved = [r for r in rows if r.get('label')]
    achieved.sort(key=lambda r: -(r.get('turnover') or 0))
    near = [r for r in rows if r.get('near_kind') and not r.get('label')]
    near.sort(key=lambda r: (r.get('gap') or {}).get(r['near_kind']) or 99)
    text = BR.render(board, rows, cfg)

    miss = b.get('missing') or []
    banner = ''
    if miss:
        banner = ('<div class="card" style="border-color:var(--warn);margin-bottom:18px">'
                  '<h3>빠진 데이터</h3>'
                  + ''.join(f'<div>· {e(m)}</div>' for m in miss) + '</div>')

    body = '\n'.join([
        banner,
        strip(b),
        section('5일 흐름', trend_table(b)),
        section('섹터', sector_table(b), '신고가 수 · 중앙 등락'),
        section('신고가 달성', stock_table(achieved, cfg['display']['max_rows_achieved']),
                f'{len(achieved)}종목 · 거래대금 순'),
        section('신고가 근접', stock_table(near, cfg['display']['max_rows_proximity']),
                f'{len(near)}종목 · 갭 {cfg["proximity"]["max_gap_pct"]}% 이내'),
        section('브리프', f'<div class="brief">{e(text)}</div>',
                '텔레그램·노션에 그대로 붙여 넣는 원문'),
    ])
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>간밤 미국장 신고가 보드 — {e(b['asof'])}</title>
<style>{css}{EXTRA_CSS}</style></head>
<body><header><div class="head-in"><div class="title-row">
<h1>🇺🇸 간밤 미국장 신고가 보드</h1>
<span class="stamp">{e(b['asof'])} · 유니버스 {b['universe']['n']:,}종목 ·
 {e(b['source'].get('universe'))}/{e(b['source'].get('history'))} ·
 생성 {e(b.get('generated_at'))}</span>
</div></div></header>
<div class="wrap"><div class="panel on">{body}</div></div>
</body></html>"""


def write(board, rows, cfg, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(render(board, rows, cfg))
    return out

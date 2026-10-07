#!/usr/bin/env python3
"""
대시보드 HTML 렌더링 — 외부 의존 없는 단일 파일 출력.

색: 국내 관행대로 상승 빨강 / 하락 파랑. 다이버징 쌍(red↔blue)은 명·암 모드
양쪽에서 색각 이상 분리도 검증을 통과한 값을 쓴다.
숫자는 tabular-nums 로 세로 정렬한다.
"""
import html
from datetime import datetime

UP, DN = '#d03b3b', '#2a78d6'
UP_D, DN_D = '#e66767', '#3987e5'

CSS = """
*{box-sizing:border-box}
:root{color-scheme:light;
 --bg:#f9f9f7; --surf:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --mut:#898781;
 --line:#e1e0d9; --ring:rgba(11,11,11,.10); --up:__UP__; --dn:__DN__; --chip:#f0efec}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){color-scheme:dark;
 --bg:#0d0d0d; --surf:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --mut:#898781;
 --line:#2c2c2a; --ring:rgba(255,255,255,.10); --up:__UPD__; --dn:__DND__; --chip:#252523}}
:root[data-theme=dark]{color-scheme:dark;
 --bg:#0d0d0d; --surf:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --mut:#898781;
 --line:#2c2c2a; --ring:rgba(255,255,255,.10); --up:__UPD__; --dn:__DND__; --chip:#252523}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:1120px;margin:0 auto;padding:20px 16px 64px}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px}
h1{font-size:19px;margin:0;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:16px 0 24px}
.kpi{background:var(--surf);border:1px solid var(--ring);border-radius:10px;padding:12px 14px}
.kpi .l{font-size:12px;color:var(--mut)}
.kpi .v{font-size:22px;font-weight:600;margin-top:2px;letter-spacing:-.02em}
section{margin:0 0 30px}
h2{font-size:15px;margin:0 0 2px;letter-spacing:-.01em}
.note{color:var(--mut);font-size:12.5px;margin:0 0 10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}
 td,th{padding:7px 10px;font-size:13px}
 .wrap{padding:16px 12px 48px}}
.card{background:var(--surf);border:1px solid var(--ring);border-radius:10px;overflow:hidden}
.card h3{font-size:13px;margin:0;padding:10px 14px;border-bottom:1px solid var(--line);
 color:var(--ink2);font-weight:600}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{padding:7px 14px;text-align:right;font-size:13.5px;border-bottom:1px solid var(--line);
 white-space:nowrap}
tr:last-child td{border-bottom:none}
th{font-size:11.5px;color:var(--mut);font-weight:500;text-align:right}
td.n,th.n{text-align:left;width:99%;white-space:normal;word-break:keep-all}
td.n a{color:inherit;text-decoration:none}
td.n a:hover{text-decoration:underline}
.code{color:var(--mut);font-size:11.5px;margin-left:5px}
.up{color:var(--up);font-weight:600}
.dn{color:var(--dn);font-weight:600}
.tag{display:inline-block;font-size:10.5px;padding:1px 5px;border-radius:4px;
 background:var(--chip);color:var(--ink2);margin-left:6px;vertical-align:1px}
.bar{height:4px;border-radius:2px;display:block;margin-top:3px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px}
.tabs button{font:inherit;font-size:12.5px;padding:4px 11px;border-radius:999px;cursor:pointer;
 border:1px solid var(--ring);background:var(--surf);color:var(--ink2)}
.tabs button[aria-selected=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.pane[hidden]{display:none}
.toggle{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink2);cursor:pointer}
.empty{padding:16px 14px;color:var(--mut);font-size:13px}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);
 color:var(--mut);font-size:12px;line-height:1.7}
.live{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
 padding:2px 8px;border-radius:999px;background:#d03b3b14;color:var(--up)}
.live i{width:6px;height:6px;border-radius:50%;background:var(--up);animation:p 2s infinite}
.live.off{background:var(--chip);color:var(--mut)}
.live.off i{background:var(--mut);animation:none}
@keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
.tt{position:fixed;z-index:9;background:var(--surf);border:1px solid var(--ring);border-radius:8px;
 padding:7px 10px;font-size:12.5px;pointer-events:none;opacity:0;transition:opacity .1s;
 box-shadow:0 6px 20px rgba(0,0,0,.14);max-width:280px}
""".replace("__UPD__", UP_D).replace("__DND__", DN_D)\
      .replace("__UP__", UP).replace("__DN__", DN)

JS = """
document.querySelectorAll('[data-tabs]').forEach(g=>{
  const bs=[...g.querySelectorAll('button')];
  bs.forEach(b=>b.onclick=()=>{
    bs.forEach(x=>x.setAttribute('aria-selected', x===b));
    g.parentElement.querySelectorAll('.pane').forEach(p=>p.hidden = p.dataset.pane!==b.dataset.tab);
  });
});
const tt=document.createElement('div'); tt.className='tt'; document.body.appendChild(tt);
document.querySelectorAll('[data-tip]').forEach(el=>{
  el.onmouseenter=e=>{tt.textContent=el.dataset.tip; tt.style.opacity=1;};
  el.onmousemove=e=>{const w=tt.offsetWidth,h=tt.offsetHeight;
    tt.style.left=Math.min(e.clientX+14, innerWidth-w-8)+'px';
    tt.style.top=Math.max(8, e.clientY-h-10)+'px';};
  el.onmouseleave=()=>tt.style.opacity=0;
});
// 장중에는 페이지가 스스로 다시 불러온다. 탭을 열어두면 계속 최신이 된다.
if(document.body.dataset.live==='1') setTimeout(()=>location.reload(), 300000);
const lv=document.getElementById('levtog');
if(lv) lv.onchange=()=>document.querySelectorAll('tr[data-lev="1"]')
  .forEach(r=>r.style.display = lv.checked?'none':'');
"""


def e(s):
    return html.escape(str(s if s is not None else ''))


def _pct(v, digits=2):
    if v is None:
        return '<td class="mut">–</td>'
    c = 'up' if v > 0 else ('dn' if v < 0 else '')
    return f'<td class="{c}">{v:+.{digits}f}%</td>'


def _amt(v):
    """억원 표기. 1조 이상은 조 단위로 접는다."""
    if v is None:
        return '–'
    if abs(v) >= 10000:
        return f'{v/10000:,.2f}조'
    return f'{v:,.0f}억'


def _name_cell(x, extra=''):
    tag = '<span class="tag">레버·인버스</span>' if x.get('lev') else ''
    url = f'https://finance.naver.com/item/main.naver?code={x["code"]}'
    return (f'<td class="n"><a href="{url}" target="_blank" rel="noopener">{e(x["name"])}</a>'
            f'<span class="code">{e(x["code"])}</span>{tag}{extra}</td>')


def _rows_move(items, key, unit='%'):
    if not items:
        return '<tr><td class="empty" colspan="3">해당 없음</td></tr>'
    mx = max(abs(x[key]) for x in items) or 1
    out = []
    for x in items:
        v = x[key]
        col = 'var(--up)' if v > 0 else 'var(--dn)'
        w = abs(v) / mx * 100
        bar = f'<span class="bar" style="width:{w:.0f}%;background:{col}"></span>'
        tip = f'{x["name"]} · 종가 {x["close"]:,.0f}원 · 시총 {_amt(x["mktcap"])} · 거래대금 {_amt(x["turnover"])}'
        out.append(f'<tr data-lev="{1 if x.get("lev") else 0}" data-tip="{e(tip)}">'
                   f'{_name_cell(x, bar)}{_pct(v)}'
                   f'<td class="mut">{_amt(x["mktcap"])}</td></tr>')
    return ''.join(out)


def _table(head, body, cls=''):
    return (f'<div class="card"><h3>{head}</h3><table>{body}</table></div>')


def build(D):
    """D = 집계 결과 dict. 섹션 하나가 비어도 나머지는 그대로 나온다."""
    P = []
    A = P.append
    base, prev = D['base'], D['prev']

    if D.get('live_ts'):
        badge = ('<span class="live"><i></i>장중</span>' if D.get('is_live')
                 else '<span class="live off"><i></i>장 마감</span>')
        A(f'<header><h1>ETF 대시보드</h1>{badge}'
          f'<span class="sub">{D["live_date"]} {D["live_ts"]} 기준 · 전 종목 {D["n_etf"]:,}개'
          f' · 구성종목은 {base} 확정분</span></header>')
    else:
        A(f'<header><h1>ETF 대시보드</h1>'
          f'<span class="sub">기준일 {base} · 직전 거래일 대비 · 전 종목 {D["n_etf"]:,}개</span></header>')

    k = D['kpi']
    A('<div class="kpis">')
    for lab, val, cls in k:
        A(f'<div class="kpi"><div class="l">{e(lab)}</div>'
          f'<div class="v {cls}">{val}</div></div>')
    A('</div>')

    # 1. 당일 등락
    A('<section><h2>당일 등락</h2>'
      f'<p class="note">{"장중 누적 · " if D.get("is_live") else ""}시가총액 50억·거래대금 1억 이상만. '
      '<label class="toggle"><input type="checkbox" id="levtog"> 레버리지·인버스 숨기기</label></p>'
      '<div class="grid2">')
    A(_table('상승 TOP', '<tr><th class="n">종목</th><th>등락률</th><th>시가총액</th></tr>'
             + _rows_move(D['up'], 'chg')))
    A(_table('하락 TOP', '<tr><th class="n">종목</th><th>등락률</th><th>시가총액</th></tr>'
             + _rows_move(D['down'], 'chg')))
    A('</div></section>')

    # 2. 기간별 수익률
    pr = D['periods']
    if pr:
        A('<section><h2>기간별 수익률</h2><p class="note">누적 수익률 기준</p>')
        A('<div class="tabs" data-tabs>')
        for i, lab in enumerate(pr):
            A(f'<button data-tab="{e(lab)}" aria-selected="{str(i==0).lower()}">{e(lab)}</button>')
        A('</div>')
        for i, (lab, v) in enumerate(pr.items()):
            A(f'<div class="pane grid2" data-pane="{e(lab)}"{"" if i==0 else " hidden"}>')
            A(_table(f'상승 TOP <span class="code">{v["ref"]} 대비</span>',
                     '<tr><th class="n">종목</th><th>수익률</th><th>시가총액</th></tr>'
                     + _rows_move(v['up'], 'ret')))
            A(_table(f'하락 TOP <span class="code">{v["ref"]} 대비</span>',
                     '<tr><th class="n">종목</th><th>수익률</th><th>시가총액</th></tr>'
                     + _rows_move(v['down'], 'ret')))
            A('</div>')
        A('</section>')

    # 3. 자금 유입·이탈
    A('<section><h2>자금 유입·이탈</h2>')
    if D['flow_span']:
        A(f'<p class="note">상장좌수 증감 × NAV 추정 · {D["flow_span"][0]} → {D["flow_span"][1]}</p>')
        A('<div class="grid2">')
        for title, items in (('유입 TOP', D['flow_in']), ('이탈 TOP', D['flow_out'])):
            body = '<tr><th class="n">종목</th><th>추정금액</th><th>좌수증감</th></tr>'
            if not items:
                body += '<tr><td class="empty" colspan="3">해당 없음</td></tr>'
            for x in items:
                c = 'up' if x['amt'] > 0 else 'dn'
                body += (f'<tr data-lev="0"><td class="n">{e(x["name"])}'
                         f'<span class="code">{e(x["code"])}</span></td>'
                         f'<td class="{c}">{_amt(x["amt"])}</td>'
                         f'<td class="{c}">{x["pct"]:+.1f}%</td></tr>')
            A(_table(title, body))
        A('</div>')
    else:
        A('<div class="card"><div class="empty">'
          '순자산 스냅샷이 하루치뿐입니다. 설정·환매는 이틀치가 쌓이는 '
          '다음 영업일부터 집계됩니다.</div></div>')
    A('</section>')

    # 4. 거래 급증
    body = '<tr><th class="n">종목</th><th>평소 대비</th><th>거래대금</th></tr>'
    if D['spikes']:
        for x in D['spikes']:
            tip = f'당일 {x["volume"]:,.0f}주 · 20일 중앙값 {x["med"]:,.0f}주'
            body += (f'<tr data-lev="{1 if x["lev"] else 0}" data-tip="{e(tip)}">'
                     f'{_name_cell(x)}<td class="up">×{x["mult"]:,.1f}</td>'
                     f'<td class="mut">{_amt(x["turnover"])}</td></tr>')
    else:
        body += '<tr><td class="empty" colspan="3">해당 없음</td></tr>'
    A('<section><h2>거래 급증</h2>'
      f'<p class="note">직전 20거래일 중앙값 대비 2배 이상'
      f'{" · 장중 누적 거래량 기준이라 장 초반에는 보수적으로 잡힙니다" if D.get("is_live") else ""}</p>'
      + _table('', body) + '</section>')

    # 5. 구성종목 변동
    A('<section><h2>ETF 구성종목 변동</h2>')
    A(f'<p class="note">{e(D["hold_note"])}</p>')
    A('<div class="grid2">')
    body = '<tr><th class="n">종목</th><th>편입 ETF</th></tr>'
    if D['h_new']:
        for x in D['h_new']:
            tip = ', '.join(x['funds'][:6]) + (f' 외 {len(x["funds"])-6}' if len(x['funds']) > 6 else '')
            ev = '<span class="tag">신규·지수편입</span>' if x.get('event') else ''
            body += (f'<tr data-tip="{e(tip)}"><td class="n">{e(x["name"])}'
                     f'<span class="code">{e(x["code"])}</span>{ev}</td>'
                     f'<td class="up">{x["n"]}개</td></tr>')
    else:
        body += '<tr><td class="empty" colspan="2">해당 없음</td></tr>'
    A(_table('새로 편입된 종목', body))

    body = '<tr><th class="n">종목</th><th>제외 / 보유</th></tr>'
    if D['h_out']:
        for x in D['h_out']:
            tip = ', '.join(x['funds'][:6]) + (f' 외 {len(x["funds"])-6}' if len(x['funds']) > 6 else '')
            body += (f'<tr data-tip="{e(tip)}"><td class="n">{e(x["name"])}'
                     f'<span class="code">{e(x["code"])}</span></td>'
                     f'<td class="dn">{x["n"]}<span class="code">/ {x.get("prior", x["n"])}개</span></td></tr>')
    else:
        body += '<tr><td class="empty" colspan="2">해당 없음</td></tr>'
    A(_table('제외된 종목', body))
    A('</div>')

    if D.get('h_events'):
        body = '<tr><th class="n">종목</th><th>보유했던 ETF</th></tr>'
        for x in D['h_events']:
            body += (f'<tr><td class="n">{e(x["name"])}<span class="code">{e(x["code"])}</span></td>'
                     f'<td class="mut">{x["n"]}개 전부</td></tr>')
        A('<div style="margin-top:14px">' + _table(
            '종목 사유로 빠진 건 <span class="code">보유하던 ETF 전부에서 동시 소멸 — '
            '거래정지·합병·분할 등. 운용사 매도 판단이 아닙니다</span>', body) + '</div>')

    if D['h_moves']:
        body = '<tr><th class="n">종목</th><th>방향</th><th>ETF 수</th><th>평균</th></tr>'
        for x in D['h_moves']:
            c = 'up' if x['kind'] == 'ADD' else 'dn'
            lab = '비중확대' if x['kind'] == 'ADD' else '비중축소'
            tip = ', '.join(x['funds'][:6])
            body += (f'<tr data-tip="{e(tip)}"><td class="n">{e(x["name"])}'
                     f'<span class="code">{e(x["code"])}</span></td>'
                     f'<td class="{c}">{lab}</td><td>{x["n"]}개</td>'
                     f'<td class="{c}">{x["avg"]:+.1f}%</td></tr>')
        A('<div style="margin-top:14px">'
          + _table('여러 ETF가 동시에 움직인 종목', body) + '</div>')
    A('</section>')

    # 6. 신규 상장
    body = '<tr><th class="n">종목</th><th>상장일</th><th>시가총액</th></tr>'
    if D['new_etf']:
        for x in D['new_etf']:
            body += (f'<tr><td class="n">{e(x["name"])}<span class="code">{e(x["code"])}</span></td>'
                     f'<td class="mut">{x["first"]}</td><td>{_amt(x["mktcap"])}</td></tr>')
    else:
        body += '<tr><td class="empty" colspan="3">최근 60일 내 신규 상장 없음</td></tr>'
    A('<section><h2>신규 상장</h2><p class="note">최근 60일</p>'
      + _table('', body) + '</section>')

    # 7. 규모·거래대금
    A('<section><h2>규모</h2><div class="grid2">')
    b1 = '<tr><th class="n">종목</th><th>시가총액</th></tr>'
    for x in D['size']:
        b1 += f'{"<tr>"}{_name_cell(x)}<td>{_amt(x["mktcap"])}</td></tr>'
    b2 = '<tr><th class="n">종목</th><th>거래대금</th></tr>'
    for x in D['turn']:
        b2 += f'{"<tr>"}{_name_cell(x)}<td>{_amt(x["turnover"])}</td></tr>'
    A(_table('순자산 TOP', b1))
    A(_table('거래대금 TOP', b2))
    A('</div></section>')

    A('<footer>'
      '구성종목은 각 운용사 공시 PDF, 시세·순자산은 네이버 금융에서 수집합니다. '
      '자금 유입·이탈은 상장좌수 증감으로 추정한 값이라 공시 설정·환매액과 다를 수 있습니다.<br>'
      f'시세 갱신 {e(D["generated"])}'
      + (' · 장중 15분마다 자동 갱신 (이 페이지는 5분마다 스스로 새로고침)' if D.get('is_live') else '')
      + f' · 구성종목 확정 {e(D.get("base_generated") or "-")}<br>'
      '투자 판단의 근거로 쓰기 전 원문을 확인하세요.'
      '</footer>')

    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>ETF 대시보드 {D.get("live_date") or base}</title><style>{CSS}</style></head>'
            f'<body data-live="{1 if D.get("is_live") else 0}"><div class="wrap">{"".join(P)}</div><script>{JS}</script></body></html>')


# ─────────────────────────── 텔레그램 요약 ───────────────────────────
def telegram(D, url=None):
    """모바일에서 3초 안에 읽히는 요약. 자세한 건 대시보드 링크로 넘긴다."""
    L = [f'*ETF 대시보드*  `{D["base"]}`']
    k = {lab: val for lab, val, _ in D['kpi']}
    kp = k.get('KODEX 200', '–')
    L.append(f'KODEX 200 {kp} · 상승/하락 ' +
             k.get('상승 / 하락', '').replace('<span class="up">', '').replace(
                 '<span class="dn">', '').replace('</span>', ''))
    L.append('')

    def block(title, items, fmt, limit=5):
        if not items:
            return
        L.append(f'*{title}*')
        for x in items[:limit]:
            L.append('  ' + fmt(x))
        L.append('')

    nolev = [x for x in D['up'] if not x.get('lev')] or D['up']
    block('상승 TOP', nolev,
          lambda x: f'{x["chg"]:+.1f}%  {x["name"][:24]}')
    nolev = [x for x in D['down'] if not x.get('lev')] or D['down']
    block('하락 TOP', nolev, lambda x: f'{x["chg"]:+.1f}%  {x["name"][:24]}')
    if D['flow_span']:
        block('자금 유입', D['flow_in'], lambda x: f'{_amt(x["amt"])}  {x["name"][:24]}', 4)
        block('자금 이탈', D['flow_out'], lambda x: f'{_amt(x["amt"])}  {x["name"][:24]}', 4)
    block('거래 급증', D['spikes'], lambda x: f'×{x["mult"]:.1f}  {x["name"][:24]}', 4)
    block('여러 ETF가 동시에 움직인 종목', D['h_moves'],
          lambda x: ('📈' if x['kind'] == 'ADD' else '📉')
                    + f' {x["name"]} {x["n"]}개 ETF {x["avg"]:+.1f}%', 6)
    block('새로 편입', D['h_new'], lambda x: f'{x["name"]} — {x["n"]}개 ETF', 5)
    if D.get('h_events'):
        L.append('*종목 사유 제외* _(거래정지·합병 등)_')
        for x in D['h_events'][:3]:
            L.append(f'  {x["name"]} — 보유 {x["n"]}개 전부')
        L.append('')
    if url:
        L.append(f'[전체 대시보드 보기]({url})')
    return '\n'.join(L)

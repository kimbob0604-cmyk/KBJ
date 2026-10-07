#!/usr/bin/env python3
"""
텔레그램 전문(全文) 리포트 — 모든 내용을 메시지로 보낸다.

설계 요점
  · 편입 종목과 보유비중 변화가 1순위다. 앞쪽 메시지에 배치한다.
  · 비중은 %p 로 보여준다. "5.2% → 7.8% (+2.6%p)" 가 "수량 +30%" 보다 읽힌다.
    다만 비중은 주가가 올라도 늘어나므로, 운용사가 실제로 사고팔았는지는 수량으로
    같이 표기한다. 둘을 함께 봐야 오독하지 않는다.
  · 텔레그램은 메시지당 4096자 제한이 있다. 섹션 경계에서 끊어 여러 통으로 보낸다.
    섹션 중간이 잘리면 읽기 어렵다.
  · parse_mode 는 HTML 을 쓴다. ETF 이름에 * _ ( ) & 가 흔해서 Markdown 은 자주 깨진다.
"""
import html

LIMIT = 3900          # 4096 제한에서 여유를 둔다


def esc(s):
    return html.escape(str(s if s is not None else ''))


def _amt(v):
    if v is None:
        return '–'
    if abs(v) >= 10000:
        return f'{v/10000:,.1f}조'
    return f'{v:,.0f}억'


def _short(s, n=26):
    s = str(s or '')
    return s if len(s) <= n else s[:n - 1] + '…'


def _pack(sections):
    """섹션 리스트를 4096자 제한에 맞춰 메시지로 묶는다. 섹션은 쪼개지 않는다."""
    msgs, cur = [], ''
    for sec in sections:
        if not sec:
            continue
        if len(sec) > LIMIT:                      # 섹션 하나가 한도를 넘으면 줄 단위로 자른다
            if cur:
                msgs.append(cur); cur = ''
            buf = ''
            for line in sec.split('\n'):
                if len(buf) + len(line) + 1 > LIMIT:
                    msgs.append(buf); buf = ''
                buf += line + '\n'
            if buf:
                msgs.append(buf)
            continue
        if len(cur) + len(sec) + 2 > LIMIT:
            msgs.append(cur); cur = sec
        else:
            cur = (cur + '\n\n' + sec) if cur else sec
    if cur:
        msgs.append(cur)
    return msgs


# ─────────────────────────── 섹션 빌더 ───────────────────────────
def _head(D):
    k = {lab: val for lab, val, _ in D['kpi']}
    ud = k.get('상승 / 하락', '')
    for t in ('<span class="up">', '<span class="dn">', '</span>'):
        ud = ud.replace(t, '')
    when = f'{D["live_date"]} {D["live_ts"]}' if D.get('live_ts') else D['base']
    L = [f'<b>📊 ETF 리포트</b>  {esc(when)}',
         f'KODEX 200 {esc(k.get("KODEX 200", "–"))} · 상승/하락 {esc(ud)} '
         f'· 순자산 {esc(k.get("전체 순자산", "–"))}']
    if D.get('hold_note'):
        L.append(f'<i>{esc(D["hold_note"])}</i>')
    return '\n'.join(L)


def _new_holdings(D):
    """1순위 — 새로 편입된 종목."""
    hn, det = D.get('h_new') or [], D.get('new_detail') or []
    if not hn and not det:
        return ''
    L = ['<b>🆕 신규 편입</b>']
    multi = [x for x in hn if x['n'] >= 2]
    if multi:
        L.append('\n<b>여러 ETF가 동시에 담은 종목</b>')
        for x in multi[:10]:
            tag = ' <i>(신규상장·지수편입)</i>' if x.get('event') else ''
            L.append(f'  <b>{esc(x["name"])}</b> — {x["n"]}개 ETF{tag}')
            L.append(f'    <i>{esc(_short(", ".join(x["funds"][:3]), 52))}'
                     + (f' 외 {len(x["funds"])-3}' if len(x['funds']) > 3 else '') + '</i>')
    if det:
        L.append('\n<b>비중을 크게 잡고 들어간 건</b>')
        for x in det[:12]:
            L.append(f'  <b>{x["wt"]:.2f}%</b>  {esc(x["name"])}')
            L.append(f'    ↳ {esc(_short(x["fund"], 34))}')
    return '\n'.join(L)


def _weight_moves(D):
    """1순위 — 보유비중이 크게 바뀐 종목."""
    ups, dns = D.get('wt_up') or [], D.get('wt_down') or []
    if not ups and not dns:
        return ''
    L = ['<b>⚖️ 보유비중 변화</b>',
         '<i>비중(%p) · 괄호는 실제 수량 증감</i>']

    def rows(items, arrow):
        out = []
        for x in items:
            out.append(f'  <b>{x["dpp"]:+.2f}%p</b>  {esc(x["name"])}   '
                       f'{x["pw"]:.2f}% {arrow} {x["cw"]:.2f}%')
            out.append(f'    ↳ {esc(_short(x["fund"], 30))} <i>(수량 {x["qty"]:+.0f}%)</i>')
        return out

    if ups:
        L.append('\n<b>📈 확대</b>')
        L += rows(ups[:10], '→')
    if dns:
        L.append('\n<b>📉 축소</b>')
        L += rows(dns[:10], '→')
    return '\n'.join(L)


def _consensus(D):
    """여러 ETF가 같은 방향으로 움직인 종목 — 개별 액션보다 강한 신호."""
    mv = D.get('h_moves') or []
    if not mv:
        return ''
    L = ['<b>🔁 여러 ETF가 같은 방향으로 움직인 종목</b>']
    for x in mv[:12]:
        ico = '📈' if x['kind'] == 'ADD' else '📉'
        lab = '확대' if x['kind'] == 'ADD' else '축소'
        L.append(f'  {ico} <b>{esc(x["name"])}</b> {x["n"]}개 ETF {lab} '
                 f'<i>(수량 평균 {x["avg"]:+.1f}%)</i>')
    return '\n'.join(L)


def _dropped(D):
    ho, ev = D.get('h_out') or [], D.get('h_events') or []
    if not ho and not ev:
        return ''
    L = []
    if ho:
        L.append('<b>❌ 제외된 종목</b>')
        L.append('<i>제외한 ETF 수 / 그 종목을 담고 있던 ETF 수</i>')
        for x in ho[:12]:
            L.append(f'  <b>{x["n"]} / {x.get("prior", x["n"])}개</b>  {esc(x["name"])}')
            L.append(f'    ↳ {esc(_short(", ".join(x["funds"][:3]), 52))}')
    if ev:
        L.append('\n<b>⚠️ 종목 사유로 빠진 건</b>')
        L.append('<i>보유하던 ETF 전부에서 동시 소멸 — 거래정지·합병·분할 등. '
                 '운용사 매도 판단이 아닙니다</i>')
        for x in ev[:6]:
            L.append(f'  {esc(x["name"])} <code>{esc(x["code"])}</code> — 보유 {x["n"]}개 전부')
    return '\n'.join(L)


def _movers(D):
    up = D.get('up_nl') or [x for x in (D.get('up') or []) if not x.get('lev')]
    dn = D.get('down_nl') or [x for x in (D.get('down') or []) if not x.get('lev')]
    lab = ' <i>(레버리지·인버스 제외)</i>'
    if not up and not dn:                      # 걸러낼 게 없으면 원본을 쓰고 라벨도 바꾼다
        up, dn, lab = D.get('up') or [], D.get('down') or [], ''
    if not up and not dn:
        return ''
    L = [f'<b>📊 당일 등락</b>{lab}']
    if up:
        L.append('\n<b>상승</b>')
        for x in up[:7]:
            L.append(f'  <b>{x["chg"]:+.2f}%</b>  {esc(_short(x["name"], 28))}')
    if dn:
        L.append('\n<b>하락</b>')
        for x in dn[:7]:
            L.append(f'  <b>{x["chg"]:+.2f}%</b>  {esc(_short(x["name"], 28))}')
    return '\n'.join(L)


def _periods(D):
    pr = D.get('periods') or {}
    if not pr:
        return ''
    L = ['<b>📈 기간별 수익률</b> <i>(상위 3)</i>']
    for lab, v in pr.items():
        top = ' · '.join(f'{x["ret"]:+.0f}% {_short(x["name"], 18)}' for x in v['up'][:3])
        L.append(f'  <b>{esc(lab)}</b>  {esc(top)}')
    return '\n'.join(L)


def _flows(D):
    if not D.get('flow_span'):
        return ('<b>💰 자금 유입·이탈</b>\n'
                '<i>순자산 스냅샷이 하루치뿐입니다. 다음 영업일부터 집계됩니다.</i>')
    L = [f'<b>💰 자금 유입·이탈</b> <i>({esc(D["flow_span"][0])} → {esc(D["flow_span"][1])})</i>']
    if D.get('flow_in'):
        L.append('\n<b>유입</b>')
        for x in D['flow_in'][:6]:
            L.append(f'  <b>{_amt(x["amt"])}</b>  {esc(_short(x["name"], 26))} '
                     f'<i>({x["pct"]:+.1f}%)</i>')
    if D.get('flow_out'):
        L.append('\n<b>이탈</b>')
        for x in D['flow_out'][:6]:
            L.append(f'  <b>{_amt(x["amt"])}</b>  {esc(_short(x["name"], 26))} '
                     f'<i>({x["pct"]:+.1f}%)</i>')
    return '\n'.join(L)


def _misc(D):
    L = []
    if D.get('spikes'):
        L.append('<b>🔥 거래 급증</b> <i>(20일 중앙값 대비)</i>')
        for x in D['spikes'][:6]:
            L.append(f'  <b>×{x["mult"]:,.1f}</b>  {esc(_short(x["name"], 26))} '
                     f'<i>{_amt(x["turnover"])}</i>')
    if D.get('new_etf'):
        L.append('\n<b>🆕 신규 상장 ETF</b> <i>(최근 60일)</i>')
        for x in D['new_etf'][:6]:
            L.append(f'  {esc(x["first"])}  {esc(_short(x["name"], 28))} '
                     f'<i>{_amt(x["mktcap"])}</i>')
    return '\n'.join(L)


def build(D, url=None):
    """전체 리포트를 텔레그램 메시지 리스트로 만든다."""
    tail = ''
    if url:
        tail = f'\n<a href="{esc(url)}">웹 대시보드에서 보기</a>'
    secs = [_head(D), _new_holdings(D), _weight_moves(D), _consensus(D), _dropped(D),
            _movers(D), _periods(D), _flows(D), _misc(D)]
    msgs = _pack([s for s in secs if s])
    if msgs and tail:
        msgs[-1] += '\n' + tail
    return msgs

#!/usr/bin/env python3
"""
스윙 시그널 텔레그램 메시지 — signals.json 을 모바일 한 화면으로.

순서는 판단 순서다. 시장 게이트 → 돌파 확인 → 돌파 감시 → 결손 → 고지.
게이트가 먼저인 이유는 원문 #42 가 "그 아래에서는 새로 사지 않는다" 를 종목보다
앞에 두기 때문이다.

한 종목이 두 줄이다. 첫 줄은 무엇이 통과했는지(가격·등락·RS·거래량), 둘째 줄은
다음 날 쓸 가격(돌파가·손절·목표·최대 비중)과 태그. 태그는 **거르지 않은 조건**이라
통과와 섞지 않는다 — 수축·수급·전고점 거래량은 참고, ⚠ 는 반대 근거다.

값이 없으면 만들지 않고 '–' 로 둔다 (CLAUDE.md 2장 1번). 파스 모드와 글자
치환은 report/telegram.py 를 그대로 쓴다.
"""
from . import telegram as TG
from ..engine.signals import CONTRACTION_NAMES

DASH = '–'
LABEL = dict(hist='역사적', w52='52주', d120='120일')  # KBJ ADR 0017
MARKET = dict(KOSPI='코스피', KOSDAQ='코스닥')


def _p(v):
    if v is None:
        return DASH
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:,.2f}'


def _pct(v, sign=True, d=1):
    if v is None:
        return DASH
    return f'{v:+.{d}f}%' if sign else f'{v:.{d}f}%'


def _gate_line(reg):
    idx = (reg or {}).get('index') or {}
    parts, closed = [], []
    for sym in ('KOSPI', 'KOSDAQ'):
        g = idx.get(sym)
        name = MARKET.get(sym, sym)
        if not g:
            parts.append(f'{name} 평균선 {DASH}')
            continue
        side = '위' if g['above'] else '아래'
        parts.append(f'{name} {_p(g["close"])} / {g["ma_n"]}일선 {_p(g["ma"])} {side}')
        if not g['above']:
            closed.append(name)
    lines = ['시장: ' + ' · '.join(parts)]
    nl = (reg or {}).get('new_low') or {}
    if nl.get('pct') is not None:
        trend = ''
        if nl.get('pct_prev') is not None:
            arrow = ('↑ 증가' if nl['pct'] > nl['pct_prev'] else
                     '↓' if nl['pct'] < nl['pct_prev'] else '같음')
            trend = f' ({nl["compare_days"]}일 전 {nl["pct_prev"]:.2f}% {arrow})'
        lines.append(f'52주 신저가 비율 {nl["pct"]:.2f}%{trend}')
    if closed:
        lines.append(f'⛔ {"·".join(closed)} 기준선 아래 — 신규 매수 보류 구간(#42)')
    return lines


def _tags(x):
    t = []
    c = x.get('contraction') or {}
    names = c.get('recent') if x['setup'] == 'breakout' else (c.get('today') or c.get('recent'))
    if names:
        t.append('수축(' + '·'.join(CONTRACTION_NAMES.get(n, n) for n in names) + ')')
    if c.get('dryup') is not None:
        t.append(f'거래량 5/20일 {c["dryup"]:.2f}')
    fl = x.get('flows')
    if fl:
        mark = '✓' if x.get('flows_pass') else '✗'
        when = f'({fl.get("as_of")} 자)' if x.get('flows_stale') else ''
        t.append(f'기관+외국인 {fl["pct"]:+.1f}%{mark}{when}')
    else:
        t.append('수급 –')
    if x.get('vol_over_prior_high') is not None:
        t.append('전고점 거래량 ' + ('↑' if x['vol_over_prior_high'] else '↓'))
    if (x.get('caution') or {}).get('max_ret_top'):
        t.append(f'⚠급등이력({x["caution"]["max_ret"]:+.1f}%, #178)')
    if x.get('gate_open') is False:
        t.append('⛔게이트')
    return ' · '.join(t)


def _risk(x):
    lv = x.get('levels') or {}
    s = [f'손절 −{lv.get("stop_pct")}% {_p(lv.get("stop"))}']
    if lv.get('low_stop') is not None:
        adr = x.get('adr_pct')
        ok = lv.get('low_within_adr')
        note = '' if ok is None else (' ADR 안' if ok else f' ADR {adr:.1f}% 초과')
        s.append(f'당일저가 {_p(lv["low_stop"])}(−{lv["low_stop_pct"]:.1f}%{note})')
    s.append(f'목표 {_p(lv.get("target"))}')
    s.append(f'최대비중 {lv.get("max_weight_pct")}%')
    return ' · '.join(s)


def _breakout(x, i):
    st = f' {x["status"]}' if x.get('status') else ''
    vm = DASH if x.get('vol_mult') is None else f'{x["vol_mult"]:.1f}배'
    head = (f'{i}. *{TG._safe(x["name"])}* {_p(x["close"])} {_pct(x.get("chg_pct"))} · '
            f'{LABEL.get(x["label"], x["label"])}{st} · RS {x["rs_pct"]:.0f} · 거래량 {vm}')
    return [head, '   ' + _risk(x), '   ' + TG._safe(_tags(x))]


def _watch(x, i):
    zone = ' 🔔알림구간' if x.get('in_alert_zone') else ''
    head = (f'{i}. *{TG._safe(x["name"])}* {_p(x["close"])} → 돌파가 {_p(x.get("trigger"))} '
            f'(갭 {x["gap"]:.1f}%, {LABEL.get(x["near_kind"], x["near_kind"])}){zone} · '
            f'RS {x["rs_pct"]:.0f}')
    return [head, '   ' + _risk(x), '   ' + TG._safe(_tags(x))]


def message(sig, sc):
    """signals.json → 발송 본문(레거시 Markdown)."""
    out = [f'*스윙 시그널 — {sig["as_of"]}*',
           '장 마감 확정값 · 다음 거래일 감시용', '']
    out += _gate_line(sig.get('regime'))
    for sec, key, fn, cap in (
            ('돌파 확인 — 52주 이상 신고가 + 정배열 + RS', 'breakout', _breakout,
             sc['output']['max_breakout']),
            ('돌파 감시 — 근접 + 수축 + 정배열 + RS', 'watch', _watch,
             sc['output']['max_watch'])):
        items = sig.get(key) or []
        out += ['', f'■ {sec} ({len(items)})']
        if not items:
            out.append('해당 없음')
            continue
        for i, x in enumerate(items[:cap], 1):
            out += fn(x, i)
        if len(items) > cap:
            out.append(f'외 {len(items) - cap}종목 (signals.json)')
    paper = sig.get('paper') or {}
    if paper:
        out += ['', '■ 페이퍼 누적 — 신호 뒤 실제 가격, 백테스트와 같은 청산']
        for key, nm in (('breakout', '돌파'), ('watch', '감시')):
            p = paper.get(key) or {}
            if not p.get('signals'):
                continue
            done = (f'종료 {p["closed"]}건 · 승률 {p["win_rate"]}% · 평균 {p["mean"]:+.2f}%'
                    if p.get('closed') else '종료 0건')
            live = (f' · 진행 {p["open"]}건 평균 {p["open_mean"]:+.2f}%'
                    if p.get('open') else '')
            out.append(f'{nm}: 신호 {p["signals"]}건({p["since"]}~) · {done}{live}')
    bt = sig.get('backtest') or {}
    if bt.get('breakout'):
        def one(k, nm):
            a, g = (bt.get(k) or {}).get('all') or {}, (bt.get(k) or {}).get('gate_open') or {}
            if not a.get('n'):
                return None
            gate = f', 게이트 위 {g["mean"]:+.2f}%' if g.get('n') else ''
            return f'{nm} PF {a["profit_factor"]} · 평균 {a["mean"]:+.2f}%{gate}'
        parts = [x for x in (one('breakout', '돌파'), one('watch', '감시'),
                             one('base', '대조군')) if x]
        out += ['', f'백테스트({bt.get("test_start")}~{bt.get("as_of")}, 비용 포함): '
                + ' / '.join(parts)]
    miss = sig.get('missing') or []
    if miss:
        out += ['', '결손: ' + TG._safe(' / '.join(miss))]
    out += ['', '_' + TG._safe(sig.get('disclaimer') or '') + '_']
    return '\n'.join(out)

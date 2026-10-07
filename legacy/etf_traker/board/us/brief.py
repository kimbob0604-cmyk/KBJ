#!/usr/bin/env python3
"""
간밤 미국장 브리프 — board.json 하나만 읽어 텍스트를 만든다.

레퍼런스 산출물의 ①~⑧ 골격을 그대로 옮긴 것이다. 여기서는 **계산하지 않는다** —
build 가 만든 값을 배치만 한다. 그래야 화면·텔레그램·노션이 같은 수를 말한다.
값이 없으면 자리를 비우거나 '–' 로 두고, 그럴듯한 숫자를 지어내지 않는다
(CLAUDE.md 2장 1번·3번).

음수 부호는 유니코드 마이너스(−)를 쓴다. 하이픈과 섞이면 표가 흔들린다.
"""
from datetime import date

MINUS = '−'
WEEK = '월화수목금토일'


# ─────────────────────────── 표기 ───────────────────────────
def pct(v, digits=1, sign=True, zero='보합', unit='%'):
    if v is None:
        return '–'
    if zero is not None and round(v, digits) == 0:
        return zero
    s = f'{abs(v):.{digits}f}{unit}'
    if not sign:
        return s
    return ('+' if v > 0 else MINUS) + s


def num(v):
    return '–' if v is None else f'{v:,.0f}'


def money(v):
    """$6.0B · $726M · $12.3K. 자릿수를 바꿔 읽는 사람이 헷갈리지 않게 한 자리."""
    if v is None:
        return '–'
    a = abs(v)
    for unit, div in (('T', 1e12), ('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if a >= div:
            x = v / div
            # $100.0B 은 자릿수만 늘린다. 딱 떨어지면 소수점을 뗀다.
            return f'${x:.0f}{unit}' if abs(x - round(x)) < 0.05 else f'${x:.1f}{unit}'
    return f'${v:.0f}'


def mult(v):
    return '' if v is None else f'×{v:.1f}'


def daylabel(asof):
    d = date.fromisoformat(asof)
    return f'{d.month}/{d.day}({WEEK[d.weekday()]})'


def short(asof):
    d = date.fromisoformat(asof)
    return f'{d.month}/{d.day}'


def _tone(s):
    """① 마지막 줄의 한 마디. 상승 비율만 보고 정한다 — 해석을 늘리지 않는다."""
    r = s.get('up_ratio')
    if r is None:
        return '판정 불가(등락률 미확보)'
    r = round(r)            # 화면에 '60%' 라고 적고 '혼조' 라고 쓰면 안 된다
    if r >= 60:
        return '강세(상승 우위)'
    if r <= 40:
        return '약세(하락 우위)'
    return '혼조(상승·하락 비슷)'


# ─────────────────────────── 절 ───────────────────────────
def head(b):
    src = b.get('source', {}).get('universe', '')
    tag = '잠정' if b.get('provisional', True) else '확정'
    name = {'nasdaq': '나스닥 스크리너'}.get(src, src)
    return [f'🇺🇸 간밤 미국장 신고가·등락 흐름',
            f'{daylabel(b["asof"])} 마감 · {tag}({name})']


def sec1(b):
    s = b['summary']
    d = s.get('newhigh_delta')
    delta = f' (전일 {"+" if (d or 0) >= 0 else MINUS}{abs(d)})' if d is not None else ''
    mega = s.get('mega') or {}
    nh_word = ('신고가 증가' if (d or 0) > 0 else
               '신고가 감소' if (d or 0) < 0 else '신고가 보합')
    strong = s.get('strong') or []
    weak = s.get('weak') or []
    ratio = '–' if s.get('up_ratio') is None else f'{s["up_ratio"]:.0f}%'
    tail = [_tone(s), nh_word]
    if strong:
        tail.append(f'강세 {strong[0]["sector"]} {pct(strong[0]["median"], zero=None)}')
    if weak:
        tail.append(f'약세 {weak[0]["sector"]} {pct(weak[0]["median"], zero=None)}')
    return ['① 한눈에',
            f'신고가 {s["newhigh"]}종목{delta} · 52주 신고가 {s["w52_high"]} vs 신저가 {s["w52_low"]}',
            f'상승 {s["up"]} / 하락 {s["down"]} → 상승 비율 {ratio}'
            f' · 중앙값 {pct(s["median"], 1)}',
            f'±{b.get("move_pct", 5):.0f}% 급등 {s["surge"]} / 급락 {s["plunge"]} · '
            f'{money(b.get("megacap_usd"))}+ {mega.get("n", 0)}개 중 상승 {mega.get("up_count", 0)}',
            '↳ ' + ' · '.join(tail)]


def sec2(b):
    tr = b.get('trend') or []
    if not tr:
        return ['② 5일 흐름', '추이 없음 — 일봉이 하루치뿐이다']
    cols = [short(t['asof']) for t in tr]
    rows = [('신고가', [num(t['newhigh']) for t in tr]),
            ('52주고', [num(t['w52_high']) for t in tr]),
            ('52주저', [num(t['w52_low']) for t in tr]),
            ('상승비', ['–' if t['up_ratio'] is None else f'{t["up_ratio"]:.0f}%' for t in tr]),
            ('중앙값', [pct(t['median'], 1, zero='0.0', unit='') for t in tr])]
    w = [max(len(c), *(len(r[1][i]) for r in rows)) + 2
         for i, c in enumerate(cols)]
    out = ['② 5일 흐름', ' ' * 7 + ''.join(c.rjust(w[i]) for i, c in enumerate(cols))]
    for label, vals in rows:
        out.append(label.ljust(6) + ''.join(v.rjust(w[i]) for i, v in enumerate(vals)))
    return out


def sec3(b):
    secs = [s for s in b.get('sectors') or [] if s.get('newhigh')]
    top = secs[:b.get('sector_top', 6)]
    out = ['③ 섹터 로테이션 (신고가 수 추이 → 오늘 · 당일/5일 중앙 등락)']
    for s in top:
        tr = s.get('trend') or []
        hist = '·'.join(str(x) for x in tr[:-1]) if len(tr) > 1 else ''
        arrow = f'{hist}→{s["newhigh"]}' if hist else f'{s["newhigh"]}'
        out.append(f'▲ {s["sector"]} {arrow} | 당일 {pct(s["median"], zero=None)} · '
                   f'5일 {pct(s["median_5d"], zero=None)}')
    gone = [s for s in b.get('sectors') or []
            if not s.get('newhigh') and (s.get('trend') or [0])[0]]
    if gone:
        out.append('▽ 빠진 섹터 ' + ', '.join(
            f'{s["sector"]} {(s.get("trend") or [0])[0]}→0' for s in gone[:4]))
    my = b.get('my') or {}
    if my.get('tickers'):
        out.append(f'◆ {my.get("label")} {my.get("n_newhigh", 0)} — '
                   + ' '.join(my['tickers']))
    return out


def sec4(b):
    out = ['④ 등락 온도 (중앙값 당일 / 5일 · 상승 비율)']
    for t in b.get('tiers') or []:
        m = t.get('median')
        mark = '▲' if (m or 0) > 0 else ('▼' if (m or 0) < 0 else '·')
        ratio = '–' if t.get('up_ratio') is None else f'{t["up_ratio"]:.0f}%'
        out.append(f'{mark} {t["name"]}  {pct(m, 2)} / {pct(t.get("median_5d"), 1)} · {ratio}')
    s = b['summary']
    if s.get('strong'):
        out.append('강세 ' + ', '.join(f'{x["sector"]} {pct(x["median"], zero=None)}'
                                      for x in s['strong']))
    if s.get('weak'):
        out.append('약세 ' + ', '.join(f'{x["sector"]} {pct(x["median"], zero=None)}'
                                      for x in s['weak']))
    return out


def sec5(b):
    m = b.get('mega') or {}
    out = [f'⑤ 대형주 {money(b.get("megacap_usd"))}+ '
           f'({m.get("n", 0)}개 · 상승 {m.get("up_count", 0)})']
    if m.get('up'):
        out.append('▲ ' + ' / '.join(f'{r["ticker"]} {pct(r["chg_pct"], zero=None)}'
                                     for r in m['up']))
    if m.get('down'):
        out.append('▼ ' + ' / '.join(f'{r["ticker"]} {pct(r["chg_pct"], zero=None)}'
                                     for r in m['down']))
    return out


def sec6(b):
    out = ['⑥ 연속·신규 신고가']
    st = b.get('streaks') or []
    if st:
        n = b.get('streak_rows', 10)
        head_rows = ', '.join(f'{r["ticker"]} {r["streak"]}일' for r in st[:n])
        rest = f' 외 {len(st) - n}' if len(st) > n else ''
        out.append(f'연속 {head_rows}{rest}')
    fr = b.get('fresh52') or []
    if fr:
        n = b.get('fresh_rows', 10)
        rows = ', '.join(f'{r["ticker"]} {pct(r["chg_pct"], zero=None)}' for r in fr[:n])
        out.append(f'52주 첫 진입(최근 {b.get("fresh_days", 5)}일 중) {len(fr)} — {rows}')
    if len(out) == 1:
        out.append('연속·첫 진입 없음')
    return out


def sec7(b):
    out = ['⑦ 52주 신고가 대장 (거래대금순)']
    for r in b.get('leaders') or []:
        ind = f' {r["industry"]}' if r.get('industry') else ''
        out.append(f'▲ {r["ticker"]} {pct(r["chg_pct"], zero=None)}{ind} · '
                   f'{money(r.get("turnover"))} {mult(r.get("turnover_mult"))}'.rstrip())
    if len(out) == 1:
        out.append('52주 신고가 없음')
    return out


def _group_line(g, mark):
    n = f' {g["n"]}' if g['n'] > 1 else ''
    rows = ', '.join(f'{r["ticker"]} {pct(r["chg_pct"], zero=None)}' for r in g['rows'])
    return f'{mark} {g["industry"]}{n} — {rows}'


def sec8(b):
    lim = money(b.get('movers_min_mktcap_usd'))
    out = [f'⑧ 등락 상위 (시총 {lim}+)']
    mv = b.get('movers') or {}
    for g in (mv.get('up') or [])[:3]:
        out.append(_group_line(g, '▲'))
    for g in (mv.get('down') or [])[:5]:
        out.append(_group_line(g, '▼'))
    return out


def sec_list(b, rows):
    """📋 신고가 전 종목 — 섹터별. 티커만 적는다. 이름은 화면에서 본다."""
    by = {}
    for r in rows:
        if r.get('label'):
            by.setdefault(r.get('sector') or '미분류', []).append(r)
    out = ['📋 신고가 전 종목 (섹터별)']
    for sec, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda r: -(r.get('turnover') or 0))
        out.append(f'{sec} {len(items)}: ' + ' '.join(r['ticker'] for r in items))
    if len(out) == 1:
        out.append('신고가 없음')
    return out


def tail(b):
    u = b.get('universe') or {}
    f = u.get('filters') or {}
    basis = '종가 기준' if b.get('basis') == 'close' else '고가 기준'
    return [f'{basis} · 유니버스 {u.get("n", 0):,}종목'
            f'(시총 {money(f.get("min_mktcap_usd"))}↑·거래대금 {money(f.get("min_turnover_usd"))}↑'
            f'·${f.get("min_price_usd", 0):.0f}↑)']


def render(board, rows=None, cfg=None):
    """board.json (+ universe rows) → 브리프 텍스트."""
    b = dict(board)
    if cfg:
        br = cfg['brief']
        b.setdefault('move_pct', br['move_pct'])
        b['megacap_usd'] = br['megacap_usd']
        b['movers_min_mktcap_usd'] = br['movers_min_mktcap_usd']
        b['sector_top'] = br['sector_top']
        b['streak_rows'] = br['streak_rows']
        b['fresh_rows'] = br['fresh_rows']
        b['fresh_days'] = br['fresh_days']
    parts = [head(b), sec1(b), sec2(b), sec3(b), sec4(b), sec5(b),
             sec6(b), sec7(b), sec8(b)]
    if rows:
        parts.append(sec_list(b, rows))
    parts.append(tail(b))
    miss = b.get('missing') or []
    if miss:
        parts.append(['⚠ 빠진 데이터'] + [f'· {m}' for m in miss])
    return '\n\n'.join('\n'.join(p) for p in parts)


# ─────────────────────────── 텔레그램 서식 ───────────────────────────
# 평문으로 보내면 ②의 표가 무너진다. 텔레그램 기본 글꼴은 가변폭이라 자릿수를
# 맞춰 놓은 열이 어긋나고, 섹션 머리가 본문과 같은 굵기로 흘러 읽는 순서가 안 보인다.
# 그래서 발송본만 HTML 로 만든다 — 표는 <pre>(고정폭), 머리는 <b>.
#
# 텔레그램 HTML 은 <b> <i> <u> <s> <code> <pre> <a> 만 안다. 그 밖의 태그는
# 400 으로 거절당하므로 본문의 & < > 는 반드시 먼저 이스케이프한다.
import unicodedata as _ud


def esc(s):
    return (str(s if s is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _w(s):
    """표시 너비. 한글·전각은 두 칸을 차지한다 — 이걸 세지 않으면 고정폭에서도 어긋난다."""
    return sum(2 if _ud.east_asian_width(c) in 'WF' else 1 for c in str(s))


def _pad(s, width, right=True):
    gap = max(0, width - _w(s))
    return (' ' * gap + str(s)) if right else (str(s) + ' ' * gap)


def _n(v):
    """세 자리 구분. 1149 는 한눈에 안 읽힌다."""
    return '–' if v is None else f'{v:,}'


def trend_block(b):
    """② 5일 흐름 — 고정폭 표. 라벨은 한글 그대로 두고 너비로 맞춘다."""
    tr = b.get('trend') or []
    if not tr:
        return None
    cols = [short(t['asof']) for t in tr]
    rows = [('신고가', [_n(t['newhigh']) for t in tr]),
            ('52주고', [_n(t['w52_high']) for t in tr]),
            ('52주저', [_n(t['w52_low']) for t in tr]),
            ('상승비', ['–' if t['up_ratio'] is None else f'{t["up_ratio"]:.0f}%' for t in tr]),
            ('중앙값', [pct(t['median'], 1, zero='0.0', unit='') for t in tr])]
    lab = max(_w(r[0]) for r in rows)
    w = [max(_w(c), *(_w(r[1][i]) for r in rows)) + 2 for i, c in enumerate(cols)]
    out = [' ' * lab + ''.join(_pad(c, w[i]) for i, c in enumerate(cols))]
    for label, vals in rows:
        out.append(_pad(label, lab, right=False)
                   + ''.join(_pad(v, w[i]) for i, v in enumerate(vals)))
    return '\n'.join(out)


def _sec(title, lines):
    """한 절. 제목만 굵게, 빈 절은 아예 내보내지 않는다."""
    body = [x for x in lines if x]
    if not body:
        return None
    return f'<b>{esc(title)}</b>\n' + '\n'.join(esc(x) for x in body)


def telegram_parts(board, rows=None, cfg=None):
    """발송본을 **절 단위 조각**으로. 조각을 쪼개지 않는 것이 분할의 전제다."""
    b = dict(board)
    if cfg:
        br = cfg['brief']
        b.setdefault('move_pct', br['move_pct'])
        for k in ('megacap_usd', 'movers_min_mktcap_usd', 'sector_top',
                  'streak_rows', 'fresh_rows', 'fresh_days'):
            b[k] = br[k]
    s = b['summary']
    mega = s.get('mega') or {}
    u = b.get('universe') or {}
    f = u.get('filters') or {}
    basis = '종가 기준' if b.get('basis') == 'close' else '고가 기준'
    d = s.get('newhigh_delta')
    delta = '' if d is None else f' (전일 {"+" if d >= 0 else MINUS}{abs(d)})'
    ratio = '–' if s.get('up_ratio') is None else f'{s["up_ratio"]:.0f}%'

    parts = [
        f'🇺🇸 <b>간밤 미국장 신고가</b> · {esc(daylabel(b["asof"]))} 마감\n'
        f'<i>{esc(basis)} · 유니버스 {u.get("n", 0):,}종목 · 잠정</i>',
        _sec('① 한눈에', [
            f'신고가 {_n(s["newhigh"])}종목{delta}',
            f'52주 신고가 {_n(s["w52_high"])} · 신저가 {_n(s["w52_low"])}',
            f'상승 {_n(s["up"])} / 하락 {_n(s["down"])} → {ratio} · 중앙값 {pct(s.get("median"), 1)}',
            f'±{b.get("move_pct", 5):.0f}% 급등 {_n(s["surge"])} / 급락 {_n(s["plunge"])}',
            f'{money(b.get("megacap_usd"))}+ {_n(mega.get("n", 0))}개 중 상승 {_n(mega.get("up_count", 0))}',
            '↳ ' + ' · '.join(x for x in (
                _tone(s),
                (f'강세 {s["strong"][0]["sector"]} {pct(s["strong"][0]["median"], zero=None)}'
                 if s.get('strong') else ''),
                (f'약세 {s["weak"][0]["sector"]} {pct(s["weak"][0]["median"], zero=None)}'
                 if s.get('weak') else '')) if x),
        ]),
    ]
    tb = trend_block(b)
    if tb:
        parts.append('<b>② 5일 흐름</b>\n<pre>' + esc(tb) + '</pre>')
    parts += [
        _sec('③ 섹터 로테이션', [x for x in sec3(b)[1:]]),
        _sec('④ 등락 온도', [x for x in sec4(b)[1:]]),
        _sec(sec5(b)[0], sec5(b)[1:]),
        _sec('⑥ 연속·신규 신고가', sec6(b)[1:]),
        _sec('⑦ 52주 신고가 대장', sec7(b)[1:]),
        _sec(sec8(b)[0], sec8(b)[1:]),
    ]
    if rows:
        parts.append(_sec('📋 신고가 전 종목', sec_list(b, rows)[1:]))
    parts.append(f'<i>시총 {money(f.get("min_mktcap_usd"))}↑ · '
                 f'거래대금 {money(f.get("min_turnover_usd"))}↑ · '
                 f'${f.get("min_price_usd", 0):.0f}↑</i>')
    miss = b.get('missing') or []
    if miss:
        parts.append(_sec('⚠ 빠진 데이터', [f'· {m}' for m in miss]))
    return [p for p in parts if p]


def telegram_html(board, rows=None, cfg=None):
    """발송본 전체. 길이 제한은 `telegram_chunks` 가 본다."""
    return '\n\n'.join(telegram_parts(board, rows, cfg))


TG_LIMIT = 3800          # 4,096 상한에서 조각 표시와 멀티바이트 여유를 뺀다


def telegram_chunks(board, rows=None, cfg=None, limit=TG_LIMIT):
    """발송 조각. **절 경계에서만 나눈다.**

    텔레그램 HTML 은 태그가 안 닫히면 메시지를 통째로 거절한다(400). 줄 단위로
    자르면 ②의 <pre> 가 두 조각으로 갈려 그 일이 벌어진다. 그래서 절을 통째로
    담고, 한 절이 혼자 상한을 넘을 때만(📋 전 종목 목록이 그렇다) 그 절 안에서
    줄 경계로 자른다 — 그 절은 머리줄에 <b>…</b> 가 닫혀 있어 갈려도 안전하다.
    """
    out, cur = [], ''
    for part in telegram_parts(board, rows, cfg):
        if len(part) > limit:
            if cur:
                out.append(cur)
                cur = ''
            buf = ''
            for line in part.split('\n'):
                if buf and len(buf) + 1 + len(line) > limit:
                    out.append(buf)
                    buf = line
                else:
                    buf = f'{buf}\n{line}' if buf else line
            if buf:
                cur = buf
            continue
        if not cur:
            cur = part
        elif len(cur) + 2 + len(part) <= limit:
            cur += '\n\n' + part
        else:
            out.append(cur)
            cur = part
    if cur:
        out.append(cur)
    n = len(out)
    if n > 1:
        out = [f'<i>({i}/{n})</i>\n{c}' for i, c in enumerate(out, 1)]
    return out

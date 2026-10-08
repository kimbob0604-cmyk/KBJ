#!/usr/bin/env python3
"""
대시보드 렌더 — state/YYYYMMDD/*.json 만 읽어 단일 HTML 을 만든다.

DB 를 모른다. 엔진이 남긴 JSON 이 유일한 입력이다 (CLAUDE.md 3장).
디자인은 web/prototype.html 을 따른다. 색은 국내 관례대로 상승 적색·하락 청색.

표는 서버에서 그린다. 자바스크립트는 필터 토글과 히트맵에만 쓴다. 스크립트가
죽어도 신고가 표는 읽힌다.
"""
import html
import json
import os
import re

from ..engine import kinds as K


# 라벨 → CSS 클래스. 알 수 없는 라벨은 기본 칩으로 떨어진다.
TAG = {'hist': 'tag-hist', 'w52': 'tag-52', 'd120': 'tag-60'}  # KBJ ADR 0017 — 최하위 축 d120(색 클래스는 그대로)


def _lookback_note(th):
    """창 설명 한 줄 — KBJ ADR 0017: 거래일 창(lookback_trading_days)·달력 창(lookback_calendar_days).
    옛 키(lookback — 직전 N영업일)도 읽는다."""
    parts = [f"{LABEL_KO.get(k, k)} {v}영업일" for k, v in (th.get('lookback') or {}).items()]
    parts += [f"{LABEL_KO.get(k, k)} {v}거래일" for k, v in (th.get('lookback_trading_days') or {}).items()]
    parts += [f"{LABEL_KO.get(k, k)} 달력 {v}일" for k, v in (th.get('lookback_calendar_days') or {}).items()]
    return ' / '.join(parts)
LABEL_KO = {}          # newhigh.json 의 labels 로 매 렌더마다 채운다
CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'board.css')

# 글꼴 CDN. 없어도 board.css 의 대체 글꼴로 떨어지므로 화면은 성립한다.
FONT_CSS = (
    'https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9'
    '/dist/web/static/pretendard.css',
    'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:'
    'wght@400;500;600&display=swap',
)

# 인라인 SVG. 파일을 따로 두지 않아 사이트 어디서 열어도 404 가 안 난다.
FAVICON = ('data:image/svg+xml,'
           "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
           "%3Crect width='16' height='16' rx='3' fill='%23232B36'/%3E"
           "%3Cpath d='M3 11.5 6.5 7l3 2.5L13 4.5' stroke='%23fff' "
           "stroke-width='1.6' fill='none' stroke-linecap='round' "
           "stroke-linejoin='round'/%3E%3C/svg%3E")


def e(v):
    return html.escape('' if v is None else str(v))


def pct(v, digits=2, suffix='%'):
    if v is None:
        return '<span class="mut">–</span>'
    c = 'up' if v > 0 else ('down' if v < 0 else '')
    return f'<span class="num {c}">{v:+.{digits}f}{suffix}</span>'


def plain(v, digits=2, suffix='%'):
    if v is None:
        return '<span class="mut">–</span>'
    return f'<span class="num">{v:.{digits}f}{suffix}</span>'


def eok(v):
    """억원 표기. 1조 이상은 조로 접는다."""
    if v is None:
        return '<span class="mut">–</span>'
    if abs(v) >= 10000:
        return f'<span class="num">{v/10000:,.2f}조</span>'
    return f'<span class="num">{v:,.0f}억</span>'


def mult(v):
    return '<span class="mut">–</span>' if v is None else f'<span class="num">{v:,.1f}배</span>'


def naver_url(code):
    return f'https://finance.naver.com/item/main.naver?code={code}'


# ─────────────────────────── 헤더 ───────────────────────────
def _strip(market):
    if not market:
        return '<li><span class="k">지수</span><span class="v mut">수집 실패</span></li>'
    out = []
    for sym in ('KOSPI', 'KOSDAQ'):
        x = (market.get('indices') or {}).get(sym)
        if not x:
            continue
        # 지수는 받았는데 기준일 이하 행이 없으면 close 가 None 으로 온다.
        # 그대로 포맷하면 렌더가 통째로 죽는다 — 한 칸 때문에 화면 전체를 잃는다.
        v = (f'{x["close"]:,.2f}' if x.get('close') is not None else '수집 실패')
        cls = 'num' if x.get('close') is not None else 'mut'
        out.append(f'<li><span class="k">{e(x.get("label") or sym)}</span>'
                   f'<span class="v {cls}">{v}</span>'
                   f'<span class="d">{pct(x.get("chg_pct"))}</span></li>')
    fx = market.get('fx')
    if fx and fx.get('value') is not None:
        out.append(f'<li><span class="k">USD/KRW</span>'
                   f'<span class="v num">{fx["value"]:,.1f}</span>'
                   f'<span class="d">{pct(fx.get("chg_pct"))}</span></li>')
    else:
        out.append('<li><span class="k">USD/KRW</span>'
                   '<span class="v mut">수집 실패</span></li>')
    out.extend(_flow_items(market.get('flows')))
    return ''.join(out)


# 헤더에 띄울 투자자 구분과 순서. 레퍼런스 코멘트의 첫 단락 주어들이다.
FLOW_ORDER = ('기타법인', '기관계', '기관', '외국인', '개인')


def _flow_items(flows):
    """투자자별 수급. 받아 왔으면 그 값을 쓴다.

    예전에는 이 자리에 '소스 미확보'를 하드코딩해 놓아서, KIS 로 받아 온 값이
    화면에 한 글자도 안 나왔다. 데이터가 있는데 없다고 적는 건 없는 걸 있다고
    적는 것만큼 나쁘다.
    """
    if not flows:
        return ['<li><span class="k">투자자별 수급</span>'
                '<span class="v mut">소스 미확보</span></li>']
    # 시장이 여럿이면 코스피를 헤더에 쓴다. 전체는 코멘트가 다룬다.
    d = flows.get('KOSPI') or flows.get('0001') or next(iter(flows.values()), None)
    by = (d or {}).get('by_date') or {}
    if not by:
        return ['<li><span class="k">투자자별 수급</span>'
                '<span class="v mut">값 없음</span></li>']
    cur = by[max(by)]
    unit = (d or {}).get('unit') or '억원'
    out = []
    for who in FLOW_ORDER:
        v = cur.get(who)
        if v is None:
            continue
        amt = f'{v/10000:+,.2f}조' if abs(v) >= 10000 else f'{v:+,.0f}{unit}'
        cls = 'up' if v > 0 else ('down' if v < 0 else '')
        out.append(f'<li><span class="k">{e(who)}</span>'
                   f'<span class="v num {cls}">{amt}</span></li>')
    return out or ['<li><span class="k">투자자별 수급</span>'
                   '<span class="v mut">구분을 못 읽음</span></li>']


def _downloads(asof):
    """엑셀 내려받기. 사이트 구조(web/site.py)의 x/ 아래에 있다."""
    if not asof:
        return ''
    return (f'<a class="dl" href="x/rankings-{e(asof)}.xlsx" download>'
            f'랭킹 엑셀</a>')


def _banner(items, title='빠진 데이터', cls='warn'):
    """경고 배너. 오류는 빨강(warn), 설계상 제외는 회색(info)으로 나눠 싣는다.

    둘을 한 색으로 칠하면 매일 같은 문구로 나오는 '유동성 하한 제외' 옆에서
    진짜 실패가 묻힌다. 감추는 것이 아니라 눈이 갈 곳을 하나로 만드는 것이다.
    """
    if not items:
        return ''
    li = ''.join(f'<li>{e(m)}</li>' for m in items)
    return (f'<div class="banner {cls}"><b>{e(title)}</b>'
            f'<ul class="miss">{li}</ul></div>')


# ─────────────────────────── 신고가 탭 ───────────────────────────
def _pills(counts_high, n_near, shown, counts_close=None, other=None, turn_min=None,
           dflt='hi'):
    """라벨 알약. 두 기준의 종목수를 함께 싣고 화면이 골라 보여 준다.

    수를 하나만 실으면 기준을 바꿨을 때 알약의 숫자가 표와 어긋난다.

    거래대금 하한은 **문구와 판정이 같은 값에서 나와야 한다**. 알약에 '50억' 이라
    적어 놓고 자바스크립트가 다른 숫자로 거르면 화면이 거짓말을 한다 (D-074).
    """
    cc, ch = counts_close or {}, counts_high or {}
    # 처음 보이는 수는 **기본 기준**의 것이어야 한다. 표가 종가 기준으로 그려져
    # 있는데 알약만 고가 수를 들고 있으면 둘이 어긋난다.
    first = cc if dflt == 'cl' else ch
    p = []
    for k in shown:
        p.append(f'<button class="pill" data-kind="{k}" aria-pressed="true" '
                 f'data-c-hi="{ch.get(k, 0)}" data-c-cl="{cc.get(k, 0)}">'
                 f'{e(LABEL_KO.get(k, k))} <span class="c">{first.get(k, 0)}</span></button>')
    tm = float(turn_min or 0)
    if tm:
        p.append(f'<button class="pill" data-filter="turnover" aria-pressed="false" '
                 f'data-min="{tm:g}">거래대금 {tm:,.0f}억↑</button>')
    # 우선주·스팩·리츠는 산업 이야기와 무관하게 표를 채운다. 빼지 않고 끌 수 있게 둔다.
    if other:
        n = sum(other.values())
        why = ' · '.join(f'{K.LABEL[k]} {v}' for k, v in sorted(other.items()))
        p.append('<button class="pill" data-filter="common" aria-pressed="false" '
                 f'title="{e(why)} 를 숨깁니다">보통주만 <span class="c">{n}</span></button>')
    p.append(f'<span class="pill-note">근접 {n_near}</span>')
    return f'<div class="pills">{"".join(p)}</div>'


def _cluster_banner(events, tmeta):
    cl = [x for x in events if x['type'] == 'proximity_cluster']
    if not cl:
        return ''
    parts = []
    for x in sorted(cl, key=lambda y: -y['severity'])[:4]:
        nm = (tmeta.get(x['theme']) or {}).get('name') or x['theme']
        parts.append(f'{e(nm)} 근접 {x["severity"]}종목')
    return ('<div class="banner"><b>돌파 임박</b><span>' + ' · '.join(parts) +
            ' — 탐지 9 근접 클러스터</span></div>')


def _basis_cell(x):
    """구분 칸 — 고가/종가 두 기준의 라벨을 모두 담고 화면이 하나를 고른다.

    선택한 기준의 라벨이 앞에 오고, 다른 기준의 결과가 옅은 표시로 뒤따른다.
    서버에서 둘 다 그려 두고 토글은 보이기만 바꾼다 — 자바스크립트가 라벨을
    만들어 내면 화면과 데이터가 갈라질 자리가 생긴다.
    """
    # 기준은 이름으로 읽는다. label 은 기본 기준이라 설정에 따라 가리키는
    # 기준이 바뀐다 — 여기서 그걸 '고가' 로 짐작하면 토글이 거짓말을 한다.
    hi = (x.get('high_basis') or {}).get('label')
    cl = (x.get('close_basis') or {}).get('label')
    return (f'<span data-b="hi">{_tag(hi)}{_other(cl, "종가")}</span>'
            f'<span data-b="cl" hidden>{_tag(cl)}{_other(hi, "고가")}</span>')


def _tag(k):
    if not k:
        return '<span class="mut">–</span>'
    return (f'<span class="tag {TAG.get(k, "tag-60")}">'
            f'{e(LABEL_KO.get(k, k))}</span>')


def _other(k, word):
    """다른 기준의 결과. 판정을 뒤집는 게 아니라 조건을 밝히는 것이라 옅게 둔다."""
    if k:
        return (f'<span class="tag tag-mut" title="{word} 기준으로는 '
                f'{e(LABEL_KO.get(k, k))}까지 갱신했다">'
                f'{word} {e(LABEL_KO.get(k, k))}</span>')
    return (f'<span class="tag tag-mut" title="{word} 기준으로는 기준 최고가를 '
            f'넘지 못했다">{word} 미달</span>')


def _close_mark(x):
    """종가 기준으로도 갱신했는지 같은 줄에 적는다.

    기본 판정은 고가 기준이다(CLAUDE.md 4장). 그런데 증권사 스크리너 상당수는
    종가 기준이라, 고가로만 뚫고 밀린 종목이 목록에 있으면 '왜 이게 신고가냐'가
    된다. 판정을 바꾸지 않고 조건을 밝힌다.
    """
    cb = (x.get('close_basis') or {}).get('label')
    if cb == (x.get('high_basis') or {}).get('label'):
        return ''
    if cb:
        return (f'<span class="tag tag-mut" title="종가 기준으로는 '
                f'{e(LABEL_KO.get(cb, cb))}까지만 갱신했다">'
                f'종가 {e(LABEL_KO.get(cb, cb))}</span>')
    return ('<span class="tag tag-mut" title="장중 고가로는 갱신했지만 종가로는 '
            '기준 최고가를 넘지 못했다">종가 미달</span>')


def _kind_of(x):
    return x.get('kind') or K.of(x.get('code'), x.get('name'))


def _kind_tag(x):
    """보통주가 아니면 이름 옆에 작게 적는다. 보통주는 아무것도 붙이지 않는다.

    표를 뒤덮지 않으면서 '이건 우선주라 얇다' 를 그 자리에서 알려 주는 게 목적이다.
    """
    k = _kind_of(x)
    if k == K.COMMON:
        return ''
    return (f'<span class="tag tag-mut" title="{e(K.LABEL[k])} — '
            f'판정 근거는 engine/kinds.py 참고">{e(K.LABEL[k])}</span>')


FLOWS = {}          # 코드 → 종목별 수급. build() 가 매 렌더마다 채운다
TRIGGERS = {}       # 코드 → 종목별 재료(triggers.json by_code). build() 가 채운다


def _trigger_tag(code):
    """이름 옆의 '재료' 표식. 마우스를 올리면 제목·매체가 보인다.

    52주 이상 종목만 모으므로 나머지 행에는 아무것도 붙지 않는다. 링크는 payload
    (실시간 보드)가 싣고, 구운 표에는 툴팁만 둔다 — 표에 열을 하나 더 만들면
    모바일에서 다른 열이 밀린다.
    """
    e_ = TRIGGERS.get(code) or {}
    items = e_.get('items') or []
    if not items:
        return ''
    tip = ' / '.join(f'{it.get("publisher") or "?"}: {it.get("title") or ""}'
                     for it in items[:3])
    return f'<span class="tag tag-trig" title="{e(tip)}">재료 {len(items)}</span>'


def net_amt(v):
    """순매수 금액(억원) 표기.

    1억 미만을 '억' 으로 반올림하면 `-0억` 이 되어 부호만 남고 값이 사라진다.
    그 구간은 만원으로 내려 적는다. 0 에는 부호를 붙이지 않는다(-0.0 도 0 이다).
    """
    a = abs(v)
    sign = '+' if v > 0 else ('-' if v < 0 else '')
    if a >= 10000:
        return f'{sign}{a / 10000:,.2f}조'
    if a >= 100:
        return f'{sign}{a:,.0f}억'
    if a >= 1:
        return f'{sign}{a:.1f}억'
    return f'{sign}{a * 10000:,.0f}만'


def _flow_cell(code):
    """종목별 순매수. 52주 이상만 받으므로 나머지 행은 **빈칸**이 맞다.

    0 으로 채우면 '안 샀다' 는 없는 사실이 된다 (CLAUDE.md 2장 1번).
    """
    # 지역 이름을 `e` 로 두면 모듈의 이스케이프 함수 e() 를 가린다.
    fl = FLOWS.get(code)
    if not fl:
        return '<td class="r"></td>'
    unit = fl.get('unit')
    parts = []
    for who in ('기관', '외국인', '개인'):
        v = fl.get(who)
        if v is None:
            continue
        t = f'{v:+,.0f}주' if unit == '주' else net_amt(v)
        c = 'up' if v > 0 else ('down' if v < 0 else '')
        parts.append(f'<span class="fl"><i>{who[0]}</i>'
                     f'<b class="num {c}">{t}</b></span>')
    if not parts:
        return '<td class="r"></td>'
    tip = []
    # 기준일이 다른 값이면 그 사실을 적는다. 아무 말이 없으면 다른 날의 사실이
    # 같은 날로 읽힌다.
    d = fl.get('as_of')
    stale = bool(d and d != FLOWS.get('_as_of'))
    if stale:
        tip.append(f'{e(d)} 자 값입니다 — 기준일 값이 아직 없습니다')
    # 네이버 폴백은 순매매량(주)이고 금액은 종가 환산 **추정**이다 (D-083). 칸에
    # 출처를 적고 추정 금액은 툴팁에 '추정' 을 붙여 둔다 — 텔레그램과 같은 규칙.
    # 조용히 바꾸지 않는다 (CLAUDE.md 2장 6번).
    src = '네이버' if fl.get('source') == 'naver' else ''
    if src:
        est = fl.get('amt_est') if isinstance(fl.get('amt_est'), dict) else {}
        est = est if est.get('is_estimate') else {}
        ests = [f'{who} {net_amt(est[who])}' for who in ('기관', '외국인', '개인')
                if isinstance(est.get(who), (int, float))]
        tip.append('네이버 순매매량(주)'
                   + (' · 종가 환산 추정 ' + ' · '.join(ests) if ests else ''))
    ttl = f' title="{" — ".join(tip)}"' if tip else ''
    star = '<span class="mut">*</span>' if stale else ''
    tag = f'<span class="mut src">({src})</span>' if src else ''
    return f'<td class="r flows"{ttl}>{"".join(parts)}{star}{tag}</td>'


def _row_achieved(x):
    tag = _basis_cell(x)
    st = (f'<span class="tag tag-new">{e(x["status"])}</span>' if x.get('status') else '')
    sus = ('<span class="tag tag-warn" title="수정주가 미반영 의심 — 역사적 판정 제외">주의</span>'
           if x.get('suspect') else '')
    cl = (x.get('close_basis') or {}).get('label') or ''
    hi = (x.get('high_basis') or {}).get('label') or ''
    return (f'<tr data-hi="{hi}" data-cl="{cl}"'
            f' data-skind="{_kind_of(x)}"'
            f' data-turnover="{x.get("turnover") or 0:.0f}"'
            f' data-mktcap="{x.get("mktcap") or 0:.0f}">'
            f'<td><a href="{naver_url(x["code"])}" target="_blank" rel="noopener">'
            f'{e(x["name"])}</a><span class="code">{e(x["code"])}</span>'
            f'{_kind_tag(x)}{sus}{_trigger_tag(x.get("code"))}</td>'
            f'<td class="stage">{e(x.get("stage") or "")}</td>'
            f'<td class="kind">{tag}</td><td>{st}</td>'
            f'<td class="r">{pct(x.get("chg_pct"))}</td>'
            f'<td class="r">{eok(x.get("turnover"))}</td>'
            f'<td class="r">{mult(x.get("vol_mult"))}</td>'
            + _flow_cell(x.get('code')) + '</tr>')


def _achieved_table(rows, tmeta):
    if not rows:
        return '<div class="card empty">오늘 신고가를 낸 종목이 없습니다.</div>'
    by = {}
    for x in rows:
        by.setdefault(x.get('theme_name') or '미분류', []).append(x)
    order = sorted(by.items(),
                   key=lambda kv: (kv[0] == '미분류', -sum(y.get('turnover') or 0
                                                         for y in kv[1])))
    body = []
    for name, items in order:
        items.sort(key=lambda y: -(y.get('turnover') or 0))
        turn = sum(y.get('turnover') or 0 for y in items)
        t = f'{turn/10000:,.2f}조' if turn >= 10000 else f'{turn:,.0f}억'
        body.append(f'<tr class="theme-row"><td colspan="8">{e(name)}'
                    f'<span>{len(items)}종목 · {t}</span></td></tr>')
        body.extend(_row_achieved(x) for x in items)
    return ('<table><thead><tr><th>종목</th><th>단계</th><th>구분</th><th>상태</th>'
            '<th class="r">등락률</th><th class="r">거래대금</th>'
            '<th class="r">거래량</th><th class="r">순매수</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _proximity_table(rows):
    if not rows:
        return '<div class="card empty">근접 조건을 만족한 종목이 없습니다.</div>'
    body = []
    for x in rows:
        k = x['near_kind']
        rv = (x.get('resistance') or {}).get(k)
        rl = (x.get('resistance_label') or {}).get(k)
        res = ('<span class="mut">–</span>' if rv is None
               else f'{e(rl)} <span class="code num">{rv:,.1f}</span>')
        # 테마가 없으면 빈칸이 아니라 '미분류'. 빈칸은 값이 없는 건지 화면이
        # 깨진 건지 구분이 안 된다.
        theme = ' · '.join(y for y in (x.get('theme_name'), x.get('stage')) if y) \
            or '미분류'
        body.append(
            f'<tr data-skind="{_kind_of(x)}"'
            f' data-turnover="{x.get("turnover") or 0:.0f}"'
            f' data-mktcap="{x.get("mktcap") or 0:.0f}">'
            f'<td><a href="{naver_url(x["code"])}" target="_blank" rel="noopener">'
            f'{e(x["name"])}</a><span class="code">{e(x["code"])}</span>'
            f'{_kind_tag(x)}</td>'
            f'<td class="stage">{e(theme)}</td>'
            f'<td><span class="tag {TAG.get(k, "tag-60")}">'
            f'{e(LABEL_KO.get(k, k))}</span></td>'
            f'<td class="r">{plain(x.get("near_gap"), 1)}</td>'
            f'<td class="r">{_narrow(x.get("near_narrow5"))}</td>'
            f'<td class="r">{res}</td>'
            f'<td class="r">{mult(x.get("vol_mult"))}</td></tr>')
    return ('<table><thead><tr><th>종목</th><th>테마 · 단계</th><th>기준</th>'
            '<th class="r">갭</th><th class="r">5일 축소</th>'
            '<th class="r">저항두께</th><th class="r">거래량</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


# ─────────────────────────── 랭킹 탭 ───────────────────────────
def _cell(kind, txt, raw):
    """수치 칸. 색은 kind 가 정하고, 계산 안 된 값은 0 이 아니라 '–' 로 둔다."""
    if txt is None:
        return '<td class="r mut">–</td>'
    if kind == 'pct':
        c = 'up' if (raw or 0) > 0 else ('down' if (raw or 0) < 0 else '')
    elif kind == 'ratio':
        c = 'up' if (raw or 0) > 100 else ('down' if (raw or 0) < 100 else '')
    else:
        c = ''
    # 정렬용 원값. 화면 문자열(2,006억 · 3.36조 · +9.65%)을 다시 파싱하지 않는다.
    dv = f' data-v="{raw}"' if isinstance(raw, (int, float)) and raw == raw else ''
    return f'<td class="r num {c}"{dv}>{e(txt)}</td>'


def _rank_delta(x, has_prev):
    """순위 옆의 전일 대비 변동. 올라가면 ▲, 내려가면 ▼.

    어제 표에 없던 섹터는 '–' 다. 0 으로 적으면 '변동 없음' 이라는 없는 사실을
    단정하게 된다 (CLAUDE.md 2장 1번). 어제 표 자체가 없으면 칸을 비운다.
    """
    if not has_prev:
        return ''
    d = x.get('rank_delta')
    if d is None:
        return ('<span class="rk mut" title="어제 이 표에 없던 섹터입니다">–</span>')
    if d == 0:
        return '<span class="rk mut" title="전일과 같은 순위">—</span>'
    was = x.get('rank_prev')
    arrow, cls = ('▲', 'up') if d > 0 else ('▼', 'down')
    return (f'<span class="rk {cls}" title="전일 {was}위 → 오늘 {x["rank"]}위">'
            f'{arrow}{abs(d)}</span>')


def _sector_board(b):
    """표 1·2 — 섹터 순위 + 각 섹터 1~5등."""
    has_prev = bool(b.get('has_prev_rank'))
    head = (f'<tr><th>섹터</th><th class="r">{e(b["ret_label"])} 순위</th>'
            f'<th class="r">{e(b["ret_label"])}</th><th>브레드스</th>'
            + ''.join(f'<th>{i}등</th>' for i in range(1, 6)) + '</tr>')
    body = []
    for x in b['sectors']:
        tops = (x.get('top') or [])[:5]
        tds = []
        for i in range(5):
            if i < len(tops):
                t = tops[i]
                tds.append(f'<td><a href="{naver_url(t["code"])}" target="_blank"'
                           f' rel="noopener" title="{e(t["name"])} {e(t.get("ret") or "")}">'
                           f'{e(t["name"])}</a></td>')
            else:
                tds.append('<td class="mut">–</td>')
        body.append(
            f'<tr><td>{e(x["name"])}<span class="code">{x["n"]}</span></td>'
            f'<td class="r num">{x["rank"]}{_rank_delta(x, has_prev)}</td>'
            f'{_cell("pct", x.get("ret"), x.get("ret_raw"))}'
            f'<td>{_breadth(x["breadth"])}</td>{"".join(tds)}</tr>')
    return f'<table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


def _stock_board(b):
    """표 3 — 종목 랭킹. 교집합 행은 강조한다."""
    cols = b['columns']
    head = ('<tr><th class="r">#</th><th>종목명</th>'
            + ''.join(f'<th class="r">{e(c["label"])}</th>' for c in cols) + '</tr>')
    body = []
    for r in b['rows']:
        cls = ' class="cross"' if r.get('cross') else ''
        cells = ''.join(_cell(c['kind'], (r['cells'] or {}).get(c['key']),
                              (r['cells_raw'] or {}).get(c['key'])) for c in cols)
        body.append(
            f'<tr{cls}><td class="r num mut">{r["rank"]}</td>'
            f'<td><a href="{naver_url(r["code"])}" target="_blank" rel="noopener">'
            f'{e(r["name"])}</a></td>{cells}</tr>')
    return f'<table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


# ─────────────────────────── 섹터 탭 ───────────────────────────
def _narrow(v):
    """5일 축소폭. **음수가 좋은 값**이라 부호와 색이 반대다.

    갭이 좁혀지면 음수인데, pct() 는 음수에 하락색을 준다. 그러면 돌파에
    가장 가까운 행이 파랗고 멀어진 행이 빨갛게 칠해진다. 여기서만 뒤집는다.
    """
    if v is None:
        return '<span class="mut">–</span>'
    c = 'up' if v < 0 else ('down' if v > 0 else '')
    return f'<span class="num {c}">{v:+.1f}%p</span>'


def _breadth(b):
    t = b.get('total') or (b.get('up', 0) + b.get('flat', 0) + b.get('down', 0)) or 1
    u = b.get('up', 0) / t * 100
    f = b.get('flat', 0) / t * 100
    d = b.get('down', 0) / t * 100
    return (f'<span class="breadth" title="상승 {b.get("up", 0)} / '
            f'보합 {b.get("flat", 0)} / 하락 {b.get("down", 0)}">'
            f'<i class="b-up" style="width:{u:.0f}%"></i>'
            f'<i class="b-flat" style="width:{f:.0f}%"></i>'
            f'<i class="b-dn" style="width:{d:.0f}%"></i></span>')


def _sector_table(rows, label):
    if not rows:
        return '<div class="card empty">집계할 데이터가 없습니다.</div>'
    body = []
    for x in rows:
        b = x['breadth']
        ld = x.get('leader') or {}
        lead = (f'{e(ld.get("name"))} {ld["chg_pct"]:+.1f}%'
                if ld.get('chg_pct') is not None else e(ld.get('name') or ''))
        body.append(
            f'<tr><td>{e(x["name"])}</td>'
            f'<td class="r">{pct(x.get("chg_pct"))}</td>'
            f'<td class="r">{pct(x.get("ret_5d"))}</td>'
            f'<td class="r">{pct(x.get("ret_21d"))}</td>'
            f'<td>{_breadth(b)}</td>'
            f'<td class="r num">{b["up"]}/{b["flat"]}/{b["down"]}</td>'
            f'<td class="r num">{x.get("n_newhigh", 0)}</td>'
            f'<td class="r">{mult(x.get("turnover_mult"))}</td>'
            f'<td class="stage">{lead}</td></tr>')
    return (f'<table><thead><tr><th>{label}</th><th class="r">일간</th>'
            '<th class="r">주간</th><th class="r">월간</th><th>브레드스</th>'
            '<th class="r">상/보/하</th><th class="r">신고가</th>'
            '<th class="r">거래대금 배수</th><th>대표 종목</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


# ─────────────────────────── 페이지 ───────────────────────────
_BOLD = re.compile(r'\*\*(.+?)\*\*')
_CODE = re.compile(r'`([^`]+)`')


def _inline(t):
    """초안에 쓰이는 인라인 마크업만. 이스케이프 먼저 하고 태그를 넣는다."""
    t = e(t)
    t = _BOLD.sub(r'<strong>\1</strong>', t)
    return _CODE.sub(r'<code>\1</code>', t)


def _md(text):
    """초안 마크다운을 최소한으로만 HTML 로. 초안은 불릿과 헤더뿐이다."""
    out, in_ul, in_tbl, quote = [], False, False, []

    def flush_quote():
        if quote:
            out.append('<p class="cite">' + '<br>'.join(quote) + '</p>')
            quote.clear()

    for raw in (text or '').splitlines():
        line = raw.rstrip()
        if not line.startswith('>'):
            flush_quote()
        if not line.strip():
            if in_ul:
                out.append('</ul>')
                in_ul = False
            if in_tbl:
                out.append('</tbody></table>')
                in_tbl = False
            continue
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells):
                continue
            if not in_tbl:
                out.append('<table class="draft-tbl"><tbody>')
                in_tbl = True
            out.append('<tr>' + ''.join(f'<td>{e(c)}</td>' for c in cells) + '</tr>')
            continue
        if in_tbl:
            out.append('</tbody></table>')
            in_tbl = False
        if line.startswith('#'):
            if in_ul:
                out.append('</ul>')
                in_ul = False
            out.append(f'<h3>{_inline(line.lstrip("#").strip())}</h3>')
        elif line.startswith('>'):
            if in_ul:
                out.append('</ul>')
                in_ul = False
            quote.append(_inline(line.lstrip('> ').strip()))
        elif line.startswith(('- ', '» ', '  » ')):
            if not in_ul:
                out.append('<ul class="draft-ul">')
                in_ul = True
            sub = ' sub' if line.lstrip().startswith('»') else ''
            out.append(f'<li class="{sub.strip()}">'
                       f'{_inline(line.lstrip("-» ").strip())}</li>')
        elif line.startswith('---'):
            continue
        else:
            if in_ul:
                out.append('</ul>')
                in_ul = False
            out.append(f'<p>{_inline(line)}</p>')
    flush_quote()
    if in_ul:
        out.append('</ul>')
    if in_tbl:
        out.append('</tbody></table>')
    return ''.join(out)


def _draft_panel(draft):
    if not draft:
        return ('<div class="card empty"><b>일간 코멘트 — 아직 없음</b><br>'
                '<code>python3 -m board.run --write</code> 로 생성합니다. '
                'ANTHROPIC_API_KEY 가 필요합니다.<br>'
                '생성된 초안은 리포트에 등장한 모든 종목명과 수치가 입력 사실 팩에 '
                '있었는지 검증을 통과한 것만 나옵니다 (CLAUDE.md 2장 4번). '
                '검증에 걸린 섹션은 빼고 사유를 문서 상단에 적습니다.</div>')
    return ('<div class="draft-bar">'
            '<span class="st">초안 · 검증 통과분만 표시</span>'
            '<button class="btn" onclick="navigator.clipboard.writeText('
            "document.getElementById('draft-src').textContent)"
            '">본문 복사</button></div>'
            f'<article class="doc">{_md(draft)}</article>'
            f'<pre id="draft-src" hidden>{e(draft)}</pre>')


def _narr_html(text):
    """테마 서술(마크다운 불릿)을 카드용 HTML 로.

    레퍼런스 문체는 `- ` 최상위 불릿과 `» ` 하위 불릿 두 층이다. 그 두 가지와
    **강조** 만 다루고 나머지는 평문으로 이스케이프한다 — 서술에 마크업이 섞여
    들어와도 화면을 깨지 못한다.
    """
    out = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        cls = 'n2' if line.startswith('»') else 'n1'
        line = e(line.lstrip('-»').strip())
        line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
        out.append(f'<li class="{cls}">{line}</li>')
    return f'<ul class="narr">{"".join(out)}</ul>' if out else ''


def _news(news, asof, narratives=None):
    """섹터 뉴스 — 왜 움직였는지.

    등락률과 거래량은 '무엇이 일어났는지'만 말한다. 왜인지는 재료에 있다.
    기사는 제목·요약·링크만 싣는다. 원문을 긁어 저장하지 않는다
    (CLAUDE.md 9장 3번의 방침).

    narratives 가 있으면(--write 가 만든 테마별 서술) 카드 맨 위에 얹는다 —
    레퍼런스 양식의 코멘트가 먼저, 그 근거 기사가 아래다. 서술이 없으면
    지금처럼 헤드라인만 싣는다. 서술은 있으면 좋은 것이지 없다고 뉴스 탭이
    비어서는 안 된다.
    """
    ts = (news or {}).get('themes') or []
    if not ts:
        why = ' '.join((news or {}).get('missing') or []) or \
            '아직 수집하지 않았습니다.'
        return (f'<div class="card empty"><b>섹터 뉴스 — 없음</b><br>{e(why)}<br>'
                '<code>python3 -m board.run --news</code> 로 수집합니다. '
                '네이버 검색 API 자격증명이 필요합니다.</div>')

    cards = []
    for t in ts:
        chg = pct(t.get('chg_pct'))
        nh = (f'<span class="tag tag-new">신고가 {t["n_newhigh"]}</span>'
              if t.get('n_newhigh') else '')
        arts = t.get('articles') or []
        if arts:
            items = ''.join(
                f'<li><a href="{e(a["url"])}" target="_blank" rel="noopener">'
                f'{e(a["title"])}</a>'
                f'<span class="src">{e(a.get("outlet") or "")}</span>'
                + (f'<span class="q">{e(a["query"])}</span>'
                   if a.get('query') and a['query'] != t['name'] else '')
                + (f'<p>{e(a["summary"])}</p>' if a.get('summary') else '')
                + '</li>'
                for a in arts)
            body = f'<ul class="news">{items}</ul>'
        else:
            # 검색은 됐는데 그날 기사가 없었다. '못 받음' 과 구분해서 적는다.
            body = (f'<p class="mut">{e(asof or "")} 자 기사를 찾지 못했습니다 '
                    f'(검색어 {e(" · ".join(t.get("queries") or []))}).</p>')
        more = (f'<span class="mut">{t["n_found"]}건 중 {len(arts)}건</span>'
                if t.get('n_found', 0) > len(arts) else '')
        narr = _narr_html((narratives or {}).get(t['name']))
        cards.append(
            f'<div class="news-card"><div class="news-h">'
            f'<b>{e(t["name"])}</b><span class="num {"up" if (t.get("chg_pct") or 0) >= 0 else "down"}">{chg}</span>'
            f'{nh}<span class="spacer"></span>{more}</div>{narr}{body}</div>')
    return f'<div class="news-grid">{"".join(cards)}</div>'


FIN_PATH = None   # 시험이 바꿔 끼운다. None 이면 ingest.financials.CACHE


def _attach_financials(hm):
    """히트맵 셀에 DART 재무를 붙인다 (D-075). 파일이 없으면 셀은 그대로다.

    툴팁이 그릴 것만 짧은 키로 싣는다 — a: [[연도, 매출, 영업이익, 순이익]…],
    q: [[분기, 매출, 영업이익]…], fs: 연결/별도, d: 받은 날짜. 값은 억원이고
    계산 안 된 것은 null 이다. 화면이 숫자를 만들지 않는다.
    """
    from ..ingest import financials as FIN
    data = FIN.load(FIN_PATH or FIN.CACHE)
    by = (data or {}).get('by_code') or {}
    if not by:
        return
    for groups in hm.values():
        for g in groups or []:
            for c in g.get('cells') or []:
                x = by.get(c.get('code'))
                if not x:
                    continue
                c['fin'] = dict(
                    a=[[r['year'], r.get('rev'), r.get('op'), r.get('ni')] for r in x.get('annual') or []],
                    q=[[r['label'], r.get('rev'), r.get('op'), bool(r.get('derived'))]
                       for r in x.get('quarterly') or []],
                    fs=x.get('fs_div'), d=x.get('as_of'), src=x.get('source'))


def notices(newhigh, market, rankings, news, universe, triggers=None):
    """배너 두 줄 — 빨강(빠진 데이터)과 회색(집계 범위).

    렌더(구운 HTML)와 페이로드(실시간 JSON)가 같은 문구를 써야 한다.
    두 곳에 따로 적으면 한쪽만 고쳐지고 화면마다 다른 말을 하게 된다.
    종목별 재료의 결손(소스를 접음·종목 조회 실패·예산 초과·인박스 없음·단계 실패)도
    여기서 합류한다 — payload 만 합치면 구운 HTML 배너가 그날 재료가 왜 없는지 말하지
    않는다. 반환 (miss, scope).
    """
    miss = list((market or {}).get('missing') or [])
    for m in (((rankings or {}).get('missing') or []) + ((news or {}).get('missing') or [])
              + ((triggers or {}).get('missing') or [])):
        if m not in miss:
            miss.append(m)
    # scope 는 rankings 가 나눠 담은 '우리가 정한 기준으로 걸러낸 것'이다.
    # 오류와 같은 목록에 담지 않는다 (rankings.build 의 주석 참조).
    scope = []
    for m in (rankings or {}).get('scope') or []:
        if m not in scope:
            scope.append(m)
    # 보드에 올리기 전에 시총으로 거른 것. 우리가 정한 기준이므로 회색이다.
    if newhigh.get('n_below_mktcap'):
        cap = newhigh.get('min_mktcap_eok') or 0
        scope.append(f'시가총액 {cap:,.0f}억 미만 {newhigh["n_below_mktcap"]:,}종목은 '
                     '신고가·근접 표에서 뺐습니다 — 화면 필터가 아니라 '
                     '생성 단계에서 거른 것이라 엑셀·텔레그램에도 없습니다')
    # 값을 모르는 것은 하한 미달과 다르다. 이건 빨간색이다 (규칙 1).
    if newhigh.get('n_mktcap_unknown'):
        miss.append(f'시가총액을 못 받은 종목 {newhigh["n_mktcap_unknown"]:,}개도 '
                    '신고가·근접 표에서 빠졌습니다 — 하한 미달이 아니라 '
                    '**값을 모르는 것**입니다. 소스 응답을 확인하세요')
    # 소스가 안 주는 투자자 구분. 머리말에서 조용히 사라지면 '오늘은 0 이었나'
    # 로 읽힌다. 무엇이 왜 없는지 적는다 (규칙 6).
    absent = []
    for mk, d in ((market or {}).get('flows') or {}).items():
        for who in (d or {}).get('columns_absent') or []:
            if who not in absent:
                absent.append(who)
    if absent:
        scope.append(f'투자자별 수급에서 {" · ".join(absent)}는 빠져 있습니다 — '
                     '소스가 개인·외국인·기관계 셋만 줍니다(D-082). '
                     '나머지에서 역산하면 추정이 되므로 적지 않습니다')
    # 눈금 검산을 못 한 날도 남긴다. 값은 썼지만 확인은 못 했다는 뜻이다.
    for mk, d in ((market or {}).get('flows') or {}).items():
        n = (d or {}).get('scale_note')
        if n:
            miss.append(f'{mk} 투자자별 수급 — {n}')
    if newhigh.get('n_suspect'):
        # 가드가 제 일을 한 결과다. 수집이 실패한 것이 아니다.
        scope.append(f'수정주가 미반영 의심 {newhigh["n_suspect"]}종목 — '
                     f'역사적 신고가 판정에서 제외했습니다.')
    if newhigh.get('n_split_cleared'):
        # 제외한 수만 적으면 왜 어제보다 줄었는지 알 수 없다. 푼 것도 적는다.
        scope.append(f'{newhigh["n_split_cleared"]}종목은 하루 ±31% 계단이 있었지만 '
                     '분할·병합·감자·무상증자 공시가 없어 실제 등락으로 보고 '
                     '역사적 판정에 포함했습니다 (D-056).')
    unres = universe.get('theme_seeds_unresolved') or []
    # 비상장이라 원래 못 붙는 이름과 원인을 모르는 이름을 나눠 센다. 합쳐 세면
    # 진짜 오타·사명변경이 '비상장 추정' 이라는 말 뒤에 매일 묻힌다.
    unknown = [x for x in unres if not x.get('known')]
    if unknown:
        # 후보를 함께 적는다. 대부분은 사명 변경이라(한국조선해양 →
        # HD한국조선해양) 후보만 보이면 바로 고칠 수 있다.
        def _one(x):
            near = x.get('near') or []
            return f'{x["seed"]}→{near[0]}' if near else x['seed']

        # 후보가 있는 것과 없는 것은 할 일이 다르다. 앞쪽은 이름만 고치면 되고,
        # 뒤쪽은 그 종목이 그날 스냅샷에 아예 없었다는 뜻이라 다른 질문이다.
        # 같은 이름이 여러 테마에 있으면 목록에 두 번 뜬다. 세는 건 시드 기준이
        # 맞지만 보여 주는 건 이름 기준이다 — 같은 이름을 두 번 고칠 일은 없다.
        seen, uniq = set(), []
        for x in unknown:
            if x['seed'] not in seen:
                seen.add(x['seed'])
                uniq.append(x)
        withnear = [x for x in uniq if x.get('near')]
        nonear = [x for x in uniq if not x.get('near')]
        parts = []
        if withnear:
            # **"사명 변경" 이라고 단정하지 않는다.** 이름이 비슷하다는 것과 같은
            # 회사라는 것은 다르다. 실제로 SK머티리얼즈→하나머티리얼즈처럼 업종
            # 접미사만 겹친 것도 섞인다. 기계는 찾을 자리를 좁혀 줄 뿐이다
            # (CLAUDE.md 2장 1번 — 추정을 단정하지 않는다).
            parts.append(
                f'{len(withnear)}건은 이름이 비슷한 상장 종목이 있습니다'
                '(같은 회사인지는 확인이 필요합니다) — '
                + ', '.join(_one(x) for x in withnear[:5])
                + ('…' if len(withnear) > 5 else ''))
        if nonear:
            parts.append(
                f'{len(nonear)}건은 비슷한 이름조차 없습니다 — '
                + ', '.join(x['seed'] for x in nonear[:5])
                + ('…' if len(nonear) > 5 else '')
                + '. 상장폐지·비상장이면 themes.yaml 의 unlisted 에 사유를 적으면 '
                  '이 줄에서 빠지고, 지금도 상장돼 있다면 그날 시세 수집이 그 '
                  '종목을 놓친 것입니다')
        miss.append(f'themes.yaml 시드 {len(unknown)}건이 상장 종목 마스터에 '
                    '없습니다. ' + ' / '.join(parts))
    if universe.get('unmapped_theme'):
        # 2층 테마는 해상도 담당이라 전 종목을 덮지 않는다. 미매핑은 정상이다.
        scope.append(f'테마 미매핑 {universe["unmapped_theme"]:,}종목 — 미분류 버킷.')
    return miss, scope


def build(newhigh, sectors, market, events, universe, meta,
          draft=None, rankings=None, news=None, narratives=None,
          stockflows=None, triggers=None):
    FLOWS.clear()
    FLOWS.update((stockflows or {}).get('by_code') or {})
    FLOWS['_as_of'] = (stockflows or {}).get('as_of')
    TRIGGERS.clear()
    TRIGGERS.update((triggers or {}).get('by_code') or {})
    LABEL_KO.clear()
    LABEL_KO.update(newhigh.get('labels') or
                    {'hist': '역사적', 'w52': '52주', 'd120': '120일'})
    counts = newhigh.get('counts') or {}
    ach = newhigh.get('achieved') or []
    near = newhigh.get('proximity') or []
    evs = (events or {}).get('events') or []
    tmeta = {t['theme']: t for t in (sectors or {}).get('themes') or []}

    miss, scope = notices(newhigh, market, rankings, news, universe, triggers)

    # 설정의 기본 기준(newhigh.default_basis)이 토글의 초기 선택이 된다.
    # 라벨 문자열은 화면이 토글과 함께 바꾸므로 여기서 만들지 않는다.
    dflt = 'hi' if newhigh.get('basis') == 'high' else 'cl'
    th = newhigh.get('thresholds') or {}
    px = (th.get('proximity') or {})

    P = []
    # 종가가 KRX 정규장 확정치인지 머리에 붙인다 (D-080). 옛 state 에는 이 키가
    # 없으므로 그때는 아무것도 적지 않는다 — 모르는 것을 '확정' 이라고 쓰지 않는다.
    cc = newhigh.get('close_confirmed')
    close_tag = '' if cc is None else (
        '<span class="close-src ok" title="KRX 정규장 확정 시세로 덮었습니다">'
        '종가 확정</span> · ' if cc else
        '<span class="close-src bad" title="KRX 정규장 확정 시세를 못 받아 16:07 '
        '네이버 값입니다. NXT 애프터마켓·시간외 단일가가 끝날 때까지 움직입니다">'
        '종가 잠정</span> · ')
    P.append(f'''<header><div class="head-in">
  <div class="title-row"><h1>국장 신고가 보드</h1>
  <span class="stamp"><span data-b="hi"{'' if dflt == 'hi' else ' hidden'}>고가</span><span data-b="cl"{'' if dflt == 'cl' else ' hidden'}>종가</span> 기준 ·
  {close_tag}{f"종목 {universe['n']:,} · " if universe.get("n") else ""}생성 {e(newhigh.get("generated_at"))}</span>
  <span class="spacer"></span>
  <label class="daypick">날짜
    <select id="daysel" data-asof="{e(newhigh.get("as_of"))}">
      <option value="">{e(newhigh.get("as_of"))}</option>
    </select></label>
  {_downloads(newhigh.get("as_of"))}<!--reports--></div>
  <ul class="strip">{_strip(market)}</ul>
  <nav role="tablist">
    <button role="tab" aria-selected="true" data-p="p1">신고가</button>
    <button role="tab" aria-selected="false" data-p="p2">섹터 랭킹</button>
    <button role="tab" aria-selected="false" data-p="p2b">종목 랭킹</button>
    <button role="tab" aria-selected="false" data-p="p3">히트맵</button>
    <button role="tab" aria-selected="false" data-p="p4">섹터 뉴스</button>
    <button role="tab" aria-selected="false" data-p="p5">일간 코멘트</button>
  </nav></div></header>''')

    P.append('<div class="wrap">')
    P.append(_banner(miss))
    P.append(_banner(scope, '집계 범위', 'info'))

    # p1
    P.append('<section class="panel on" id="p1" role="tabpanel">')
    # 두 표에 같은 종목이 있을 수 있어 코드로 한 번만 센다.
    other = K.counts({x['code']: x for x in list(ach) + list(near)}.values())
    P.append(_pills(newhigh.get('counts_high') or counts, len(near),
                    newhigh.get('displayed') or ['hist', 'w52', 'd120'],
                    newhigh.get('counts_close'), other,
                    turn_min=newhigh.get('min_turnover_eok'), dflt=dflt))
    P.append(_cluster_banner(evs, tmeta))
    P.append(f'''<div class="sec"><div class="sec-h"><h2>달성</h2>
      <span class="note">{e(_lookback_note(th))} ·
      거래량 배수는 20일 평균 대비</span>
      <span class="spacer"></span>
      <div class="basis-sw" role="group" aria-label="신고가 판정 기준">
        <button data-basis="hi" aria-pressed="{str(dflt == "hi").lower()}">고가 기준</button>
        <button data-basis="cl" aria-pressed="{str(dflt == "cl").lower()}">종가 기준</button>
      </div></div>
      {_achieved_table(ach, tmeta)}</div>''')
    P.append(f'''<div class="sec"><div class="sec-h"><h2>근접</h2>
      <span class="note">갭 {px.get("max_gap_pct")}% 이내 · 시총 {px.get("min_mktcap_eok"):,.0f}억 이상 ·
      {px.get("narrow_days")}일 축소폭 순 정렬</span></div>
      {_proximity_table(near)}</div>''')
    P.append('</section>')

    # p2 — 섹터 랭킹
    rk = rankings or {}
    sb = rk.get('sector_boards') or []
    P.append('<section class="panel" id="p2" role="tabpanel">')
    if sb:
        P.append('<div class="tabs" data-tabs>')
        for i, b in enumerate(sb):
            P.append(f'<button data-tab="sb-{e(b["key"])}" '
                     f'aria-selected="{str(i == 0).lower()}">{e(b["label"])}</button>')
        P.append('</div>')
        for i, b in enumerate(sb):
            w = ('시가총액 가중' if b.get('weighting') == 'mktcap' else '단순 평균')
            P.append(f'<div class="pane" data-pane="sb-{e(b["key"])}"'
                     f'{"" if i == 0 else " hidden"}>'
                     f'<div class="sec"><div class="sec-h">'
                     f'<h2>{e(b["ret_label"])} 순위</h2>'
                     f'<span class="note">{e(rk.get("taxonomy") or "")} 분류 '
                     f'{len(b["sectors"])}개 · 등락률은 구성종목 {w} 계산값 · '
                     f'종목명 옆 숫자는 구성 종목 수</span></div>'
                     + _sector_board(b) + '</div></div>')
    else:
        P.append('<div class="card empty">랭킹이 아직 없습니다.</div>')
    P.append('<div class="sec"><div class="sec-h"><h2>테마별 등락</h2>'
             '<span class="note">knowledge/themes.yaml 2층 분류 · 다대다라 한 종목이 '
             '여러 줄에 등장합니다</span></div>'
             + _sector_table((sectors or {}).get('themes') or [], '테마') + '</div>')
    P.append('</section>')

    # p2b — 종목 랭킹
    kb = rk.get('stock_boards') or []
    P.append('<section class="panel" id="p2b" role="tabpanel">')
    if kb:
        n_cross = len(rk.get('cross_codes') or [])
        P.append(f'<div class="banner"><b>양쪽 동시 등장 {n_cross}종목</b>'
                 f'<span>수익률 상위이면서 거래량도 며칠째 붙어 있는 종목. '
                 f'표에서 파란 줄로 표시했고, 텔레그램은 이것만 보냅니다.</span></div>')
        P.append('<div class="grid2">')
        for b in kb:
            P.append(f'<div class="sec"><div class="sec-h"><h2>{e(b["title"])}</h2></div>'
                     + _stock_board(b) + '</div>')
        P.append('</div>')
    else:
        P.append('<div class="card empty">랭킹이 아직 없습니다.</div>')
    P.append('</section>')

    # p3
    P.append('''<section class="panel" id="p3" role="tabpanel">
  <div class="tools">
    <label class="tool">크기 <select id="sizeBy">
      <option value="turnover">거래대금</option><option value="mktcap">시가총액</option>
      <option value="mono">모노 사이즈</option></select></label>
    <label class="tool">색상 <select id="colorBy">
      <option value="chg_pct">일간 등락률</option><option value="ret_5d">주간</option>
      <option value="ret_21d">월간</option><option value="ret_250d">1년(250영업일)</option></select></label>
    <label class="tool">그룹 <select id="groupBy">
      <option value="theme">자체 테마</option><option value="sector">KRX 업종</option></select></label>
    <label class="tool">신고가 강조 <select id="nhOn">
      <option value="1">켬</option><option value="0">끔</option></select></label>
  </div>
  <div class="hm-grid" id="hm"></div>
  <div class="legend"><span>면적 = 선택한 크기 기준</span><span>색 = 선택한 기간 등락률</span>
    <span><span class="swatch"></span> 당일 신고가</span>
    <span class="scale"><i style="background:#5A9BE0"></i><i style="background:#9DC4EE"></i>
      <i style="background:#DCE9F8"></i><i style="background:#F8E0DE"></i>
      <i style="background:#EFAAA6"></i><i style="background:#DC6660"></i></span>
    <span>-3% → +3%</span></div></section>''')

    # p4 · p5 — 입력 데이터가 없다. 가짜로 채우지 않는다.
    P.append('<section class="panel" id="p4" role="tabpanel">')
    P.append(_news(news, newhigh.get('as_of'), narratives))
    P.append('</section>')
    P.append('<section class="panel" id="p5" role="tabpanel">'
             + _draft_panel(draft) + '</section>')

    P.append(f'''<footer>
  <b>종가는 KRX 정규장 종가입니다</b> (D-080). 16:07 에 KRX 오픈API 일별매매정보로
  당일 봉을 덮습니다 — 네이버가 그 시각에 주는 값은 NXT 애프터마켓(15:30~20:00)·
  시간외 단일가가 끝날 때까지 움직이는 잠정치라 종가 기준 라벨이 이튿날 바뀝니다.
  확정치를 못 받은 날은 머리에 '종가 잠정' 이 붙습니다.<br>
  그 밖의 시세·업종은 네이버 금융에서 수집합니다. 인증키가 필요 없는 대신 공개 API 가
  아니라 사이트 개편이면 깨집니다. 실행 전 <code>run.py --check</code> 로 확인하세요.<br>
  신고가·갭·저항두께 정의는 CLAUDE.md 4장 고정 정의를 그대로 따릅니다.
  저항두께와 거래대금 20일 평균은 종가×거래량 추정치입니다.<br>
  전 종목 {universe.get("n", 0):,} · 탐지 이벤트 {len(evs)}건 · 기준일 {e(newhigh.get("as_of"))}
</footer></div>''')

    hm = dict(theme=(sectors or {}).get('heatmap_theme') or [],
              sector=(sectors or {}).get('heatmap_sector') or [])
    _attach_financials(hm)
    with open(CSS_PATH, encoding='utf-8') as f:
        css = f.read()
    with open(os.path.join(os.path.dirname(CSS_PATH), 'board.js'), encoding='utf-8') as f:
        js = f.read()
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>국장 신고가 보드 {e(newhigh.get("as_of"))}</title>'
        f'<link rel="icon" href="{FAVICON}">'
        # 폰트는 CDN 이라 렌더를 막지 않게 비동기로 건다. 로컬에서 오프라인이거나
        # CDN 이 죽어도 화면은 즉시 뜬다 — board.css 의 대체 글꼴로 떨어진다.
        + ''.join(
            f'<link rel="stylesheet" href="{u}" media="print" '
            'onload="this.media=\'all\'">' for u in FONT_CSS)
        + '<noscript>' + ''.join(
            f'<link rel="stylesheet" href="{u}">' for u in FONT_CSS) + '</noscript>'
        f'<style>{css}</style></head><body>{"".join(P)}'
        f'<script id="hm-data" type="application/json">{_json_in_html(hm)}</script>'
        f'<script>{js}</script></body></html>')


def _json_in_html(obj):
    """`<script>` 안에 넣을 JSON.

    `json.dumps` 결과를 그대로 넣으면 문자열 안의 `</script` 가 HTML 파서에게
    스크립트 끝으로 읽힌다. 그 뒤는 전부 마크업이 되어 히트맵이 깨지고 임의
    태그가 문서에 들어간다. 종목명·섹터명은 외부 소스에서 온 값이라 우리가
    내용을 통제하지 못한다.

    `<` 를 \u003c 로 쓰면 JSON 으로는 같은 문자열이고 HTML 파서에는 태그로
    안 보인다. `application/json` 이라 JS 로 평가되지 않으므로 U+2028/2029 는
    문제가 되지 않는다.
    """
    return json.dumps(obj, ensure_ascii=False).replace('<', '\\u003c')


def write(out_path, doc):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(doc)
    return out_path


def from_state(asof, out_path=None):
    """state/YYYYMMDD 를 읽어 HTML 문자열을 돌려준다.

    out_path 를 주면 그 파일에도 쓴다. 사이트 조립은 web/site.py 가 한다 —
    렌더는 그리는 일만 하고 어디에 놓을지는 모른다.
    """
    from ..engine.build import read
    from ..ingest import triggers as TR
    nhj = read(asof, 'newhigh.json')
    if not nhj:
        raise RuntimeError(f'state/{asof} 에 newhigh.json 이 없다. 엔진을 먼저 돌려라.')
    from ..engine.build import read_text
    doc = build(nhj, read(asof, 'sectors.json') or {}, read(asof, 'market.json') or {},
                read(asof, 'events.json') or {}, read(asof, 'universe.json') or {},
                meta={}, draft=read_text(asof, 'draft.md'),
                rankings=read(asof, 'rankings.json') or {},
                news=read(asof, 'news.json') or {},
                narratives=(read(asof, 'narratives.json') or {}).get('themes'),
                # payload.from_state 와 **같은 입력**을 넘겨야 한다. 여기 빠뜨리면
                # FLOWS 가 빈 채로 남아 구운 보드(docs/d/)와 아티팩트의 '순매수'
                # 칸이 전 종목 빈칸으로 나간다 — 값을 못 받은 날과 구분이 안 된다.
                stockflows=read(asof, 'stockflows.json'),
                # 파일이 없으면 '수집되지 않음(단계 실패)' 결손 한 줄짜리 대역 —
                # payload 와 같은 입력이라야 두 배너가 같은 말을 한다.
                triggers=TR.load(asof))
    if out_path:
        write(os.path.abspath(out_path), doc)
    return doc

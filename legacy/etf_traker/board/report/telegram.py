#!/usr/bin/env python3
"""
텔레그램 리포트 — rankings.json 과 draft.md 를 모바일 한 화면으로 줄인다.

메시지는 둘이고 원칙이 다르다 (D-084).

**랭킹은 전체 표를 옮기지 않는다.** 표는 대시보드와 엑셀이 맡는다
(RANKINGS-CONTRACT.md '소비자별 사용 범위'). 랭킹을 그대로 옮기면 텔레그램은
대시보드의 열등한 사본이 될 뿐이라, 스크롤 없이 3초에 읽히는 것만 남겼다.

  · 결손 — `missing` 을 맨 위 한 줄로. 무엇이 빠졌는지 모르고 읽으면 안 되니
    제목보다 위에 둔다 (CLAUDE.md 2장 6번).
  · 섹터 — 상위 5 · 하위 3, 각 섹터의 1등 종목 하나. 순환매의 방향은 양 끝만 봐도
    읽히고 중간 순위는 정보가 아니라 줄 수만 늘린다. 1등 종목을 곁들이는 이유는
    섹터 등락률만 보면 한 종목이 끌었는지 전체가 올랐는지 알 수 없기 때문이고,
    같은 이유로 브레드스(상승/구성)를 괄호에 붙인다.
  · 종목 — `cross_codes` 에 든 것만. 수익률 상위와 거래량 급증 양쪽에 동시에 뜬
    종목이 시그널이고 한쪽에만 뜬 건 노이즈다. 교차분만 추리는 것이 텔레그램이
    전체 표보다 나을 수 있는 유일한 지점이다. 두 표의 정렬 기준값을 한 줄에
    합쳐 준다 — 표를 오가지 않아도 되는 게 교차의 이점이라서다.

**초안은 전부 보낸다.** 17:15 초안은 알림이 아니라 검수 대상이라 문단도 표도 고르지
않는다 — 여기서 추리면 검수할 것이 사라진다. 표는 모바일에서 읽히지 않으니 행마다
한 줄로 풀어 보낸다(`draft_message`). 길어지면 `send()` 가 나눠 보낸다.

`null` 은 계산되지 않았다는 뜻이라 0 으로 채우지 않고 `–` 로 남긴다 (2장 1번).
표시용 문자열(`cells`)은 다시 포맷하지 않고, 계산 여부만 `cells_raw` 로 판정한다.

4,096자를 넘으면 텔레그램이 조용히 자른다. 넘칠 때는 뒤에서부터 버리되 무엇을 몇 건
버렸는지 마지막 줄에 적는다. 신고가 종목의 재료 줄을 어느 구획보다 먼저(뒤 종목부터)
버리고, 그다음 섹터를 종목보다 먼저 버린다 — 재료는 대시보드에 링크째 있고 섹터는
대시보드 첫 화면에 그대로 있지만 교차 종목은 여기서만 걸러 주기 때문이다. 메시지
제목이 '등락률 Top 랭킹' 인데 재료 줄이 섹터 랭킹을 밀어내면 안 된다.

파스 모드는 레거시 `Markdown` 이다. MarkdownV2 는 `.` `-` `(` 까지 전부 이스케이프를
요구해서 수치와 종목명이 섞인 본문은 한 글자만 놓쳐도 400 이 난다. 레거시는 문법이
별표·밑줄·대괄호·백틱 다섯 글자뿐이라 사고 지점이 적다. 대신 레거시에는 백슬래시
이스케이프가 규정되어 있지 않아, 종목명에 섞인 그 글자들은 이스케이프하지 않고
파서가 집지 않는 전각 유사 글자로 바꾼다(`_safe`). 글자 수와 가독성은 유지된다.

발송 실패는 예외로 올리지 않고 `(False, 사유)` 로 돌려준다. 18:00 발송이 막혀도
대시보드·아카이브는 끝나야 한다. 대신 사유는 반드시 문자열로 남긴다 (2장 6번).
"""
import os
import re

# ─────────────────────────── 임계값 ───────────────────────────
TG_LIMIT = 4096              # sendMessage 본문 상한. 넘으면 텔레그램이 자른다
TIMEOUT = 20                 # 발송 1건 타임아웃(초)
API = 'https://api.telegram.org/bot{token}/{method}'


def _safe_err(ex, *secrets):
    """예외 메시지에서 자격증명을 지운다.

    requests 의 연결 예외 메시지에는 **요청 URL 이 통째로** 들어 있고, 텔레그램은
    토큰을 URL 경로에 담는다. 그대로 로그에 찍으면 네트워크 실패 한 번에 봇
    토큰이 GitHub Actions 로그에 영구히 남는다. D-013 의 마스킹 규칙이 이 경로만
    비껴 있었다.
    """
    msg = f'{type(ex).__name__}: {ex}'
    for sec in secrets:
        if sec:
            msg = msg.replace(str(sec), '<TOKEN>')
    return msg

SECTOR_TOP_N = 5             # 상위 섹터 노출 수
SECTOR_BOTTOM_N = 3          # 하위 섹터 노출 수
LEADER_N = 1                 # 섹터당 곁들이는 상위 종목 수
CROSS_MAX = 12               # 교차 종목 상한. 이보다 많으면 이미 시그널이 아니다
MISSING_MAX = 3              # 결손 한 줄에 이름으로 적는 항목 수. 나머지는 '외 N건'
MISSING_ITEM_MAX = 40        # 결손 항목 하나의 글자 상한
SECTOR_BOARD_PREF = ('1d',)  # 섹터 보드가 여럿이면 이 키부터 쓴다
NEWHIGH_MAX = 20             # 신고가·수급 줄 상한. 52주 이상은 보통 한 자릿수다
NEWHIGH_KIND = 'w52'         # 본문에 수급까지 싣는 최소 등급 (역사적 포함)
KIND_ORDER = ('d60', 'w52', 'hist')
TRIGGER_LINES_MAX = 2        # 종목당 재료 줄 상한. 셋째부터는 대시보드(app.js 재료 줄)에 있다
TRIGGER_TITLE_MAX = 60       # 재료 제목 글자 수. triggers.json 의 title_chars_tg 가 우선
# triggers.json 이 없을 때 결손 첫 줄에 넣는 문장. ingest/triggers.ABSENT_LINE 과 같아야
# 한다(시험이 대조한다) — 세 소비자가 같은 말을 해야 사람이 같은 일로 읽는다.
TRIGGER_ABSENT_LINE = '재료: 수집되지 않음(단계 실패)'
# 재료 줄의 접두. 출처가 다르면 읽는 쪽이 신뢰도를 달리 두므로 섞어 적지 않는다.
TRIGGER_PREFIX = {'dart': '공시', 'telegram_x': 'X'}

DASH = '–'                   # 계산되지 않은 값. 0 이 아니다 (CLAUDE.md 2장 1번)
WEIGHTING_KO = {'mktcap': '시총가중', 'equal': '동일가중'}

# 레거시 Markdown 이 문법으로 집는 글자 → 파서가 집지 않는 전각 유사 글자.
# 이스케이프가 아니라 치환인 이유는 모듈 docstring 참고.
MD_SAFE = {'_': '＿', '*': '＊', '[': '［', ']': '］', '`': 'ˋ'}

_BOLD = re.compile(r'\*\*(.+?)\*\*')
_CODE = re.compile(r'`([^`]+)`')
_MARK = re.compile('\x1a(\\d+)\x1a')   # 자리표시자. 본문에 없을 제어문자를 쓴다


# ─────────────────────────── 문자열 ───────────────────────────
def _safe(v):
    """종목명·섹터명처럼 우리가 통제하지 못하는 문자열을 파서 안전하게 만든다."""
    if v is None:
        return ''
    return ''.join(MD_SAFE.get(c, c) for c in str(v).replace('\x00', ''))


def _num(obj, key):
    """표시용 문자열을 그대로 쓰되 `*_raw` 가 null 이면 계산 안 된 값이라 '–'."""
    if not isinstance(obj, dict) or obj.get(key + '_raw') is None:
        return DASH
    v = obj.get(key)
    return DASH if v is None else _safe(v)


def _cell(row, key):
    """stock_boards 행의 셀. 판정 기준은 `cells` 가 아니라 `cells_raw` 다."""
    if (row.get('cells_raw') or {}).get(key) is None:
        return DASH
    v = (row.get('cells') or {}).get(key)
    return DASH if v is None else _safe(v)


def _link(url, label):
    """URL 에 괄호가 섞이면 레거시 파서가 링크를 못 닫는다. 그러면 맨 URL 로 뺀다."""
    u = (url or '').strip()
    if not u:
        return None
    return f'{label}: {u}' if ('(' in u or ')' in u) else f'[{label}]({u})'


# ─────────────────────────── 길이 맞추기 ───────────────────────────
def _omit_line(dropped):
    """생략 안내 한 줄. 어디에 전체가 있는지는 **실제로 있는 곳**만 적는다 —
    재료는 대시보드(payload 의 achieved[].trigger)에만 있고 엑셀에는 열이 없다."""
    parts = ' · '.join(f'{k} {n}건' for k, n in dropped.items() if n)
    kinds = {k for k, n in dropped.items() if n}
    if kinds == {'재료'}:
        where = '재료 전체는 대시보드에 있다'
    elif '재료' in kinds:
        where = '전체는 대시보드에 있다 · 섹터·종목은 엑셀에도'
    else:
        where = '전체는 대시보드와 엑셀에 있다'
    return f'— 길이 제한으로 {parts} 생략. {where}'


def _flatten(items):
    """구획의 항목을 줄로 편다.

    항목은 문자열(한 줄)이거나 **종목 단위 엔트리** `{'lines': […], 'trig': […]}` 다.
    엔트리로 묶어 두는 이유는 `_fit` 이 줄이기 전에 뒤 종목의 재료 줄부터 뺄 수
    있어야 해서다 — 줄로 펴 놓으면 어느 줄이 재료인지 알 수 없다.
    """
    out = []
    for e in items:
        if isinstance(e, dict):
            out.extend(e.get('lines') or [])
            out.extend(e.get('trig') or [])
        else:
            out.append(e)
    return out


def _compose(head, groups, tail, dropped):
    lines = list(head)
    for g in groups:
        if not g['items']:
            continue                      # 항목이 다 빠진 구획은 제목도 남기지 않는다
        if g.get('head'):
            lines.append('')
            lines.append(g['head'])
        lines.extend(_flatten(g['items']))
    lines.extend(tail)
    if dropped:
        lines.append(_omit_line(dropped))
    return '\n'.join(x for x in lines if x is not None)


def _drop_one(g):
    """구획에서 하나를 뺀다. 반환은 뺀 것의 종류.

    재료 줄이 남아 있으면 **뒤 종목부터 재료 줄을 먼저** 뺀다 — 재료는 대시보드에
    링크째 있지만 종목·수급 줄은 여기서만 붙여 주는 것이라서다. 재료가 다 빠진 뒤에야
    구획 안의 뒤쪽 항목(종목)을 통째로 뺀다.
    """
    for e in reversed(g['items']):
        if isinstance(e, dict) and e.get('trig'):
            e['trig'].pop()
            return '재료'
    g['items'].pop()                      # 구획 안에서는 순위가 낮은 뒤쪽부터
    return g['kind']


def _cut_lines(text, room):
    """줄 단위로 뒤에서 잘라 room 에 맞춘다. 반환 (남은 본문, 잘라낸 줄 수)."""
    lines = text.split('\n')
    n = 0
    while lines and len('\n'.join(lines)) > room:
        lines.pop()
        n += 1
    return '\n'.join(lines), n


def _has_trig(g):
    return any(isinstance(e, dict) and e.get('trig') for e in g['items'])


def _fit(head, groups, tail, limit=TG_LIMIT):
    """한도에 맞을 때까지 버리고, 버린 것을 마지막 줄에 적는다. 조용히 자르지 않는다.

    재료 줄이 남은 구획이 있으면 drop_rank 와 무관하게 거기서 재료부터 뺀다 —
    drop_rank 순으로만 고르면 섹터(0)·교차(1) 구획이 신고가(2) 구획의 재료 줄보다
    먼저 통째로 빠져, 대시보드에도 있는 재료가 이 메시지의 본체인 랭킹을 밀어냈다
    (52주 이상 20종목 + 수급 줄 + 재료 2줄이면 섹터 8행 중 1행만 남았다). 재료가
    다 빠진 뒤에야 drop_rank 순서다.
    """
    dropped = {}
    text = _compose(head, groups, tail, dropped)
    order = sorted(groups, key=lambda g: g.get('drop_rank', 0))
    while len(text) > limit:
        g = (next((g for g in order if _has_trig(g)), None)
             or next((g for g in order if g['items']), None))
        if g is None:
            break
        kind = _drop_one(g)
        dropped[kind] = dropped.get(kind, 0) + 1
        text = _compose(head, groups, tail, dropped)
    # 머리·꼬리만으로 넘치는 병적인 입력. 여기까지 와도 조용히 자르지는 않는다.
    for _ in range(4):
        if len(text) <= limit:
            break
        note = _omit_line(dropped) if dropped else ''
        body = text[:-(len(note) + 1)] if dropped and note and text.endswith(note) else text
        body, n = _cut_lines(body, limit - len(note) - 1)
        dropped['줄'] = dropped.get('줄', 0) + n
        text = body + '\n' + _omit_line(dropped)
    return text


def _split(text, limit=TG_LIMIT):
    """발송용 분할. 줄 경계에서 자르고, 문장 중간에서 자르지 않는다."""
    out, cur = [], ''
    for line in (text or '').split('\n'):
        while len(line) > limit:          # 한 줄이 통째로 한도를 넘는 병적인 경우
            cut = line.rfind(' ', 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                out.append(cur)
                cur = ''
            out.append(line[:cut])
            line = line[cut:].lstrip()
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += '\n' + line
        else:
            out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return [x for x in out if x.strip()] or ['']


# ─────────────────────────── 랭킹 메시지 ───────────────────────────
def _missing_line(missing):
    """결손을 한 줄로. 여러 건이면 앞의 몇 개만 이름을 적고 나머지는 건수만."""
    items = [str(m).strip().split('\n')[0] for m in missing if str(m).strip()]
    if not items:
        return None
    shown = [x if len(x) <= MISSING_ITEM_MAX else x[:MISSING_ITEM_MAX - 1] + '…'
             for x in items[:MISSING_MAX]]
    tail = f' 외 {len(items) - len(shown)}건' if len(items) > len(shown) else ''
    return f'*빠진 데이터 {len(items)}건* — {_safe(" · ".join(shown))}{tail}'


def _head_lines(r):
    out = []
    miss = _missing_line(r.get('missing') or [])
    if miss:
        out.append(miss)                  # 제목보다 위. 모르고 읽으면 안 된다
    bits = [x for x in (r.get('market'), r.get('source')) if x]
    sub = f' · {_safe(" · ".join(str(b) for b in bits))}' if bits else ''
    out.append(f'*{_safe(r.get("as_of") or "기준일 미상")} 랭킹*{sub}')
    return out


def _pick_board(boards, prefer):
    """보드가 여럿이어도 텔레그램은 하나만 쓴다. 선호 키가 없으면 첫 번째."""
    boards = [b for b in (boards or []) if isinstance(b, dict)]
    for key in prefer:
        for b in boards:
            if b.get('key') == key:
                return b
    return boards[0] if boards else None


def _sector_line(s, mark):
    b = s.get('breadth') or {}
    n = s.get('n')
    br = f' ({b["up"]}/{n})' if b.get('up') is not None and n else ''
    lead = ''
    for t in (s.get('top') or [])[:LEADER_N]:
        nm = _safe(t.get('name') or t.get('code'))
        if nm:
            lead = f' — {nm} {_num(t, "ret")}'
    return f'{mark} {_safe(s.get("name") or "?")} {_num(s, "ret")}{br}{lead}'


def _sector_group(r):
    board = _pick_board(r.get('sector_boards'), SECTOR_BOARD_PREF)
    if not board:
        return dict(kind='섹터', drop_rank=0, head=None, items=[])
    secs = [s for s in (board.get('sectors') or []) if isinstance(s, dict)]
    w = WEIGHTING_KO.get(board.get('weighting'), board.get('weighting') or '가중 미상')
    label = board.get('ret_label') or board.get('label') or board.get('key') or '등락률'
    items = []
    for i in range(min(SECTOR_TOP_N, len(secs))):
        items.append(_sector_line(secs[i], f'{secs[i].get("rank") or i + 1}.'))
    # 하위는 상위와 겹치지 않을 때만. 섹터가 8개 미만이면 양 끝이 만난다.
    for i in range(max(len(secs) - SECTOR_BOTTOM_N, SECTOR_TOP_N), len(secs)):
        items.append(_sector_line(secs[i], '↓'))
    head = f'*섹터 · {_safe(label)} · {_safe(w)}* (괄호는 상승/구성)'
    return dict(kind='섹터', drop_rank=0, head=head, items=items)


def _col_label(board, key):
    for c in board.get('columns') or []:
        if c.get('key') == key:
            return c.get('label') or key
    return board.get('title') or key or '값'


def _cross_group(r):
    """두 표에 동시에 뜬 종목만. 각 표의 정렬 기준값을 한 줄에 합친다."""
    boards = [b for b in (r.get('stock_boards') or []) if isinstance(b, dict)]
    codes = [str(c) for c in (r.get('cross_codes') or []) if c]
    keys = [b.get('sort_by') for b in boards]
    labels = [_safe(_col_label(b, k)) for b, k in zip(boards, keys)]
    head = f'*교차 시그널 — {" · ".join(labels)}*' if labels else '*교차 시그널*'
    if not boards:
        # 랭킹이 계산되지 않은 것과 교집합이 0 인 것은 다르다.
        # 전자를 '겹치는 종목 없음'으로 적으면 계산 안 된 값을 사실로 단정하는
        # 것이 된다 (CLAUDE.md 2장 1번).
        # 다른 가지와 같은 모양(dict)이어야 한다. 리스트를 돌려주면 _compose 가
        # g['items'] 에서 통째로 죽는다 — 종목 랭킹이 비는 날 메시지가 아예
        # 안 나간다.
        return dict(kind='종목', drop_rank=1, head=None,
                    items=['*교차 시그널* — 랭킹이 계산되지 않아 판정할 수 없음'])
    if not codes:
        # 겹치는 종목이 없다는 것도 사실이다. 빈 구획으로 두지 않고 그렇게 적는다.
        note = '겹치는 종목 없음 — 수익률 상위와 거래량 급증이 만나지 않았다'
        return dict(kind='종목', drop_rank=1, head=head, items=[note])

    rows_by_board = [{str(x.get('code')): x for x in (b.get('rows') or [])} for b in boards]
    merged = []
    for code in codes:
        hits = [rb.get(code) for rb in rows_by_board]
        if not any(hits):
            continue                      # 계약상 없어야 하지만, 없으면 지어내지 않는다
        name = next((h.get('name') for h in hits if h and h.get('name')), code)
        ranks = [h.get('rank') for h in hits if h and h.get('rank')]
        vals = [_cell(h, k) if h else DASH for h, k in zip(hits, keys)]
        merged.append((min(ranks) if ranks else 10 ** 6, code, name, vals))
    # 두 표의 순위 중 나은 쪽으로 세운다. 한쪽에서 1등이면 다른 쪽 순위와 무관하게
    # 먼저 봐야 하고, 어느 표를 기준으로 삼을지는 고를 이유가 없다.
    merged.sort(key=lambda x: (x[0], x[1]))

    items = [f'· {_safe(name)} {" · ".join(vals)}'
             for _, _, name, vals in merged[:CROSS_MAX]]
    if len(merged) > CROSS_MAX:
        items.append(f'· 외 {len(merged) - CROSS_MAX}종목 — 전체는 대시보드')
    return dict(kind='종목', drop_rank=1, head=head, items=items)


def _eok(v, unit):
    """순매수 표기. 단위를 숨기지 않는다.

    1억 미만을 '억' 으로 반올림하면 `-0억` 이 되어 부호만 남고 값이 사라진다.
    그 구간은 만원으로 내려 적는다 — 화면(web/render.net_amt)과 같은 규칙이다.
    0 에는 부호를 붙이지 않는다. `-0.0 >= 0` 이 참이라 매도 추정치가 '+0만' 으로
    나간 적이 있다 — 0 은 산 것도 판 것도 아니다.
    """
    if v is None:
        return None
    if v == 0:
        return '0주' if unit == '주' else '0만'
    if unit == '주':
        return f'{v:+,.0f}주'
    a, sign = abs(v), ('+' if v > 0 else '-')
    if a >= 10000:
        return f'{sign}{a / 10000:,.2f}조'
    if a >= 100:
        return f'{sign}{a:,.0f}억'
    if a >= 1:
        return f'{sign}{a:.1f}억'
    return f'{sign}{a * 10000:,.0f}만'


# 수급 출처의 한글 표기. KIS 가 주 소스라 KIS 값에는 출처를 따로 적지 않고,
# 폴백 값에만 적는다 — 성격이 다르다(금액 vs 수량). 조용히 바꾸지 않는다(2장 6번).
FLOW_SOURCE_KO = {'naver': '네이버'}
# 사유 한 줄의 상한. 종목마다 붙으므로 길면 구획이 통째로 잘린다(_fit).
REASON_MAX = 100


def _flow_bit(fl):
    """한 종목의 수급 한 줄. 받은 구분만 적는다 — 없는 구분은 0 이 아니다.

    네이버 폴백(주 단위)이면 `기관 +63,482주(추정 +186억) · 외국인 … (네이버)` —
    출처를 적고, 종가 환산 금액에는 반드시 '추정' 을 붙인다 (CLAUDE.md 2장 1번).
    """
    if not fl:
        return None
    unit = fl.get('unit')
    est = fl.get('amt_est') if isinstance(fl.get('amt_est'), dict) else {}
    est = est if est.get('is_estimate') else {}
    parts = []
    for who in ('기관', '외국인', '개인'):
        t = _eok(fl.get(who), unit)
        if t is None:
            continue
        a = _eok(est.get(who), est.get('unit') or '억원')
        if a is not None:
            t += f'(추정 {a})'
        parts.append(f'{who} {t}')
    if not parts:
        return None
    bit = ' · '.join(parts)
    src = FLOW_SOURCE_KO.get(fl.get('source'))
    return f'{bit} ({src})' if src else bit


def _qualifies(x, key, kind):
    lab = (x.get(key) or {}).get('label')
    if lab in KIND_ORDER and KIND_ORDER.index(lab) >= KIND_ORDER.index(kind):
        return lab
    return None


def _md(published_at):
    """'2026-09-21T09:10+09:00' → '9/21'. 날짜를 못 읽으면 None."""
    s = str(published_at or '')[:10]
    if len(s) == 10 and s[4] == '-' and s[7] == '-':
        try:
            return f'{int(s[5:7])}/{int(s[8:10])}'
        except ValueError:
            return None
    return None


def _cut(text, n):
    t = ' '.join(str(text or '').split())
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


def _trigger_lines(code, triggers, max_lines=TRIGGER_LINES_MAX):
    """한 종목의 재료 줄. 종목당 최대 두 줄, **링크 없음**.

      ↳ 재료: <제목 60자> (매체 · M/D)
      ↳ 공시: <제목 60자> (DART · M/D)
      ↳ X: <본문 60자> (@계정 · M/D)

    링크를 넣지 않는 이유 — 4,096자 한도에서 URL 하나가 종목 한 줄만큼 길고, 레거시
    Markdown 은 괄호가 든 URL 에서 링크를 못 닫는다. 링크는 대시보드 payload 에 있다.
    제목·매체는 우리가 통제하지 못하는 문자열이라 `_safe` 를 거친다.
    """
    if not isinstance(triggers, dict):
        return []
    e = (triggers.get('by_code') or {}).get(code) or {}
    n = triggers.get('title_chars_tg') or TRIGGER_TITLE_MAX
    out = []
    for it in (e.get('items') or [])[:max_lines]:
        pre = TRIGGER_PREFIX.get(it.get('source'), '재료')
        who = it.get('publisher') or ('DART' if it.get('source') == 'dart' else '출처 미상')
        md = _md(it.get('published_at'))
        tail = f'{_safe(who)} · {md}' if md else _safe(who)
        out.append(f'  ↳ {pre}: {_safe(_cut(it.get("title"), n))} ({tail})')
    return out


def _newhigh_line(x, flows, labels, kind=NEWHIGH_KIND, triggers=None):
    """한 종목 한 줄. **목록에 든 근거가 되는 라벨을 적는다.**

    종가로 60일, 고가로 52주를 뚫은 종목이 있다. 그런 줄에 '60일' 이라고 적으면
    '52주 이상' 이라는 제목 아래 60일이 앉아 있어, 읽는 쪽은 목록이 틀렸다고
    본다. 어느 기준으로 들어왔는지를 라벨이 말해야 한다.

    `triggers` 를 주면 수급 줄 뒤에 재료 줄(최대 둘)을 붙인다. 반환은 줄 목록이다.
    """
    lines = _newhigh_base(x, flows, labels, kind)
    if triggers is not None:
        lines.extend(_trigger_lines(x.get('code'), triggers))
    return lines


def _newhigh_base(x, flows, labels, kind=NEWHIGH_KIND):
    """종목 줄 + 수급 줄. 재료 줄은 `_trigger_lines` 가 따로 만든다."""
    cl = _qualifies(x, 'close_basis', kind)
    hi = _qualifies(x, 'high_basis', kind)
    lab = cl or hi or ''
    ko = labels.get(lab, lab)
    # 종가로는 못 넘고 고가로만 뚫은 종목은 그렇게 적는다. 같은 줄에 섞으면
    # '일간 마이너스인데 신고가' 가 되어 읽는 쪽이 매번 멈춘다.
    if not cl:
        ko += '(고가)'
    head = f'· {_safe(x.get("name") or x.get("code"))} {ko}'
    bits = []
    if x.get('chg_pct') is not None:
        bits.append(f'{x["chg_pct"]:+.2f}%')
    if x.get('vol_mult') is not None:
        bits.append(f'거래량 {x["vol_mult"]:,.1f}배')
    if bits:
        head += ' ' + ' · '.join(bits)
    fl = (flows or {}).get(x.get('code'))
    bit = _flow_bit(fl)
    if bit:
        # 기준일이 다르면 그 날짜를 적는다. 아무 말이 없으면 서로 다른 날의
        # 사실이 같은 날로 읽힌다.
        d = fl.get('as_of')
        stale = f' ({d} 자)' if d and d != (flows or {}).get('_as_of') else ''
        return [head, f'  ↳ {bit}{stale}']
    if flows is None:
        return [head]
    # 두 소스 다 못 받은 종목. 사유가 있으면 한 줄로 붙인다 — '못 받음' 만 열한
    # 줄 서 있으면 무엇을 고쳐야 하는지 읽는 쪽이 알 수 없다.
    why = (flows.get('_failed') or {}).get(x.get('code'))
    if why:
        why = str(why)
        if len(why) > REASON_MAX:
            why = why[:REASON_MAX - 1].rstrip() + '…'   # 잘랐다는 것도 보인다
        return [head, f'  ↳ 수급 못 받음 — {_safe(why)}']
    return [head, '  ↳ 수급 못 받음']


def _newhigh_group(newhigh, stockflows, kind=NEWHIGH_KIND, triggers=None):
    """52주 이상 신고가 + 종목별 수급 + 재료. 문턱 없이 **전 종목**이다.

    맨 앞에 둔다. 이 리포트의 제목이 '신고가 및 등락률 Top 랭킹' 이고,
    신고가가 먼저다. 항목은 종목 단위 엔트리로 둔다 — `_fit` 이 재료 줄을
    먼저 뺄 수 있게 (`_drop_one`).
    """
    nh = newhigh if isinstance(newhigh, dict) else {}
    labels = nh.get('labels') or {}
    rows = [x for x in nh.get('achieved') or []
            if _qualifies(x, 'close_basis', kind) or _qualifies(x, 'high_basis', kind)]
    ko = labels.get(kind, kind)
    sf = stockflows if isinstance(stockflows, dict) else None
    flows = dict(sf.get('by_code') or {}) if sf else None
    if flows is not None:
        flows['_as_of'] = sf.get('as_of')
        flows['_failed'] = sf.get('failed') or {}
    if not rows:
        # 0 건인 것도 사실이다. 빈 구획으로 두지 않는다.
        return dict(kind='신고가', drop_rank=2, head=f'*{_safe(ko)} 이상 신고가*',
                    items=[f'{_safe(ko)} 이상 신고가를 낸 종목이 없음'])
    # 거래량이 많이 는 순. 같은 배수면 거래대금 순.
    rows.sort(key=lambda y: (-(y.get('vol_mult') or 0), -(y.get('turnover') or 0)))
    # 파일이 없는 대역(absent)과 단계가 통째로 죽은 파일(failed)은 재료를 모은 것이
    # 아니다 — 재료 줄도, '재료는 …' 머리도 붙이지 않는다. 결손 줄은 missing 이 말한다.
    tr = (triggers if isinstance(triggers, dict)
          and not triggers.get('absent') and not triggers.get('failed') else None)
    items = []
    for x in rows[:NEWHIGH_MAX]:
        items.append(dict(lines=_newhigh_base(x, flows, labels, kind),
                          trig=_trigger_lines(x.get('code'), tr) if tr else []))
    if len(rows) > NEWHIGH_MAX:
        items.append(f'· 외 {len(rows) - NEWHIGH_MAX}종목 — 전체는 대시보드')
    note = '↳ 는 그날 종목별 순매수'
    if tr:
        # 기사·공시는 당일 것만, X 포워딩은 인박스 창(기본 24시간)이라 전일 오후
        # 포워딩도 든다 — 줄에 M/D 가 붙지만 머리가 범위를 잘못 말하면 안 된다.
        note += ' · 재료는 당일 기사·공시'
        hours = tr.get('inbox_hours')
        note += f' · X 포워딩은 최근 {hours}시간' if hours else ' · X 포워딩'
        # '공시는 HH:MM 접수분까지' 는 DART 를 실제로 부른 날에만 — ingest 의 _finish 와
        # 같은 조건이다. DART 를 접은 날(키 없음·한도)에 이 문장이 서면 결손 줄과
        # 모순되고, 읽는 쪽은 공시를 봤다고 읽는다 (CLAUDE.md 2장 1번).
        hhmm = str(tr.get('collected_at') or '')[11:16]
        if hhmm and ((tr.get('sources') or {}).get('dart') or {}).get('ok'):
            note += f' · 공시는 {hhmm} 접수분까지'
    head = f'*{_safe(ko)} 이상 신고가 {len(rows)}종목* ({note})'
    if flows and any(isinstance(v, dict) and v.get('source') == 'naver'
                     for v in flows.values()):
        head += ' · (네이버) 표시는 순매매량(주), 억원은 종가 환산 추정'
    # 가장 마지막에 버린다. 다른 구획은 대시보드 첫 화면에 그대로 있지만
    # 종목별 수급은 여기서만 한 줄로 붙여 준다.
    return dict(kind='신고가', drop_rank=2, head=head, items=items)


def rankings_message(rankings, url=None, newhigh=None, stockflows=None, triggers=None):
    """rankings.json → 텔레그램 마크다운 문자열. 발송하지 않는다.

    `newhigh` 를 주면 52주 이상 신고가와 **종목별 수급**을 맨 앞에 싣는다.
    수급은 `stockflows`(state/stockflows.json)에서, 재료는 `triggers`
    (state/triggers.json)에서 온다 — 여기서 받지 않는다. triggers 의 결손은 결손
    첫 줄에 합류하고, 신고가를 싣는데 triggers 가 None 이면(파일 없음) '수집되지
    않음(단계 실패)' 을 결손에 넣는다 — 재료 줄이 없는 것과 못 모은 것은 다르다.
    """
    r = rankings if isinstance(rankings, dict) else {}
    groups = []
    if newhigh:
        miss = list(r.get('missing') or [])
        extra = ([TRIGGER_ABSENT_LINE] if triggers is None
                 else list((triggers or {}).get('missing') or []))
        for m in extra:
            if m not in miss:
                miss.append(m)
        r = dict(r, missing=miss)
        groups.append(_newhigh_group(newhigh, stockflows, triggers=triggers))
    groups += [_sector_group(r), _cross_group(r)]
    tail = ['']
    link = _link(url, '전체 표')
    if link:
        tail.append(link)
    return _fit(_head_lines(r), groups, tail)


# ─────────────────────────── 초안 메시지 ───────────────────────────
def _inline(t):
    """의도된 마크업(**굵게**·`코드`)만 살리고 나머지 특수문자는 중화한다."""
    keep = []

    def stash(text):
        keep.append(text)
        return f'\x1a{len(keep) - 1}\x1a'

    t = str(t).replace('\x1a', '')      # 자리표시자와 부딪히는 글자를 먼저 없앤다
    t = _BOLD.sub(lambda m: stash('*' + _safe(m.group(1)) + '*'), t)
    t = _CODE.sub(lambda m: stash('`' + _safe(m.group(1)) + '`'), t)
    return _MARK.sub(lambda m: keep[int(m.group(1))], _safe(t))


_PIPE = re.compile(r'(?<!\\)\|')     # 칸 구분자. `\|` 는 GFM 이스케이프라 칸을 가르지 않는다


def _cells(line):
    """표 한 줄 → 칸 목록. 양 끝 파이프는 GFM 에서 선택이라 있으면 벗긴다."""
    s = line.strip()
    s = s[1:] if s.startswith('|') else s
    s = s[:-1] if s.endswith('|') and not s.endswith('\\|') else s
    return [c.strip().replace('\\|', '|') for c in _PIPE.split(s)]


def _is_rule(cells):
    """`|---|---:|` 구분선. 행이 아니라 표의 서식이라 줄로 내지 않는다.

    칸마다 대시가 하나는 있어야 한다(GFM). 빈 칸뿐인 줄(`|` 하나)까지 구분선으로
    보면 바로 앞 데이터 행을 헤더로 삼켜 한 행이 조용히 빠진다.
    """
    return all(c and '-' in c and set(c) <= set('-: ') for c in cells)


def _table_line(cells, cols):
    """표 한 행 → 모바일에서 읽히는 한 줄.

    `| 로봇 | +2.31% | 2 | 1,234억 |` 은 `• 로봇 +2.31% · 신고가 2 · 거래대금 1,234억`
    이 된다. 첫 칸은 행의 이름이라 열 이름을 안 붙이고, 나머지 칸은 열 이름을 앞에
    적는다 — 표에서는 위의 헤더가 말해 주던 것이 줄로 풀면 사라져 '2' 가 무엇의
    2인지 알 수 없다. 예외는 이름 옆의 등락률 하나다. 둘째 칸의 열 이름이 등락률이면
    이름에 그냥 붙인다 — 이 레포는 어디서나 '이름 +x%' 로 적으므로(CLAUDE.md 7장
    문체, 랭킹 줄) 그 자리의 % 는 그대로 읽힌다. 둘째 칸이 신고가 종목수·거래대금
    같은 다른 열이면 셋째 칸처럼 이름을 단다. `2` 만 있는 줄은 순위인지 종목수인지
    모른다.
    `–` 는 계산되지 않은 값의 표식이라 지우지 않는다(모듈 규칙, CLAUDE.md 2장
    6번). 표에서 보이던 '이 값은 없다' 가 줄에서 사라지면 그 열이 원래 없던 표와
    구분이 안 된다. 빈 칸만 뺀다 — 표에서는 빈 칸이 정렬을 지켜 주지만 한 줄에서는
    글자 수만 늘린다.
    헤더 없는 표(`cols` 가 비었다)는 값만 ` · ` 로 잇는다. 헤더보다 칸이 많은 행의
    넘친 칸도 이름 없이 나가고, 그 사실은 `draft_message` 가 표 끝에 한 번 적는다.
    """
    head = _inline(cells[0]) if cells[0] else ''
    parts = []
    for j, c in enumerate(cells[1:], start=1):
        if not c:
            continue
        name = cols[j] if j < len(cols) else ''
        if j == 1 and (not name or '등락률' in name):
            head = f'{head} {_inline(c)}'.strip()
            continue
        parts.append(f'{_inline(name)} {_inline(c)}' if name else _inline(c))
    if head:
        parts.insert(0, head)
    return '• ' + ' · '.join(parts) if parts else None


def draft_message(draft_md, url=None):
    """writer 가 만든 draft.md → 텔레그램용으로 다듬은 문자열.

    초안은 17:15 에 사람이 검수하는 물건이라 **내용을 고르지 않고 서식만 바꾼다.**
    랭킹과 달리 여기서 문단을 추려 내면 검수할 것이 사라진다. 같은 이유로 4,096자를
    넘어도 버리지 않는다 — 넘치는 건 `send()` 가 줄 경계에서 나눠 보내면 될 일이라
    한 화면에 맞추자고 본문을 깎을 이유가 없다.
    표도 버리지 않는다 (D-084). 모바일 폭에서 표는 읽히지 않으니 행마다 한 줄로 푼다
    (`_table_line`). 다음 줄이 구분선인 줄이 헤더다 — 그 열 이름을 읽어 두었다가
    표가 끝나면 잊는다. 표가 여럿이면 열 구성이 다르고, 빈 줄 없이 붙은 두 표도
    헤더가 나오는 자리에서 갈린다. 구분선은 행이 아니라 줄로 내지 않는다.
    앞 파이프는 GFM 에서 선택이라 구분선·행은 없어도 알아보지만, 표 밖에서 앞
    파이프 없이 `|` 만 든 줄은 본문으로 본다 — 산문에 섞인 `|` 를 표로 오해하는
    쪽이 더 나쁘다.
    헤더보다 칸이 많은 행은 넘친 칸을 이름 없이 싣고 표 끝에 그 사실을 한 줄 적는다.
    조용히 버리지 않는다(2장 6번). 칸이 모자란 행은 빈 칸으로 본다 — GFM 과 같다.
    """
    raw = (draft_md or '').splitlines()
    lines = []
    cols = None      # 지금 읽는 표의 열 이름. 헤더 없는 표면 [], 표 밖이면 None
    wide = 0         # 이 표에서 헤더보다 칸이 많았던 행 수

    def end_table():
        nonlocal cols, wide
        if wide:
            lines.append(f'— 위 표 {wide}행은 헤더({len(cols)}열)보다 칸이 많다. '
                         '넘친 칸은 열 이름 없이 실었다')
        cols, wide = None, 0

    for i, r in enumerate(raw):
        line = r.rstrip()
        s = line.lstrip()
        if '|' in s:
            cells = _cells(s)
            if _is_rule(cells):
                continue
            nxt = raw[i + 1].strip() if i + 1 < len(raw) else ''
            if '|' in nxt and _is_rule(_cells(nxt)):
                end_table()               # 빈 줄 없이 붙은 앞 표는 여기서 끝난다
                cols = cells
                continue
            if cols is not None or s.startswith('|'):
                if cols is None:
                    cols = []             # 헤더 없이 데이터부터 시작한 표
                if cols and len(cells) > len(cols):
                    wide += 1
                out = _table_line(cells, cols)
                if out:
                    lines.append(out)
                continue
        end_table()                       # 표가 끝났다. 열 이름은 다음 표로 새지 않는다
        if line.startswith('---'):
            continue
        if not line.strip():
            lines.append('')
            continue
        if line.startswith('#'):
            lines.append(f'*{_inline(line.lstrip("#").strip())}*')
        elif line.startswith('- '):
            lines.append('• ' + _inline(line[2:].strip()))
        elif line.lstrip().startswith('»'):
            lines.append('  ' + _inline(line.strip()))
        elif line.startswith('>'):
            lines.append(_inline(line.lstrip('> ').strip()))
        else:
            lines.append(_inline(line))
    end_table()
    while lines and not lines[-1]:
        lines.pop()

    tail = ['']
    link = _link(url, '대시보드')
    if link:
        tail.append(link)
    return '\n'.join(lines + tail)


# ─────────────────────────── 발송 ───────────────────────────
def _cred(name):
    """자격증명. .env 가 없거나 로딩이 깨져도 예외를 내지 않고 빈 문자열."""
    try:
        from ..ingest import creds
        return creds.get(name) or ''
    except Exception:                                    # noqa: BLE001 - 사유는 호출부가 만든다
        return ''


def _why(r):
    """텔레그램이 준 거절 사유를 그대로 옮긴다. 우리가 요약하지 않는다."""
    try:
        j = r.json()
        return j.get('description') or str(j)
    except Exception:                                    # noqa: BLE001
        return (r.text or '')[:200]


def send(text, token=None, chat_id=None, silent=False, parse_mode='Markdown'):
    """실제 발송. 반환 (ok, 사유). 4096자 초과분은 여러 건으로 나눠 보낸다.

    parse_mode 를 None 으로 주면 **서식 없이 평문**으로 보낸다. 미국장 브리프가
    그렇다 — 본문에 `**...**` 와 `—` 가 섞여 있어 Markdown 으로 보내면 텔레그램이
    엔티티가 안 닫혔다며 400 으로 거절한다. 국장 리포트는 우리가 서식을 붙여
    만들므로 기본값은 그대로 둔다.
    """
    token = token or _cred('TELEGRAM_BOT_TOKEN')
    chat_id = chat_id or _cred('TELEGRAM_CHAT_ID')
    if not token:
        return False, 'TELEGRAM_BOT_TOKEN 이 없다. board/.env 에 넣거나 환경변수로 주입하라'
    if not chat_id:
        return False, 'TELEGRAM_CHAT_ID 가 없다. 보낼 대화방을 모른다'
    if not (text or '').strip():
        return False, '본문이 비어 있다. 보낼 것이 없다'
    try:
        import requests
    except ImportError as ex:
        return False, f'requests 가 없다: {ex}'

    chunks = _split(text)
    for i, chunk in enumerate(chunks, 1):
        try:
            r = requests.post(
                API.format(token=token, method='sendMessage'),
                data=dict(chat_id=chat_id, text=chunk,
                          disable_web_page_preview=True,
                          disable_notification=bool(silent),
                          **({'parse_mode': parse_mode} if parse_mode else {})),
                timeout=TIMEOUT)
        except Exception as ex:                          # noqa: BLE001 - 사유를 문자열로 보존
            return False, (f'{i}/{len(chunks)}번째 조각 전송 실패: '
                           + _safe_err(ex, token))
        if r.status_code != 200:
            return False, f'{i}/{len(chunks)}번째 조각 거부: HTTP {r.status_code} · {_why(r)}'
    return True, f'{len(chunks)}건 발송'


# sendDocument 캡션 상한. 본문(4096)과 다르다 — 넘기면 텔레그램이 거부한다.
TG_CAPTION_LIMIT = 1024


def send_document(path, caption='', token=None, chat_id=None, silent=False):
    """파일 한 개를 첨부로 보낸다. 반환 (ok, 사유).

    17:30 자동 발송(D-063)이 보드 HTML 과 랭킹 엑셀을 이걸로 보낸다.
    파일이 없으면 조용히 성공으로 넘기지 않는다 — 없는 것을 보냈다고 적으면
    받은 사람이 찾다가 끝난다.
    """
    token = token or _cred('TELEGRAM_BOT_TOKEN')
    chat_id = chat_id or _cred('TELEGRAM_CHAT_ID')
    if not token:
        return False, 'TELEGRAM_BOT_TOKEN 이 없다'
    if not chat_id:
        return False, 'TELEGRAM_CHAT_ID 가 없다'
    if not os.path.exists(path):
        return False, f'보낼 파일이 없다: {path}'
    try:
        import requests
    except ImportError as ex:
        return False, f'requests 가 없다: {ex}'
    try:
        with open(path, 'rb') as f:
            r = requests.post(
                API.format(token=token, method='sendDocument'),
                data=dict(chat_id=chat_id,
                          caption=(caption or '')[:TG_CAPTION_LIMIT],
                          disable_notification=bool(silent)),
                files={'document': (os.path.basename(path), f)},
                timeout=TIMEOUT * 4)     # 파일 업로드는 본문보다 오래 걸린다
    except Exception as ex:                              # noqa: BLE001
        return False, f'전송 실패: {_safe_err(ex, token)}'
    if r.status_code != 200:
        return False, f'거부: HTTP {r.status_code} · {_why(r)}'
    return True, os.path.basename(path)


def check(token=None, chat_id=None):
    """봇 연결 확인. getMe 를 부르고 (ok, 봇이름 또는 사유) 반환."""
    token = token or _cred('TELEGRAM_BOT_TOKEN')
    chat_id = chat_id or _cred('TELEGRAM_CHAT_ID')
    if not token:
        return False, 'TELEGRAM_BOT_TOKEN 이 없다'
    try:
        import requests
        r = requests.get(API.format(token=token, method='getMe'), timeout=TIMEOUT)
    except Exception as ex:                              # noqa: BLE001
        return False, 'getMe 실패: ' + _safe_err(ex, token)
    if r.status_code != 200:
        return False, f'getMe HTTP {r.status_code} · {_why(r)}'
    name = '@' + (((r.json() or {}).get('result') or {}).get('username') or '이름없음')
    if not chat_id:
        # 봇은 살아 있어도 보낼 곳이 없으면 발송은 실패한다. 절반의 성공을 참으로 보고하지 않는다.
        return False, f'{name} 은 응답하지만 TELEGRAM_CHAT_ID 가 없다'

    # 봇이 살아 있다는 것과 그 방에 보낼 수 있다는 것은 다르다. 봇이 방에서
    # 쫓겨났거나 chat_id 가 틀리면 getMe 는 통과하고 발송만 실패한다.
    # getChat 은 **메시지를 보내지 않고** 목적지를 확인한다.
    try:
        c = requests.get(API.format(token=token, method='getChat'),
                         params={'chat_id': chat_id}, timeout=TIMEOUT)
    except Exception as ex:                              # noqa: BLE001
        return False, f'{name} · getChat 실패: ' + _safe_err(ex, token)
    if c.status_code != 200:
        return False, f'{name} · 대화방 확인 실패 HTTP {c.status_code} · {_why(c)}'
    room = (c.json() or {}).get('result') or {}
    where = room.get('title') or room.get('username') or room.get('type') or '대화방'
    return True, f'{name} → {where}'

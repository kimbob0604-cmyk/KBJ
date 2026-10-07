#!/usr/bin/env python3
"""일일 노트 → 텔레그램 메시지. 계약은 `docs/NOTE.md`.

`rankings_message` 와 반대 방향의 물건이다. 랭킹은 보드가 계산한 것을 추려
3초에 읽히게 만들고, 이쪽은 **사람이 쓴 판단을 나른다.** 그래서 추리지 않는다
(NOTE.md D4) — 넘치면 `send()` 가 줄 경계에서 나눈다.

사람이 쓰는 것은 제목·사실·판단뿐이고 **수치는 쓰지 않는다**(D2). 등락률과
신고가 라벨은 보드 state 에서 채운다. 손으로 옮기면 출처가 사라지고(2장 2번)
옮겨 적다 틀린 값은 아무도 못 잡는다.

이름을 유니버스에서 못 찾으면 **보내지 않는다**(D3). 오타나 사명 변경을 그냥
통과시키면 그 종목이 노트에서 조용히 빠지고, 빠진 줄 모른 채 읽게 된다.
후보를 붙이는 판정은 `engine/themes.near_names` 를 그대로 쓴다 — 사본을 두면
다음에 문턱을 손볼 때 한쪽만 고쳐진다.
"""
from ..engine.themes import display_index, near_names, norm
from .telegram import _inline, _safe

# newhigh.json 의 hits 키 → 사람이 읽는 이름. aggregate.MULTI_LABEL_SET 과 같은 셋이다.
HIT_LABEL = {'hist': '역사적', 'w52': '52주', 'd60': '60일'}
# 우선순위. 역사적이면 52주·60일도 참이라 가장 센 것 하나만 적는다.
HIT_ORDER = ('hist', 'w52', 'd60')

PICK_HEAD = {'w52': '52주 신고가', 'watch': '개별', 'surge': '급등 포착'}
PICK_ORDER = ('w52', 'watch', 'surge')

WEEKDAY = '월화수목금토일'


def _pct(v):
    """등락률 표시. `None` 은 계산되지 않았다는 뜻이라 0 으로 채우지 않는다 (2장 1번)."""
    if v is None:
        return None
    return f'{v:+.2g}%' if abs(v) < 1 else f'{v:+.1f}%'


def _title(date):
    """`2026-09-21` → `9/21(월) 국장 신고가`. 파싱이 안 되면 원문을 그대로 쓴다."""
    import datetime as dt
    try:
        d = dt.date.fromisoformat(str(date))
    except ValueError:
        return f'{date} 국장 신고가'
    return f'{d.month}/{d.day}({WEEKDAY[d.weekday()]}) 국장 신고가'


class Resolver:
    """이름 → 보드가 아는 수치. 못 찾은 이름과 수치가 빈 이름을 따로 모은다."""

    def __init__(self, universe, newhigh=None):
        stocks = (universe or {}).get('stocks') or []
        self._by_name = {}
        for s in stocks:
            if s.get('name'):
                self._by_name.setdefault(norm(s['name']), s)
        self._display = display_index(stocks)
        self._hits = {}
        for group in ('achieved', 'proximity'):
            for r in (newhigh or {}).get(group) or []:
                if r.get('code'):
                    self._hits[r['code']] = r.get('hits') or {}
        self.unknown = []      # [(이름, [후보…])] — 있으면 보내지 않는다
        self.no_value = []     # [이름] — 찾았지만 등락률이 없다

    def label(self, name):
        """`이름 +24.0% (52주)`. 못 찾으면 None 을 돌려주고 unknown 에 적는다."""
        s = self._by_name.get(norm(name))
        if s is None:
            self.unknown.append((name, near_names(name, self._display)))
            return None
        bits = [_safe(name)]
        pct = _pct(s.get('chg_pct'))
        if pct:
            bits.append(pct)
        else:
            self.no_value.append(name)
        hit = next((k for k in HIT_ORDER if (self._hits.get(s.get('code')) or {}).get(k)), None)
        if hit:
            bits.append(f'({HIT_LABEL[hit]})')
        return ' '.join(bits)


def _facts(lines):
    return [_inline(x) for x in (lines or []) if str(x).strip()]


def _views(lines):
    if isinstance(lines, str):
        lines = [lines]
    return ['  » ' + _inline(x) for x in (lines or []) if str(x).strip()]


def _head_message(note, r, missing):
    out = []
    if missing:
        out.append(missing)
    out.append(f'*{_inline(_title(note.get("date")))}*')
    out.append(_inline(note['verdict']))
    if note.get('index'):
        out += ['', '*지수*'] + _facts(note['index'])
    macro = note.get('macro') or []
    if macro:
        out += ['', '*매크로*']
        for m in macro:
            what = _inline(m.get('what', ''))
            note_ = m.get('note')
            out.append(f'• {what} — {_inline(note_)}' if note_ else f'• {what}')
            out += _views(m.get('view'))
    return '\n'.join(out)


def _theme_message(note, r):
    themes = note.get('themes') or []
    if not themes:
        return None
    out = ['*테마*']
    for t in themes:
        out += ['', f'*{_inline(t.get("name", ""))}*']
        out += _facts(t.get('driver'))
        names = [x for x in (r.label(n) for n in t.get('names') or []) if x]
        if names:
            out.append(' · '.join(names))
        out += _views(t.get('view'))
    return '\n'.join(out)


def _picks_message(note, r):
    picks = note.get('picks') or {}
    out = []
    for key in PICK_ORDER:
        items = picks.get(key) or []
        if not items:
            continue
        if out:
            out.append('')
        out.append(f'*{PICK_HEAD[key]}*')
        for it in items:
            lab = r.label(it.get('name', ''))
            if lab is None:
                continue
            why = it.get('why')
            out.append(f'{lab} — {_inline(why)}' if why else lab)
            out += _views(it.get('view'))
    return '\n'.join(out) if out else None


def note_messages(note, universe, newhigh=None):
    """노트 → (메시지 목록, 막는 사유 목록).

    사유가 하나라도 있으면 **보내지 않는다.** 부분 발송을 하지 않는 이유는
    노트가 하나의 글이기 때문이다 — 종목 하나가 조용히 빠진 노트는 읽는 사람이
    그 사실을 알 길이 없다.
    """
    errors = []
    if not (note or {}).get('date'):
        errors.append('date 가 없다')
    if not (note or {}).get('verdict'):
        errors.append('verdict 가 없다 — 첫 줄에 나갈 결론이다')
    if not (universe or {}).get('stocks'):
        errors.append('universe.json 이 없거나 비었다 — 수치를 채울 근거가 없다')
    if errors:
        return [], errors

    r = Resolver(universe, newhigh)
    # 결손 줄은 머리보다 위에 둔다 (2장 6번). 해석을 먼저 돌려야 개수를 안다.
    body = [_theme_message(note, r), _picks_message(note, r)]
    if r.unknown:
        return [], ['유니버스에서 못 찾은 이름 — ' + ' / '.join(
            f'{nm}{"(혹시 " + ", ".join(c) + "?)" if c else ""}' for nm, c in r.unknown)]
    missing = (f'— 등락률을 못 채운 종목 {len(r.no_value)}건 '
               f'({", ".join(r.no_value[:3])}{"…" if len(r.no_value) > 3 else ""})'
               if r.no_value else None)
    return [m for m in [_head_message(note, r, missing)] + body if m], []

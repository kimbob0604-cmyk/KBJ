#!/usr/bin/env python3
"""
다이제스트 검증 — XDIGEST.md 3-3. `writer/verify.py` 의 규약을 옮긴다.

다른 것은 **대조 대상 하나**다. 보드 초안은 코드가 계산한 사실 팩과 대조하는데,
여기는 **게시물 원문**과 대조한다. 그래서 `posts.json` 의 `text` 는 원문 그대로
저장돼 있어야 한다 — 요약본이면 이 파일은 아무것도 검증하지 못한다.

`norm` · `BARE` · `FIELD_LEAK` 는 `writer/verify.py` 에서 가져온다. 사본을 두면
다음에 경계 규칙을 손볼 때 한쪽만 고쳐진다 — 2026-09-21 에 `FIELD_LEAK` 의 경계를
ASCII 로 고친 것이 그런 종류의 버그였다.

보는 것 (3-3 표):

  1. 수치 토큰    그 줄의 근거 원문에서 같은 방식으로 뽑은 토큰 집합에 있어야
                 한다 (계산·환산·절단·단위 탈락 금지). 부분 문자열이 아니다 —
                 `writer/verify.allowed_numbers` 와 같은 방식이고, 계약
                 3-3 1번의 '부분 문자열' 문언은 이 구현으로 고친다
  2. `@계정`      posts.json 의 account 집합 안
  3. `$티커`      그 줄의 근거 원문에 있어야 한다
  4. 국내 종목명  표제어가 출력에 있으면 근거 원문에 그 표제어의 별칭 중 하나가 있어야 한다
  5. 종목코드     모델 출력에 `(NNNNNN)` 이 있으면 위반 — 렌더가 사전에서 붙인다
  6. 근거 id      실재하고 그 단위에 배정된 id
  7. snake_case   필드 이름 유출

**위반 줄은 지운다.** 재시도 뒤에도 걸리면 그 줄을 빼고 결손에 적는다. 조용히
통과시키지 않는다 (CLAUDE.md 2장 4·6번). 소주제 단위 판정(3-3 8번)은
`unit_ok()` 가 한다 — 통과한 사실이 1개 미만이거나 출처 계정이 2개 미만이면
단독 소식으로 내린다.

인명·기관명(Simon Woo, 이종환)은 기계 검증 대상이 아니다 — `post_ids` 로 추적만
되게 한다 (3-3).
"""
import os
import re

import yaml

from ..engine.config import ROOT
from ..writer.verify import BARE, FIELD_LEAK, norm

NAMES_FILE = os.path.join(ROOT, 'knowledge', 'xdigest_names.yaml')

# ── 수치 토큰 (3-3 1번) ──────────────────────────────────────────────
# 대조는 **토큰 집합**이다. 부분 문자열로 보면 `$73.39` 의 근거로 `$73.3` 이,
# `$80.4B` 의 근거로 `$80.4` 가 통과한다 — 절단·단위 탈락이 사실 줄로 나간다.
# `writer/verify.allowed_numbers` 가 처음부터 집합으로 본 이유가 그것이고, 이쪽이
# 베끼겠다고 한 규약이라 같은 방식으로 맞춘다.
#
# 토큰은 `(부호, 숫자, 단위 반열)` 로 정규화한다.
#   - 천단위 쉼표는 지우고 소수점은 남긴다 — `6,808.21` 과 `6,808.2` 는 다른 값이다.
#   - `+` 는 버리고 `-` 는 남긴다 — 부호가 뒤집히면 다른 사실이지만 `30%` 와
#     `+30%` 는 같은 값이다.
#   - `$` 는 버린다 — 통화 기호를 떼는 것은 자릿수를 바꾸지 않는다.
#   - 자릿수를 바꾸는 단위는 반열이 다르다(`M`↔`만` 은 다른 반열) — 환산을
#     통과시키면 안 된다. 표기만 다른 것은 같은 반열이다(`million`↔`M`).

# 단위 반열. 없는 단위는 글자 그대로가 반열이다(`%`·`조`·`nm`).
UNITS = {
    # 자릿수를 바꾸는 단위. 표기가 달라도 배수가 같으면 같은 반열이다.
    'k': 'K', 'thousand': 'K',
    'm': 'M', 'mn': 'M', 'million': 'M', 'millions': 'M',
    'b': 'B', 'bn': 'B', 'billion': 'B', 'billions': 'B',
    't': 'T', 'tn': 'T', 'trillion': 'T', 'trillions': 'T',
    # 자릿수를 바꾸지 않는 수량·기간 단위. 단위 없는 수치와 같은 반열로 둔다 —
    # 원문이 `2030` 이라고만 써도 우리말은 `2030년` 이고, 그것은 환산이 아니다.
    '년': '', 'year': '', 'years': '', 'yr': '',
    '개월': '', 'month': '', 'months': '',
    '개': '', '건': '', '장': '', '대': '', '명': '',
}

# 숫자 뒤에 붙는 단위. 긴 것부터 적는다 — `Gbps` 가 `Gb` 로, `억원` 이 `억` 으로
# 잘리면 반열이 달라진다. ASCII 단위는 뒤에 영문·숫자가 붙으면 단위가 아니고
# (`2Bn` 은 `B` 가 아니다), 한글 단위는 뒤에 한글 수량사가 붙을 수 있다(`3만개`).
ASCII_UNITS = ('%p', '%', 'Gbps', 'GB', 'Gb', 'TB', 'GW', 'MW', 'bp', 'pt', 'nm',
               'millions', 'million', 'billions', 'billion', 'trillions',
               'trillion', 'thousand', 'months', 'month', 'years', 'year',
               'yr', 'Mn', 'Bn', 'Tn', 'mn', 'bn', 'tn',
               'T', 'B', 'M', 'K', 'k', 'x')
KOREAN_UNITS = ('억원', '조원', '만원', '천원', '억', '조', '만', '천', '원',
                '주', '배', '개월', '년', '장', '대', '건', '개', '명')

_N = r'[+-]?\d[\d,]*(?:\.\d+)?'
# 단위 갈래가 먼저다. `$` 를 단위 갈래가 함께 삼켜야 `$46.7B` 가 `$46.7` + 버린 `B`
# 로 쪼개지지 않는다 — 쪼개지면 `$46.7B` → `$46.7M` 자릿수 스왑이 그대로 통과했다.
NUM = re.compile(
    r'\$?\s*' + _N + r'\s*(?:' + '|'.join(ASCII_UNITS) + r')(?![A-Za-z0-9])'
    r'|\$?\s*' + _N + r'\s*(?:' + '|'.join(KOREAN_UNITS) + r')(?!\d)'
    r'|\$\s*' + _N)

# 토큰을 반열로 가르는 갈래. `norm` 뒤(공백 없음)의 문자열에 건다.
PARTS = re.compile(r'^\$?([+-]?)(\d[\d,]*(?:\.\d+)?)(.*)$')

# 근거 원문 쪽에서만 더 뽑는 것 — 단위 없는 정수와 영어 수사.
# 원문 쪽을 넓게 뽑는 이유: 위반 판정은 **출력 쪽 토큰**으로만 하고, 대조 대상이
# 넓으면 원문을 그대로 옮긴 줄(`2030` → `2030년`, `five months` → `5개월`)이
# 오탐으로 빠지지 않는다. 원문에 없는 수치는 넓게 뽑아도 생기지 않는다.
SRC_INT = re.compile(r'(?<![\d.,])\d[\d,]*(?![\d.,])')
WORDS = dict(one='1', two='2', three='3', four='4', five='5', six='6',
             seven='7', eight='8', nine='9', ten='10', eleven='11',
             twelve='12', fifteen='15', twenty='20', thirty='30', forty='40',
             fifty='50')
SCALES = dict(thousand='K', million='M', billion='B', trillion='T')
SRC_WORD = re.compile(r'(?i)\b(' + '|'.join(WORDS) + r')\b'
                      r'(?:\s+(' + '|'.join(SCALES) + r')s?\b)?')


def token(m):
    """수치 문자열 → 대조용 토큰. 모양이 아니면 None."""
    g = PARTS.match(norm(m))
    if not g:
        return None
    sign, digits, unit = g.groups()
    u = UNITS.get(unit, UNITS.get(unit.lower(), unit))
    return ('-' if sign == '-' else '') + digits.replace(',', '') + '|' + u


def _spans(text):
    """단위가 붙은 토큰과 그 자리. 반환 ({토큰: 원문 표기}, [자리]).

    자리를 함께 돌려주는 이유 — `$80.4B` 안에는 `80.4` 도 있다. 그것을 단위 없는
    토큰으로 따로 넣으면 `$80.4B` 를 근거로 `$80.4`(10억 배 차이)가 통과한다.
    단위가 붙은 토큰이 먹은 자리에서는 단위 없는 토큰을 뽑지 않는다.
    """
    out, spans = {}, []
    for m in NUM.finditer(text):
        t = token(m.group())
        if t:
            out.setdefault(t, m.group().strip())
            spans.append(m.span())
    return out, spans


def _outside(m, spans):
    return not any(a <= m.start() and m.end() <= b for a, b in spans)


def tokens(text):
    """출력 한 줄의 수치 토큰. 반환 {토큰: 그 줄의 원문 표기}.

    단위·소수점·천단위 쉼표가 없는 정수(`2027`·`3Q`)는 뽑지 않는다 — 원문 쪽만
    넓게 뽑는다(`src_tokens`). 위반 줄에는 모델이 쓴 글자를 그대로 적는다.
    """
    out, spans = _spans(text)
    for m in BARE.finditer(text):
        if not _outside(m, spans):
            continue
        t = token(m.group())
        if t:
            out.setdefault(t, m.group().strip())
    return out


def src_tokens(text):
    """근거 원문이 허용하는 수치 토큰 집합."""
    out, spans = _spans(text)
    ok = set(out)
    for rx in (BARE, SRC_INT):
        for m in rx.finditer(text):
            if not _outside(m, spans):
                continue
            t = token(m.group())
            if t:
                ok.add(t)
    for num, scale in SRC_WORD.findall(text):
        d = WORDS[num.lower()]
        ok.add(d + '|')
        if scale:
            ok.add(d + '|' + SCALES[scale.lower()])
    return ok


# `$MU` 꼴 티커. 두 글자 미만은 통화 기호와 구분이 안 되고, 일곱 글자 넘는 것은 없다.
TICKER = re.compile(r'\$([A-Z]{2,6})(?![A-Za-z0-9])')

# `@계정`. 블루스카이 핸들은 점을 포함한다(`someone.bsky.social`). 문장 끝 마침표가
# 핸들에 붙어 들어오므로 뒤에 붙은 점은 떼고 본다.
ACCOUNT = re.compile(r'@([A-Za-z0-9][A-Za-z0-9._\-]{1,60})')

# 국내 종목코드. 모델이 붙이면 위반이다 (3-3 5번).
CODE = re.compile(r'\((\d{6})\)')


def low(s):
    """별칭 대조용 정규화. NFKC·공백 제거 + 소문자 (3-3 4번)."""
    return norm(s).lower()


def load_names(path=None):
    """별칭 사전. 표제어 → {code, aliases}. 표제어 자신도 별칭에 넣는다.

    표제어 자신을 넣는 이유 — 해석 줄(④)의 대조 대상은 게시물 원문이 아니라 ②③ 의
    사실 줄이고, 그 줄은 이미 우리말 종목명으로 옮겨져 있다. 같은 함수로 둘을
    검증하려면 표제어가 자기 별칭이어야 한다.
    """
    p = path or NAMES_FILE
    with open(p, encoding='utf-8') as f:
        y = yaml.safe_load(f) or {}
    out = {}
    for head, v in (y.get('names') or {}).items():
        v = v or {}
        al = [head] + [str(x) for x in (v.get('aliases') or [])]
        out[str(head)] = dict(code=str(v.get('code') or ''),
                              aliases=[a for a in al if a])
    return out


def code_of(alias, head):
    """표제어의 종목코드. 렌더가 이름 뒤에 붙인다."""
    return (alias.get(head) or {}).get('code') or ''


def _sources(ids, sources):
    """근거 원문을 하나로 잇는다. 정규화한 뒤 `|` 로 이어 경계를 남긴다 —
    이어 붙인 자리에서 없던 문자열이 생기지 않게 한다.

    티커(3번)·별칭(4번) 대조에만 쓴다. 수치(1번)는 근거마다 토큰을 뽑아
    집합으로 대조한다 — 이어 붙인 문자열의 부분 문자열로 보면 절단·단위 탈락이
    통과한다.
    """
    return '|'.join(norm(sources.get(i, '')) for i in ids)


def check(text, ids, sources, accounts, alias, allowed=None):
    """한 줄을 검증한다. 반환 위반 목록 (`writer/verify.check` 와 같은 모양).

    text      모델이 쓴 한 줄
    ids       그 줄에 딸린 근거 키 (`post_ids` 또는 `fact_ids`)
    sources   {키: 대조할 원문} — 사실 줄은 게시물 본문, 해석 줄은 ②③ 의 사실 줄
    accounts  posts.json 의 `account` 집합
    alias     `load_names()` 사전
    allowed   그 단위에 배정된 키 집합. None 이면 `sources` 의 키 전부
    """
    v = []
    ok_ids = set(allowed if allowed is not None else sources)

    # ── 6. 근거 id ───────────────────────────────────
    # 먼저 본다. 근거가 틀렸으면 아래 대조는 엉뚱한 원문과 하는 것이다.
    good = []
    for i in (ids or []):
        if i in ok_ids:
            good.append(i)
        else:
            v.append(dict(kind='ids', value=str(i),
                          why='근거 id 가 실재하지 않거나 이 단위에 배정되지 않았다'))
    if not good:
        v.append(dict(kind='ids', value='(없음)', why='근거 id 가 없는 줄이다'))
        return v

    src = _sources(good, sources)
    src_low = src.lower()
    line_low = low(text)

    # ── 1. 수치 ──────────────────────────────────────
    # 근거 원문에서 같은 방식으로 뽑은 토큰 집합에 있어야 한다. 부분 문자열이 아니다
    # — 같은 토큰이 한 줄에 두 번 나오면 위반도 한 번만 적는다(`tokens` 가 집합).
    ok_nums = set()
    for i in good:
        ok_nums |= src_tokens(str(sources.get(i, '')))
    for t, m in tokens(text).items():
        if t not in ok_nums:
            v.append(dict(kind='number', value=m,
                          why='근거 게시물 본문에 없는 수치 — 계산·환산·어림은 금지'))

    # ── 2. 계정 ──────────────────────────────────────
    for a in ACCOUNT.findall(text):
        h = a.rstrip('.')
        if h not in accounts:
            v.append(dict(kind='account', value='@' + h,
                          why='posts.json 에 없는 계정'))

    # ── 3. 티커 ──────────────────────────────────────
    for t in TICKER.findall(text):
        if f'${t}' not in src:
            v.append(dict(kind='ticker', value=f'${t}',
                          why='근거 게시물 본문에 없는 티커'))

    # ── 4. 국내 종목명 별칭 매핑 ─────────────────────
    # 표제어가 출력에 있으면 근거 원문에 그 표제어의 별칭 중 하나가 있어야 한다.
    # 영어 게시물의 `Samsung` 을 `삼성전자` 로 옮기는 것은 문체가 요구하는 일이라
    # 위반이 아니고, 어느 별칭도 없는 종목명이 위반이다.
    for head, info in alias.items():
        h = low(head)
        if len(h) < 3 or h not in line_low:
            continue
        if not any(low(a) in src_low for a in info['aliases']):
            v.append(dict(kind='name', value=head,
                          why='근거 게시물 본문에 이 종목의 별칭이 하나도 없다 — 사전 매핑 실패'))

    # ── 5. 종목코드 ──────────────────────────────────
    for c in CODE.findall(text):
        v.append(dict(kind='code', value=f'({c})',
                      why='종목코드는 렌더가 별칭 사전에서 붙인다 — 모델이 쓰면 위반'))

    # ── 7. 필드 이름 유출 ────────────────────────────
    for m in set(FIELD_LEAK.findall(text)):
        v.append(dict(kind='jargon', value=m,
                      why='필드 이름이 본문에 그대로 나왔다 — 우리말로 풀어야 한다'))

    return v


def unit_ok(facts, posts, min_accounts=2):
    """소주제 단위 판정 (3-3 8번). 반환 (자격 있음, 사유|None).

    통과한 `•` 가 1개 미만이거나 출처 계정이 2개 미만이면 자격이 없다 — 단독
    소식으로 내린다. 계정은 **통과한 줄의** 게시물에서 센다. 검증에서 줄이 빠지면
    그 줄만 대던 계정도 함께 빠지므로, 원래 배정으로 세면 `└` 줄이 거짓이 된다.
    """
    kept = [f for f in facts if f.get('text')]
    if len(kept) < 1:
        return False, '통과한 사실 줄이 없음'
    ids = {i for f in kept for i in (f.get('post_ids') or [])}
    accts = {(posts.get(i) or {}).get('account') for i in ids}
    accts.discard(None)
    if len(accts) < min_accounts:
        return False, f'통과한 줄의 출처 계정 {len(accts)}개 — {min_accounts}개 미만'
    return True, None


def report(violations, limit=20):
    """`writer/verify.report` 와 같은 모양. 사유를 요약하지 않는다."""
    if not violations:
        return '검증 통과'
    lines = [f'검증 실패 {len(violations)}건']
    for x in violations[:limit]:
        lines.append(f'  [{x["kind"]}] {x["value"]} — {x["why"]}')
    if len(violations) > limit:
        lines.append(f'  ... 외 {len(violations) - limit}건')
    return '\n'.join(lines)

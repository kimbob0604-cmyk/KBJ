#!/usr/bin/env python3
"""
구루 브리핑 렌더 — research.json + synth.json → brief.txt 와 발송할 통 목록.

양식은 사용자가 2026-09-29 에 준 예시 그대로다(docs/GURU.md 1장). 코드가 정하는 것:
머리의 날짜·요일·기준 시각, PASS 인원 수와 이름(검증 통과한 사람만), 그룹 순서·인물
순서(설정 순서), 출처 매체 목록(검증 통과 출처에서), 다음 브리핑 날짜.

빠진 것이 있으면 맨 위에 `※ 확인 못 한 것` 한 줄을 둔다 — 예시 양식에는 없던 줄이지만
CLAUDE.md 2장 6번(빠진 것을 상단에 적는다)이 우선이다.

평문이다(parse_mode 없음). 4,096자를 넘으면 구획 경계에서 나눈다.
"""
import json
import os
from datetime import date, datetime
from urllib.parse import urlsplit

from ..engine.config import ROOT
from . import research as R

STATE = os.path.join(ROOT, 'state', 'guru')
LIMIT = 4096
DISCLAIMER = '본 브리핑은 공개 보도 요약이며 투자 자문이 아닙니다.'


def day_dir(asof, make=False):
    p = os.path.join(STATE, asof.replace('-', ''))
    if make:
        os.makedirs(p, exist_ok=True)
    return p


def read(asof, name):
    p = os.path.join(day_dir(asof), name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return f.read() if name.endswith('.txt') else json.load(f)


def write(asof, name, obj):
    p = os.path.join(day_dir(asof, make=True), name)
    with open(p, 'w', encoding='utf-8') as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write('\n')
    return p


def _md(d):
    d = d if isinstance(d, date) else date.fromisoformat(d)
    return f'{d.month}/{d.day}'


def _person(p):
    head = f'{p["name"]} ({p["org"]})'
    if p['status'] == 'pass':
        body = [head, p['remark']]
        if p.get('implication'):
            body += ['', f'→ {p["implication"]}']
        return '\n'.join(body)
    if p['status'] == 'unknown':
        return f'{head}\n확인 못 함 (조사 실패)'
    line = '금일 발언 없음'
    if p.get('note'):
        line += f' (참고: {p["note"]})'
    return f'{head}\n{line}'


def outlets(research):
    seen = []
    srcs = list((research.get('market') or {}).get('sources') or [])
    for g in research['groups']:
        for p in g['people']:
            srcs += p.get('sources') or []
            srcs += p.get('note_sources') or []
    for s in srcs:
        name = s.get('outlet') or urlsplit(s.get('url') or '').netloc.removeprefix('www.')
        if name and name not in seen:
            seen.append(name)
    return seen


def blocks(research, synth, cfg, missing=()):
    """구획 목록 — 분할 단위. 이어 붙이면 brief.txt 다."""
    win = research['window']
    d, wd = R.ko_date(win['asof'])
    s, swd = R.ko_date(win['session'])
    at = datetime.fromisoformat(win['end'])      # 조사 시각(기준 시각)
    people = [p for g in research['groups'] for p in g['people']]
    passed = [p['name'] for p in people if p['status'] == 'pass']
    unknown = sum(1 for p in people if p['status'] == 'unknown')
    out = []

    head = []
    if missing:
        head.append('※ 확인 못 한 것: ' + ' · '.join(missing))
        head.append('')
    head += [f'{cfg.get("title", "글로벌 투자 구루 브리핑")} | {d} ({wd})', '',
             f'검증 기준: {s}({swd}) 미국장 뉴스 사이클 '
             f'(KST {at:%m.%d %H:%M} 기준 최근 {cfg.get("window_hours", 24)}시간)',
             f'당일 발언 확인(PASS): {len(passed)}명 / {len(people)}명'
             + (f' ({", ".join(passed)})' if passed else '')
             + (f' · 확인 못 함 {unknown}명' if unknown else '')]
    m = research.get('market')
    head += ['', f'시장 배경 ({_md(win["session"])} 미국장): '
             + (m['line'] if m else '확인 못 함'), '']       # 다음 구획 앞 빈 줄(예시 양식)
    out.append('\n'.join(head))

    if synth:
        sec = ['[오늘의 투자 시사점 종합]', '']
        if synth.get('macro'):
            sec += ['매크로 방향성', synth['macro'], '']
        if synth.get('sectors'):
            sec += ['섹터별 힌트', ' '.join(f'{i}) {x}' for i, x in enumerate(synth['sectors'], 1)), '']
        if synth.get('positions'):
            sec += ['주목할 포지션 변화', synth['positions'], '']
        if synth.get('contrarian'):
            sec += ['컨트래리언 시그널', synth['contrarian']]
        out.append('\n'.join(sec).rstrip())

    for g in research['groups']:
        out.append('\n\n'.join([f'[{g["title"]}]'] + [_person(p) for p in g['people']]))

    if synth and synth.get('risks'):
        r = ['[리스크 & 경고 신호]']
        for i, x in enumerate(synth['risks'], 1):
            r += ['', f'{i}. {x["title"]}', x['body']]
        out.append('\n'.join(r))

    nxt = R.next_run(date.fromisoformat(win['asof']), cfg)
    tail = ['', f'수집완료: {at:%Y.%m.%d %H:%M} KST',
            '출처: ' + (', '.join(outlets(research)) or '없음')]
    if nxt:
        nd, nwd = R.ko_date(nxt)
        tail.append(f'다음 브리핑: {nd}({nwd}) 오전 KST')
    tail.append(DISCLAIMER)
    out.append('\n'.join(tail))
    return out


def text(parts):
    return '\n'.join(parts)


def split(bl, limit=LIMIT):
    """구획을 통에 담는다. 한 구획이 넘치면 빈 줄 경계에서 나눈다."""
    pieces = []
    for b in bl:
        if len(b) <= limit:
            pieces.append(b)
            continue
        cur = ''
        for para in b.split('\n\n'):
            add = para if not cur else cur + '\n\n' + para
            if len(add) <= limit:
                cur = add
            else:
                if cur:
                    pieces.append(cur)
                cur = para[:limit]
        if cur:
            pieces.append(cur)
    parts, cur = [], ''
    for p in pieces:
        add = p if not cur else cur + '\n' + p
        if len(add) <= limit:
            cur = add
        else:
            parts.append(cur)
            cur = p
    if cur:
        parts.append(cur)
    return parts

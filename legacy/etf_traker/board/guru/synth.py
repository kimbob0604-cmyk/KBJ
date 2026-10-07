#!/usr/bin/env python3
"""
구루 브리핑 종합 — 검증을 통과한 인물 발언·시장 배경만 넣고 '오늘의 투자 시사점 종합'
과 '리스크 & 경고 신호' 를 쓴다. 웹 검색을 켜지 않는다.

**새 수치를 만들지 못하게 한다.** 출력의 숫자 토큰이 입력(검증된 사실)에 없으면 한 번
되묻고, 그래도 남으면 그 칸을 통째로 뺀다(CLAUDE.md 2장 3·4번). 종합은 해석이라
숫자를 새로 계산하거나 기억에서 꺼낼 자리가 아니다.
"""
import json
import re

from .research import Spend, _g

NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['macro', 'sectors', 'positions', 'contrarian', 'risks'],
    'properties': {
        'macro': {'type': 'string'},
        'sectors': {'type': 'array', 'items': {'type': 'string'}},
        'positions': {'type': 'string'},
        'contrarian': {'type': 'string'},
        'risks': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['title', 'body'],
            'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}}}},
    },
}

SYSTEM = """당신은 바이사이드 리서치 애널리스트다. 주어진 '검증된 사실' 만으로 아침 브리핑의
종합 구획을 쓴다. 검색하지 않는다.

규칙
- 사실에 없는 수치·날짜·인물·사건을 쓰지 않는다. 수치는 사실에 적힌 표기 그대로 옮긴다.
  계산(차이·합계·환산)하지 않는다.
- 해석은 해석으로 쓴다('~로 보입니다', '~할 수 있습니다'). 단정하지 않는다.
- 한국어 경어체(~했습니다/~입니다). 형용사로 강조하지 않는다.
- 발언이 없는 날은 억지로 채우지 않는다 — 근거가 없으면 그 칸을 짧게 '해당 없음' 으로.

칸
- macro: 매크로 방향성 2~4문장
- sectors: 섹터별 힌트 2~4개. 각 항목은 '섹터명: 설명' 한두 문장(번호 없이)
- positions: 주목할 포지션 변화 1~3문장
- contrarian: 컨트래리언 시그널 1~3문장
- risks: 리스크 & 경고 신호 3~5개. title 은 짧게, body 는 2~3문장"""


def facts(research):
    """모델에 넣는 검증된 사실. 이 문자열이 숫자 대조의 기준이다."""
    out = {'market': (research.get('market') or {}).get('line') or '',
           'pass': [], 'notes': []}
    for g in research['groups']:
        for p in g['people']:
            if p['status'] == 'pass':
                out['pass'].append({'name': p['name'], 'org': p['org'], 'remark': p['remark'],
                                    'implication': p['implication']})
            elif p.get('note'):
                out['notes'].append({'name': p['name'], 'note': p['note']})
    return out


def _nums(s):
    return {n.replace(',', '') for n in NUM.findall(s or '')}


def stray_numbers(obj, allowed):
    """출력 안에서 입력에 없던 숫자 토큰 {칸: [숫자]}."""
    bad = {}

    def walk(key, v):
        if isinstance(v, str):
            x = sorted(_nums(v) - allowed)
            if x:
                bad.setdefault(key, []).extend(x)
        elif isinstance(v, list):
            for i, it in enumerate(v):
                walk(f'{key}[{i}]', it)
        elif isinstance(v, dict):
            for k, it in v.items():
                walk(f'{key}.{k}' if key else k, it)

    walk('', obj)
    return bad


def _drop(obj, bad):
    """숫자가 샌 칸을 뺀다. risks[i] 는 그 항목만, 나머지는 칸 전체."""
    out = json.loads(json.dumps(obj))
    gone = []
    for key in sorted(bad, reverse=True):
        m = re.match(r'(risks|sectors)\[(\d+)\]', key)
        if m:
            arr, i = m.group(1), int(m.group(2))
            if i < len(out.get(arr) or []) and out[arr][i] is not None:
                out[arr][i] = None
                gone.append(key)
        else:
            top = key.split('.')[0].split('[')[0]
            if out.get(top):
                out[top] = '' if isinstance(out[top], str) else []
                gone.append(top)
    for arr in ('risks', 'sectors'):
        out[arr] = [x for x in (out.get(arr) or []) if x is not None]
    return out, gone


def ask(cl, cfg, system, prompt, spend):
    kw = dict(model=cfg['model'], max_tokens=int(cfg.get('synth_max_tokens', 8000)),
              system=system, messages=[{'role': 'user', 'content': prompt}],
              output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}})
    if cfg.get('effort'):
        kw['output_config']['effort'] = cfg['effort']
    resp = cl.messages.create(**kw)
    spend.add(_g(resp, 'usage'))
    if _g(resp, 'stop_reason') in ('refusal', 'max_tokens'):
        raise RuntimeError(f'종합 응답 중단 — {_g(resp, "stop_reason")}')
    txt = ''.join(_g(b, 'text') or '' for b in _g(resp, 'content') or [] if _g(b, 'type') == 'text')
    return json.loads(txt)


def run(cl, cfg, research, log=print):
    """반환 (종합 dict 또는 None, drops, spend dict)."""
    f = facts(research)
    spend = Spend()
    base = json.dumps(f, ensure_ascii=False, indent=1)
    allowed = _nums(base) | _nums(research['window']['session']) | _nums(research['window']['asof'])
    prompt = f'검증된 사실(이것만 쓴다):\n{base}'
    drops = []
    try:
        out = ask(cl, cfg, SYSTEM, prompt, spend)
        bad = stray_numbers(out, allowed)
        if bad:
            log(f'  종합 — 입력에 없는 숫자 {bad} — 한 번 되묻는다')
            fix = (prompt + '\n\n앞 답에 사실에 없는 숫자가 있었다: '
                   + json.dumps(bad, ensure_ascii=False)
                   + '\n그 숫자를 빼고 다시 써라. 사실에 있는 숫자만 쓴다.')
            out = ask(cl, cfg, SYSTEM, fix, spend)
            bad = stray_numbers(out, allowed)
            if bad:
                out, gone = _drop(out, bad)
                drops.append(f'종합 — 출처 없는 숫자가 남아 뺀 칸: {", ".join(gone)}')
    except Exception as e:                           # noqa: BLE001 — 결손으로 적는다
        log(f'  종합 실패: {e}')
        return None, [f'종합 실패 — {type(e).__name__}: {str(e)[:120]}'], spend.as_dict(cfg['price'])
    return out, drops, spend.as_dict(cfg['price'])

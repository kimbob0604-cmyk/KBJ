#!/usr/bin/env python3
"""
국장 섹터 모니터 — 업종 묶음 (DART 표준산업분류).

공공데이터포털 4개 API 어디에도 업종 필드가 없다(배포본 화면 문구: '기업개황
sicNm 은 전부 공란으로 실측'). 업종은 DART `company.json` 의 `induty_code`
(KSIC 숫자)로만 온다 — 그런데 **DART 는 코드만 주고 이름은 안 준다.**

그래서 두 갈래로 채운다.

1. `knowledge/industries.json`
   배포본에서 뽑은 업종 58개 이름과 그 시점의 종목 배정 639건. 곧바로 쓴다.
   [KBJ P1] 공개 레포에는 그 사전 대신 같은 모양의 합성 사전(가상 업종 58개)을 둔다
   (tests/fixtures/make_synthetic.py). 실사용 사전은 P5 에서 직접 다시 만든다.

2. 학습한 KSIC 표 (`cache/ksic.json`)
   1번의 '종목 → 업종명' 과 DART 의 '종목 → induty_code' 를 **조인**하면
   'induty_code → 업종명' 표가 나온다. 한 번 배우면 새로 상장한 종목도
   코드만 보고 배정할 수 있다.

   같은 코드에 서로 다른 이름이 붙으면 더 많이 나온 쪽을 쓰되, 표가 흔들린
   코드는 기록해 둔다. 애매하면 배정하지 않는다 — 틀린 업종은 없는 업종보다
   나쁘다.
"""
from __future__ import annotations

import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

ROOT = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE = os.path.join(ROOT, 'knowledge', 'industries.json')
KSIC_CACHE = os.path.join(ROOT, 'cache', 'ksic.json')

# 한 코드에 이름이 여러 개 붙었을 때, 최빈 이름이 이 비율 미만이면 안 쓴다.
CONFIDENT = 0.8


def load_knowledge(path=KNOWLEDGE):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_ksic(path=KSIC_CACHE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return (json.load(f) or {}).get('by_code') or {}
    except (OSError, ValueError):
        return {}


def save_ksic(table, path=KSIC_CACHE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'note': 'induty_code → 업종명. 지식 배정과 DART 코드를 조인해 학습.',
                   'by_code': table}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def learn_ksic(known_by_stock, induty_by_stock):
    """{종목: 업종명} × {종목: induty_code} → {induty_code: 업종명}.

    한 코드에 이름이 갈리면 최빈 이름이 CONFIDENT 이상일 때만 채택한다.
    """
    votes = collections.defaultdict(collections.Counter)
    for code, name in known_by_stock.items():
        ind = induty_by_stock.get(code)
        if ind and name:
            votes[str(ind)][name] += 1

    table, shaky = {}, []
    for ind, cnt in votes.items():
        name, n = cnt.most_common(1)[0]
        if n / sum(cnt.values()) >= CONFIDENT:
            table[ind] = name
        else:
            shaky.append(ind)
    return table, shaky


def assign(codes, knowledge=None, ksic=None, induty_by_stock=None):
    """{종목: 업종명}. 모르는 종목은 **키 자체를 넣지 않는다**.

    억지로 '기타' 에 몰면 그 칸이 실제 업종인 것처럼 보인다. 배정 못 한
    종목은 화면의 업종 묶음에서 빠질 뿐이고, 그 수는 meta 로 따로 싣는다.
    """
    knowledge = knowledge or load_knowledge()
    out = {}
    for sec in knowledge['sectors']:
        for c in sec['members']:
            out[c] = sec['name']

    # 지식에 없는 종목은 학습한 KSIC 표로 채운다.
    ksic = ksic or {}
    induty_by_stock = induty_by_stock or {}
    for c in codes:
        if c in out:
            continue
        name = ksic.get(str(induty_by_stock.get(c) or ''))
        if name:
            out[c] = name
    return {c: out[c] for c in codes if c in out}


def fetch_induty(codes, log=print, max_calls=1500):
    """DART 기업개황에서 induty_code 를 받는다. 실패는 종목 단위로 삼킨다.

    업종은 거의 안 바뀌므로 한 번 받은 종목은 다시 받지 않는다.
    """
    from board.ingest import dart
    cached = {}
    path = os.path.join(ROOT, 'cache', 'induty.json')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                cached = json.load(f) or {}
        except (OSError, ValueError):
            cached = {}

    todo = [c for c in codes if c not in cached][:max_calls]
    got = 0
    if todo:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(dart.company, c): c for c in todo}
            for fu in cf.as_completed(futs):
                c = futs[fu]
                try:
                    cached[c] = (fu.result() or {}).get('induty_code')
                    got += 1
                except Exception:  # noqa: BLE001 — corp_code 없음 등 흔하다
                    cached[c] = None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cached, f, ensure_ascii=False)
    log(f'  업종코드: {got}종목 새로 받음 · 캐시 {len(cached)}종목')
    return {c: v for c, v in cached.items() if v}


def refresh(codes, log=print, fetch=True):
    """업종 배정을 만들고, 가능하면 KSIC 표를 학습해 캐시에 남긴다."""
    knowledge = load_knowledge()
    known = {c: sec['name'] for sec in knowledge['sectors'] for c in sec['members']}

    induty = {}
    if fetch:
        try:
            induty = fetch_induty(list(codes), log=log)
        except Exception as e:  # noqa: BLE001 — 키 없음 등
            log(f'  업종코드 수집 건너뜀: {e}')

    ksic = load_ksic()
    if induty:
        learned, shaky = learn_ksic(known, induty)
        if learned:
            ksic.update(learned)
            save_ksic(ksic)
            log(f'  KSIC 표: {len(learned)}개 학습 (애매해서 뺀 코드 {len(shaky)}개)')

    return assign(list(codes), knowledge, ksic, induty)

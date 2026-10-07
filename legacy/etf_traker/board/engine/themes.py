#!/usr/bin/env python3
"""
2층 자체 테마 매핑 — knowledge/themes.yaml 을 종목코드로 정규화한다.

themes.yaml 의 seeds 는 종목명으로만 적혀 있고, 그 안에는 비상장사가 섞여 있다
(CLAUDE.md 9장 미확정 6번: 세메스·삼성디스플레이·SK온·토스뱅크는 비상장,
앰코는 국내 미상장). 그래서 여기서 상장 종목 마스터와 대조해 이름이 붙지 않는
시드는 전량 걸러내고, 걸러낸 목록을 그대로 돌려준다. 조용히 버리지 않는다.

매핑은 다대다다. 한 종목이 여러 테마·여러 단계에 동시에 속할 수 있다.
"""
import difflib
import re
import unicodedata


def norm(name):
    """종목명 대조 키. 공백·괄호·점·대소문자 차이를 흡수한다.

    'JYP Ent.' / 'LS ELECTRIC' / 'HD현대미포' 처럼 소스마다 표기가 흔들린다.
    """
    if not name:
        return ''
    s = unicodedata.normalize('NFKC', str(name))
    s = re.sub(r'[\s.·,\-_()（）]', '', s)
    return s.upper()


def display_index(rows):
    """정규화 키 → 원래 표기. `near_names` 가 후보를 찾을 사전이다.

    rows 는 `name` 을 가진 것들의 iterable 이면 된다 — universe 의 값들이든
    universe.json 의 `stocks` 든 같은 사전을 만든다.
    """
    out = {}
    for x in rows or ():
        nm = (x or {}).get('name')
        if nm:
            out.setdefault(norm(nm), nm)
    return out


def near_names(name, display, k=3):
    """비슷한 상장 종목 표기. **사명 변경으로 볼 만한 것만** 돌려준다.

    difflib 의 비율만으로는 못 거른다. 실측(2026-08-31)에서 진짜 개명
    (한국조선해양 → HD한국조선해양)이 0.857 인데, 아무 관계 없는
    삼화에이스 → 에이럭스 · 리노스 → 노머스 · 뉴로스 → 로스웰이 전부
    0.667 로 나왔다. 짧은 이름끼리 글자가 우연히 겹친 것뿐이다. 문턱을
    올리면 현대미포조선 → HD현대미포(0.667) 같은 진짜 개명이 같이 잘린다.

    개명은 **옛 이름이 거의 통째로 남는다.** 가장 긴 공통 부분문자열이
    짧은 쪽 이름의 60% 이상이고 3글자 이상일 때만 후보로 본다.
    위 세 개는 공통 부분이 2글자 이하라 전부 떨어진다.

    (2026-09-21: themes 안의 클로저였던 것을 모듈 함수로 꺼냈다. 노트
     발송(report/note.py)이 같은 판정을 써야 하는데, 사본을 두면 다음에
     문턱을 손볼 때 한쪽만 고쳐진다.)
    """
    a = norm(name)
    out = []
    for key in difflib.get_close_matches(a, display, n=k * 3, cutoff=0.55):
        m = difflib.SequenceMatcher(None, a, key).find_longest_match(
            0, len(a), 0, len(key))
        short = min(len(a), len(key)) or 1
        if m.size >= 3 and m.size / short >= 0.6:
            out.append(display[key])
        if len(out) >= k:
            break
    return out


def build(themes_yaml, universe, cfg):
    """반환 (mapping, themes_meta, unresolved)

    mapping    {code: [ {theme, name, axis, stage, confidence}, ... ]}
    themes_meta {theme_id: {name, axis, parent, stages, n_seeds, n_mapped}}
    unresolved  [ {theme, stage, seed, known, near} ] — 상장 마스터에서 못 찾은 시드.
                `near` 는 이름이 가장 비슷한 상장 종목 후보다. 대부분은 사명
                변경이라(한국조선해양 → HD한국조선해양) 후보만 보여도 바로
                고칠 수 있다. "없습니다" 만 적으면 사람이 하나씩 검색해야 한다.
                `known` 은 themes.yaml 의 `unlisted:` 에 사유와 함께 적어 둔 것.
                비상장이라 원래 못 붙는 이름을 매일 결손으로 세면, 진짜 오타나
                사명 변경이 그 숫자에 묻힌다.
    """
    conf = cfg['themes']['seed_confidence']
    by_name = {}
    for code, x in universe.items():
        by_name.setdefault(norm(x.get('name')), code)

    axes = themes_yaml.get('axes') or {}
    # 상장돼 있지 않다고 이미 확인한 이름들. 지우지 않고 남기는 이유는, 이들이
    # 밸류체인의 실제 구성원이라서다 — 세메스가 없는 반도체 전공정 시드는
    # 그 자체로 틀린 기록이다. 붙지 않는다는 사실만 미리 적어 둔다.
    unlisted = {norm(k): v for k, v in (themes_yaml.get('unlisted') or {}).items()}
    # 사명 변경 표. **시드 이름을 직접 고치지 않고 표로 남기는 이유**는, 옛 이름이
    # 밸류체인 기록의 일부이고 근거를 함께 적어 둬야 나중에 검증할 수 있어서다.
    # 여기 넣을 자격은 하나다 — 마스터가 증명해야 한다. 기억으로 넣지 않는다.
    # (2026-09-03: 089970 을 '에이피티씨' 로 알고 물었더니 마스터가 '브이엠'
    #  이라고 답했다. 기억은 후보를 낼 뿐이고 판정은 마스터가 한다.)
    renames = {norm(k): norm(v) for k, v in (themes_yaml.get('renames') or {}).items()}
    mapping, meta, unresolved = {}, {}, []

    # 이름이 비슷한 후보를 찾을 사전. 정규화 키 → 원래 표기.
    display = {}
    for code, x in universe.items():
        if x.get('name'):
            display.setdefault(norm(x['name']), x['name'])

    for t in themes_yaml.get('themes') or []:
        tid, tname = t.get('id'), t.get('name')
        axis = t.get('axis')
        seeds = t.get('seeds') or {}
        if isinstance(seeds, list):                      # stages 없는 축의 축약 표기
            seeds = {'기타': seeds}
        n_seed = n_map = 0
        for stage, names in seeds.items():
            for nm in (names or []):
                n_seed += 1
                key = norm(nm)
                # 사명이 바뀐 이름은 표를 한 번 거쳐 지금 이름으로 찾는다.
                code = by_name.get(key) or by_name.get(renames.get(key, ''))
                if not code:
                    known = unlisted.get(norm(nm))
                    unresolved.append(dict(
                        theme=tid, stage=stage, seed=nm, known=known,
                        # 비상장이라 적어 둔 이름에는 후보를 붙이지 않는다.
                        # 안 붙는 게 정상인데 "혹시 이건가" 를 띄우면 노이즈다.
                        near=[] if known else near_names(nm, display)))
                    continue
                n_map += 1
                mapping.setdefault(code, []).append(dict(
                    theme=tid, name=tname, axis=axis, stage=stage, confidence=conf))
        meta[tid] = dict(id=tid, name=tname, axis=axis, parent=t.get('parent'),
                         krx_hint=t.get('krx_hint') or [],
                         stages=(axes.get(axis) or {}).get('stages') or [],
                         external_index=t.get('external_index') or [],
                         n_seeds=n_seed, n_mapped=n_map)
    return mapping, meta, unresolved


def primary(mapping, code):
    """대표 테마 하나. 표에서 종목당 한 줄만 쓸 때의 선택.

    seeds 는 위에서부터 읽으므로 첫 번째가 themes.yaml 기재 순서상 대표에 가깝다.
    """
    lst = mapping.get(code)
    return lst[0] if lst else None


#!/usr/bin/env python3
"""
종목 → 섹터 배치 분류.

**이 프로젝트에서 LLM 병렬 처리가 실제로 값어치 있는 유일한 자리다.**

랭킹 표는 정렬하고 세는 것뿐이라 코드가 만든다. 서술은 이미 계산된 사실을 잇는
일이라 섹터당 한 번씩만 부르면 된다. 반면 종목 2,800개를 48개 섹터에 배정하는 건
(a) 항목마다 독립이고 (b) 판단이 필요하고 (c) 한 번 해 두면 신규 상장 때만 다시
하면 된다. Batch API 가 정확히 이런 작업을 위한 것이다.

## 비용을 줄이는 세 가지

1. **Batch API** — 입출력 50% 할인 (CLAUDE.md 7장)
2. **프롬프트 캐시** — 섹터 사전은 모든 요청에서 같은 접두사다. 캐시 히트는 입력가의 10%
3. **묶어 보내기** — 한 요청에 여러 종목. 요청당 접두사가 한 번만 든다
4. **krx_hint 사전 필터** — KRX 업종으로 후보 섹터를 좁혀서 보낸다. 프롬프트가
   짧아지고, 보험 종목이 반도체로 가는 사고를 값싸게 막는다

## 사람이 이긴다

결과는 knowledge/sector_map.yaml 에 쌓인다. 사람이 고친 항목에 `locked: true` 를
달면 재분류가 덮어쓰지 않는다. 분류기가 틀린 걸 매번 다시 고치게 만들지 않는다.

## 신뢰도

0.7 미만은 `미분류` 로 떨어뜨린다 (CLAUDE.md 2장 5번과 같은 문턱). 억지로 배정해서
랭킹 표에 잘못된 섹터로 올라가느니 미분류가 낫다. 미분류 종목 수는 랭킹의
`missing` 에 그대로 노출된다.
"""
import json
import os
import time

import yaml

from ..engine.config import ROOT
from ..ingest import creds
from ..writer import claude as C

MAP_PATH = os.path.join(ROOT, 'knowledge', 'sector_map.yaml')
CHUNK = 25              # 한 요청에 넣을 종목 수
MAX_BATCH_WAIT = 6 * 3600   # 배치 대기 상한(초). 넘기면 취소하고 받은 것만 쓴다
MIN_CONF = 0.7
UNMAPPED = '미분류'

SYSTEM = """당신은 한국 상장 종목을 섹터로 분류한다.

## 규칙

1. 아래 섹터 목록에 **있는 이름만** 쓴다. 새 섹터를 만들지 마라.
2. 매출 비중이 가장 큰 사업으로 배정한다. 지주회사는 자회사 사업이 아니라
   지주회사 자체로 본다 — 순수지주는 `복합기업`, 금융지주는 `은행`.
3. 확신이 없으면 confidence 를 낮게 주라. 억지로 맞추지 마라.
   0.7 미만은 어차피 미분류로 떨어진다.
4. 종목명만으로 판단이 안 서면 confidence 0.3 이하를 주라.

## 헷갈리는 경계

- 반도체는 다섯으로 나눈다 (D-061). 매출의 주된 자리가 기준이다.
  `반도체제조` 는 칩을 직접 만드는 IDM·파운드리, `반도체설계` 는 공장 없는
  팹리스·IP·디자인하우스, `반도체장비` 는 전공정 장비, `반도체후공정` 은
  OSAT·패키징·테스트 (서비스든 장비든 후공정이면 여기), `반도체소재부품` 은
  웨이퍼·가스·케미컬·타깃·쿼츠 등 소모성 소재·부품이다.
  디스플레이가 주력이면 `디스플레이장비`/`디스플레이패널` 로 보낸다 —
  반도체·디스플레이 겸용이면 매출 비중이 큰 쪽이다.
- `전기제품` 은 가전·중전기, `전기장비` 는 전력기기·배전, `전자제품` 은 부품·모듈이다.
- `기타자본재` 는 위 어디에도 안 맞는 산업재다. 먼저 구체적인 섹터를 찾아라.
- `상사` 는 종합상사·무역, `기타유통` 은 도소매·이커머스다.
- `생명과학` 은 CRO·CDMO·진단, `제약` 은 의약품, `건강관리장비,서비스` 는
  의료기기·병원·미용기기다.
- `에너지` 는 정유·가스개발, `유틸리티` 는 전력·가스 공급이다."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'assignments': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'code': {'type': 'string'},
                    'sector': {'type': 'string'},
                    'confidence': {'type': 'number'},
                    'why': {'type': 'string'},
                },
                'required': ['code', 'sector', 'confidence', 'why'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['assignments'],
    'additionalProperties': False,
}


# ─────────────────────────── 사전 ───────────────────────────
def taxonomy():
    with open(os.path.join(ROOT, 'knowledge', 'sectors.yaml'), encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def candidates(krx_sector, tax):
    """KRX 업종으로 후보 섹터를 좁힌다. 힌트가 안 맞으면 전체를 준다."""
    secs = tax.get('sectors') or []
    if krx_sector:
        hit = [s['name'] for s in secs if krx_sector in (s.get('krx_hint') or [])]
        if hit:
            return hit
    return [s['name'] for s in secs]


def load_map():
    if not os.path.exists(MAP_PATH):
        return {}
    with open(MAP_PATH, encoding='utf-8') as f:
        return (yaml.safe_load(f) or {}).get('map') or {}


def save_map(m):
    with open(MAP_PATH, 'w', encoding='utf-8') as f:
        f.write('# 종목 → 섹터 배정. classify/sectors.py 가 만들고 사람이 고친다.\n'
                '#\n'
                '# 손으로 고친 항목에는 locked: true 를 달아라. 재분류가 덮어쓰지 않는다.\n'
                '# 분류 체계는 knowledge/sectors.yaml.\n\n')
        yaml.safe_dump(dict(map=m), f, allow_unicode=True, sort_keys=True,
                       default_flow_style=False)
    return MAP_PATH


# ─────────────────────────── 프롬프트 ───────────────────────────
def _system(tax):
    """매 요청 동일한 접두사. 캐시 대상이라 날짜·실행시각을 넣으면 안 된다.

    desc 가 있는 섹터는 이름 옆에 설명을 붙인다. 이름만으로는 경계가 갈리지
    않는 세분류(반도체장비 vs 반도체후공정)에서 모델이 추측하게 두지 않는다.
    """
    secs = tax.get('sectors') or []
    lines = [f'- {x["name"]} — {x["desc"]}' if x.get('desc') else f'- {x["name"]}'
             for x in secs]
    text = SYSTEM + '\n\n## 섹터 목록 (' + str(len(secs)) + '개)\n\n' + \
        '\n'.join(lines) + '\n'
    return [{'type': 'text', 'text': text, 'cache_control': {'type': 'ephemeral'}}]


def _prompt(chunk, tax):
    lines = []
    for x in chunk:
        cand = candidates(x.get('krx_sector'), tax)
        line = f'- {x["code"]} {x["name"]}'
        if x.get('krx_sector'):
            line += f' (KRX 업종: {x["krx_sector"]})'
        if x.get('business'):
            line += f'\n    사업: {x["business"][:300]}'
        if len(cand) < len(tax.get('sectors') or []):
            line += f'\n    후보: {", ".join(cand)}'
        lines.append(line)
    return ('아래 종목을 섹터에 배정하라. 각 종목마다 code 를 그대로 옮기고, '
            '섹터 목록에 있는 이름을 하나 고르고, confidence 를 0~1 로 주고, '
            'why 를 한 줄로 적어라.\n\n' + '\n'.join(lines))


def _chunks(rows, n=CHUNK):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


# ─────────────────────────── 실행 ───────────────────────────
def run(rows, cfg, log=print, use_batch=True, dry_run=False, poll=60):
    """rows: [{code, name, krx_sector, business?}]  → 갱신된 매핑 dict.

    use_batch=True 면 Batch API (50% 할인, 최대 24시간). False 면 실시간 병렬.
    최초 전 종목 분류는 배치, 신규 상장 몇 개는 실시간이 맞다.
    """
    tax = taxonomy()
    known = {s['name'] for s in tax.get('sectors') or []}
    cur = load_map()

    # 이미 배정됐고 사람이 잠근 것, 그리고 이미 배정된 것은 다시 묻지 않는다.
    todo = [x for x in rows
            if x['code'] not in cur or not cur[x['code']].get('sector')]
    log(f'  분류 대상 {len(todo):,}종목 (기존 배정 {len(cur):,} 유지)')
    if not todo:
        return cur

    parts = list(_chunks(todo))
    if dry_run:
        log(f'  드라이런 — 요청 {len(parts)}건 (종목 {len(todo):,}, '
            f'요청당 {CHUNK}) · 배치 {"켬" if use_batch else "끔"}')
        return cur

    system = _system(tax)
    got = (_run_batch(parts, system, tax, cfg, log, poll) if use_batch
           else _run_live(parts, system, tax, cfg, log))

    n_low, n_bad = 0, 0
    for a in got:
        code = str(a.get('code') or '').strip()
        sec = (a.get('sector') or '').strip()
        conf = float(a.get('confidence') or 0)
        if code in cur and cur[code].get('locked'):
            continue                       # 사람이 고친 건 건드리지 않는다
        if sec not in known:
            n_bad += 1
            sec = UNMAPPED
        elif conf < MIN_CONF:
            n_low += 1
            sec = UNMAPPED
        cur[code] = dict(sector=sec, confidence=round(conf, 2),
                         why=(a.get('why') or '')[:120], source='claude')
    save_map(cur)
    log(f'  배정 {len(got):,} · 신뢰도 미달 {n_low} · 목록 밖 이름 {n_bad} → 미분류')
    log(f'  → {MAP_PATH}')
    return cur


def _run_live(parts, system, tax, cfg, log):
    """실시간 병렬. 신규 상장 몇 개를 붙일 때 쓴다.

    배치 경로와 **같은 스키마**를 강제한다. 예전에는 실시간만 자유 형식이라
    모델이 ```json 펜스를 두르면 json.loads 가 실패하고 25종목이 사유 없이
    사라졌다. effort 도 뺀다 — 분류 모델(haiku)이 받지 않는 파라미터라
    요청이 전량 400 으로 떨어지고 그 400 은 bad 로 삼켜졌다.
    """
    import concurrent.futures as cf
    cl = C.client()
    usage = C.Usage()
    model = cfg['writer']['models']['cluster']
    cfg2 = {**cfg, 'writer': {**cfg['writer'], 'effort': None,
                              'models': {**cfg['writer']['models'],
                                         'narrate': model}}}
    out, n_bad, n_parse = [], 0, 0

    def one(part):
        txt = C.ask(cl, cfg2, system, _prompt(part, tax),
                    usage=usage, schema=SCHEMA, max_tokens=8000)
        return json.loads(txt).get('assignments') or []

    workers = max(1, int(cfg['writer'].get('parallel') or 1))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for f in cf.as_completed([ex.submit(one, p) for p in parts]):
            try:
                out.extend(f.result())
            except ValueError as e:                  # JSON 파싱 실패
                n_parse += 1
                log(f'   응답 파싱 실패: {e}')
            except Exception as e:                   # noqa: BLE001
                n_bad += 1
                log(f'   요청 실패: {type(e).__name__}: {str(e)[:120]}')
    if n_bad or n_parse:
        log(f'  요청 실패 {n_bad}건 · 파싱 실패 {n_parse}건 '
            f'(각 {CHUNK}종목이 배정되지 않았다)')
    log('  ' + usage.line())
    return out


def _run_batch(parts, system, tax, cfg, log, poll):
    """Batch API. 입출력 50% 할인. 대부분 1시간 안에, 최대 24시간."""
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    creds.load()
    cl = anthropic.Anthropic()
    model = cfg['writer']['models']['cluster']
    reqs = [Request(custom_id=f'sec-{i}',
                    params=MessageCreateParamsNonStreaming(
                        model=model, max_tokens=8000, system=system,
                        messages=[{'role': 'user', 'content': _prompt(p, tax)}],
                        output_config={'format': {'type': 'json_schema',
                                                  'schema': SCHEMA}}))
            for i, p in enumerate(parts)]
    batch = cl.messages.batches.create(requests=reqs)
    log(f'  배치 생성 {batch.id} · 요청 {len(reqs)}건. 완료까지 대기한다')
    # 배치는 최대 24시간이다. 탈출 조건이 'ended' 하나뿐이면 어떤 상태에서
    # 멈췄을 때 프로세스가 영원히 대기한다. 18:00 발송 파이프라인 안이라
    # 데드라인을 둔다. 넘기면 취소하고 그때까지 받은 것만 쓴다.
    deadline = time.monotonic() + MAX_BATCH_WAIT
    while True:
        b = cl.messages.batches.retrieve(batch.id)
        if b.processing_status == 'ended':
            break
        if time.monotonic() > deadline:
            log(f'  배치가 {MAX_BATCH_WAIT/3600:.0f}시간 안에 안 끝났다. 취소한다')
            try:
                cl.messages.batches.cancel(batch.id)
            except Exception as e:                   # noqa: BLE001
                log(f'   취소 실패: {e}')
            break
        c = b.request_counts
        log(f'   {b.processing_status} · 처리중 {c.processing} / 완료 {c.succeeded} '
            f'/ 실패 {c.errored}')
        time.sleep(poll)

    # 결과는 순서가 보장되지 않는다. custom_id 가 유일한 앵커다.
    # 이걸 안 쓰면 매칭이 '모델이 code 를 정확히 되돌려 준다'는 가정 하나에
    # 걸린다 — 앞자리 0 을 흘리거나 뒤바꾸면 A 의 섹터가 B 에 배정된다.
    want = {f'sec-{i}': set(x['code'] for x in part)
            for i, part in enumerate(parts)}
    out, n_err, n_off = [], 0, 0
    got = set()
    for r in cl.messages.batches.results(batch.id):
        cid = getattr(r, 'custom_id', None)
        if r.result.type != 'succeeded':
            n_err += 1
            continue
        got.add(cid)
        txt = '\n'.join(x.text for x in r.result.message.content if x.type == 'text')
        try:
            items = json.loads(txt).get('assignments') or []
        except ValueError:
            n_err += 1
            continue
        asked = want.get(cid) or set()
        for a in items:
            # 이 요청에 넣지 않은 코드가 돌아오면 버린다. 조용히 쓰면
            # 엉뚱한 종목에 섹터가 붙는다.
            if asked and str(a.get('code') or '') not in asked:
                n_off += 1
                continue
            out.append(a)
    miss = [c for c in want if c not in got]
    log(f'  배치 완료 · 배정 {len(out):,} · 실패 요청 {n_err} · '
        f'응답 없음 {len(miss)} · 요청 밖 코드 {n_off}')
    return out


def apply_to_db(conn, mapping, log=print):
    """배정 결과를 sector_map 테이블에 반영한다. 랭킹은 이 테이블을 본다."""
    from ..engine.db import now_kst
    now = now_kst()
    rows = [(c, v.get('sector') or UNMAPPED, 'board48', now)
            for c, v in mapping.items() if v.get('sector')]
    conn.executemany('INSERT OR REPLACE INTO sector_map VALUES(?,?,?,?)', rows)
    conn.commit()
    log(f'  sector_map 갱신 {len(rows):,}종목')
    return len(rows)

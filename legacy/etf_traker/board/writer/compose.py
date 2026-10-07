#!/usr/bin/env python3
"""
초안 조립 — 사실 팩 -> 섹션별 호출 -> 검증 -> draft.md.

흐름
  1. engine/facts.py 가 만든 사실 팩을 섹션으로 쪼갠다
  2. 장 흐름 1회 + 테마별 N회(병렬) + 캘린더 1회 호출
  3. 각 섹션을 그 섹션의 사실 팩으로 검증한다 (writer/verify.py)
  4. 검증 실패면 위반 내역을 붙여 재요청. 재시도 초과면 그 섹션을 뺀다
  5. 통과한 섹션만 이어 붙이고, 빠진 것을 문서 상단에 적는다

검증에 걸린 섹션을 그냥 내보내지 않는다. 리포트가 짧아지는 편이
지어낸 수치가 섞이는 것보다 낫다 (CLAUDE.md 2장 1·4·6번).
"""
import concurrent.futures as cf
import json

from ..engine import facts as F
from . import claude as C
from . import prompts as P
from . import verify as V


def _sub(pack, keys):
    """사실 팩에서 섹션에 필요한 부분만 잘라낸다. 안 쓰는 정보를 안 보여 준다."""
    return {k: pack[k] for k in keys if k in pack}


def _dump(x):
    return json.dumps(x, ensure_ascii=False, indent=1)


# 금지 문구(트리거를 모른다고 적는 문장)가 걸렸을 때 한 번 더 부르며 붙이는 지시.
PHRASE_NOTE = ('\n\n---\n\n직전 시도에 금지 문구가 있었다. 트리거를 모르면 트리거 문장을 '
               '**아예 쓰지 마라** — "미확인"·"뉴스 없음"·"재료 부재"·"단정하긴 어려움" 류의 '
               '문장을 지우고, 그 자리에는 등락률·신고가·거래량 사실만 적어라. 걸린 문장:\n')


def _one(cl, cfg, system, key, prompt, pack_slice, universe_names, usage, log):
    """한 섹션을 생성하고 검증한다. 반환 (본문, 실패 사유, 덧붙일 결손 목록).

    종목명·수치 위반은 재시도 뒤 섹션을 뺀다 — 지어낸 사실이 섞이는 것보다 짧은
    리포트가 낫다. 금지 문구(kind='phrase')만 남았을 때는 다르다: **한 번 더** 부르고,
    그래도 남으면 그 문장만 지우고 `{note, lost}` 를 돌려준다 — `note` 는 검수용이고
    `lost`(종목 사실이 함께 있던 문장 수)만 리포트의 결손이 된다.
    이 문장은 지워도 없는 사실이 생기지 않으므로 초안 전체를 막을 이유가 없다.
    """
    retries = cfg['writer']['retries']
    strict = cfg['writer']['strict_numbers']
    msg = prompt
    last = None
    attempt = 0
    phrase_retried = False
    while True:
        # API 예외를 여기서 잡는다. 예전에는 429 하나에 그날 초안 전체가
        # 사라졌다 — 한 섹션의 실패가 나머지를 죽일 이유가 없다.
        try:
            txt = C.ask(cl, cfg, system, msg, usage=usage)
        except Exception as e:                       # noqa: BLE001
            last = [dict(kind='api', value=type(e).__name__, why=str(e)[:200])]
            log(f'   [{key}] 호출 실패: {type(e).__name__}: {str(e)[:120]}')
            if attempt < retries:
                attempt += 1
                continue
            break
        ok, viol = V.check(txt, pack_slice, universe_names, strict_numbers=strict)
        hard = [x for x in viol if x['kind'] != 'phrase']
        soft = [x for x in viol if x['kind'] == 'phrase']
        if not hard:
            if not soft:
                if attempt or phrase_retried:
                    log(f'   [{key}] 재시도 후 통과')
                return txt, None, []
            if not phrase_retried:
                phrase_retried = True
                log(f'   [{key}] 금지 문구 {len(soft)}건 — 한 번 더 부른다')
                msg = prompt + PHRASE_NOTE + V.report(soft, limit=10)
                continue
            # 지운 문장에 `이름(값)` 짝이 있었으면 종목 사실도 함께 사라진 것이다 —
            # 문장 경계가 마침표라 쉼표로 이어진 등락률·거래량이 같이 나간다. 검수자가
            # 알아야 하므로 결손 문구에 그 수를 따로 적는다.
            lost = sum(1 for sent in V.phrases(txt) if V.PAIR.search(sent))
            txt, n = V.strip_phrases(txt)
            log(f'   [{key}] 금지 문구가 남아 문장 {n}개를 지웠다')
            if not txt.strip():
                return None, '금지 문구를 지우니 남는 문장이 없다', []
            note = f'문장 {n}개 제거(금지 문구)'
            if lost:
                note += f' — 그중 {lost}개는 종목 사실(이름·등락률)이 함께 있던 문장'
            # `note` 는 검수용이다 — '금지 문구' 는 우리 검증 절차의 말이라 리포트에
            # 싣지 않는다. 다만 `lost` 는 다르다: 종목 사실이 함께 지워졌으면 그만큼
            # **리포트에서 빠진 데이터**이고 2장 6번이 적으라는 바로 그것이다.
            # 사실이 함께 나간 문장이 없으면(lost=0) 빠진 데이터도 없다.
            return txt, None, [dict(note=note, lost=lost)]
        last = viol
        log(f'   [{key}] {V.report(viol, limit=5)}')
        if attempt < retries:
            attempt += 1
            msg = (prompt + '\n\n---\n\n직전 시도가 검증에 걸렸다. 아래를 고쳐서 다시 써라.\n'
                   + V.report(viol, limit=20)
                   + '\n\n사실 팩에 없는 종목명과 수치는 문장에서 빼라. '
                     '수치는 사실 팩의 문자열을 글자 그대로 옮겨라. '
                     '트리거를 모르면 트리거 문장을 쓰지 마라.')
            continue
        break
    return None, V.report(last), []


def run(asof, cfg, log=print, dry_run=False):
    """반환 (draft_md, meta). dry_run 이면 API 를 부르지 않고 프롬프트만 만든다."""
    pack = F.build(asof, cfg, log=log)
    from ..engine.build import read
    uni = read(asof, 'universe.json') or {}
    universe_names = [x['name'] for x in (uni.get('stocks') or []) if x.get('name')]

    system = P.system_blocks()
    jobs = []

    # 1. 장 흐름
    mkt_slice = _sub(pack, ['as_of', 'basis', 'counts', 'market', 'sectors',
                            'sectors_bottom', 'movers'])
    jobs.append(('장 흐름', P.MARKET.format(facts=_dump(mkt_slice)), mkt_slice, None))

    # 2. 테마별 (병렬)
    for t in pack.get('themes') or []:
        jobs.append((t['name'], P.THEME.format(name=t['name'], facts=_dump(t)),
                     t, f'#{t["name"]}'))

    # 3. 캘린더
    cal = pack.get('calendar') or {}
    if (cal.get('review') or cal.get('preview')):
        jobs.append(('이벤트', P.CALENDAR.format(facts=_dump(cal)), cal, None))

    if dry_run:
        log(f'  드라이런 — 호출 {len(jobs)}건 생성 (API 미호출)')
        body = []
        for key, prompt, _slice, _h in jobs:
            body.append(f'\n{"="*70}\n[{key}]\n{"="*70}\n{prompt}')
        return ('\n'.join(body),
                dict(dry_run=True, sections=[j[0] for j in jobs], pack=pack))

    cl = C.client()
    usage = C.Usage()
    out, skipped, notes = {}, {}, {}
    # 섹션별 병렬 호출 (CLAUDE.md 7장). 예전 주석은 병렬이라고 적어 놓고
    # 실제로는 전부 순차였다. 섹션이 7개면 지연이 그대로 7배다.
    workers = max(1, int(cfg['writer'].get('parallel') or 1))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, cl, cfg, system, key, prompt, sl,
                          universe_names, usage, log): key
                for key, prompt, sl, _h in jobs}
        for f in cf.as_completed(futs):
            key = futs[f]
            try:
                txt, err, ns = f.result()
            except Exception as e:                   # noqa: BLE001
                txt, err, ns = None, f'{type(e).__name__}: {e}', []
            if txt:
                out[key] = txt
                if ns:
                    notes[key] = ns
            else:
                skipped[key] = err or '사유 미상'

    md = _assemble(pack, jobs, out, skipped, notes)
    log('  ' + usage.line())
    # 테마 섹션만 따로 돌려준다. 뉴스 탭이 테마 카드 위에 이 서술을 얹는다 —
    # draft.md 전체에서 다시 파싱하게 두면 조립 형식이 바뀔 때마다 깨진다.
    narratives = {key: out[key] for key, _p, _s, header in jobs
                  if header and key in out}
    return md, dict(dry_run=False, usage=usage.__dict__,
                    sections=list(out), skipped=skipped, notes=notes, pack=pack,
                    narratives=narratives)


def _assemble(pack, jobs, out, skipped, notes=None):
    L = [f'#{pack["title_date"]}_신고가 및 등락률 Top 랭킹 코멘트', '']

    missing = list(pack.get('missing') or [])
    # 섹션이 안 나간 것은 읽는 사람이 알아야 할 결손이다. 다만 **왜** 안 나갔는지는
    # 내부 사정이다 — '생성 실패 — 검증 실패 1건' 은 우리 검증 절차의 말이지
    # 리포트의 말이 아니다(2026-09-22 실발송이 그랬다). 사실만 적고 사유는
    # `meta.skipped` 와 로그에 그대로 남긴다.
    for k in skipped:
        missing.append(f'{k} 섹션은 이번 회차에 없습니다')
    # 섹션은 살렸지만 손을 댄 것. 메모 자체는 검수자용이라 `meta.notes` 로만 남기고,
    # 그중 **종목 사실이 함께 지워진 수**만 리포트의 결손으로 올린다.
    for k, ns in (notes or {}).items():
        lost = sum(int((n or {}).get('lost') or 0)
                   for n in ns if isinstance(n, dict))
        if lost:
            missing.append(f'{k} 섹션에서 종목 사실이 담긴 문장 {lost}개가 빠졌습니다')
    if missing:
        L.append('> **빠진 것**')
        for m in missing:
            L.append(f'> - {m}')
        L.append('')

    for key, _p, _s, header in jobs:
        if key not in out:
            continue
        if header:
            L.append(header)
        L.append(out[key])
        L.append('')

    if pack.get('themes_table'):
        L.append('#기타 테마 (수치만)')
        L.append('')
        L.append('| 테마 | 등락률 | 신고가 | 거래대금 |')
        L.append('|---|---:|---:|---:|')
        for t in pack['themes_table']:
            L.append(f'| {t["name"]} | {t.get("chg","–")} | '
                     f'{t.get("n_newhigh",0)} | {t.get("turnover","–")} |')
        L.append('')

    L.append('---')
    # 'board 엔진이 계산한 값이고 서술은 그 수치를 옮긴 것이다' 는 우리가 어떻게
    # 만드는지에 대한 말이라 리포트에 싣지 않는다. 남는 것은 이 숫자가 언제
    # 무엇을 기준으로 한 값인가 하나다 — 그건 읽는 데 필요하다.
    L.append(f'{pack["as_of"]} · {pack["basis"]} 기준')
    return '\n'.join(L)


def extract_claims(draft, pack, cfg, log=print):
    """내일 검증할 주장을 뽑는다. 탐지기 7(claim_check)의 입력."""
    cl = C.client()
    usage = C.Usage()
    try:
        data = C.ask_json(cl, cfg, P.system_blocks(),
                          P.CLAIMS.format(draft=draft), P.CLAIMS_SCHEMA, usage=usage)
    except Exception as e:                       # noqa: BLE001
        log(f'  주장 추출 실패: {e}')
        return dict(as_of=pack['as_of'], claims=[], error=str(e))
    log(f'  주장 {len(data.get("claims") or [])}건 추출 · {usage.line()}')
    return dict(as_of=pack['as_of'], source='claude', claims=data.get('claims') or [])

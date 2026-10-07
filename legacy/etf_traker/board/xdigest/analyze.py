#!/usr/bin/env python3
"""
다이제스트 분석 — XDIGEST.md 3-2·3-3. `posts.json` → `themes.json` · `facts.json`.

흐름 (계약의 호출 ①~④. ⑤ 계정 인사이트는 대상 계정이 없어 만들지 않는다)

  ① 배정   Haiku   창 안 게시물 전부 → 소주제 / 단독 / 제외        1회
  ② 사실   Sonnet  소주제 하나의 게시물만 → `•` 줄                소주제 수(≤8) 병렬
  ③ 단독   Sonnet  단독 게시물 → `•` 줄 하나씩                    1회
  ④ 해석   Sonnet  **②③ 의 사실 줄만** → `»` 줄                   1회
  ⑥ 구획   Sonnet  별도 구획 하나의 게시물만 → 관련 키 + `•` 줄    구획 수(D-NEXT-Q)

①~④ 는 **기본 구획**(반도체·AI) 게시물만 받는다. 별도 구획(AI 최신 뉴스 ·
퀀트·백테스트)의 게시물은 수집이 붙인 `topic` 으로 코드가 갈라 ⑥ 에만 넣는다 —
구획을 모델이 가르면 반도체 글이 AI 뉴스로 새도 되짚을 근거가 없다. `topic` 이
없는 게시물(구획이 생기기 전 posts.json)은 기본 구획이다.

각 단계 뒤에 코드가 검사한다. **LLM 이 하는 일은 묶기와 한 줄로 옮기기뿐이다** —
숫자·순위·건수·출처 계정 줄은 코드가 만든다 (CLAUDE.md 2장 3번).

① 뒤에 코드가 보는 것 (3-2 끝):
  - 모든 키가 실재하는지            (없는 키는 버리고 위반으로 적는다)
  - 한 키가 두 곳에 있지 않은지     (먼저 나온 곳만 남긴다)
  - 소주제의 계정이 2개 이상인지    (아니면 단독 소식으로 내린다)
  - 소주제 상한 8                   (초과분은 단독으로 내리고 결손에 적는다)

②③④ 뒤에는 `xdigest/verify.py` 가 줄마다 게시물 원문과 대조한다. 걸린 줄은
재시도 2회(`writer.retries`) 뒤에도 안 되면 **뺀다.** 빠진 것은 결손에 적는다 —
`writer/compose._one` 의 흐름 그대로다. 조용히 통과시키지 않는다.

① 입력의 게시물 키는 `p001` 꼴이다. `posts.json` 의 `id` 는 AT-URI(60자 남짓)라
모델이 그대로 돌려주면 출력이 길어져 잘리고, 잘리면 3-6 의 '분석 실패' 경로로
빈 다이제스트가 나간다. 코드가 키↔AT-URI 를 되돌리고 저장되는 JSON 에는 AT-URI
만 들어간다 (DECISIONS D-NEXT-A2).

이 환경은 프록시가 Anthropic API 를 막는다. 라이브 호출은 러너에서만 돌고,
여기서는 `client=` 로 가짜를 넣어 흐름을 시험한다.
"""
import json
from datetime import datetime, timedelta, timezone

from ..ingest import bsky as BS
from ..writer import claude as C
from . import prompts as P
from . import verify as V

KST = timezone(timedelta(hours=9))

# 건수 상한. 계약 1장 표 그대로이고 설정(`xdigest.limits`)이 덮을 수 있다.
LIMITS = dict(subtopics=8, facts_per_subtopic=5, standalone=12, views=6,
              min_accounts=2)

# ① 의 `excluded.why` 는 이 셋 중 하나다. 자유 문장이 아니다 (3-2).
WHY = ('off_topic', 'ad', 'no_text')

# 프롬프트에 쓰는 게시물 키·사실 줄 키.
PKEY = 'p{:03d}'
FKEY = 'f{:03d}'
VKEY = 'v{:d}'


def now_kst():
    return datetime.now(KST).isoformat(timespec='seconds')


def limits(cfg):
    """상한. 설정에 있으면 그 값, 없으면 계약 1장 표의 값."""
    out = dict(LIMITS)
    out.update((cfg.get('xdigest') or {}).get('limits') or {})
    return out


def _assign_tokens(n):
    """① 의 출력 상한. 건수에 비례해 잡는다 (3-2: `1,000 + 30 × 건수`).

    고정 4000 이면 150건에서 잘려 `json.loads` 가 터진다. 그것이 `ask_json` 에
    `max_tokens` 를 더한 이유다 (D-NEXT-A).
    """
    return 1000 + 30 * max(1, n)


# ─────────────────────────── 입력 ───────────────────────────


def _pool(posts):
    """분석 대상 — 창 안이고 본문이 있는 게시물. 반환 (행, 결손, 건수).

    본문이 없는 게시물을 조용히 버리지 않는다. 창 밖도 건수로 남긴다 — 받는 쪽이
    '그런 게시물이 없었다' 로 읽으면 안 된다 (CLAUDE.md 2장 6번).
    """
    rows, no_text, out_window = [], 0, 0
    for p in (posts.get('posts') or []):
        if not p.get('in_window', True):
            out_window += 1
            continue
        if not str(p.get('text') or '').strip():
            no_text += 1
            continue
        rows.append(p)
    gaps = []
    if no_text:
        gaps.append(dict(scope='pool', why=f'본문 미수집 {no_text}건'))
    if out_window:
        gaps.append(dict(scope='pool', why=f'창 밖 {out_window}건 제외'))
    return rows, gaps, dict(pool=len(rows), no_text=no_text, out_window=out_window)


def _by_topic(rows, secs):
    """분석 대상을 구획별로 나눈다. 반환 (기본 구획 행, {구획 키: 행}).

    나누는 근거는 수집이 적은 `topic`(처음 잡힌 질의의 구획) 하나다. `topic` 이
    없거나 지금 설정에 없는 구획 키면 기본 구획으로 본다 — 설정에서 구획을 뺀 날
    그 게시물이 어디에도 안 실리면 조용히 사라진다(CLAUDE.md 2장 6번).
    """
    keys = {sec['key'] for sec in secs}
    main, parts = [], {k: [] for k in keys}
    for p in rows:
        t = p.get('topic')
        if t in keys:
            parts[t].append(p)
        else:
            main.append(p)
    return main, parts


def _keys(rows):
    """게시물 행 → (프롬프트용 행, 키→id, id→키)."""
    fwd, rev, out = {}, {}, []
    for i, p in enumerate(rows, 1):
        k = PKEY.format(i)
        fwd[k] = p['id']
        rev[p['id']] = k
        out.append((k, p.get('account') or '', p.get('text') or ''))
    return out, fwd, rev


def _accounts(ids, index):
    """출처 계정 순서 — 그 단위 게시물 수 내림차순 → 계정명 오름차순(대소문자 무시).

    계약 1장이 `└` 줄의 순서로 고정한 규칙이다. 렌더가 다시 세지 않게 여기서
    정해 둔다 — 두 곳에서 세면 한쪽만 고쳐진다.
    """
    cnt = {}
    for i in ids:
        a = (index.get(i) or {}).get('account')
        if a:
            cnt[a] = cnt.get(a, 0) + 1
    return sorted(cnt, key=lambda a: (-cnt[a], a.lower()))


def _rows_for(ids, index):
    """프롬프트용 (키, 계정, 본문) 과 그 키→id 지도. 키는 단위 안에서만 쓰인다."""
    local = {PKEY.format(n): i for n, i in enumerate(ids, 1)}
    rows = [(k, (index.get(i) or {}).get('account') or '',
             (index.get(i) or {}).get('text') or '') for k, i in local.items()]
    return rows, local


# ─────────────────────────── ① 배정 ───────────────────────────


def _apply(raw, fwd, index, lim):
    """① 출력에 코드 검사를 건다. 반환 (소주제, 단독 id, 제외, 미배정 id, 위반, 결손).

    위반은 재시도의 근거이고, 재시도가 끝나면 아래 수선이 그대로 결과가 된다 —
    없는 키는 버리고, 겹친 키는 먼저 나온 곳만 남기고, 계정 1개 소주제와 상한
    초과 소주제는 단독으로 내린다.
    """
    viol, gaps = [], []
    used = {}
    themes, untitled = [], []
    for t in (raw.get('themes') or []):
        title = str(t.get('title') or '').strip()
        keys = []
        for k in (t.get('post_ids') or []):
            if k not in fwd:
                viol.append(dict(kind='ids', value=str(k),
                                 why='① 입력에 없는 게시물 키'))
                continue
            if k in used:
                viol.append(dict(kind='dup', value=str(k),
                                 why=f'키가 두 곳에 있다 — 이미 {used[k]}'))
                continue
            used[k] = f'소주제 "{title}"'
            keys.append(k)
        if not title:
            viol.append(dict(kind='title', value='(빈 제목)',
                             why='소주제 제목이 없다'))
            # 제목이 없어 소주제를 버릴 때 게시물까지 버리면 어디에도 안 남는다 —
            # `used` 에 표시된 채 테마가 사라지면 미배정 계산에서도 빠진다. 단독
            # 소식으로 내리고 결손에 건수를 적는다 (3-2: 모든 게시물이 셋 중 하나).
            untitled.extend(keys)
            continue
        if keys:
            themes.append(dict(title=title, post_ids=[fwd[k] for k in keys]))

    standalone = []
    for k in untitled:
        used[k] = '단독'
        standalone.append(fwd[k])
    if untitled:
        gaps.append(dict(scope='assign',
                         why=f'제목 없는 소주제의 게시물 {len(untitled)}건 '
                             f'단독으로 내림'))
    for k in (raw.get('standalone') or []):
        if k not in fwd:
            viol.append(dict(kind='ids', value=str(k), why='① 입력에 없는 게시물 키'))
            continue
        if k in used:
            viol.append(dict(kind='dup', value=str(k),
                             why=f'키가 두 곳에 있다 — 이미 {used[k]}'))
            continue
        used[k] = '단독'
        standalone.append(fwd[k])

    excluded = []
    for x in (raw.get('excluded') or []):
        k, why = str((x or {}).get('id') or ''), str((x or {}).get('why') or '')
        if k not in fwd:
            viol.append(dict(kind='ids', value=k, why='① 입력에 없는 게시물 키'))
            continue
        if k in used:
            viol.append(dict(kind='dup', value=k,
                             why=f'키가 두 곳에 있다 — 이미 {used[k]}'))
            continue
        if why not in WHY:
            # 사유를 `off_topic` 으로 덮어쓰면 모델이 말하지 않은 것을 단정한다
            # (CLAUDE.md 2장 1번). 제외로 받지 않고 미배정으로 흘려보낸다 —
            # `missing` 에 남아 건수가 보이고, 위반은 그대로 적힌다.
            viol.append(dict(kind='why', value=why or '(빈 사유)',
                             why=f'제외 사유는 {" · ".join(WHY)} 셋 중 하나다 — '
                                 f'제외로 받지 않고 미배정으로 둠'))
            continue
        used[k] = '제외'
        excluded.append(dict(id=fwd[k], why=why))

    missing = [fwd[k] for k in fwd if k not in used]
    if missing:
        viol.append(dict(kind='missing', value=str(len(missing)),
                         why='셋 중 어디에도 들어가지 않은 게시물이 있다'))

    # 계정 2개 미만 소주제를 내린다 (3-2). `└` 줄이 계정 하나면 소주제가 아니다.
    keep = []
    for t in themes:
        accts = _accounts(t['post_ids'], index)
        if len(accts) < lim['min_accounts']:
            standalone.extend(t['post_ids'])
            gaps.append(dict(scope='assign',
                             why=f'소주제 "{t["title"]}" 는 출처 계정 {len(accts)}개 — '
                                 f'단독 소식으로 내림'))
            continue
        t['accounts'] = accts
        t['n_posts'] = len(t['post_ids'])
        keep.append(t)

    # 상한 8. 정렬은 계약 1장 — 출처 계정 수 내림차순 → 게시물 수.
    keep.sort(key=lambda t: (-len(t['accounts']), -t['n_posts'], t['title']))
    if len(keep) > lim['subtopics']:
        cut = keep[lim['subtopics']:]
        keep = keep[:lim['subtopics']]
        for t in cut:
            standalone.extend(t['post_ids'])
        gaps.append(dict(scope='assign',
                         why=f'소주제 {len(cut)}건 생략 — 상한 {lim["subtopics"]}'))
    for i, t in enumerate(keep, 1):
        t['key'] = f't{i}'
    return keep, standalone, excluded, missing, viol, gaps


def _assign(cl, cfg, system, rows, fwd, index, lim, log, usage, retries):
    """① 호출 + 코드 검사. 반환 (결과 dict, 위반, 시도 수, 사유|None)."""
    prompt = P.ASSIGN.format(n=len(rows), posts=P.posts_block(rows))
    model = cfg['writer']['models'].get('cluster')
    mx = _assign_tokens(len(rows))
    msg, last = prompt, None
    for attempt in range(retries + 1):
        try:
            raw = C.ask_json(cl, cfg, system, msg, P.ASSIGN_SCHEMA, usage=usage,
                             max_tokens=mx, model=model, strict_stop=True)
        except C.Truncated:
            # 상한에서 잘린 것이면 같은 상한으로 다시 불러도 같은 자리에서 잘린다.
            # 상한을 올려 부르고 사유를 '잘림' 으로 남긴다 — 예전 판은 세 번 다
            # 같은 상한으로 부른 뒤 'JSONDecodeError' 만 남겼다 (D-NEXT-A).
            last = f'① 출력 잘림 (max_tokens {mx:,})'
            mx = int(mx * 2)
            log(f'   {last} — 상한 {mx:,} 으로 다시 부른다')
            msg = prompt
            continue
        except Exception as e:                       # noqa: BLE001
            last = f'{type(e).__name__}: {str(e)[:200]}'
            log(f'   ① 호출 실패: {last}')
            msg = prompt
            continue
        got = _apply(raw, fwd, index, lim)
        themes, standalone, excluded, missing, viol, gaps = got
        if not viol or attempt >= retries:
            if viol:
                log(f'   ① {V.report(viol, limit=5)}')
                gaps.append(dict(scope='assign',
                                 why=f'① 배정 위반 {len(viol)}건 — 코드가 수선함'
                                     + (f' · 미배정 {len(missing)}건'
                                        if missing else '')))
            return (dict(themes=themes, standalone=standalone, excluded=excluded,
                         unassigned=missing, gaps=gaps, violations=viol,
                         attempts=attempt + 1, model=model), viol,
                    attempt + 1, None)
        log(f'   ① {V.report(viol, limit=5)} — 다시 부른다')
        msg = prompt + P.RETRY_NOTE + V.report(viol, limit=20)
    return None, [], retries + 1, last or '① 응답을 받지 못함'


# ─────────────────────────── ②③ 사실 줄 ───────────────────────────


def _lines(raw, field, keymap, allowed, sources, accounts, alias):
    """모델이 준 줄들을 (키→id 되돌림 + 검증)한다. 반환 [(줄, 위반)].

    없는 키는 그대로 남겨 검증 6번이 잡게 한다 — 여기서 조용히 버리면 근거 없는
    줄이 통과한다.
    """
    out = []
    for it in (raw.get(field) or []):
        text = str((it or {}).get('text') or '').strip()
        ids = [keymap.get(k, k) for k in ((it or {}).get('post_ids') or [])]
        if not text:
            continue
        viol = V.check(text, ids, sources, accounts, alias, allowed=allowed)
        out.append((dict(text=text, post_ids=ids), viol))
    return out


def _facts_rounds(cl, cfg, system, themes, index, alias, accounts, lim,
                  retries, log, usage, records):
    """② — 소주제별 사실 줄. 병렬 4, 라운드마다 걸린 소주제만 다시 부른다.

    반환 (done, failed). `failed` 는 {소주제 키: 사유} — **호출이 막혀** 결과가
    없는 소주제다. 검증으로 줄이 빠진 것과 429·키 없음으로 아무 검증도 못 한 것을
    부르는 쪽이 가려 적어야 한다. 섞어 적으면 결손 줄이 '모델이 지어냈다' 고
    말하는데 실제로는 API 가 막힌 것이다 (shared: 403/429/503 은 막힌 것이다).

    `ask_many` 에 `schema` 를 넘긴다(그것이 claude.py 를 고친 이유 중 하나다).
    한 줄이라도 걸리면 그 소주제를 다시 부르고, 재시도가 끝나면 걸린 줄만 뺀다.
    """
    sources = {p['id']: p.get('text') or '' for p in index.values()}
    workers = max(1, int(cfg['writer'].get('parallel') or 1))
    pending = {t['key']: t for t in themes}
    done, notes, failed = {}, {}, {}
    for attempt in range(retries + 1):
        if not pending:
            break
        jobs, locals_ = [], {}
        for k, t in pending.items():
            rows, locals_[k] = _rows_for(t['post_ids'], index)
            body = P.FACTS.format(title=t['title'], posts=P.posts_block(rows),
                                  cap=lim['facts_per_subtopic'])
            jobs.append((k, body + notes.get(k, '')))
        out, bad = C.ask_many(cl, cfg, system, jobs, usage=usage, workers=workers,
                              log=log, schema=P.FACTS_SCHEMA, strict_stop=True)
        nxt = {}
        for k, why in bad.items():
            # 호출 자체가 실패한 소주제도 재시도 대상이다. 예전 판은 여기서
            # 조용히 떨어져 429 한 번에 그 소주제가 사라졌다.
            if attempt < retries:
                nxt[k] = pending[k]
                continue
            failed[k] = why
            records.append(dict(unit=f'theme:{k}', attempts=attempt + 1,
                                error=why, kept=0, dropped=0, violations=[]))
        for k, txt in out.items():
            t = pending[k]
            local = locals_[k]
            try:
                raw = json.loads(txt)
            except ValueError as e:
                nxt[k] = t
                notes[k] = P.RETRY_NOTE + f'JSON 파싱 실패 — {e}'
                continue
            title = str(raw.get('title') or t['title']).strip() or t['title']
            lines = _lines(raw, 'facts', local, set(t['post_ids']), sources,
                           accounts, alias)
            viol = [v for _l, vs in lines for v in vs]
            if viol and attempt < retries:
                nxt[k] = t
                notes[k] = P.RETRY_NOTE + V.report(viol, limit=20)
                continue
            keep = [l for l, vs in lines if not vs]
            drop = [(l, vs) for l, vs in lines if vs]
            records.append(dict(
                unit=f'theme:{k}', attempts=attempt + 1, kept=len(keep),
                dropped=len(drop),
                violations=[dict(line=l['text'][:120], **v)
                            for l, vs in drop for v in vs]))
            done[k] = dict(key=k, title=title, post_ids=t['post_ids'],
                           accounts=t['accounts'], facts=keep,
                           dropped=len(drop))
        pending = nxt
        if pending and attempt < retries:
            log(f'   ② 재시도 {attempt + 1} — 소주제 {len(pending)}건')
    for k in pending:
        failed[k] = '재시도 뒤에도 응답을 못 받음'
        records.append(dict(unit=f'theme:{k}', attempts=retries + 1, kept=0,
                            dropped=0, error=failed[k], violations=[]))
    return done, failed


STANDALONE_MAX_TOKENS = 8000


def _standalone(cl, cfg, system, ids, index, alias, accounts, lim, retries,
                log, usage, records):
    """③ — 단독 소식. 한 번 부르고 걸린 줄은 재시도 뒤에 뺀다."""
    if not ids:
        return [], []
    sources = {p['id']: p.get('text') or '' for p in index.values()}
    # 정렬은 계약 1장 — 게시물 시각 내림차순. 상한을 넘으면 **뒤에서** 버리므로
    # 자르기 전에 정렬해야 버리는 것이 오래된 쪽이 된다.
    ids = sorted(ids, key=lambda i: str((index.get(i) or {}).get('posted_at') or ''),
                 reverse=True)
    rows, local = _rows_for(ids, index)
    prompt = P.STANDALONE.format(posts=P.posts_block(rows), cap=lim['standalone'])
    msg, gaps = prompt, []
    for attempt in range(retries + 1):
        try:
            # 4,000(기본)이면 재시도 때 잘린다 — 2026-09-23 미리보기에서 ③ 재시도가
            # 'Truncated (max_tokens 4,000)' 로 죽었다. 단독 소식은 줄마다 수치·
            # 출처가 길어 상한 개수를 다 채우면 4,000 을 넘는다.
            raw = C.ask_json(cl, cfg, system, msg, P.STANDALONE_SCHEMA,
                             usage=usage, max_tokens=STANDALONE_MAX_TOKENS,
                             strict_stop=True)
        except Exception as e:                       # noqa: BLE001
            why = f'{type(e).__name__}: {str(e)[:200]}'
            log(f'   ③ 호출 실패: {why}')
            records.append(dict(unit='standalone', attempts=attempt + 1, kept=0,
                                dropped=0, error=why, violations=[]))
            msg = prompt
            continue
        lines = _lines(raw, 'items', local, set(ids), sources, accounts, alias)
        viol = [v for _l, vs in lines for v in vs]
        if viol and attempt < retries:
            log(f'   ③ {V.report(viol, limit=5)} — 다시 부른다')
            msg = prompt + P.RETRY_NOTE + V.report(viol, limit=20)
            continue
        keep = [l for l, vs in lines if not vs]
        drop = [(l, vs) for l, vs in lines if vs]
        records.append(dict(
            unit='standalone', attempts=attempt + 1, kept=len(keep),
            dropped=len(drop),
            violations=[dict(line=l['text'][:120], **v)
                        for l, vs in drop for v in vs]))
        if drop:
            gaps.append(dict(scope='standalone',
                             why=f'검증 실패로 뺀 단독 소식 {len(drop)}건'))
        # 시각 순으로 자른다. `keep` 의 순서는 모델 출력 순서라 다시 정렬한다.
        keep.sort(key=lambda l: max(
            str((index.get(i) or {}).get('posted_at') or '') for i in l['post_ids']),
            reverse=True)
        if len(keep) > lim['standalone']:
            gaps.append(dict(scope='standalone',
                             why=f'단독 소식 {len(keep) - lim["standalone"]}건 생략 — '
                                 f'상한 {lim["standalone"]}'))
            keep = keep[:lim['standalone']]
        return keep, gaps
    gaps.append(dict(scope='standalone', why='③ 응답을 못 받아 단독 소식 없음'))
    return [], gaps


# ─────────────────────────── ④ 해석 ───────────────────────────


def _views(cl, cfg, system, facts, alias, accounts, lim, retries, log, usage,
           records, blocked=False):
    """④ — 투자 인사이트. 입력은 ②③ 의 사실 줄만이다.

    게시물 원문을 다시 주지 않는다. 주면 사실 줄에서 걸러 낸 성분이 해석 줄로
    되살아난다. 그래서 검증의 대조 대상도 사실 줄이다.

    `blocked` 는 ②③ 호출이 막혀 사실 줄이 없는 경우다. '사실 줄이 없다' 와
    '막혀서 못 만들었다' 를 같은 문장으로 적지 않는다 (CLAUDE.md 2장 6번).
    """
    if not facts:
        why = ('②③ 호출이 막혀 사실 줄이 없음 — ④ 를 부르지 않음' if blocked
               else '사실 줄이 없어 ④ 를 부르지 않음')
        return [], [dict(scope='views', why=why)]
    sources = {f['id']: f['text'] for f in facts}
    prompt = P.VIEWS.format(facts=P.facts_block([(f['id'], f['text']) for f in facts]),
                            cap=lim['views'])
    msg, gaps = prompt, []
    for attempt in range(retries + 1):
        try:
            raw = C.ask_json(cl, cfg, system, msg, P.VIEWS_SCHEMA, usage=usage,
                             strict_stop=True)
        except Exception as e:                       # noqa: BLE001
            why = f'{type(e).__name__}: {str(e)[:200]}'
            log(f'   ④ 호출 실패: {why}')
            records.append(dict(unit='views', attempts=attempt + 1, kept=0,
                                dropped=0, error=why, violations=[]))
            msg = prompt
            continue
        lines, viol = [], []
        for it in (raw.get('views') or []):
            text = str((it or {}).get('text') or '').strip()
            ids = [str(k) for k in ((it or {}).get('fact_ids') or [])]
            if not text:
                continue
            vs = V.check(text, ids, sources, accounts, alias)
            lines.append((dict(text=text, fact_ids=ids), vs))
            viol.extend(vs)
        if viol and attempt < retries:
            log(f'   ④ {V.report(viol, limit=5)} — 다시 부른다')
            msg = prompt + P.RETRY_NOTE + V.report(viol, limit=20)
            continue
        keep = [l for l, vs in lines if not vs]
        drop = [(l, vs) for l, vs in lines if vs]
        records.append(dict(
            unit='views', attempts=attempt + 1, kept=len(keep), dropped=len(drop),
            violations=[dict(line=l['text'][:120], **v)
                        for l, vs in drop for v in vs]))
        if drop:
            gaps.append(dict(scope='views',
                             why=f'검증 실패로 뺀 투자 인사이트 {len(drop)}건'))
        if len(keep) > lim['views']:
            gaps.append(dict(scope='views',
                             why=f'투자 인사이트 {len(keep) - lim["views"]}건 생략 — '
                                 f'상한 {lim["views"]}'))
            keep = keep[:lim['views']]
        for i, l in enumerate(keep, 1):
            l['id'] = VKEY.format(i)
        return keep, gaps
    gaps.append(dict(scope='views', why='④ 응답을 못 받아 투자 인사이트 없음'))
    return [], gaps


# ─────────────────────────── ⑥ 별도 구획 ───────────────────────────


def _section_pool(sec, rows):
    """구획 게시물 → (모델에 넣을 행, 키워드로 거른 건수, 상한으로 뺀 건수).

    `keywords` 가 있으면 본문(소문자)에 그중 하나라도 있는 것만 남긴다. 검색은
    본문 밖(핸들·표시 이름)에서도 걸리는 일이 있어, 검색어가 본문에 한 번도 안
    나오는 글은 그 구획 소식일 수가 없다. 상한 `max_pool` 은 최신부터 남긴다 —
    넘친 건수는 결손에 적는다(조용히 자르지 않는다).
    """
    kws = sec.get('keywords') or []
    kept = [p for p in rows
            if not kws or any(k in str(p.get('text') or '').lower() for k in kws)]
    filtered = len(rows) - len(kept)
    kept.sort(key=lambda p: str(p.get('posted_at') or ''), reverse=True)
    cap = int(sec.get('max_pool') or 150)
    return kept[:cap], filtered, max(0, len(kept) - cap)


def _section(cl, cfg, system, sec, rows, index, alias, accounts, retries, log,
             usage, records):
    """⑥ — 별도 구획 하나(D-NEXT-Q). 반환 (구획 dict, 결손).

    ① 의 제외와 ③ 의 한 줄 옮기기를 한 호출에 한다. 모델은 범위에 맞는 게시물
    키(`relevant`)와 `•` 줄(`items`)을 돌려주고, 코드가 ③ 과 같은 검증(3-3)을
    건다 — 걸린 줄은 재시도 뒤에 뺀다. 구획 건수(머리 2행)는 `relevant` 와 남은
    줄이 댄 게시물의 합집합이다. 모델이 센 숫자가 아니라 코드가 센 키 수다.

    게시물이 0건이면 **부르지 않는다.** 렌더가 '수집 구간 내 게시물 0건' 을
    적는다 — 없는 날에 지어낼 것이 없다(CLAUDE.md 2장 6번).
    """
    key, title = sec['key'], sec['title']
    pool, filtered, cut = _section_pool(sec, rows)
    doc = dict(key=key, title=title, label=sec['label'], pool=len(rows),
               filtered=filtered, cut=cut, relevant=[], analyzed=0, items=[],
               dropped=0, error=None)
    gaps = []
    if filtered:
        gaps.append(dict(scope=f'section:{key}',
                         why=f'{title} 본문에 검색어가 없는 게시물 {filtered}건 제외'))
    if cut:
        gaps.append(dict(scope=f'section:{key}',
                         why=f'{title} 게시물 {cut}건 생략 — 상한 {sec["max_pool"]}'))
    if not pool:
        return doc, gaps
    ids = [p['id'] for p in pool]
    sources = {p['id']: p.get('text') or '' for p in index.values()}
    prow, local = _rows_for(ids, index)
    prompt = P.SECTION.format(title=title, n=len(prow), posts=P.posts_block(prow),
                              cap=sec['max_items'])
    mx = _assign_tokens(len(prow))
    msg, last = prompt, None
    for attempt in range(retries + 1):
        try:
            raw = C.ask_json(cl, cfg, system, msg, P.SECTION_SCHEMA, usage=usage,
                             max_tokens=mx, strict_stop=True)
        except C.Truncated:
            # ① 과 같다 — 같은 상한으로 다시 부르면 같은 자리에서 잘린다.
            last = f'⑥ 출력 잘림 (max_tokens {mx:,})'
            mx = int(mx * 2)
            log(f'   ⑥ {title} {last} — 상한 {mx:,} 으로 다시 부른다')
            msg = prompt
            continue
        except Exception as e:                       # noqa: BLE001
            last = f'{type(e).__name__}: {str(e)[:200]}'
            log(f'   ⑥ {title} 호출 실패: {last}')
            msg = prompt
            continue
        lines = _lines(raw, 'items', local, set(ids), sources, accounts, alias)
        viol = [v for _l, vs in lines for v in vs]
        if viol and attempt < retries:
            log(f'   ⑥ {title} {V.report(viol, limit=5)} — 다시 부른다')
            msg = prompt + P.RETRY_NOTE + V.report(viol, limit=20)
            continue
        keep = [l for l, vs in lines if not vs]
        drop = [(l, vs) for l, vs in lines if vs]
        # 없는 키는 버린다. `relevant` 는 건수의 근거일 뿐 줄이 아니라 재시도하지
        # 않는다 — 버린 건수는 기록에 남긴다.
        rel = [local[k] for k in dict.fromkeys(raw.get('relevant') or []) if k in local]
        bad_rel = len(raw.get('relevant') or []) - len(rel)
        records.append(dict(
            unit=f'section:{key}', attempts=attempt + 1, kept=len(keep),
            dropped=len(drop), bad_relevant=bad_rel,
            violations=[dict(line=l['text'][:120], **v)
                        for l, vs in drop for v in vs]))
        if drop:
            gaps.append(dict(scope=f'section:{key}',
                             why=f'검증 실패로 뺀 {title} 줄 {len(drop)}건'))
        if len(keep) > sec['max_items']:
            gaps.append(dict(scope=f'section:{key}',
                             why=f'{title} {len(keep) - sec["max_items"]}건 생략 — '
                                 f'상한 {sec["max_items"]}'))
            keep = keep[:sec['max_items']]
        cited = [i for l in keep for i in l['post_ids']]
        relevant = list(dict.fromkeys(rel + cited))
        doc.update(relevant=relevant, analyzed=len(relevant), items=keep,
                   dropped=len(drop))
        return doc, gaps
    why = last or '⑥ 응답을 받지 못함'
    records.append(dict(unit=f'section:{key}', attempts=retries + 1, kept=0,
                        dropped=0, error=why, violations=[]))
    gaps.append(dict(scope=f'section:{key}', why=f'{title} 분석 실패 — {why}'))
    doc['error'] = why
    return doc, gaps


def _sections_run(cl, cfg, system, secs, parts, index, alias, accounts, retries,
                  log, usage, records):
    """구획마다 ⑥ 을 부른다. 반환 (구획 dict 목록, 결손)."""
    out, gaps = [], []
    for sec in secs:
        d, g = _section(cl, cfg, system, sec, parts.get(sec['key']) or [], index,
                        alias, accounts, retries, log, usage, records)
        out.append(d)
        gaps += g
        log(f'  ⑥ {sec["title"]} — 게시물 {d["pool"]} · 관련 {d["analyzed"]} · '
            f'줄 {len(d["items"])}' + (f' · 실패 {d["error"]}' if d['error'] else ''))
    return out, gaps


def _empty_sections(secs, parts):
    """⑥ 을 부르지 못한 날의 구획 dict. 게시물 수만 코드가 센다."""
    return [dict(key=sec['key'], title=sec['title'], label=sec['label'],
                 pool=len(parts.get(sec['key']) or []), filtered=0, cut=0,
                 relevant=[], analyzed=0, items=[], dropped=0, error=None)
            for sec in secs]


# ─────────────────────────── 진입점 ───────────────────────────


def run(posts, cfg, log=print, client=None, dry_run=False):
    """`posts.json` → (themes, facts, meta).

    themes  themes.json 내용 — 게시물 id 가 소주제·단독·제외 중 어디로 갔는지
    facts   facts.json 내용 — `•`·`»` 줄. 줄마다 근거 id 가 딸린다
    meta    `verify` (verify.json 내용) · `usage` · `error` · `prompts`(드라이런)

    `dry_run` 은 API 를 부르지 않고 ① 프롬프트만 만든다(`writer/compose.run` 의
    관례). ②③④ 의 프롬프트는 ① 의 출력이 있어야 만들어지므로 여기서는 못 만든다 —
    없는 것을 만들어 보여 주지 않는다.

    ① 이 끝내 실패하면 themes·facts 에 `error` 를 적어 돌려준다. ②③④ 가 하나도
    성공하지 못한 경우도 같다 — 그러면 사실 줄이 0 이라 `error` 없이 돌려주면
    머리 2행이 'N건 분석' 을 주장하는 빈 다이제스트가 나간다. 예외로 멈추지
    않는 이유는 3-6 에 있다 — 그 경우 다이제스트는 머리 세 줄 + `분석 실패` 결손 +
    URL 목록으로 나가야 하고, 그 판단은 렌더·발송 단계가 한다.
    """
    asof = posts.get('asof')
    lim = limits(cfg)
    retries = int(cfg['writer'].get('retries') or 0)
    xcfg = cfg.get('xdigest') or {}
    # 기본 구획의 말. `scope` 가 없으면 머리 2행과 같은 `topics_label` 을 쓴다 —
    # 두 곳이 다른 말을 하면 읽는 사람과 모델이 다른 범위를 본다.
    scope = xcfg.get('scope') or xcfg.get('topics_label')
    secs, skipped = BS.sections(xcfg)
    for w in skipped:
        log(f'  설정 {w} — 그 구획은 뺐다')
    system = P.system_blocks(scope, secs)
    rows_all, gaps, cnt = _pool(posts)
    index = {p['id']: p for p in rows_all}
    rows_raw, parts = _by_topic(rows_all, secs)
    cnt['pool'] = len(rows_raw)
    accounts = {p.get('account') for p in (posts.get('posts') or []) if p.get('account')}
    alias = V.load_names()
    log(f'  분석 대상 {len(rows_all)}건 (본문 미수집 {cnt["no_text"]} · '
        f'창 밖 {cnt["out_window"]})'
        + (' — 기본 {} · {}'.format(len(rows_raw), ' · '.join(
            f'{sec["title"]} {len(parts[sec["key"]])}' for sec in secs)) if secs else ''))

    rows, fwd, _ = _keys(rows_raw)

    # 본문이 구획 줄(`[p002] @계정`)을 흉내 낸 건수. 프롬프트에서 무력화하고
    # 건수를 남긴다 — 조용히 고치면 누가 그것을 시도했는지 기록이 없다.
    # 별도 구획 게시물도 같은 `posts_block` 으로 들어가므로 함께 센다.
    n_forged = P.forged([(None, p.get('account'), p.get('text')) for p in rows_all])
    if n_forged:
        gaps.append(dict(scope='pool',
                         why=f'게시물 본문이 구획 줄을 흉내 낸 것 {n_forged}건 — '
                             f'프롬프트에서 줄머리를 들여써 무력화함'))
        log(f'  구획 줄 흉내 {n_forged}건 — 무력화함')

    if dry_run:
        prompt = P.ASSIGN.format(n=len(rows), posts=P.posts_block(rows))
        prompts = {'assign': prompt}
        # ⑥ 은 ① 출력과 무관하게 만들어진다 — 구획 게시물만으로 프롬프트가 선다.
        for sec in secs:
            pool, _f, _c = _section_pool(sec, parts[sec['key']])
            if pool:
                prow, _l = _rows_for([p['id'] for p in pool], index)
                prompts[f'section:{sec["key"]}'] = P.SECTION.format(
                    title=sec['title'], n=len(prow), posts=P.posts_block(prow),
                    cap=sec['max_items'])
        log(f'  드라이런 — ① 프롬프트 {len(prompt):,}자 생성 (API 미호출). '
            f'②③④ 는 ① 출력이 있어야 만들어진다'
            + (f' · ⑥ 프롬프트 {len(prompts) - 1}개' if secs else ''))
        return None, None, dict(dry_run=True, asof=asof, prompts=prompts,
                                system=system, gaps=gaps,
                                max_tokens=_assign_tokens(len(rows)))

    if not rows_all:
        why = '창 안에 본문 있는 게시물이 없다'
        log(f'  {why}')
        empty = _empty_sections(secs, parts)
        return (_themes_doc(posts, [], [], [], [], gaps, cnt, None, 0, why, empty),
                _facts_doc(posts, [], [], [], gaps, None, why, empty),
                dict(dry_run=False, verify=_verify_doc(asof, [], gaps), error=why))

    cl = client or C.client()
    usage = C.Usage()
    records = []

    if rows_raw:
        got, _viol, attempts, err = _assign(cl, cfg, system, rows, fwd, index, lim,
                                            log, usage, retries)
    else:
        # 기본 구획 게시물이 없고 별도 구획에만 있는 날. ① 을 빈 입력으로 부르지
        # 않는다 — 구획 ⑥ 은 그대로 돈다. 렌더는 기본 구획 셋에 빈 사유를 적는다.
        log('  기본 구획 게시물 0건 — ①~④ 를 부르지 않는다')
        got = dict(themes=[], standalone=[], excluded=[], unassigned=[],
                   gaps=[dict(scope='assign', why='기본 구획 게시물 0건')], model=None)
        attempts, err = 0, None
    if got is None:
        log(f'  분석 실패 — {err}')
        gaps = gaps + [dict(scope='assign', why=f'분석 실패 — {err}')]
        # ① 이 죽으면 렌더는 결손 경로(계정별 건수 + URL)로 간다 — 그 목록은 구획을
        # 가리지 않고 창 안 게시물 전부라 별도 구획 게시물도 거기 실린다. ⑥ 은
        # 부르지 않는다: 같은 키·같은 사유로 막혔을 가능성이 크고, 불러도 결손
        # 경로는 그 결과를 싣지 않는다.
        empty = _empty_sections(secs, parts)
        return (_themes_doc(posts, [], [], [], [], gaps, cnt, None, attempts, err,
                            empty),
                _facts_doc(posts, [], [], [], gaps, usage, err, empty),
                dict(dry_run=False, usage=usage.__dict__,
                     verify=_verify_doc(asof, records, gaps), error=err))

    gaps = gaps + got['gaps']
    log(f'  ① 소주제 {len(got["themes"])} · 단독 {len(got["standalone"])} · '
        f'제외 {len(got["excluded"])} · 미배정 {len(got["unassigned"])}')

    done, failed = _facts_rounds(cl, cfg, system, got['themes'], index, alias,
                                 accounts, lim, retries, log, usage, records)

    # 3-3 8번 — 통과한 줄로 다시 판정한다. 자격을 잃은 소주제는 단독으로 내린다.
    kept_themes, standalone_ids = [], list(got['standalone'])
    for t in got['themes']:
        d = done.get(t['key'])
        if not d:
            standalone_ids.extend(t['post_ids'])
            # 호출이 막힌 것과 검증에 걸린 것을 가려 적는다. 429·키 없음으로
            # 아무 검증도 못 한 소주제를 '검증 실패' 로 적으면 읽는 사람은
            # '모델이 지어내서 뺐다' 로 읽는다 (CLAUDE.md 2장 6번).
            why = failed.get(t['key'])
            gaps.append(dict(
                scope='facts',
                why=(f'② 호출 실패로 뺀 소주제 1건({t["title"]}) — {why}'
                     if why else f'검증 실패로 뺀 소주제 1건({t["title"]})')))
            continue
        # 상한을 먼저 적용한 뒤에 계정을 다시 세고 자격을 본다. 순서를 뒤집으면
        # 상한에 잘려 나간 줄이 유일하게 대던 계정이 함께 빠져도 소주제가 살아남아
        # `└` 줄에 계정 1개가 실린다 (계약 1장 `▸` · 3-3 8번).
        if len(d['facts']) > lim['facts_per_subtopic']:
            gaps.append(dict(
                scope='facts',
                why=f'소주제 "{d["title"]}" 사실 '
                    f'{len(d["facts"]) - lim["facts_per_subtopic"]}건 생략 — '
                    f'상한 {lim["facts_per_subtopic"]}'))
            d['facts'] = d['facts'][:lim['facts_per_subtopic']]
        # `└` 줄은 남은 줄의 게시물에서 다시 뽑는다. 빠진 줄의 계정이 남으면
        # 출처 줄이 거짓이 된다.
        d['accounts'] = _accounts(
            [i for f in d['facts'] for i in f['post_ids']], index)
        ok, why = V.unit_ok(d['facts'], index, min_accounts=lim['min_accounts'])
        if not ok:
            standalone_ids.extend(t['post_ids'])
            gaps.append(dict(scope='facts',
                             why=f'검증 실패로 뺀 소주제 1건({d["title"]}) — {why}'))
            continue
        if d['dropped']:
            gaps.append(dict(scope='facts',
                             why=f'소주제 "{d["title"]}" 사실 {d["dropped"]}건 '
                                 f'검증 실패로 뺌'))
        kept_themes.append(d)

    # 소주제 순서를 남은 줄로 다시 잡는다. 배정 시점 값으로 굳히면 계약 1장 표
    # (출처 계정 수 내림차순 → 게시물 수)와 어긋난다 — 검증에서 줄이 빠지며 계정
    # 수가 줄기 때문이다. 키(t1·t2…)는 `verify.json` 의 단위 이름과 짝이라 그대로
    # 두고 순서만 고친다.
    kept_themes.sort(key=lambda d: (
        -len(d['accounts']),
        -len({i for f in d['facts'] for i in f['post_ids']}),
        d['title']))

    items, sg = _standalone(cl, cfg, system, standalone_ids, index, alias,
                            accounts, lim, retries, log, usage, records)
    gaps += sg

    # 사실 줄 키를 코드가 붙인다. ④ 의 근거가 이 키다.
    n = 0
    flat = []
    for t in kept_themes:
        for f in t['facts']:
            n += 1
            f['id'] = FKEY.format(n)
            flat.append(f)
    for it in items:
        n += 1
        it['id'] = FKEY.format(n)
        flat.append(it)

    views, vg = _views(cl, cfg, system, flat, alias, accounts, lim, retries,
                       log, usage, records,
                       blocked=bool([r for r in records if r.get('error')]))
    gaps += vg

    # ②③④ 가 하나도 성공하지 못했으면 분석 실패다. 예전 판은 `error` 를 None 으로
    # 두어, 머리 2행이 'N건 분석' 을 주장하고 본문이 텅 빈 다이제스트가 나갔다 —
    # 3-6 이 요구한 `분석 실패 — <사유>` 결손 줄이 어디에도 붙지 않았다.
    # ⑥ 의 기록을 섞기 **전에** 판정한다. 섞으면 구획 하나가 성공한 날 ②③④ 가 다
    # 막혀도 실패가 아니게 되고, 기본 구획 셋이 '해당 없음' 으로 나간다 — 막힌 것을
    # 없는 것으로 적는 것이다(CLAUDE.md 2장 6번).
    blocked = [r for r in records if r.get('error')]
    error = None
    if blocked and not [r for r in records if not r.get('error')]:
        error = f'분석 실패 — {blocked[0]["error"]}'
        log(f'  {error}')
        gaps.append(dict(scope='facts', why=error))

    sections = []
    if secs:
        sections, sg = _sections_run(cl, cfg, system, secs, parts, index, alias,
                                     accounts, retries, log, usage, records)
        gaps += sg
        for d in sections:
            for it in d['items']:
                n += 1
                it['id'] = FKEY.format(n)

    log(f'  ② 소주제 {len(kept_themes)} · 사실 {sum(len(t["facts"]) for t in kept_themes)}건 '
        f'· ③ 단독 {len(items)}건 · ④ 해석 {len(views)}건'
        + ''.join(f' · ⑥ {d["title"]} {len(d["items"])}건' for d in sections))
    log('  ' + usage.line())

    themes = _themes_doc(posts, kept_themes, standalone_ids, got['excluded'],
                         got['unassigned'], gaps, cnt, got['model'], attempts,
                         error, sections)
    facts = _facts_doc(posts, kept_themes, items, views, gaps, usage, error, sections)
    return themes, facts, dict(dry_run=False, usage=usage.__dict__,
                               verify=_verify_doc(asof, records, gaps),
                               error=error)


def _themes_doc(posts, themes, standalone, excluded, unassigned, gaps, cnt,
                model, attempts, error, sections=()):
    """themes.json — 게시물 id 가 어디로 갔는지. 머리 2행의 `분석 M` 이 여기서 나온다.

    `counts` 는 기본 구획 몫이다. 별도 구획은 `sections` 에 구획마다 게시물 수 ·
    키워드로 거른 수 · 상한으로 뺀 수 · 관련 게시물 id 를 남긴다(D-NEXT-Q) —
    머리 2행의 구획 건수가 어디서 왔는지 이 파일로 되짚는다.
    """
    in_themes = sum(len(t['post_ids']) for t in themes)
    return dict(
        asof=posts.get('asof'), window=posts.get('window'),
        analyzed_at=now_kst(), source='claude', model=model, attempts=attempts,
        themes=[dict(key=t['key'], title=t['title'], post_ids=t['post_ids'],
                     accounts=t['accounts'], n_posts=len(t['post_ids']))
                for t in themes],
        standalone=list(standalone), excluded=excluded,
        unassigned=list(unassigned),
        counts=dict(pool=cnt['pool'],
                    subtopics=len(themes), in_themes=in_themes,
                    standalone=len(standalone), excluded=len(excluded),
                    unassigned=len(unassigned),
                    analyzed=in_themes + len(standalone),
                    no_text=cnt['no_text'], out_window=cnt['out_window']),
        sections=[dict(key=d['key'], title=d['title'], pool=d['pool'],
                       filtered=d['filtered'], cut=d['cut'],
                       relevant=list(d['relevant']), analyzed=d['analyzed'],
                       error=d['error']) for d in sections],
        gaps=gaps, error=error)


def _facts_doc(posts, themes, items, views, gaps, usage, error, sections=()):
    """facts.json — `•`·`»` 줄. 줄마다 근거 id 가 딸린다.

    `sections` 는 별도 구획(D-NEXT-Q)이다. 설정에 있는 구획은 **게시물이 0건이어도**
    항목이 있다 — 렌더가 그 구획 제목 아래 '0건' 을 적어야 하고, 항목이 없으면
    구획이 조용히 사라진다(CLAUDE.md 2장 6번). 구획이 생기기 전의 facts.json 에는
    이 키가 없고, 렌더는 그때의 세 구획만 그린다.
    """
    return dict(
        asof=posts.get('asof'), generated_at=now_kst(), source='claude',
        themes=[dict(key=t['key'], title=t['title'], accounts=t['accounts'],
                     facts=[dict(id=f['id'], text=f['text'],
                                 post_ids=f['post_ids']) for f in t['facts']])
                for t in themes],
        standalone=[dict(id=i['id'], text=i['text'], post_ids=i['post_ids'])
                    for i in items],
        views=[dict(id=v['id'], text=v['text'], fact_ids=v['fact_ids'])
               for v in views],
        sections=[dict(key=d['key'], title=d['title'], label=d['label'],
                       pool=d['pool'], analyzed=d['analyzed'],
                       items=[dict(id=i.get('id'), text=i['text'],
                                   post_ids=i['post_ids']) for i in d['items']],
                       error=d['error']) for d in sections],
        counts=dict(subtopics=len(themes),
                    facts=sum(len(t['facts']) for t in themes),
                    standalone=len(items), views=len(views)),
        usage=(usage.__dict__ if usage else None), gaps=gaps, error=error)


def _verify_doc(asof, records, gaps):
    """verify.json — 단위마다 시도 수·남긴 줄·뺀 줄·위반 내역. 사유를 요약하지 않는다."""
    return dict(asof=asof, checked_at=now_kst(), units=records,
                dropped=sum(r.get('dropped') or 0 for r in records),
                gaps=gaps)

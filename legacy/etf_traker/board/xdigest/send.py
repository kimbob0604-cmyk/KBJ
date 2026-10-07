#!/usr/bin/env python3
"""
X 다이제스트 발송 — 통마다 한 번씩, **평문**으로. 계약은 `docs/XDIGEST.md` 3-5·3-6·4장.

## 왜 통마다 직접 보내나

`report/telegram.py` 의 `send()` 는 4,096자를 넘는 본문을 `_split` 으로 한 번 더
나눈다. 본문을 통째로 넘기면 우리가 `■` 단위로 나눈 경계와 그쪽이 줄 단위로
나눈 경계가 엇갈린다 — `▸` 제목과 첫 `•` 사이, `•` 와 `└` 사이가 갈릴 수 있다.
그래서 렌더가 나눈 통을 **하나씩** 넘긴다. 통마다 4,096자 안이므로 `_split` 은
그대로 한 조각을 돌려준다(1장 '4,096자 분할' 4번 — 그쪽은 마지막 안전망이다).

## 평문이다

`parse_mode=None` 으로 보낸다. `send()` 는 None 이면 요청에서 그 키를 뺀다.
계정명에 `_`·`-`·`.` 이 들어가고 게시물에서 `$MU`·`[`·`*` 가 그대로 오는데,
레거시 Markdown 은 그것을 서식으로 집어 400 을 내거나 계정명을 바꿔 버린다
(1장 '파스 모드'). `telegram.py` 는 **고치지 않는다** — 다른 경로가 쓰는 파일이다.

## 받는 방

`XDIGEST_CHAT_ID` 가 있으면 그것, 없으면 `TELEGRAM_CHAT_ID`(보드 방). 어느
것을 썼는지 `sent.json` 에 남긴다 — 2통이 어디로 갔는지 나중에 물을 일이 있다.

## 중간에 실패하면

k번째 통에서 멈추고 사유를 `sent.json` 에 적고 **실패로 끝난다**(잡은 빨강).
그래도 `sent.json`·`digest.txt` 는 커밋된다 — 워크플로의 state 커밋 단계가
`if: always()` 인 이유다(4장). 다음 `send`·`daily` 는 `sent.json` 을 보고 **못
보낸 통부터** 보낸다. 다시 렌더하지 않는다: 같은 `digest.txt` 를 같은 규칙으로
나누면 같은 통 목록이라, 1통을 두 번 보내지 않으려면 그 목록이 바뀌면 안 된다.

재시도가 믿는 것은 통 번호가 아니라 **통 목록의 지문**이다. 번호만 믿으면 그날
`digest.txt` 가 다시 렌더돼 통 수가 줄었을 때(게시물이 줄거나 검증 탈락이 늘면
흔하다) 아직 안 보낸 1통을 '앞 실행에서 보냈다' 로 건너뛰고, 보낸 통이 0인데
`ok` 가 참이 되어 표식까지 찍힌다 — 그날 다이제스트가 조용히 아무것도 안 나가는
경로다. 그래서 `sent.json` 에 지문을 함께 적고, 지문이 다르면 재시도가 아니라 새
발송으로 보아 1통부터 보낸다. 1통을 두 번 받는 편이 하나도 못 받는 것보다 낫다
(3-5). 지문이 없는 낡은 `sent.json` 도 같게 본다.

`message_id` 는 적지 않는다. `telegram.send()` 가 `(ok, 사유)` 만 돌려주고
우리는 그 파일을 고치지 않으므로 우리가 아는 값이 아니다 — 없는 값을 지어내지
않는다(CLAUDE.md 2장 1번).

## 표식

마지막 통까지 성공한 뒤에만 `docs/api/xdigest-sent.json` 에 기준일을 쓴다
(`run.py` `_sent_mark` 와 같은 모양·같은 이유 — 러너 캐시에 두면 캐시가 비워진
날 또 보낸다). 자동 경로는 이 표식으로 중복을 막고, 손으로 돌릴 때는 막지 않는다.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from ..engine.config import ROOT
from ..ingest import creds
from ..report import telegram as TG
from . import render as R

KST = timezone(timedelta(hours=9))
# 사이트 루트. run.py 와 같은 규칙으로 찾는다 — 표식이 커밋으로 남아야 해서
# 러너 캐시가 아니라 docs/ 안이다.
SITE = os.environ.get('BOARD_SITE', os.path.join(os.path.dirname(ROOT), 'docs'))
MARK = ('api', 'xdigest-sent.json')


def chat():
    """(대화방 id, 어느 이름에서 왔나). 둘 다 없으면 ('', '')."""
    v = creds.get('XDIGEST_CHAT_ID')
    if v:
        return v, 'XDIGEST_CHAT_ID'
    v = creds.get('TELEGRAM_CHAT_ID')
    return (v, 'TELEGRAM_CHAT_ID') if v else ('', '')


def mark_path():
    return os.path.join(SITE, *MARK)


def mark(asof=None, parts=None):
    """표식을 읽거나(asof=None) 쓴다. 반환: 이미 보낸 기준일 또는 None."""
    p = mark_path()
    if asof is None:
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding='utf-8') as f:
                return (json.load(f) or {}).get('as_of')
        except ValueError:
            # 깨진 표식을 '안 보냈다' 로 읽으면 그날 또 보낸다. 사유를 올린다.
            raise RuntimeError(f'표식이 깨졌다: {p}. 손으로 지우거나 고쳐라')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({'as_of': asof, 'sent_at': datetime.now(KST).isoformat(timespec='seconds'),
                   'parts': parts}, f, ensure_ascii=False)
        f.write('\n')
    return asof


def _late(asof, cfg, now):
    """예정 시각을 넘겼으면 `(실제 발송 14:02)`. 임계는 설정 `send_at`(08:00) 하나다.

    **날이 바뀌었으면 날짜까지 적는다** — 지난 기준일을 손으로 다시 보내는
    경우(`--asof 2026-09-20` 을 9/22 에 돌리는 것)에 시각만 적으면 '그날 14:02 에
    늦게 보낸 것' 으로 읽힌다. 1장은 이 부기를 '발송이 send_at 을 넘겼을 때' 로만
    정했고 이틀 밀린 표기는 없었다. 형식은 `render._stamp` 가 이미 만든다.
    """
    hhmm = str(R.opt(cfg, 'send_at', R.SEND_AT))
    try:
        due = datetime.fromisoformat(f'{asof}T{hhmm}:00').replace(tzinfo=KST)
    except ValueError:
        return ''
    if now <= due:
        return ''
    when = f'{now:%H:%M}' if now.date() == due.date() else R._stamp(now)
    return f'(실제 발송 {when})'


def _stamp_first(part, note, cfg):
    """1통 **1행**(제목 줄) 끝에 부기를 붙인다. 결손 줄이 위에 있으면 그 아래다."""
    if not note:
        return part
    lines = part.split('\n')
    title = str(R.opt(cfg, 'title', R.TITLE))
    for i, ln in enumerate(lines):
        if ln.startswith(title):
            lines[i] = f'{ln} {note}'
            return '\n'.join(lines)
    lines[0] = f'{lines[0]} {note}'
    return '\n'.join(lines)


def fingerprint(parts):
    """통 목록의 지문. 같은 `digest.txt` 를 같은 규칙으로 나눈 목록인지 본다.

    통 번호는 그때의 통 목록 안에서만 뜻이 있다. 통마다 길이와 본문을 함께
    넣어 잰다 — 순서가 바뀐 것도 다른 지문이어야 한다.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(f'{len(p)}\n{p}\n\0'.encode('utf-8'))
    return h.hexdigest()[:16]


def done(sent, fp=None):
    """이미 성공한 통 번호. **지문이 다르면 아무것도 건너뛰지 않는다.**

    `fp` 를 주면 `sent.json` 의 지문과 같을 때만 그 기록을 믿는다. 지문이 없는
    낡은 기록도 '다르다' 로 본다 — 그 기록이 지금 통 목록의 것이라는 근거가
    없다(2장 1번).
    """
    rec = sent or {}
    if fp is not None and str(rec.get('fingerprint') or '') != fp:
        return set()
    return {r.get('n') for r in (rec.get('results') or []) if r.get('ok')}


def send_parts(parts, asof, cfg=None, now=None, sent=None, sender=None, chat_id=None,
               limit=TG.TG_LIMIT):
    """통 목록을 보낸다. 반환 (전부 됐나, sent.json 에 쓸 기록).

    `sent` 를 주면 **지문이 같을 때만** 그 기록에서 성공한 통을 건너뛴다(재시도).
    `sender` 는 시험용 주입 자리다 — 이 환경은 외부 접속이 막혀 있어 실제 발송은
    러너에서만 돈다.
    """
    now = now or datetime.now(KST)
    sender = sender or TG.send
    cid, where = (chat_id, '인자') if chat_id else chat()
    note = _late(asof, cfg, now)
    fp = fingerprint(parts)
    already = done(sent, fp)
    # 앞 기록은 있는데 지문이 어긋났다 — 그날 digest.txt 가 다시 렌더된 것이다.
    # 재시도가 아니라 새 발송이라 1통부터 보낸다(위 docstring '재시도가 믿는 것').
    stale = bool((sent or {}).get('results')) and not already
    ok_all, results, over = True, [], []
    for n, part in enumerate(parts, 1):
        if n in already:
            results.append({'n': n, 'ok': True, 'why': '앞 실행에서 보냈다', 'at': None})
            continue
        text = _stamp_first(part, note, cfg) if n == 1 else part
        if len(text) > limit:
            # 부기를 붙인 **뒤의** 길이다. `R.over` 는 붙이기 전을 봤으므로 한도에
            # 딱 붙은 1통이 여기서 넘어간다 — 그러면 `telegram.send` 의 `_split`
            # 이 줄 경계에서 다시 나눠 `•` 와 `└` 가 갈릴 수 있다(1장 3번).
            over.append(n)
        if not cid:
            ok, why = False, '보낼 대화방이 없다 — XDIGEST_CHAT_ID 또는 TELEGRAM_CHAT_ID'
        else:
            ok, why = sender(text, chat_id=cid, parse_mode=None)
        results.append({'n': n, 'ok': bool(ok), 'why': str(why),
                        'at': datetime.now(KST).isoformat(timespec='seconds')})
        if not ok:
            ok_all = False
            break                      # k번째가 실패하면 거기서 멈춘다 (3-5)
    # 보낼 통이 0개인 것을 '전부 보냈다' 로 적으면 표식이 남아 그날 다이제스트가
    # 영영 안 온다. 빈 목록은 성공이 아니다.
    rec = {'asof': asof, 'at': now.isoformat(timespec='seconds'),
           'parts': len(parts), 'sent': sum(1 for r in results if r['ok']),
           'sent_now': sum(1 for r in results if r['ok'] and r['at']),
           'ok': bool(parts) and ok_all and len(results) == len(parts),
           'chat': where or None, 'note': note or None, 'parse_mode': None,
           'fingerprint': fp, 'stale': stale or None, 'over': over or None,
           'results': results}
    return rec['ok'], rec


def run(asof, once=False, log=print, now=None, sender=None):
    """`digest.txt` → 발송 → `sent.json`. 반환은 종료 코드다."""
    text = R.read(asof, 'digest.txt')
    if not (text or '').strip():
        # `daily` 가 큐에 밀려 아직 안 돈 것이다. 렌더하지 않는다 — 그것이 돌면
        # 보낸다. 잡은 초록이고 요약에 한 줄만 남는다 (3-6).
        log(f'  오늘 digest 없음 ({asof}) — 보내지 않는다')
        return 0
    if once and mark() == asof:
        log(f'  {asof} 다이제스트는 이미 보냈다 — 다시 보내지 않는다')
        return 0
    cfg = R.cfg_load()
    parts = R.split(text)
    over = R.over(parts)
    if over:
        # 나눈 뒤에도 넘는 통이 있으면 `send()` 의 `_split` 이 줄 경계에서 받는다.
        # 조용히 넘기지 않고 어느 통인지 적는다.
        log(f'  4,096자를 넘는 통 {over} — telegram.send 의 _split 이 한 번 더 나눈다')
    for ln in R.stray(text):
        # 기호로 시작하지 않는 본문 줄 = 모델 문자열의 줄바꿈이 샌 것이다.
        # 가짜 구획은 통 순서까지 바꾸므로 조용히 보내지 않고 적는다(2장 6번).
        log(f'  기호 없는 본문 줄: {ln[:80]}')
    ok, rec = send_parts(parts, asof, cfg=cfg, now=now, sent=R.read(asof, 'sent.json'),
                         sender=sender)
    p = os.path.join(R.day_dir(asof, make=True), 'sent.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.write('\n')
    if rec['stale']:
        log('  앞 발송 기록의 지문이 지금 통 목록과 달라 1통부터 다시 보냈다 '
            '— digest.txt 가 다시 렌더된 날이다')
    if rec['over']:
        log(f"  부기를 붙인 뒤 4,096자를 넘는 통 {rec['over']} "
            '— telegram.send 의 _split 이 줄 경계에서 한 번 더 나눈다')
    for r in rec['results']:
        log(f"  {r['n']}/{rec['parts']}통 {'발송' if r['ok'] else '실패'} — {r['why']}")
    if rec['parts'] and not rec['sent_now']:
        # 이번 실행이 보낸 통이 0이다. 지문이 같은 앞 기록으로 전부 보낸 것으로
        # 보는 경로라 정상이지만, 표식이 찍히는 자리이므로 사유를 남긴다.
        log('  이번 실행이 보낸 통 0 — 지문이 같은 앞 실행 기록으로 전부 보낸 것으로 본다')
    if ok:
        mark(asof, rec['parts'])
    else:
        log('  실패한 통부터 다음 send 가 이어 보낸다 — sent.json 에 남겼다')
    return 0 if ok else 1

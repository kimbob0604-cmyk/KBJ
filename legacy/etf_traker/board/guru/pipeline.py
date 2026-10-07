#!/usr/bin/env python3
"""
구루 브리핑 실행 — 조사 → 종합 → 렌더, 그리고 발송. 단계마다 파일을 남긴다.

    board/state/guru/YYYYMMDD/research.json   인물별 확정값 + 결손 + 추정 비용
    board/state/guru/YYYYMMDD/synth.json      종합 구획
    board/state/guru/YYYYMMDD/brief.txt       발송 본문 전체
    board/state/guru/YYYYMMDD/sent.json       통마다 결과 · 지문
    board/state/guru/cost.json                월별 추정 비용 누계 (예산 판정)
    docs/api/guru-sent.json                   --once 표식 (기준일 하나)

발송은 X 다이제스트의 통 단위 재시도 규약을 그대로 쓴다(xdigest/send.py 의 지문·done).
받는 방도 같다 — XDIGEST_CHAT_ID, 없으면 TELEGRAM_CHAT_ID.
"""
import json
import os
from datetime import datetime

import yaml

from ..engine.config import ROOT
from ..report import telegram as TG
from ..xdigest import send as XS
from . import render as RD
from . import research as RS
from . import synth as SY

CFG = os.path.join(ROOT, 'config', 'guru.yaml')
COST = os.path.join(RD.STATE, 'cost.json')
MARK = os.path.join(XS.SITE, 'api', 'guru-sent.json')


def cfg_load(path=CFG):
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


# ─────────────────────────── 예산 ───────────────────────────
def cost_read():
    if not os.path.exists(COST):
        return {}
    with open(COST, encoding='utf-8') as f:
        return json.load(f)


def cost_add(asof, usd):
    c = cost_read()
    m = c.setdefault(asof[:7], {'usd': 0.0, 'runs': 0, 'is_estimate': True})
    m['usd'] = round(m['usd'] + usd, 4)
    m['runs'] += 1
    os.makedirs(os.path.dirname(COST), exist_ok=True)
    with open(COST, 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
        f.write('\n')
    return m['usd']


def over_budget(asof, cfg):
    spent = (cost_read().get(asof[:7]) or {}).get('usd', 0.0)
    return spent >= float(cfg.get('budget_usd_month', 30)), spent


# ─────────────────────────── 만들기 ───────────────────────────
def build(cl=None, cfg=None, now=None, log=print):
    """조사·종합·렌더. 반환 종료 코드. 도는 요일이 아니면 아무것도 하지 않는다."""
    cfg = cfg or cfg_load()
    now = now or datetime.now(RS.KST)
    win = RS.window(now, cfg)
    asof = win['asof']
    if not RS.runs_on(now.date(), cfg):
        log(f'  {asof} — 도는 요일이 아니다(설정 run_weekdays). 새 미국장이 없다')
        return 0
    over, spent = over_budget(asof, cfg)
    if over:
        msg = (f'{cfg.get("title")}: 이번 달 추정 비용 ${spent:.2f} 가 예산 '
               f'${cfg.get("budget_usd_month")} 에 닿아 오늘은 조사하지 않았습니다. '
               'board/config/guru.yaml budget_usd_month 를 올리면 다시 돕니다.')
        RD.write(asof, 'brief.txt', msg + '\n')
        log('  ' + msg)
        return 0
    if cl is None:
        from ..writer import claude as C
        cl = C.client()
    log(f'  구루 브리핑 — 조사 창 {win["start"]} ~ {win["end"]} · 미국장 {win["session"]}')
    res = RS.run(cl, cfg, win, log=log)
    RD.write(asof, 'research.json', res)
    syn, sdrops, sspend = SY.run(cl, cfg, res, log=log)
    RD.write(asof, 'synth.json', {'synth': syn, 'drops': sdrops, 'spend': sspend})
    usd = res['spend']['usd'] + sspend['usd']
    total = cost_add(asof, usd)
    log(f'  추정 비용 오늘 ${usd:.2f} · 이번 달 ${total:.2f} / ${cfg.get("budget_usd_month")}')
    missing = summarize_drops(res['drops'] + sdrops)
    people = [p for g in res['groups'] for p in g['people']]
    if not res.get('market') and all(p['status'] == 'unknown' for p in people):
        # 조사가 하나도 안 됐다(2026-10-02: 크레딧 부족으로 6개 호출 전부 400). 36명이 전부
        # '확인 못 함' 인 빈 양식은 정보가 없다 — 사유 한 줄만 보낸다(CLAUDE.md 2장 6번).
        text = (f'{cfg.get("title")} — {win["asof"]} 조사 실패, 오늘 브리핑 없음\n'
                + '\n'.join(f'  • {m}' for m in missing))
    else:
        text = RD.text(RD.blocks(res, syn, cfg, missing))
    RD.write(asof, 'brief.txt', text + '\n')
    passed = sum(1 for g in res['groups'] for p in g['people'] if p['status'] == 'pass')
    log(f'  brief.txt {len(text):,}자 · PASS {passed}명 · 결손 {len(res["drops"]) + len(sdrops)}건')
    for d in res['drops'] + sdrops:
        log(f'    - {d}')
    return 0


def summarize_drops(drops):
    """상단 한 줄용 요약. 전문은 research.json·로그에 있다."""
    if not drops:
        return []
    out = []
    if any('credit balance' in d for d in drops):
        # 계정 크레딧이 바닥나면 이후 호출이 전부 400 이다. 사람이 충전해야 풀린다.
        out.append('Anthropic API 크레딧 부족으로 일부 조사 실패 — console.anthropic.com 에서 충전 필요')
    fail = [d for d in drops if '조사 실패' in d or '종합 실패' in d or '시장 배경' in d]
    src = [d for d in drops if '출처' in d and d not in fail]
    other = [d for d in drops if d not in fail and d not in src]
    out += [d.split(' — ')[0] for d in fail]
    if src:
        out.append(f'출처 대조에서 뺀 항목 {len(src)}건')
    if other:
        out.append(f'기타 {len(other)}건')
    return out


# ─────────────────────────── 보내기 ───────────────────────────
def mark(asof=None, parts=None):
    if asof is None:
        if not os.path.exists(MARK):
            return None
        with open(MARK, encoding='utf-8') as f:
            return (json.load(f) or {}).get('as_of')
    os.makedirs(os.path.dirname(MARK), exist_ok=True)
    with open(MARK, 'w', encoding='utf-8') as f:
        json.dump({'as_of': asof, 'sent_at': datetime.now(RS.KST).isoformat(timespec='seconds'),
                   'parts': parts}, f, ensure_ascii=False)
        f.write('\n')
    return asof


def send(asof=None, once=False, sender=None, log=print):
    asof = asof or datetime.now(RS.KST).date().isoformat()
    text = RD.read(asof, 'brief.txt')
    if not (text or '').strip():
        log(f'  {asof} 브리핑 없음 — 보내지 않는다')
        return 0
    if once and mark() == asof:
        log(f'  {asof} 브리핑은 이미 보냈다')
        return 0
    parts = RD.split([text.rstrip('\n')])
    sender = sender or TG.send
    cid, where = XS.chat()
    fp = XS.fingerprint(parts)
    already = XS.done(RD.read(asof, 'sent.json'), fp)
    results, ok_all = [], True
    for n, part in enumerate(parts, 1):
        if n in already:
            results.append({'n': n, 'ok': True, 'why': '앞 실행에서 보냈다', 'at': None})
            continue
        if not cid:
            ok, why = False, '보낼 대화방이 없다 — XDIGEST_CHAT_ID 또는 TELEGRAM_CHAT_ID'
        else:
            ok, why = sender(part, chat_id=cid, parse_mode=None)
        results.append({'n': n, 'ok': bool(ok), 'why': str(why),
                        'at': datetime.now(RS.KST).isoformat(timespec='seconds')})
        log(f'  {n}/{len(parts)}통 {"발송" if ok else "실패"} — {why}')
        if not ok:
            ok_all = False
            break
    ok = bool(parts) and ok_all and len(results) == len(parts)
    RD.write(asof, 'sent.json', {'asof': asof, 'parts': len(parts), 'ok': ok, 'chat': where or None,
                                 'fingerprint': fp, 'results': results})
    if ok:
        mark(asof, len(parts))
    return 0 if ok else 1

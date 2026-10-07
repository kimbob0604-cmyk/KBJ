#!/usr/bin/env python3
"""
페이퍼 트레이딩 장부 — 매일 낸 시그널을 쌓고, 그 뒤 실제 가격으로 채점한다.

원문 CLAUDE.md '개발 전 확인' 의 순서(백테스트 → 워크포워드 → **페이퍼** → 수동
승인 주문)의 세 번째 칸이다. 백테스트는 과거에 규칙을 대 본 것이고, 이 장부는
**시그널을 낸 뒤에야 알게 된 가격**으로만 채점하므로 표본 밖(out-of-sample)이다.

  - 파일은 state 루트 `signal_ledger.json`. 날짜와 무관하게 쌓인다(Actions 캐시로 나른다).
  - 한 줄 = (신호일, 종목, 구획). 같은 날 다시 돌려도 중복되지 않는다.
  - 채점은 engine/backtest.simulate 와 **같은 규칙**이다(다음 날 체결 · #42 청산 ·
    비용). 청산이 끝나지 않은 줄은 open 으로 두고 매일 다시 채점한다.
  - 과거 줄을 고치지 않는다. 규칙(signals.yaml)을 바꾸면 바뀐 뒤의 줄만 새 규칙이다 —
    그래서 줄마다 그날의 규칙 버전(`rules`)을 적는다.
"""
import hashlib
import json
import os

from . import backtest as BT
from . import db as DB
from . import build as B

NAME = 'signal_ledger.json'


def path():
    return os.path.join(B.STATE, NAME)


def load():
    p = path()
    if not os.path.exists(p):
        return dict(source='board-paper', entries=[])
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save(led):
    os.makedirs(B.STATE, exist_ok=True)
    led['updated_at'] = B.now_kst()
    with open(path(), 'w', encoding='utf-8') as f:
        json.dump(led, f, ensure_ascii=False, indent=1)


def rules_version(sc):
    """판정·청산에 쓰는 값의 지문. 값이 바뀌면 이 문자열이 바뀐다."""
    keys = ('universe', 'rs', 'trend', 'contraction', 'setups', 'risk', 'backtest')
    blob = json.dumps({k: sc.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


def append(led, sig, sc):
    """signals.json 의 돌파·감시를 장부에 더한다. 반환 새로 더한 수."""
    have = {(e['signal_date'], e['code'], e['setup']) for e in led['entries']}
    ver, n = rules_version(sc), 0
    for x in (sig.get('breakout') or []) + (sig.get('watch') or []):
        key = (sig['as_of'], x['code'], x['setup'])
        if key in have:
            continue
        ref = x.get('ref') if x['setup'] == 'breakout' else x.get('trigger')
        if ref is None:
            continue
        led['entries'].append(dict(
            signal_date=sig['as_of'], code=x['code'], name=x.get('name'), setup=x['setup'],
            ref=ref, close=x.get('close'), rs_pct=x.get('rs_pct'),
            gate_open=x.get('gate_open'), flows_pass=x.get('flows_pass'),
            rules=ver, status='open'))
        n += 1
    return n


def score(led, conn, sc, upto):
    """열린 줄을 DB 일봉으로 다시 채점한다. 반환 (채점한 수, 닫힌 수)."""
    open_ = [e for e in led['entries'] if e['status'] in ('open', 'pending')]
    codes = sorted({e['code'] for e in open_})
    ser = {}
    for code, rows in DB.series_for(conn, codes, upto).items():
        s = BT.Series()
        for r in rows:
            s.add(r)
        ser[code] = s
    n_closed = 0
    for e in open_:
        s = ser.get(e['code'])
        i = s.at.get(e['signal_date']) if s else None
        if i is None:
            e['status'], e['note'] = 'pending', '신호일 봉이 DB 에 없다'
            continue
        t, why = BT.simulate(s, i, e['setup'], e['ref'], sc)
        if t is None:
            if why == 'no_next_bar':
                e['status'] = 'pending'          # 다음 거래일이 아직 안 왔다
                continue
            e['status'], e['result'] = 'void', why   # 상한가 갭 · 미체결 — 거래가 아니다
            n_closed += 1
            continue
        e['result'] = t
        if t['reason'] in ('open_end',):
            e['status'] = 'open'
        else:
            e['status'] = 'closed'
            n_closed += 1
    return len(open_), n_closed


def summary(led):
    """구획별 누적 — 텔레그램 한 줄과 signals.json `paper` 에 싣는다."""
    out = {}
    for setup in ('breakout', 'watch'):
        es = [e for e in led['entries'] if e['setup'] == setup]
        closed = [e['result'] for e in es if e['status'] == 'closed']
        opened = [e['result'] for e in es if e['status'] == 'open' and e.get('result')]
        st = BT.stats(closed)
        out[setup] = dict(
            signals=len(es), closed=len(closed), open=len(opened),
            void=sum(1 for e in es if e['status'] == 'void'),
            pending=sum(1 for e in es if e['status'] == 'pending'),
            win_rate=st.get('win_rate'), mean=st.get('mean'),
            profit_factor=st.get('profit_factor'),
            open_mean=round(sum(r['ret_pct'] for r in opened) / len(opened), 2)
            if opened else None,
            since=min((e['signal_date'] for e in es), default=None))
    return out

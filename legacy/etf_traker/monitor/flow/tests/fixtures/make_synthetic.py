#!/usr/bin/env python3
"""
monitor/flow 합성 골든 생성기 (KBJ P1, U3).

원본 골든은 KRX 정보데이터시스템 워크북 실데이터(실제 종목 한 개, 2026.08.18~09.14
20거래일)였다. 로그인 등급 실데이터라 공개 레포에 넣지 않고, 같은 형태의 합성 종목·
합성 수급으로 바꾼다.

  입력(합성, 시드 고정): code·name·baseDate·dates·closes·amt(13구분 일별 금액, 원)·
                        qty(13구분 일별 수량, 주)·totals(구분별 기간합계 6칸)
  기대값(독립 계산):     expect.main·expect.inst·expect.narrative + 문장 숫자(expect.burst 등)

기대값은 원본 골든이 그랬듯 **워크북 정의를 그대로 따른 별도 계산**으로 만든다.
monitor.flow.analyze·narrative 를 import 하지 않는다 — 시험 대상의 출력을 베끼면
시험이 아무것도 못 잡는다. 정의(워크북 기준):

  month    = 조회 20거래일 순매수 금액 합 ÷ 1억
  recent   = 마지막 5거래일 합 ÷ 1억
  qty      = 20거래일 순매수 수량 합
  days     = 순매수 금액 > 0 인 날 수(보합 0 은 세지 않음)
  share    = 기관 구분 month ÷ 기관합계 month
  집중구간 = 기관합계 누적(기준일 0 출발)에서 4거래일 증가폭이 가장 큰 구간
  구간가격 = 구간 첫날 '직전' 거래일 종가 → 구간 끝날 종가
  이후     = 구간 끝날 종가 → 마지막 거래일 종가

합성 수급은 원본 워크북과 같은 '이야기 구조'를 갖도록 짠다 — 9/1~9/4 기관 집중 매수와
주가 동행, 이후 조정, 최근 5일 기관 매도·외국인 매수 교차, 기관 순매수의 상위 둘은
투신·사모, 최근 기관 매도의 주동자는 사모. 시험의 정성 판정(날짜·이름)은 이 구조에
기대고, 숫자는 위 독립 계산값(expect)과 대조한다. 생성 뒤 그 구조가 실제로 성립하는지
이 스크립트가 확인하고 아니면 멈춘다.

실행 (legacy/etf_traker 에서):

    python monitor/flow/tests/fixtures/make_synthetic.py
"""
from __future__ import annotations

import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'golden.json')

SEED = 20261007
CODE, NAME = '999990', '가상전자'
BASE = '20260814'
DATES = ['20260818', '20260819', '20260820', '20260821', '20260824', '20260825',
         '20260826', '20260827', '20260828', '20260831', '20260901', '20260902',
         '20260903', '20260904', '20260907', '20260908', '20260909', '20260910',
         '20260911', '20260914']
BURST = ('20260901', '20260904')
RECENT = 5
EOK = 100_000_000

INST = ('금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금 등')
OTHERS = ('기타법인', '개인', '외국인', '기타외국인')
ORDER = INST[:4] + INST[4:] + ('기타법인', '개인', '외국인', '기타외국인', '전체', '기관합계')
MAIN_ROWS = ('개인', '외국인', '기관합계', '기타법인', '기타외국인')


def won(x):
    """원 단위 정수. 25원 단위로 떨어뜨려 실제 원자료처럼 보이게만 한다."""
    return int(round(x / 25.0)) * 25


def make(rnd):
    # ---- 종가: 완만 → 9/1~9/4 급등 → 조정
    closes, px = {BASE: 18000}, 18000.0
    for d in DATES:
        if BURST[0] <= d <= BURST[1]:
            px *= 1 + rnd.uniform(0.05, 0.09)
        elif d > BURST[1]:
            px *= 1 + rnd.uniform(-0.045, 0.01)
        else:
            px *= 1 + rnd.uniform(-0.03, 0.035)
        closes[d] = int(round(px / 50.0)) * 50

    amt, qty = {}, {}
    recent = DATES[-RECENT:]
    for d in DATES:
        a = {}
        burst = BURST[0] <= d <= BURST[1]
        late = d in recent
        a['금융투자'] = won(rnd.gauss(2e7, 4e7) + (2e8 if burst else 0))
        a['보험'] = won(rnd.choice((0, 0, rnd.gauss(0, 2e7))) + (3e7 if burst else 0))
        a['투신'] = won(rnd.gauss(3e7, 6e7) + (rnd.uniform(1.2e9, 2.2e9) if burst else 0)
                       + (-rnd.uniform(0, 4e7) if late else 0))
        a['사모'] = won(rnd.gauss(2e7, 6e7) + (rnd.uniform(1.2e9, 2.0e9) if burst else 0)
                       + (-rnd.uniform(3e8, 6e8) if late else 0))
        a['은행'] = 0
        a['기타금융'] = won(rnd.choice((0, 0, 0, -2.3e6)))
        a['연기금 등'] = won(rnd.choice((0, 0, rnd.gauss(0, 1e7))) + (2e8 if burst else 0))
        a['기관합계'] = sum(a[k] for k in INST)
        a['기타법인'] = won(rnd.gauss(2e7, 8e7))
        a['기타외국인'] = won(rnd.gauss(0, 6e6))
        a['외국인'] = won(rnd.gauss(-6e8, 5e8) - (2.5e9 if burst else 0)
                        + (rnd.uniform(9e8, 1.5e9) if late else 0))
        a['개인'] = -(a['기관합계'] + a['기타법인'] + a['기타외국인'] + a['외국인'])
        a['전체'] = 0
        q = {k: (int(round(a[k] / closes[d])) if k not in ('기관합계', '개인', '전체') else 0)
             for k in a}
        q['기관합계'] = sum(q[k] for k in INST)
        q['개인'] = -(q['기관합계'] + q['기타법인'] + q['기타외국인'] + q['외국인'])
        amt[d] = {k: a[k] for k in ORDER}
        qty[d] = {k: q[k] for k in ORDER}

    # ---- 기간합계: 순매수 = 일별 합(원·주 단위로 정확히), 매수/매도 총액은 합성
    totals = {}
    for k in INST + OTHERS:
        net_a = sum(amt[d][k] for d in DATES)
        net_q = sum(qty[d][k] for d in DATES)
        gross = {'개인': 1.1e11, '외국인': 6.0e10}.get(k, 1.5e9 if k in INST else 2.0e9)
        sell_a = won(abs(net_a) * rnd.uniform(0.2, 0.6) + gross * rnd.uniform(0.5, 1.0))
        if k == '은행':
            sell_a = 0
        sell_q = int(round(sell_a / closes[DATES[-1]]))
        totals[k] = dict(sell_qty=sell_q, buy_qty=sell_q + net_q, net_qty=net_q,
                         sell_amt=sell_a, buy_amt=sell_a + net_a, net_amt=net_a)
    agg = {f: sum(totals[k][f] for k in INST) for f in totals['투신']}
    totals['기관합계'] = agg
    tot_sell_a = sum(totals[k]['sell_amt'] for k in ('기관합계',) + OTHERS)
    tot_sell_q = sum(totals[k]['sell_qty'] for k in ('기관합계',) + OTHERS)
    totals['전체'] = dict(sell_qty=tot_sell_q, buy_qty=tot_sell_q, net_qty=0,
                          sell_amt=tot_sell_a, buy_amt=tot_sell_a, net_amt=0)
    order_t = INST[:4] + INST[4:] + ('기관합계', '기타법인', '개인', '외국인', '기타외국인', '전체')
    totals = {k: totals[k] for k in order_t}
    for k, t in totals.items():          # 매수·매도 총액이 음수면 원자료 모양이 아니다
        assert t['sell_amt'] >= 0 and t['buy_amt'] >= 0 and t['sell_qty'] >= 0 and t['buy_qty'] >= 0, (k, t)
    return closes, amt, qty, totals


# ------------------------------------------------------------ 독립 계산

def expect(closes, amt, qty):
    recent = DATES[-RECENT:]

    def s(who, ds, src=amt):
        return sum(src[d][who] for d in ds)

    main = {w: dict(month=s(w, DATES) / EOK, recent=s(w, recent) / EOK,
                    qty=s(w, DATES, qty), days=sum(1 for d in DATES if amt[d][w] > 0))
            for w in MAIN_ROWS}
    inst_tot = s('기관합계', DATES) / EOK
    inst = {w: dict(month=s(w, DATES) / EOK, recent=s(w, recent) / EOK,
                    share=(s(w, DATES) / EOK) / inst_tot)
            for w in INST}
    inst = dict(sorted(inst.items(), key=lambda kv: -kv[1]['month']))

    # 집중 구간: 기관합계 누적의 4거래일 증가폭 최대
    seq = [BASE] + DATES
    cum, run = {BASE: 0.0}, 0.0
    for d in DATES:
        run += amt[d]['기관합계'] / EOK
        cum[d] = run
    best = None
    for i in range(len(seq) - 4):
        delta = cum[seq[i + 4]] - cum[seq[i]]
        if best is None or delta > best[2]:
            best = (seq[i + 1], seq[i + 4], delta)
    b0, b1, bamt = best
    prev = seq[seq.index(b0) - 1]
    bprice = (closes[b1] / closes[prev] - 1) * 100.0
    after = (closes[DATES[-1]] / closes[b1] - 1) * 100.0

    shares = sorted(((w, v['share']) for w, v in inst.items()), key=lambda kv: -abs(kv[1]))
    top2 = shares[:2]
    top2_sum = sum(v for _, v in top2)
    rec = {w: v['recent'] for w, v in inst.items()}
    drv = max(rec, key=lambda k: abs(rec[k]))
    drv_share = rec[drv] / sum(rec.values())

    f = lambda v: f'{v:+,.1f}억원'                       # noqa: E731
    i_m, f_m = main['기관합계'], main['외국인']
    narrative = [
        f'{NAME} 최근 한 달 수급 분석',
        f'기관 매수는 {int(b0[4:6])}월 {int(b0[6:])}일~{int(b1[4:6])}월 {int(b1[6:])}일에 집중.',
        f'한 달: 기관 {f(i_m["month"])}, 외국인 {f(f_m["month"])}.',
        f'최근 {RECENT}일: 기관 {f(i_m["recent"])}, 외국인 {f(f_m["recent"])}.',
        f'집중 구간 기관 {f(bamt)} 매수와 주가 {bprice:+.1f}%가 동행. 이후 {after:+.1f}%.',
        f'기관 순매수 중 {top2[0][0]}과 {top2[1][0]}가 {top2_sum * 100:.1f}%.',
        f'최근 {RECENT}일 기관 움직임의 {abs(drv_share) * 100:.1f}%는 {drv}에서 발생.',
        '수급과 가격의 동행이며 인과는 식별되지 않음.',
    ]
    texts = [f(i_m['month']), f(f_m['month']), f(i_m['recent']), f(f_m['recent']),
             f(bamt), f'{bprice:+.1f}%', f'{after:+.1f}%',
             f'{top2_sum * 100:.1f}%', f'{abs(drv_share) * 100:.1f}%']
    out = dict(main=main, inst=inst, narrative=narrative,
               burst=dict(start=b0, end=b1, amt=bamt, prev=prev, price_pct=bprice,
                          after_pct=after),
               top2=dict(names=[w for w, _ in top2], share=top2_sum),
               recent_driver=dict(who=drv, share=drv_share),
               texts=texts)

    # 원본 워크북과 같은 이야기 구조가 성립하는지 — 시험의 정성 판정이 여기에 기댄다.
    assert (b0, b1) == BURST, (b0, b1)
    assert bamt >= 10.0, bamt
    assert i_m['recent'] < 0 < f_m['recent'], '최근 5일 기관 매도·외국인 매수 교차'
    assert f_m['month'] < 0, '외국인 한 달 순매도 → 최근 전환'
    assert out['top2']['names'] == ['투신', '사모'], out['top2']
    assert drv == '사모', drv
    assert inst_tot > 0
    return out


def main():
    rnd = random.Random(SEED)
    closes, amt, qty, totals = make(rnd)
    g = dict(code=CODE, name=NAME, baseDate=BASE, dates=DATES, closes=closes,
             amt=amt, qty=qty, totals=totals, expect=expect(closes, amt, qty),
             note=('합성 — monitor/flow/tests/fixtures/make_synthetic.py '
                   f'(seed={SEED}). 가상 종목. 기대값은 워크북 정의의 독립 계산.'))
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
        f.write('\n')
    e = g['expect']
    print(f'golden → {OUT}\n  집중 {e["burst"]}\n  상위둘 {e["top2"]} · 최근주동 {e["recent_driver"]}')


if __name__ == '__main__':
    main()

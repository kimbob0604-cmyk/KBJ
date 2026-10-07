#!/usr/bin/env python3
"""
수급 리포트 문장 — 규칙 기반.

board/CLAUDE.md 2장 3번: **LLM 에 숫자를 계산시키지 않는다.** 여기서는 아예 LLM
을 쓰지 않는다. 모든 판정은 아래 함수가 계산하고, 문장은 그 결과를 끼운 틀이다.
같은 입력이면 같은 문장이 나오고, 전부 시험할 수 있다.

예시 워크북의 문장을 옮겼다.

  "기관 매수는 9월 초에 집중. 최근 5거래일은 기관 매도와 외국인 매수가 교차함."
  "9월 1~4일 기관 +NNN.N억원 매수와 주가 +NN.N%가 동행. 이후 −NN.N% 조정."
  "기관 순매수 중 투신과 사모가 NN.N%."   (KBJ P1: 실데이터 숫자는 지웠다)

--------------------------------------------------------------------------
인과로 쓰지 않는다
--------------------------------------------------------------------------
'동행' 이라고 쓰고 '때문에' 라고 쓰지 않는다. 이 데이터로는 어느 쪽이 원인인지
식별되지 않는다 — 리포트 하단의 한계 문구(analyze.LIMITS)와 같은 이유다.
추세 판단은 단정하지 않고 '이른 구간' 처럼 유보한다.
"""
from __future__ import annotations

from .analyze import INSTITUTIONS

# 집중 구간을 찾을 때 볼 창 길이(거래일).
BURST_WINDOW = 4

# 이 금액(억원) 미만이면 '집중' 이라고 부르지 않는다.
BURST_MIN = 10.0


def _fmt(v, unit='억원', sign=True):
    if v is None:
        return 'N/A'
    s = f'{v:+,.1f}' if sign else f'{v:,.1f}'
    return f'{s}{unit}'


def _mmdd(d):
    return f'{int(d[4:6])}월 {int(d[6:8])}일'


def burst(rep, who='기관합계', window=BURST_WINDOW):
    """가장 크게 사들인 연속 구간. (시작일, 끝일, 합계억원) 또는 None.

    누적선이 가장 가파른 곳을 찾는 것이고, 그 구간 금액이 BURST_MIN 미만이면
    '집중' 이라고 부르지 않는다 — 작은 값에 서사를 붙이지 않는다.
    """
    cum = rep['cum'].get(who) or []
    if len(cum) <= window:
        return None
    best = None
    for i in range(len(cum) - window):
        a, b = cum[i], cum[i + window]
        if a[1] is None or b[1] is None:
            continue
        delta = b[1] - a[1]
        if best is None or delta > best[2]:
            best = (cum[i + 1][0], b[0], delta)
    if not best or best[2] < BURST_MIN:
        return None
    return best


def _pct(c0, c1):
    """두 종가의 변화율(%). 어느 한쪽이 없으면 None."""
    if not c0 or not c1:
        return None
    return (c1 / c0 - 1) * 100.0


def prev_trading_day(rep, day):
    """구간 시작일의 **직전** 거래일. 없으면 기준일."""
    ds = [rep['baseDate']] + list(rep['dates'])
    try:
        return ds[ds.index(day) - 1]
    except (ValueError, IndexError):
        return rep['baseDate']


def price_between(rep, d0, d1):
    """d0 **직전** 거래일 종가 → d1 종가 변화율(%). 종가가 없으면 None.

    d0 당일 종가를 기준으로 잡으면 d0 하루의 움직임이 통째로 빠진다. 그날의
    매수는 그날 장중에 일어났으므로 그 구간의 가격 변화에 포함돼야 한다 —
    누적 순매수를 조회 시작 직전일 0 에서 쌓는 것과 같은 이유다.

    실제로 여기서 한 번 틀렸다. 예시 워크북이 9/1~9/4 구간을 8/31 종가 → 9/4 종가로
    적는데 당일(9/1) 기준으로 재니 절반 남짓한 값이 나왔다.
    """
    c0, c1 = rep['closes'].get(prev_trading_day(rep, d0)), rep['closes'].get(d1)
    if not c0 or not c1:
        return None
    return (c1 / c0 - 1) * 100.0


def crossed(rep):
    """최근 5일에 기관과 외국인의 방향이 엇갈렸는가."""
    m = {r['who']: r for r in rep['main']}
    i = (m.get('기관합계') or {}).get('recent')
    f = (m.get('외국인') or {}).get('recent')
    if i is None or f is None:
        return None
    return (i > 0) != (f > 0)


def turned(rep, who):
    """한 달 방향과 최근 5일 방향이 반대인가 — '전환'."""
    m = {r['who']: r for r in rep['main']}.get(who) or {}
    a, b = m.get('month'), m.get('recent')
    if a is None or b is None:
        return None
    return (a > 0) != (b > 0)


def top_institutions(rep, n=2):
    """기여율 상위 n 구분과 그 합. [(이름, 기여율)], 합."""
    share = rep.get('share') or {}
    rows = [(k, v) for k, v in share.items() if v is not None]
    if not rows:
        return [], None
    rows.sort(key=lambda kv: -abs(kv[1]))
    top = rows[:n]
    return top, sum(v for _, v in top)


def recent_driver(rep):
    """최근 5일 기관 움직임을 가장 크게 끈 구분. (이름, 비중) 또는 None."""
    vals = {r['who']: r['recent'] for r in rep['inst'] if r['recent'] is not None}
    tot = sum(vals.values())
    if not vals or abs(tot) < 0.05:
        return None
    who = max(vals, key=lambda k: abs(vals[k]))
    return who, vals[who] / tot


def lines(rep):
    """리포트 상단 문장들. 계산이 안 된 것은 아예 쓰지 않는다."""
    out = []
    m = {r['who']: r for r in rep['main']}
    inst, foreign = m.get('기관합계') or {}, m.get('외국인') or {}

    # 1. 집중 구간과 최근 교차
    b = burst(rep)
    head = []
    if b:
        head.append(f'기관 매수는 {_mmdd(b[0])}~{_mmdd(b[1])}에 집중.')
    x = crossed(rep)
    if x is True:
        i_dir = '매수' if (inst.get('recent') or 0) > 0 else '매도'
        f_dir = '매수' if (foreign.get('recent') or 0) > 0 else '매도'
        head.append(f'최근 {len(rep["recent"])}거래일은 기관 {i_dir}와 외국인 {f_dir}가 교차함.')
    elif x is False:
        head.append(f'최근 {len(rep["recent"])}거래일은 기관과 외국인이 같은 방향.')
    if head:
        out.append(' '.join(head))

    # 2. 숫자 요약
    out.append(
        f'{period_label(rep)}: 기관 {_fmt(inst.get("month"))}, '
        f'외국인 {_fmt(foreign.get("month"))}. '
        f'최근 {len(rep["recent"])}일: 기관 {_fmt(inst.get("recent"))}, '
        f'외국인 {_fmt(foreign.get("recent"))}.')

    # 3. 집중 구간의 가격 동행 — '때문에' 가 아니라 '동행'
    if b:
        pr = price_between(rep, b[0], b[1])
        if pr is not None:
            s = (f'{_mmdd(b[0])}~{_mmdd(b[1])} 기관 {_fmt(b[2])} 매수와 '
                 f'주가 {pr:+.1f}%가 동행.')
            # '이후' 는 집중 구간이 끝난 **다음날부터**다. 그래서 기준은
            # 구간 끝날 당일 종가 — price_between 의 직전일 규칙을 쓰면 안 된다.
            after = _pct(rep['closes'].get(b[1]), rep['closes'].get(rep['dates'][-1]))
            if after is not None and b[1] != rep['dates'][-1]:
                s += f' 이후 {_mmdd(rep["dates"][-1])}까지 {after:+.1f}%.'
            out.append(s)

    # 4. 기관 구분 쏠림
    top, tot = top_institutions(rep)
    if top and tot is not None:
        names = '과 '.join(k for k, _ in top)
        out.append(f'기관 순매수 중 {names}가 {tot * 100:.1f}%.')
    drv = recent_driver(rep)
    if drv:
        who, w = drv
        sign = '순매수' if (inst.get('recent') or 0) > 0 else '순매도'
        out.append(f'최근 {len(rep["recent"])}일 기관 {sign}의 {abs(w) * 100:.1f}%는 {who}에서 발생.')

    # 5. 해석 — 단정하지 않는다
    t = turned(rep, '외국인')
    if t and (foreign.get('recent') or 0) > 0 and (inst.get('recent') or 0) < 0:
        out.append('최근 외국인 매수 전환은 관측되나, 기관 매수 재확대가 동반된 '
                   '상승 추세로 해석하기에는 이른 구간.')
    return out


def period_label(rep):
    """이 리포트가 실제로 덮은 기간. **요청한 일수가 아니라 받은 일수다.**

    소스가 요청보다 적게 주는 일이 있다(KIS 종목 수급은 최근 며칠치만 준다).
    그때 '최근 60거래일' 이라고 적으면 받는 쪽은 60일치를 봤다고 읽는다.
    `rep['dates']` 가 곧 사실이므로 거기서 만든다.

    20~22거래일은 사람이 '한 달' 이라고 부르는 구간이라 그대로 둔다. 그 밖은
    거래일 수를 그대로 적는다 — 60거래일을 '3개월' 로 접으면 12주가 3개월이
    되어 버린다.
    """
    n = len(rep.get('dates') or [])
    if 20 <= n <= 22:
        return '한 달'
    return f'{n}거래일'


def numbers(rep):
    """텔레그램 본문에 실을 숫자표. 차트의 대비 WARN 을 구제하는 자리이기도 하다."""
    per, rec = period_label(rep), len(rep.get('recent') or [])
    lines_ = [f'투자자 | {per} | 최근{rec}일 | 매수일수']
    for r in rep['main']:
        lines_.append(f"{r['who']} | {_fmt(r['month'])} | {_fmt(r['recent'])} | {r['days']}일")
    share = rep.get('share') or {}
    inst = [r for r in rep['inst']
            if r['month'] is not None and abs(r['month']) >= 0.005]
    # 기관 세부가 없으면(KIS 3구분) 표 머리만 남기지 않는다 — 빈 표는
    # '기관이 아무것도 안 샀다' 로 읽힌다. 없는 것은 없다고 본문이 말한다.
    if inst:
        lines_.append('')
        lines_.append(f'기관 구분 | {per} | 기여율')
        for r in inst:
            sh = share.get(r['who'])
            lines_.append(f"{r['who']} | {_fmt(r['month'])} | "
                          f"{(f'{sh * 100:.1f}%' if sh is not None else 'N/A')}")
    return '\n'.join(lines_)

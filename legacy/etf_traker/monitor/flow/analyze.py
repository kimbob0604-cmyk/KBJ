#!/usr/bin/env python3
"""
수급 분석 — 일별 원자료 → 예시 워크북과 같은 표·차트 데이터·검산.

외부 호출을 하지 않는다. 그래서 예시 워크북을 고정 표본으로 삼아 전부 대조할 수
있다(tests/test_analyze.py).

--------------------------------------------------------------------------
단위
--------------------------------------------------------------------------
원자료는 원과 주 그대로 받고, **표시 직전에만** 억원으로 접는다. 중간에 접으면
합계와 차분이 어긋난다 — 예시 워크북도 '원자료 단위는 원/주 유지' 라고 적어 뒀다.

--------------------------------------------------------------------------
누적은 기준일 0 에서 출발한다
--------------------------------------------------------------------------
차트의 누적선은 조회 시작일 **직전 거래일**을 0 으로 놓고 쌓는다. 가격 Base100
도 같은 날을 100 으로 잡는다. 그래야 "이 구간 동안" 이라는 말이 성립한다.
예시 워크북의 08.14(=조회 20거래일 직전)가 그 자리다.

--------------------------------------------------------------------------
이 분석이 말하지 않는 것
--------------------------------------------------------------------------
누적 순매수는 **조회기간의 매매 차액**이지 보유잔고나 지분율이 아니다. 그리고
수급과 가격이 같이 움직였다는 것뿐, 어느 쪽이 원인인지는 이 데이터로 식별되지
않는다. 두 문장 다 리포트 하단에 그대로 싣는다(LIMITS).
"""
from __future__ import annotations

EOK = 100_000_000          # 1억

# 기관합계를 이루는 7구분.
INSTITUTIONS = ('금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금 등')

# 화면 상단 투자자 표의 행 순서.
MAIN_ROWS = ('개인', '외국인', '기관합계', '기타법인', '기타외국인')

# '최근'을 며칠로 볼 것인가.
RECENT_DAYS = 5

# 기간합계 대조를 못 했을 때 본문에 싣는 줄. 침묵하면 '대조했다' 로 읽힌다.
UNVERIFIED = ('기간합계 원자료가 없어 일별 합 대조를 하지 못함(KIS 3구분). '
              '기관 내부 구분(투신·사모·연기금 등)은 이 경로에서 제공되지 않음.')

LIMITS = (
    '누적 순매수는 조회기간의 매매 차액이며, 보유잔고나 지분율 자체를 뜻하지 않음.',
    '수급과 가격의 동행을 설명하는 분석임. 특정 주체의 매매가 가격 변화를 '
    '일으켰는지까지 식별되지 않음.',
)


def eok(won):
    """원 → 억원. None 은 None 으로 둔다 — 0 으로 바꾸면 '없음'이 '0원'이 된다."""
    return None if won is None else won / EOK


def _sum(daily, dates, who):
    """구간 합. 값이 없는 날은 건너뛴다(0 으로 세지 않는다)."""
    tot, n = 0.0, 0
    for d in dates:
        v = (daily.get(d) or {}).get(who)
        if v is not None:
            tot += v
            n += 1
    return (tot if n else None), n


def buy_days(daily, dates, who):
    """순매수한 날의 수. 보합(0)은 매수로 세지 않는다."""
    return sum(1 for d in dates
               if (daily.get(d) or {}).get(who) is not None
               and (daily[d][who]) > 0)


def cumulative(daily, dates, who, base_label='base'):
    """기준일 0 에서 출발하는 누적. [(라벨, 값)] 로 돌려준다.

    첫 항목은 기준일이고 값은 항상 0 이다 — 조회 시작 직전을 0 으로 놓아야
    '이 구간 동안 얼마' 가 된다.
    """
    out = [(base_label, 0.0)]
    run = 0.0
    for d in dates:
        v = (daily.get(d) or {}).get(who)
        if v is not None:
            run += v
        out.append((d, run))
    return out


def base100(closes, dates, base_date):
    """기준일 종가를 100 으로 둔 가격 지수. 기준 종가가 없으면 None."""
    base = closes.get(base_date)
    if not base:
        return None
    out = [(base_date, 100.0)]
    for d in dates:
        c = closes.get(d)
        out.append((d, (c / base * 100.0) if c else None))
    return out


def institution_share(totals_eok):
    """기관 7구분의 기여율. 분모는 **양수 기여분의 합이 아니라 기관합계**다.

    기관합계가 0 이거나 부호가 갈리면 기여율을 내지 않는다(None) — 분모가
    0 에 가까우면 비율이 폭발하고, 그 숫자는 아무것도 설명하지 못한다.
    """
    tot = totals_eok.get('기관합계')
    if not tot:
        return {k: None for k in INSTITUTIONS}
    return {k: (totals_eok.get(k) / tot if totals_eok.get(k) is not None else None)
            for k in INSTITUTIONS}


def reconcile(daily_amt, daily_qty, totals, dates):
    """검산 — 일별 합이 KRX 기간합계와 맞는가.

    예시 워크북의 '대조 항목' 블록이 이것이고 전부 0 이어야 한다. 하나라도
    벌어지면 어느 쪽이 틀렸는지 모르므로 **리포트를 내지 않는다.**

    돌려주는 것: [(항목, 차이, 통과여부)]
    """
    out = []
    for label, src, who, key in (
            ('기관 금액 vs 기간합계', daily_amt, '기관합계', 'net_amt'),
            ('외국인 금액 vs 기간합계', daily_amt, '외국인', 'net_amt'),
            ('개인 금액 vs 기간합계', daily_amt, '개인', 'net_amt'),
            ('기관 수량 vs 기간합계', daily_qty, '기관합계', 'net_qty'),
            ('외국인 수량 vs 기간합계', daily_qty, '외국인', 'net_qty')):
        got, _ = _sum(src or {}, dates, who)
        want = (totals.get(who) or {}).get(key)
        if got is None or want is None:
            out.append((label, None, False))
            continue
        diff = got - want
        # 원/주 단위 정수라 반올림 오차가 없어야 한다. 1 원(주) 이상이면 실패.
        out.append((label, diff, abs(diff) < 1))

    # 전체 순매수는 정의상 0 이다. 아니면 구분을 하나 빠뜨린 것이다.
    total_net, _ = _sum(daily_amt or {}, dates, '전체')
    if total_net is not None:
        out.append(('순매수 금액 전체', total_net, abs(total_net) < 1))
    return out


def present(daily, dates, names):
    """원자료에 실제로 값이 있는 구분만. 없는 구분은 표에서 아예 뺀다.

    KIS 는 3구분만 준다. 없는 줄을 'N/A' 로 남겨 두면 '0 이었나?' 로 읽힌다.
    """
    return [w for w in names
            if any(w in (daily.get(d) or {}) for d in dates)]


def analyze(code, name, daily_amt, daily_qty, totals, closes, dates, base_date,
            source='krx', extra_checks=()):
    """전부 묶어 리포트 한 건. 검산이 깨지면 ok=False 로 돌려주고 쓰지 않는다.

    dates 는 조회 구간의 거래일(오름차순), base_date 는 그 직전 거래일이다.

    `totals` 가 None 이면 **기간합계 원자료가 없다는 뜻**이다(KIS 경로).
    그때는 검산을 '통과' 로 적지 않는다 — 하지도 않은 대조를 했다고 하는 것이
    제일 나쁘다. `verified=False` 로 표시하고 리포트 하단이 그 사실을 싣는다.
    """
    recent = dates[-RECENT_DAYS:] if len(dates) >= RECENT_DAYS else dates[:]

    month_amt = {w: eok(_sum(daily_amt, dates, w)[0]) for w in
                 set(MAIN_ROWS) | set(INSTITUTIONS)}
    recent_amt = {w: eok(_sum(daily_amt, recent, w)[0]) for w in
                  set(MAIN_ROWS) | set(INSTITUTIONS)}
    net_qty = {w: _sum(daily_qty, dates, w)[0] for w in MAIN_ROWS}
    days = {w: buy_days(daily_amt, dates, w) for w in MAIN_ROWS}

    verified = totals is not None
    checks = list(extra_checks)
    if verified:
        checks = reconcile(daily_amt, daily_qty, totals, dates) + checks
    ok = all(passed for _, _, passed in checks)

    price = base100(closes, dates, base_date)
    ret_pct = None
    if price and price[-1][1] is not None:
        ret_pct = price[-1][1] - 100.0

    rows = present(daily_amt, dates, MAIN_ROWS)
    inst_rows = present(daily_amt, dates, INSTITUTIONS)

    return dict(
        code=code, name=name, ok=ok, checks=checks,
        source=source, verified=verified,
        dates=dates, baseDate=base_date, recent=recent,
        unit='억원',
        main=[dict(who=w, month=month_amt.get(w), recent=recent_amt.get(w),
                   qty=net_qty.get(w), days=days.get(w)) for w in rows],
        inst=[dict(who=w, month=month_amt.get(w), recent=recent_amt.get(w),
                   share=None) for w in inst_rows],
        share=institution_share(month_amt),
        cum={w: [(d, eok(v)) for d, v in cumulative(daily_amt, dates, w, base_date)]
             for w in ('개인', '외국인', '기관합계') + INSTITUTIONS},
        bars=[(d, eok((daily_amt.get(d) or {}).get('외국인')),
               eok((daily_amt.get(d) or {}).get('기관합계'))) for d in dates],
        price=price, retPct=ret_pct,
        closes={d: closes.get(d) for d in [base_date] + list(dates)},
    )

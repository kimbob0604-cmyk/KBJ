#!/usr/bin/env python3
"""
수급 리포트 — 신고가 + 거래량 급증 종목을 매일 돌려 텔레그램으로 보낸다.

  python -m monitor.flow.run                  기본: 52주 신고가 + 거래량 2배, 5종목
  python -m monitor.flow.run --top 3          개수 지정
  python -m monitor.flow.run --vol-mult 3     거래량 문턱 지정 (0 이면 문턱 없음)
  python -m monitor.flow.run --codes 092870   종목 지정 (쉼표 구분)
  python -m monitor.flow.run --dry-run        보내지 않고 파일만 만든다
  python -m monitor.flow.run --check          KRX 접속만 확인

대상은 board 가 이미 만드는 `state/<날짜>/newhigh.json` 의 `achieved` 에서
가져온다. 신고가가 선 종목 중 **그날 거래량이 20거래일 평균의 배수**(보드가 실어
보내는 `vol_mult`)가 문턱을 넘은 것만 남기고, 많이 는 순서로 집는다.

신고가만으로 고르면 조용히 신고가를 낸 종목이 섞인다. 수급 리포트는 누가 사고
팔았는지를 보는 것이라, 평소와 다르게 손이 바뀐 날이어야 읽을 거리가 있다.

--------------------------------------------------------------------------
검산이 깨지면 보내지 않는다
--------------------------------------------------------------------------
일별 합과 KRX 기간합계가 원 단위로 맞지 않으면 어느 쪽이 틀렸는지 모른다.
그 상태의 리포트는 틀린 숫자를 사실처럼 보내는 것이므로 **그 종목을 건너뛰고**
무엇이 얼마나 어긋났는지 로그에 남긴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from . import (analyze as A, chart as C, krx as K, kissrc as KIS,  # noqa: E402
               narrative as N, returns as RET, telegram as T)

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, '..', '..')
STATE = os.path.join(REPO, 'board', 'state')
OUT = os.path.join(ROOT, 'out')

# 기본 대상 종목 수. 한 종목당 그림 3~4장이라 너무 늘리면 대화창이 잠긴다.
# 52주 이상 신고가는 보통 하루 한 자릿수라 이 수에 먼저 닿는 일이 드물다.
TOP_N = 12

# 거래량 문턱 — 그날 거래량이 20거래일 평균의 몇 배 이상이어야 대상인가.
#
# **기본값은 0(문턱 없음)이다.** 전에는 2.0 이었는데, 그러면 52주 신고가를 낸
# 종목의 수급이 거래량 때문에 통째로 빠진다 — 실제로 2026-09-17 에 6종목 중
# 2종목만 남았고, 나머지 넷은 신고가를 냈는데도 수급이 한 줄도 안 갔다.
# 신고가를 낸 종목의 수급은 빠짐없이 본다는 것이 이 리포트의 일이다.
#
# 문턱을 다시 걸고 싶으면 `--vol-mult 2` 로 준다. 보드가 이미 매 종목에
# `vol_mult`(당일 거래량 ÷ 20일 평균)를 실어 보내므로 그 값을 그대로 쓴다 —
# 여기서 다시 계산하면 화면의 '거래량 N배' 와 대상이 갈린다.
VOL_MULT_MIN = 0.0

# 조회할 거래일 수. 예시 워크북이 20거래일이다.
# 기본 조회 구간. 사용자 요청으로 20 → 60 거래일 (2026-09-17).
# 소스가 그만큼 못 주면 받은 만큼만 쓰고, 제목도 받은 일수로 적는다
# (narrative.period_label) — 요청한 일수를 제목에 적으면 거짓이 된다.
DAYS = 60

# 어느 신고가를 볼 것인가. 보드의 라벨 키다 — hist(역사적) · w52(52주) · d60(60일).
#
# **역사적 신고가도 52주에 든다.** 역사적으로 최고면 52주로도 최고다. 보드의
# 알약과 같은 규약(`hits[k]`)이라 화면의 '52주 N' 과 여기 대상이 어긋나지
# 않는다. 라벨(`label`)로 거르면 역사적이 빠져 둘이 갈린다.
#
# 판정 기준은 **종가**다(`hit()` 참고). 고가 기준은 장중에 잠깐 뚫고 종가는
# 하락 마감한 날도 신고가로 잡는다.
KIND = 'w52'
KINDS = ('hist', 'w52', 'd60', 'all')


def latest_newhigh(state=STATE):
    """가장 최근 newhigh.json. 없으면 None."""
    hits = sorted(glob.glob(os.path.join(state, '*', 'newhigh.json')))
    if not hits:
        return None
    with open(hits[-1], encoding='utf-8') as f:
        return json.load(f)


def _hits(row, key, kind):
    return bool(((row.get(key) or {}).get('hits') or {}).get(kind))


def basis_of(row, kind):
    """그 신고가가 **어느 기준으로** 섰는가. '고가' · '종가' · '고가·종가' · None.

    기준은 이름으로 읽는다(`high_basis` · `close_basis`). `hits` 는 보드의 기본
    기준이라 설정(`default_basis`)에 따라 가리키는 기준이 바뀌는데, 그걸 '고가'
    로 짐작하면 설정을 바꾸는 순간 리포트가 틀린 기준을 적는다.

    `high_basis` 가 없는 옛 state 파일(캐시에 남아 있을 수 있다)은 `hits` 로
    물러서되, 대상 선정은 어차피 종가 기준이라 잘못 뽑힐 일은 없다.
    """
    cl = _hits(row, 'close_basis', kind)
    hi = (_hits(row, 'high_basis', kind) if row.get('high_basis') is not None
          else bool((row.get('hits') or {}).get(kind)))
    if hi and cl:
        return '고가·종가'
    return '고가' if hi else ('종가' if cl else None)


def hit(row, kind):
    """대상인가 — **종가 기준으로 신고가가 선 종목만** 본다.

    고가 기준은 장중에 잠깐 뚫고 종가는 전일보다 낮게 끝난 날도 신고가로
    잡는다. 그런 종목을 수급 리포트로 받으면 '일간 수익률은 마이너스인데 52주
    신고가' 가 되어 매번 멈춰서 따져 보게 된다. 종가 기준은 그런 날이 없다 —
    오늘 종가 > 과거 종가 최고 ≥ 어제 종가라 등락률이 반드시 플러스다.

    보드의 기본 기준도 종가로 맞춰 뒀다(board/config/settings.yaml). 여기서
    `close_basis` 를 직접 보는 것은 설정이 다시 바뀌어도 이 리포트의 대상이
    흔들리지 않게 하기 위해서다.
    """
    return _hits(row, 'close_basis', kind)


def pick_targets(nh, top=TOP_N, kind=KIND, vol_min=VOL_MULT_MIN):
    """대상 고르기. 반환 `([(코드, 이름, 거래량배수)], 집계)`.

    신고가(`kind`)가 선 종목 중 **거래량이 평소보다 는** 것만 남기고, 많이
    는 순서로 `top` 개를 집는다. 평소는 보드가 재 둔 20거래일 평균이다.

    `vol_min` 이 0 이하면 문턱을 걸지 않고 **보드가 매긴 순서**를 그대로 쓴다.
    보드의 정렬은 라벨 우선순위(역사적 > 52주 > 60일) + 거래대금이라, 거래량
    기준으로 다시 세우면 두 화면의 순서가 갈린다. 문턱을 걸 때만 갈라진다.

    `집계` 는 왜 줄었는지를 적는다 — 몇이 라벨에서, 몇이 거래량에서, 몇이
    거래량을 못 구해서 빠졌는지. 조용히 사라지면 '오늘은 없었다' 와 '자료가
    없었다' 가 구분되지 않는다.
    """
    seen = set()
    rows = []
    st = dict(achieved=0, kind_out=0, no_vol=0, below=0, passed=0)
    for r in (nh or {}).get('achieved') or []:
        code, name = r.get('code'), r.get('name')
        if not code or code in seen:
            continue
        seen.add(code)
        st['achieved'] += 1
        if kind and kind != 'all' and not hit(r, kind):
            st['kind_out'] += 1
            continue
        vm = r.get('vol_mult')
        if vol_min > 0:
            if vm is None:
                # 거래량을 못 구한 종목이다(분할 가드로 구간이 잘렸거나 원자료
                # 결측). '0배' 로 치면 없는 사실을 지어내는 것이라 빼고 센다.
                st['no_vol'] += 1
                continue
            if vm < vol_min:
                st['below'] += 1
                continue
        st['passed'] += 1
        rows.append((code, name or code, meta_of(r, kind)))
    if vol_min > 0:
        # 많이 는 순. 같은 배수면 보드가 매긴 순서를 지킨다(안정 정렬).
        rows.sort(key=lambda t: -(t[2].get('vol_mult') or 0))
    return rows[:top], st


def meta_of(row, kind=KIND):
    """보드가 이미 잰 값을 그대로 옮긴다 — 여기서 다시 계산하지 않는다.

    `turnover_is_estimate` 는 거래대금을 종가x거래량으로 채웠다는 뜻이다(소스가
    거래대금을 안 줄 때). 추정치를 실측처럼 적지 않으려고 같이 들고 온다.
    """
    return dict(
        basis=basis_of(row, kind),                      # 고가 / 종가 / 고가·종가
        vol_mult=row.get('vol_mult'),
        mktcap=row.get('mktcap'),                       # 억원
        turnover=row.get('turnover'),                   # 억원
        turnover_est=bool(row.get('turnover_is_estimate')),
        turnover_avg20=row.get('turnover_avg20'),       # 억원 (종가x거래량 추정)
    )


def trading_window(days=DAYS, end=None):
    """조회 구간. 넉넉히 잡고 실제 거래일은 응답에서 추린다.

    주말·공휴일을 모르므로 달력일로 1.9배 잡는다. KRX 가 거래일만 돌려주므로
    받아 온 뒤 뒤에서 days 개를 쓴다.
    """
    end = end or dt.date.today()
    start = end - dt.timedelta(days=int(days * 1.9) + 10)
    return start.isoformat(), end.isoformat()


def build_one(code, name, days=DAYS, log=print, source='auto'):
    """한 종목 분석. 검산이 깨지면 (None, 사유).

    원자료는 두 곳에서 올 수 있다.

      krx  기관 7구분 + 기간합계(검산 가능). 지금 이 환경에서는 막혀 있다.
      kis  개인·외국인·기관 3구분. 기간합계가 없어 검산을 못 한다.

    'auto' 는 KRX 를 한 번 찔러 보고 막히면 KIS 로 간다. 어느 쪽으로 만들었는지는
    리포트에 싣는다 — 3구분인지 7구분인지가 그 한 줄로 갈린다.
    """
    start, end = trading_window(days)
    totals, used, why_krx = None, None, ''
    amt = qty = None
    if source in ('auto', 'krx'):
        try:
            amt = K.fetch_daily(code, start, end, unit='amt')
            qty = K.fetch_daily(code, start, end, unit='qty')
            totals = K.fetch_total(code, start, end)
            used = 'krx'
        except Exception as e:  # noqa: BLE001
            why_krx = str(e)
            if source == 'krx':
                return None, f'KRX 조회 실패: {e}'
            log(f'  KRX 막힘 — KIS 3구분으로 간다 ({str(e)[:80]})')

    extra = ()
    if used != 'krx':
        try:
            amt, qty, kis_closes = KIS.fetch_daily(code, days=days)
        except Exception as e:  # noqa: BLE001
            return None, (f'KIS 조회 실패: {e}'
                          + (f' / KRX: {why_krx[:80]}' if why_krx else ''))
        used, totals = 'kis', None
        # 기간합계가 없으니 검산을 못 한다. 대신 단위를 잘못 알지 않았는지는
        # 확인할 수 있다 — 자릿수가 틀리면 모든 숫자가 100만 배로 틀린다.
        extra = (KIS.unit_check(amt, qty, kis_closes),)

    dates = sorted(amt)[-days:]
    if len(dates) < 2:
        return None, f'거래일이 {len(dates)}일뿐이다'

    # 기준일 = 조회 구간 **직전** 거래일. 누적 0 과 가격 100 이 여기서 출발한다.
    everything = sorted(amt)
    i = everything.index(dates[0])
    base = everything[i - 1] if i > 0 else dates[0]

    ohlcv = _ohlcv(code, base, dates, log=log)
    closes = {r['asof'].replace('-', ''): r['close'] for r in ohlcv}
    if not closes:
        return None, '일봉을 못 받았다'

    # 기관합계는 7구분의 합이다. KRX 가 따로 주지 않는 화면도 있어 여기서 만든다.
    for src in (amt, qty):
        for d, row in src.items():
            if '기관합계' not in row:
                row['기관합계'] = sum(row.get(k) or 0 for k in K.INSTITUTIONS)

    rep = A.analyze(code, name, amt, qty, totals, closes, dates, base,
                    source=used, extra_checks=extra)
    # 차트가 캔들을 그리는 자리다 — 없으면 종가 선으로 물러선다. 차트는 조회
    # 구간만 보여 준다(수급 차트와 x축을 맞춘다). 수익률은 그보다 긴 구간이
    # 필요하므로 받아 온 전 구간으로 잰다.
    rep['ohlcv'] = [r for r in ohlcv
                    if base <= r['asof'].replace('-', '') <= dates[-1]]
    rep['returns'] = RET.compute(ohlcv)
    if not rep['ok']:
        bad = '; '.join(f'{lbl} 차이 {diff}' for lbl, diff, ok in rep['checks'] if not ok)
        return None, f'검산 실패 — {bad}'
    return rep, used


def _ohlcv(code, base, dates, log=print):
    """일봉 한 구간. 보드가 이미 쓰는 네이버 시세를 그대로 쓴다.

    KRX 원자료의 시세는 **수정주가 미적용**이다(예시 워크북도 그렇게 적어 뒀다).
    네이버 siseJson 은 수정주가다(D-056 에서 삼성전자 1,635일 전수 대조로 확정).
    구간 안에 액면분할·권리락이 끼면 두 소스의 종가가 달라지는데, 가격은
    수정주가로 재는 쪽이 맞다 — 그래서 수급은 KRX, 가격은 네이버를 쓴다.

    종가만이 아니라 시·고·저·거래량까지 들고 온다. 가격 차트를 실제 일봉으로
    그리기 때문이다(예전에는 기준일=100 의 선 하나였다).
    """
    end = dates[-1]
    # 시작은 **작년 12월 1일**이다. 연초 대비는 작년 마지막 거래일 종가로 재고
    # 1개월 대비는 달력으로 한 달 전을 찾아야 하니, 조회 구간(20거래일)만
    # 받아서는 둘 다 못 잰다. 한 번의 요청으로 같이 받는다.
    start = f'{int(end[:4]) - 1}-12-01'
    try:
        from board.ingest import naver
        return naver.fetch_ohlcv(code, start,
                                 f'{end[:4]}-{end[4:6]}-{end[6:8]}')
    except Exception as e:  # noqa: BLE001
        log(f'  일봉 실패: {e}')
        return []


LABEL_KO = {'hist': '역사적', 'w52': '52주', 'd60': '60일'}


def run(codes=None, top=TOP_N, days=DAYS, dry=False, log=print, source='auto',
        kind=KIND, vol_min=VOL_MULT_MIN):
    """대상 종목을 돌려 리포트를 만들고 보낸다. 반환: 보낸 건수."""
    label = LABEL_KO.get(kind, '전체')
    if codes:
        targets = [(c, c, {}) for c in codes]
    else:
        nh = latest_newhigh()
        if not nh:
            # 조용히 0 건으로 끝내면 매일 아무것도 안 보내면서 초록으로 보인다.
            raise SystemExit('newhigh.json 이 없다. 보드 state 를 못 꺼냈다 — '
                             '캐시 열쇠나 보드 실행 순서를 본다.')
        targets, st = pick_targets(nh, top, kind=kind, vol_min=vol_min)
        # 키 이름은 as_of 다(board/engine/build.py 의 prov). asof 로 읽어서
        # 여태 '보드 ?' 로 찍혔고, 그래서 기준일이 어긋나도 보이지 않았다.
        nh_asof = nh.get('as_of') or nh.get('asof') or '?'
        for _, _, m in targets:
            m['asof'] = nh_asof
        log(f'대상 {len(targets)}종목 · {label} 신고가 + 거래량 {vol_min}배 이상 '
            f'(보드 {nh_asof}): '
            + ', '.join(f"{n}({m.get('basis')}·{m.get('vol_mult')}배)"
                        for _, n, m in targets))
        log(f'  달성 {st["achieved"]}종목 → {label} 아님 {st["kind_out"]} · '
            f'거래량 미달 {st["below"]} · 거래량 모름 {st["no_vol"]} '
            f'→ 통과 {st["passed"]}')
        if st['achieved'] == 0:
            raise SystemExit('신고가 달성 종목이 0건이다. 보드 목록이 비었다 — '
                             'state 를 제대로 꺼냈는지 본다.')
        if not targets:
            # 이건 사고가 아니다. 신고가는 났지만 거래량이 평소와 같은 날이다.
            # 그래도 침묵하지는 않는다 — 안 온 것과 못 온 것이 구분돼야 한다.
            msg = (f'오늘 {label} 신고가 {st["achieved"] - st["kind_out"]}종목 중 '
                   f'거래량이 20일 평균의 {vol_min}배를 넘은 종목이 없습니다. '
                   f'(미달 {st["below"]} · 거래량 모름 {st["no_vol"]})')
            log(msg)
            if not dry:
                ok, why = T.send_text(f'📉 수급 리포트 — 대상 없음\n{msg}')
                log(f'  {"알림 보냄" if ok else "알림 실패"} — {why}')
            return 0

    sent = 0
    for code, name, meta in targets:
        log(f'[{code} {name}]')
        rep, why = build_one(code, name, days=days, log=log, source=source)
        if not rep:
            log(f'  건너뜀 — {why}')
            continue
        # 왜 이 종목이 뽑혔는지를 본문이 말한다. 다섯을 받아 보면 '왜 얘냐' 가
        # 먼저 떠오르는데, 고른 근거가 리포트 밖에 있으면 확인할 길이 없다.
        rep['pick'] = dict(kind=kind, label=label, **(meta or {}))
        log(f'  원자료 {why} · 구분 {len(rep["main"])}개'
            + ('' if rep['verified'] else ' · 기간합계 대조 못 함'))
        charts = C.render_all(rep, OUT)
        text = T.compose(rep, N.lines(rep), N.numbers(rep))
        if dry:
            log(f'  그림 {len(charts)}장 · 본문 {len(text)}자 (보내지 않음)')
            sent += 1
            continue
        ok, why2 = T.send_report(rep, charts, text)
        log(f'  {"보냄" if ok else "실패"} — {why2}')
        sent += 1 if ok else 0
    return sent


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=TOP_N)
    ap.add_argument('--days', type=int, default=DAYS)
    ap.add_argument('--codes', default='', help='쉼표로 구분한 종목코드')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--check', action='store_true', help='원자료 접속만 확인')
    ap.add_argument('--source', default='auto', choices=('auto', 'krx', 'kis'),
                    help='원자료. auto 는 KRX 를 찔러 보고 막히면 KIS')
    ap.add_argument('--kind', default=KIND, choices=KINDS,
                    help='어느 신고가를 볼 것인가 (기본 w52=52주)')
    ap.add_argument('--vol-mult', type=float, default=VOL_MULT_MIN,
                    dest='vol_mult',
                    help='거래량이 20일 평균의 몇 배 이상인 종목만 (0=문턱 없음)')
    a = ap.parse_args(argv)

    if a.check:
        rows = []
        if a.source in ('auto', 'krx'):
            rows += K.probe()
        if a.source in ('auto', 'kis'):
            rows += KIS.probe()
        for label, ok, why in rows:
            print(f'  {"PASS" if ok else "FAIL"} {label}: {why}')
        return 0

    codes = [c.strip() for c in a.codes.split(',') if c.strip()]
    n = run(codes=codes or None, top=a.top, days=a.days, dry=a.dry_run,
            source=a.source, kind=a.kind, vol_min=a.vol_mult)
    print(f'완료: {n}건')
    return 0


if __name__ == '__main__':
    sys.exit(main())

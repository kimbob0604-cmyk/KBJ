#!/usr/bin/env python3
"""
수집 파이프라인 — 소스에서 받아 DB 에 넣는 것까지가 여기 책임이다.

--init   전 종목 일봉을 과거 구간까지 한 번에 받아 역사적 최고가 스칼라를 만든다.
--daily  당일 스냅샷과 최근 일봉만 받아 증분 적재한다.

일봉은 최근 KEEP_DAYS 만 DB 에 남긴다. 그 이전 구간의 정보는 alltime 스칼라가
들고 있으므로 52주 판정에는 지장이 없다 (CLAUDE.md 4장).
"""
from datetime import date, datetime, timedelta, timezone

from ..engine import db as DB
from ..engine import newhigh as nh
from . import creds, flows, funds, kis, krx, naver
from .http import Fetch, gather, session

KST = timezone(timedelta(hours=9))
KEEP_DAYS = 420          # 252영업일 + 여유. 달력일 기준이라 넉넉히 잡는다.

# DB 안의 값이 어떤 규약으로 만들어졌는지. **수집 단위나 스칼라 의미를 바꾸면
# 반드시 올린다.** daily 는 값이 다르면 스스로 다시 쌓는다.
#
# 왜 필요한가. 2026-08-29 에 고쳐진 코드가 옛 코드로 만든 DB 위에서 돌았다.
# 워크플로가 'board.db 파일이 있으면 init 을 건너뛴다' 였고, Actions 캐시가
# 옛 DB 를 복원했기 때문이다. 결과는 역사적 신고가 43(정상 5), 거래대금 전 종목
# 0억 — 코드는 고쳐졌는데 화면은 고치기 전과 같았다. 파일의 존재는 내용의
# 정합성을 보장하지 않는다.
#
#   1  최초
#   2  거래대금 단위를 백만원→억원으로 정정 (D-035), init 이 alltime 을
#      비우고 다시 쌓도록 수정 (D-033)
#   3  스냅샷을 실행 날짜가 아니라 실제 장 세션 날짜로 맞춤 (D-041).
#      휴장일에 적재한 v2 DB 는 기준일보다 뒤에 스냅이 있어 쓸 수 없다
#   4  '종가' 를 KRX 정규장 종가로 고정 (D-080). v3 DB 의 당일 종가는
#      NXT·시간외가 섞인 잠정치라 종가 기준 라벨과 스칼라가 그 위에서 굳어 있다
DATA_VERSION = 4
# --init 이 받아올 시작일. 상장 이후 전 구간을 받아 역사적 최고가 스칼라를 만든다.
# 소스는 없는 구간을 알아서 비워 보내므로 이 날짜를 넉넉히 잡아도 문제가 없다.
FIRST_DAY = '1990-01-01'


def today_kst():
    return datetime.now(KST).date()


# ─────────────────────── KRX 정규장 확정치 (D-080) ───────────────────────
# **'종가' 는 KRX 정규장 종가다.**
#
# 네이버 목록·siseJson 이 16:07 에 주는 당일 값은 종가가 아니다. NXT
# 애프터마켓(15:30~20:00)과 시간외 단일가가 끝날 때까지 계속 움직이는 잠정치다.
# 2026-09-01 state 를 9/7 시점 siseJson 과 대조하면 유니버스 2,686종목 중
# 2,369종목(88.2%)의 종가가 달랐고 |차| 중앙값 0.67%, 3% 초과가 108종목이었다.
# 달성 표 안에서만 14건이 종가 기준 라벨을 바꿀 크기였다(위더스제약 9,090→8,320,
# 헝셩그룹 3,800→3,500, SK케미칼 54,700→54,100). 고가는 전부 일치했다 —
# 판정 로직이 아니라 **수집 시점**의 문제다.
#
# KRX 오픈API `stk_bydd_trd` 는 정규장 확정치를 하루 1회로 준다. 받으면 당일
# 행을 덮고, 못 받으면(미공표·인증키 없음) 네이버 값을 그대로 두되 **잠정으로
# 표시한다.** 조용히 잠정치를 확정치처럼 내보내지 않는다 (CLAUDE.md 2장 1·6번).
KRX_LOOKBACK_DAYS = 7

# 이 이상 달라야 '값이 바뀌었다' 로 센다. 그 아래는 반올림 차이다.
CLOSE_DIFF_PCT = 0.05


def krx_regular_day(conn, asof, log=print):
    """당일 정규장 확정 시세. `(day, reason)` 을 돌려준다.

    day 는 `dict(by_code=..., session=...)` 이고, 못 받으면 `(None, 사유)` 다.
    사유는 삼키지 않고 호출자가 run_log 에 남긴다.

    asof 에서 최대 KRX_LOOKBACK_DAYS 일을 뒤로 훑어 가장 최근 공표일을 찾되,
    **그 기준일이 asof 도 아니고 DB 의 마지막 장 세션도 아니면 쓰지 않는다.**
    그건 '오늘 것이 아직 안 나왔다' 는 뜻이고, 그 값으로 오늘 스냅을 덮으면
    보드가 조용히 어제 값으로 나간다.
    """
    if not creds.has('KRX_API_KEY'):
        return None, 'KRX_API_KEY 없음 — 네이버 잠정 종가로 진행'
    last = DB.last_asof(conn)
    d = date.fromisoformat(asof)
    rows = []
    try:
        s = krx.new_session()
        for _ in range(KRX_LOOKBACK_DAYS):
            rows = krx.fetch_day(d.isoformat(), s=s)
            if rows:
                break
            d -= timedelta(days=1)
        else:
            return None, (f'{asof} 이전 {KRX_LOOKBACK_DAYS}일 안에 공표된 '
                          '일별매매정보가 없다')
    except Exception as e:                              # noqa: BLE001
        return None, f'KRX 일별매매정보 실패: {str(e)[:160]}'
    day = d.isoformat()
    if day not in (asof, last):
        return None, (f'KRX 최신 공표일이 {day} 다 (기준일 {asof}) — 정규장 '
                      '확정치가 아직 안 나왔다')
    by_code = {x['code']: x for x in rows
               if x.get('close') and (not x.get('asof') or x['asof'] == day)}
    if not by_code:
        return None, f'{day} 응답에 쓸 수 있는 종목이 없다'
    # **기준일 것인지 과거 세션 것인지를 구분해 싣는다.**
    #
    # 예전에는 `day in (asof, last)` 만 보고 둘을 같게 다뤘다. 그런데 `last` 는
    # 첫 실행 시점에 거의 언제나 **전 영업일**이다(sync_px 전에 부르므로).
    # 그래서 KRX 가 오늘 분을 아직 안 낸 시각에 돌리면 어제 값이 게이트를 통과해
    # 오늘 스냅을 덮고 source='krx' 가 찍혔다 — 보드가 어제 값을 '종가 확정'
    # 이라고 내보내는 자리였다. 이 파일 독스트링이 막겠다고 적어 둔 바로 그것을
    # 코드가 막지 못했다.
    #
    # 이제 기준일 것이 아니면 **스냅샷은 건드리지 않는다.** 과거 세션 봉을
    # 확정치로 정정하는 데만 쓴다(_apply_krx_bar). 그러면 그날의
    # close_confirmed 는 False 로 남아 '잠정' 이라고 정직하게 나간다.
    is_asof = (day == asof)
    log(f'  KRX 정규장 확정치 {day} · {len(by_code):,}종목'
        + ('' if is_asof else f' — 기준일({asof}) 것이 아니라 과거 세션 봉 정정에만 쓴다'))
    return dict(by_code=by_code, session=day, is_asof=is_asof), None


def _apply_krx_snapshot(uni, day):
    """스냅샷 시세를 정규장 확정치로 덮는다. `(대조수, 바뀐수, 큰차이 예시)`.

    시가총액·거래대금도 함께 덮는다. KRX 는 거래대금을 실제로 주므로 그날의
    `turnover_is_estimate` 가 False 가 된다 — 네이버 목록은 못 줄 때가 있어
    종가x거래량 추정으로 채우고 있었다.
    """
    by = day['by_code']
    n, changed, big = 0, 0, []
    for code, x in uni.items():
        k = by.get(code)
        if not k:
            continue
        n += 1
        old = x.get('close')
        if old:
            d = (k['close'] / old - 1) * 100
            if abs(d) > CLOSE_DIFF_PCT:
                changed += 1
                if abs(d) > 3:
                    big.append(f"{x.get('name')} {d:+.1f}%")
        x['close'] = k['close']
        for f in ('chg_pct', 'volume', 'mktcap'):
            if k.get(f) is not None:
                x[f] = k[f]
        if k.get('turnover') is not None:
            x['turnover'] = k['turnover']
            x['turnover_is_estimate'] = False
        x['source'] = krx.SOURCE
    return n, changed, big


def _apply_krx_bar(code, rows, day, stat):
    """일봉의 **당일 행**을 정규장 확정치로 덮는다.

    스칼라(alltime)를 굴리기 **전에** 덮어야 한다. 뒤에 덮으면 사상 최고가·
    사상 최고 종가가 잠정치로 굴려진 채 남아 이튿날에도 안 고쳐진다.

    소스가 못 준 칸은 원래 값을 남긴다. None 으로 덮으면 일봉에 구멍이 뚫린다.
    """
    k = day['by_code'].get(code)
    if not k or not k.get('close'):
        return rows
    s = day['session']
    got = {f: k.get(f) for f in ('open', 'high', 'low', 'close', 'volume')
           if k.get(f) is not None}
    for i, r in enumerate(rows):
        if r['asof'] != s:
            continue
        old = r.get('close')
        merged = dict(r, **got, source=krx.SOURCE)
        if old and abs(merged['close'] / old - 1) * 100 > CLOSE_DIFF_PCT:
            stat['px_fixed'] += 1
        rows = list(rows)
        rows[i] = merged
        return rows
    # 네이버 일봉에 당일 행이 아예 없는 종목. KRX 가 준 확정치로 채운다.
    if rows and rows[-1]['asof'] < s:
        stat['px_added'] += 1
        c = k['close']
        return list(rows) + [dict(asof=s, open=k.get('open') or c,
                                  high=k.get('high') or c,
                                  low=k.get('low') or c, close=c,
                                  volume=k.get('volume') or 0,
                                  source=krx.SOURCE)]
    return rows


# ─────────────────────────── 전 종목 스냅샷 ───────────────────────────
def sync_universe(conn, asof, log=print, krx_day=None):
    """시세·시총·거래대금 스냅샷. 실패하면 그날 리포트 자체가 성립하지 않는다.

    ETF·ETN 은 여기서 뺀다. 신고가 보드는 주식 보드다. 채권형 ETF 는 이자가
    쌓여 매일 사상 최고가라 걸러내지 않으면 표를 통째로 점령한다
    (2026-08-27 실측: 역사적 신고가 49종목 중 44개가 채권형 ETF).
    """
    try:
        uni = naver.fetch_universe(log=log)
    except Exception as e:                              # noqa: BLE001
        DB.log_step(conn, asof, 'universe', False, str(e))
        raise

    etf = None
    try:
        etf = funds.fetch_etf_codes()
    except Exception as e:                              # noqa: BLE001
        # 목록을 못 받아도 이름 판정으로 계속한다. 다만 놓칠 수 있으므로 남긴다.
        DB.log_step(conn, asof, 'etf_list', False, str(e))
        log(f'  ETF 목록 실패 — 이름 판정으로 대체: {e}')
    stocks, dropped, how = funds.split(uni, etf)
    if dropped:
        log(f'  ETF·ETN {len(dropped):,}종목 제외 ({how["text"]})')
        # 이름으로 걸린 것은 오탐일 수 있다. 몇 개를 예시로 남겨 눈으로 보게 한다.
        sample = ', '.join(how['by_name'][:5])
        note = f'{len(dropped)}종목 제외 / {how["text"]}'
        if sample:
            note += f' (이름 예: {sample})'
        DB.log_step(conn, asof, 'funds_excluded', True, note)
    uni = stocks
    # 종가·시총·거래대금을 정규장 확정치로 덮는다 (D-080). krx_day 가 None 이면
    # 네이버 잠정치가 그대로 남고, 호출자가 그 사실을 run_log 에 적는다.
    # is_asof 가 아니면 과거 세션 값이다. 오늘 스냅을 그걸로 덮으면 보드가
    # 조용히 어제가 된다 — 잠정치보다 나쁜 실패다.
    if krx_day and not krx_day.get('is_asof'):
        why = (f"KRX 확정치가 {krx_day['session']} 분이라 기준일 {asof} 스냅샷에는 "
               '쓰지 않았습니다 (과거 세션 봉 정정에만 사용)')
        log(f'  {why}')
        DB.log_step(conn, asof, 'close_krx', False, why)
    elif krx_day:
        n, changed, big = _apply_krx_snapshot(uni, krx_day)
        note = (f'KRX 정규장 확정치로 {n:,}종목 교체 · 네이버 잠정치와 다른 종목 '
                f'{changed:,}' + (f" (3% 초과: {', '.join(big[:5])})" if big else ''))
        log(f'  {note}')
        DB.log_step(conn, asof, 'close_krx', True, note)
    conn.executemany(
        'INSERT OR REPLACE INTO snap(code,asof,name,market,close,chg_pct,volume,'
        'turnover,mktcap,turnover_is_estimate,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        [(x['code'], asof, x['name'], x['market'], x['close'], x['chg_pct'],
          x['volume'], x['turnover'], x['mktcap'],
          1 if x['turnover_is_estimate'] else 0, x['source']) for x in uni.values()])
    conn.commit()
    shrink = coverage_drop(conn, asof, len(uni))
    if shrink:
        DB.log_step(conn, asof, 'universe', False, shrink)
        log(f'  경고 — {shrink}')
    else:
        DB.log_step(conn, asof, 'universe', True, f'{len(uni)}종목')
    log(f'  전 종목 {len(uni):,}')
    return uni


# 직전 영업일 대비 이만큼 줄면 수집이 종목을 놓친 것으로 본다.
# 상장폐지·거래정지로 하루에 5% 넘게 줄 일은 없다.
COVERAGE_FLOOR = 0.95

# 절대 하한. 코스피·코스닥 상장사는 2010년대 이후 줄곧 2,000곳을 넘는다
# (코스피 약 950 + 코스닥 약 1,700). 펀드를 뺀 주식 수가 이보다 적으면
# 목록을 덜 받은 것이다.
#
# 어제와 비교하는 검사만으로는 이걸 못 잡는다 — 어제도 똑같이 덜 받았으면
# 변화가 없어서 조용하다. 실제로 LIG넥스원이 스냅샷에 없는데 경고가 안 떴다.
MIN_STOCKS = 2000


def coverage_drop(conn, asof, n_today):
    """전 종목 수가 직전 스냅샷보다 크게 줄었는지 본다.

    `fetch_universe` 는 페이지를 훑다가 빈 페이지를 만나면 멈춘다. 중간 한
    페이지가 일시적으로 비면 그 뒤가 통째로 빠지는데, **적게 받아도 예외가 나지
    않는다.** 보드는 그냥 종목이 적은 채로 정상 생성된다.

    실제로 2026-08-31 보드에서 LIG넥스원이 상장 종목 마스터에 없었다. 이름
    문제가 아니라 그날 스냅샷에 없던 것이다(D-051).

    "몇 개 받았나" 는 절대값으로 판단할 수 없다 — 상장 수는 계속 변한다.
    직전 영업일과 비교한다.
    """
    if n_today < MIN_STOCKS:
        # 어제와의 비교보다 먼저 본다. 매일 똑같이 덜 받고 있으면 비교로는
        # 영원히 안 걸린다.
        return (f'전 종목이 {n_today:,}종목뿐입니다. 코스피·코스닥 상장사는 '
                f'{MIN_STOCKS:,}곳을 넘습니다 — 시세 목록을 덜 받았습니다. '
                '빠진 종목은 신고가·랭킹·히트맵에서 통째로 빠집니다')
    row = conn.execute(
        'SELECT asof, COUNT(*) n FROM snap WHERE asof < ? '
        'GROUP BY asof ORDER BY asof DESC LIMIT 1', (asof,)).fetchone()
    if not row or not row['n']:
        return None                      # 비교할 어제가 없다. 첫 실행이다
    prev_asof, prev_n = row['asof'], row['n']
    if n_today >= prev_n * COVERAGE_FLOOR:
        return None
    return (f'전 종목 수가 {prev_asof} {prev_n:,}종목 → {n_today:,}종목으로 '
            f'{(1 - n_today / prev_n) * 100:.1f}% 줄었습니다. 시세 목록 수집이 '
            '중간에 끊겼을 수 있습니다 — 빠진 종목은 신고가·랭킹에서 통째로 '
            '빠집니다')


# ─────────────────────────── 일봉 ───────────────────────────
PX_SQL = 'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)'
ALLTIME_SQL = (
    'INSERT OR REPLACE INTO alltime(code,hi,hi_date,cl,cl_date,prev_hi,prev_cl,'
    'first_date,last_date,n_days,suspect,suspect_date,suspect_note,updated_at) '
    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)')


def sync_px(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
            krx_day=None):
    """일봉 적재 + 역사적 최고가 스칼라 갱신.

    **결과를 모아 두지 않고 종목 단위로 흘려 보낸다.** 전 구간을 받으면
    2,800종목 x 수천 봉이라 전부 들고 있으면 십수 GB가 되어 러너가 죽는다.
    받는 즉시 스칼라를 갱신하고 DB 에 넣고 버린다.

    keep_from 을 주면 그 날짜 이후 봉만 px 에 넣는다. 그 이전 구간은 스칼라를
    만드는 데만 쓰고 저장하지 않는다 — CLAUDE.md 4장이 말한 구조다.
    역사적 최고가는 스칼라가 들고 있으므로 잃는 정보가 없다.

    개별 종목 실패는 모아서 돌려준다. 하나 실패했다고 전체를 멈추지 않되
    무엇이 빠졌는지는 run_log 와 리포트에 남긴다.
    """
    s = session(referer='https://finance.naver.com/')
    prev = {r['code']: dict(r) for r in conn.execute('SELECT * FROM alltime')}
    now = DB.now_kst()
    stat = dict(bars=0, kept=0, stale=0, empty_keep=0, px_fixed=0, px_added=0)
    # 응답이 잘려 오는 경우를 잡는 가드. 소스가 오래된 구간부터 N행만 돌려주면
    # 최근 봉이 통째로 빠지는데, 그러면 리포트가 조용히 옛날 데이터로 나간다.
    # 마지막 봉이 요청 끝에서 이만큼 이상 떨어져 있으면 의심으로 센다.
    stale_days = 10
    px_buf, at_buf = [], []

    def flush():
        if px_buf:
            conn.executemany(PX_SQL, px_buf)
            px_buf.clear()
        if at_buf:
            conn.executemany(ALLTIME_SQL, at_buf)
            at_buf.clear()
        conn.commit()

    def take(code, rows):
        # 스칼라를 굴리기 전에 당일 행을 확정치로 덮는다 (D-080).
        if krx_day:
            rows = _apply_krx_bar(code, rows, krx_day, stat)
        stat['bars'] += len(rows)
        last = rows[-1]['asof'] if rows else None
        if last and (date.fromisoformat(end) - date.fromisoformat(last)).days > stale_days:
            stat['stale'] += 1
        keep = [r for r in rows if not keep_from or r['asof'] >= keep_from]
        if not keep:
            stat['empty_keep'] += 1
        stat['kept'] += len(keep)
        px_buf.extend((code, r['asof'], r['open'], r['high'], r['low'], r['close'],
                       r['volume'], r.get('source') or naver.SOURCE) for r in keep)
        a = nh.roll_alltime(prev.get(code), rows, cfg)
        at_buf.append((code, a['hi'], a['hi_date'], a['cl'], a['cl_date'],
                       a['prev_hi'], a['prev_cl'],
                       a['first_date'], a['last_date'], a['n_days'],
                       1 if a['suspect'] else 0, a['suspect_date'],
                       a['suspect_note'], now))
        prev.pop(code, None)                 # 다 쓴 이전 스칼라는 버린다
        if len(px_buf) >= 200_000:
            flush()

    ok, bad = gather(lambda c: naver.fetch_ohlcv(c, start, end, s), list(codes),
                     workers=workers, log=log, label='일봉', on_result=take)
    flush()
    log(f'  일봉 {stat["bars"]:,}행 수신 · {stat["kept"]:,}행 보관 · '
        f'종목 {len(ok):,} · 실패 {len(bad)}')
    if krx_day and (stat['px_fixed'] or stat['px_added']):
        log(f'  당일 봉을 KRX 정규장 확정치로 교체 — 종가가 달라진 종목 '
            f'{stat["px_fixed"]:,} · 네이버에 당일 봉이 없어 새로 채운 종목 '
            f'{stat["px_added"]:,}')
    if stat['stale'] or stat['empty_keep']:
        note = (f'마지막 봉이 {stale_days}일 이상 오래된 종목 {stat["stale"]:,} · '
                f'보관 구간에 봉이 하나도 없는 종목 {stat["empty_keep"]:,}')
        log(f'  경고 — {note}')
        # 절반을 넘으면 응답이 잘려 오는 것이지 거래정지가 아니다. 멈춘다.
        n = len(ok) or 1
        if stat['empty_keep'] > n * 0.5:
            raise Fetch(
                f'{note} — 전체의 절반을 넘는다. 소스가 요청 구간을 잘라서 '
                f'돌려주고 있을 가능성이 높다. 이대로 진행하면 리포트가 옛날 '
                f'데이터로 나간다. --years 로 구간을 좁혀서 다시 시도하라.')
        DB.log_step(conn, end, 'px_stale', False, note)
    return ok, bad


def prune(conn, keep_days=KEEP_DAYS):
    """오래된 일봉 정리. 역사적 최고가는 스칼라가 들고 있으므로 잃지 않는다."""
    cut = (today_kst() - timedelta(days=keep_days)).isoformat()
    n = conn.execute('DELETE FROM px WHERE asof<?', (cut,)).rowcount
    conn.commit()
    return n


# ─────────────────────────── 업종 ───────────────────────────
def sync_sectors(conn, asof, log=print):
    """종목 → 업종 역매핑. 1층 분류라 전 종목이 하나씩 배정돼야 한다."""
    try:
        idx = naver.fetch_sector_index()
    except Exception as e:                              # noqa: BLE001
        DB.log_step(conn, asof, 'sector', False, str(e))
        log(f'  업종 수집 실패: {e}')
        return 0
    ok, bad = gather(lambda g: naver.fetch_sector_members(g['no']), idx['sectors'],
                     workers=6, log=log, label='업종')
    now = DB.now_kst()
    buf = [(c, g['name'], idx['taxonomy'], now) for g, codes in ok for c in codes]

    # **board48 배정을 덮지 않는다.** 네이버 업종은 14개짜리 성긴 분류이고,
    # 1층 분류는 자체 48섹터다(CLAUDE.md 5장). 여기서 REPLACE 로 밀면 매일
    # daily 가 --classify 결과를 지운다 — 실제로 그랬다. 화면에는 "목표 분류는
    # board48 인데 실제로 붙어 있는 분류는 naver_upjong" 이 매일 떴고, 원인이
    # 분류를 안 돌려서인 줄 알기 쉬웠다. 분류는 돌아갔고 그다음 daily 가 지웠다.
    #
    # 아직 배정 안 된 종목(신규 상장 포함)에는 그대로 채운다. 그게 없으면
    # --classify 가 후보를 좁힐 힌트(krx_hint)도 없어진다.
    before = conn.execute(
        "SELECT COUNT(*) FROM sector_map WHERE taxonomy='board48'").fetchone()[0]
    conn.executemany(
        'INSERT INTO sector_map(code,sector,taxonomy,updated_at) VALUES(?,?,?,?) '
        'ON CONFLICT(code) DO UPDATE SET '
        '  sector=excluded.sector, taxonomy=excluded.taxonomy, '
        '  updated_at=excluded.updated_at '
        "WHERE sector_map.taxonomy IS NOT 'board48'", buf)
    conn.commit()
    kept = conn.execute(
        "SELECT COUNT(*) FROM sector_map WHERE taxonomy='board48'").fetchone()[0]
    DB.log_step(conn, asof, 'sector', not bad,
                f'{len(ok)}업종 {len(buf)}종목' + (f' / 실패 {len(bad)}' if bad else '')
                + (f' / board48 {kept:,}종목 유지' if kept else ''))
    log(f'  업종 {len(ok)}개 · 매핑 {len(buf):,}종목' + (f' · 실패 {len(bad)}' if bad else '')
        + (f' · board48 배정 {kept:,}종목은 그대로 둔다' if kept else ''))
    if before and kept < before:
        # 있어서는 안 되는 일이다. 조용히 넘기면 다음 보드가 성긴 분류로 나간다.
        raise RuntimeError(f'board48 배정이 {before:,} → {kept:,} 로 줄었다')
    return len(buf)


# ─────────────────────────── 지수·환율·수급 ───────────────────────────
def sync_market(conn, asof, log=print):
    """market.json 입력. 지수·환율은 받고 투자자별 수급은 아직 소스가 없다.

    KRX 투자자별 매매동향은 data.krx.co.kr 이 로그인을 요구하게 되면서 열쇠 없이는
    못 받는다 (etf_tracker_v9/README.md 참고). 추정으로 채우지 않고 비워 둔 채
    무엇이 빠졌는지 리포트 상단에 올린다 (CLAUDE.md 2장 1번·6번).
    """
    end = asof
    start = (date.fromisoformat(asof) - timedelta(days=20)).isoformat()
    # flows 를 None 으로 두면 아래 setdefault 가 기존 None 을 돌려줘 터진다.
    # 수집이 **성공했을 때만** 나는 오류라 지금까지 계속 실패해 온 탓에 안 드러났다.
    out = dict(as_of=asof, generated_at=datetime.now(KST).isoformat(timespec='seconds'),
               indices={}, fx=None, flows={},
               missing=[])
    for sym, label in (('KOSPI', '코스피'), ('KOSDAQ', '코스닥')):
        try:
            rows = naver.fetch_index(sym, start, end)
            cur = next((r for r in reversed(rows) if r['asof'] <= asof), None)
            pv = [r for r in rows if r['asof'] < (cur or {}).get('asof', '')]
            chg = (round((cur['close'] / pv[-1]['close'] - 1) * 100, 2)
                   if cur and pv and pv[-1]['close'] else None)
            # 레퍼런스 코멘트 첫 줄이 시가·장중저가·고가·종가를 다 쓴다.
            # ("6,727 출발 -> 6,704까지 밀렸다가 6,887 고점 찍고 6,808.21로 마감")
            out['indices'][sym] = dict(
                label=label, asof=cur and cur['asof'], source=naver.SOURCE,
                open=cur and cur.get('open'), high=cur and cur.get('high'),
                low=cur and cur.get('low'), close=cur and cur.get('close'),
                chg_pct=chg, streak=_streak(rows, (cur or {}).get('asof')))
        except Exception as e:                          # noqa: BLE001
            out['missing'].append(f'{label} 지수: {e}')
            DB.log_step(conn, asof, f'index:{sym}', False, str(e))
    # 투자자별 수급. 한국투자증권(KIS)을 먼저 쓰고, 안 되면 네이버 스크래핑으로
    # 떨어진다. 네이버 쪽은 2026-08-27 실행에서 표 파싱에 실패했다 — 페이지
    # 구조를 추정해 쓴 정규식이라 예상된 실패다. KIS 는 공식 API 라 이쪽이 본류다.
    for label, code in (('KOSPI', '0001'), ('KOSDAQ', '1001')):
        got, why = None, []
        if creds.has('KIS_APP_KEY', 'KIS_APP_SECRET'):
            try:
                got = kis.market_flows(code)
            except Exception as e:                      # noqa: BLE001
                why.append(f'KIS: {e}')
        else:
            why.append('KIS: 앱키 없음')
        if got is None:
            try:
                got = flows.fetch_market(label)
            except Exception as e:                      # noqa: BLE001
                why.append(f'네이버: {e}')
        if got is not None:
            out['flows'][label] = got
        else:
            out['missing'].append(f'{label} 투자자별 수급 — ' + ' / '.join(why))
            DB.log_step(conn, asof, f'flows:{label}', False, ' / '.join(why))
    if not out.get('flows'):
        out['flows'] = None

    try:
        out['fx'] = naver.fetch_fx()
    except Exception as e:                              # noqa: BLE001
        out['missing'].append(f'USD/KRW: {e}')
        DB.log_step(conn, asof, 'fx', False, str(e))

    log('  지수·환율 수집 완료'
        + ('' if out.get('flows') else ' (투자자별 수급 실패 — docs/APIS.md B1)'))
    return out


def _streak(rows, asof):
    """기준일까지 며칠 연속 상승(양수)인지 하락(음수)인지. 레퍼런스의 '이틀 연속 상승'."""
    seq = [r for r in rows if asof and r['asof'] <= asof]
    if len(seq) < 2:
        return 0
    n, sign = 0, None
    for a, b in zip(reversed(seq[:-1]), reversed(seq[1:])):
        if not a.get('close') or not b.get('close'):
            break
        d = b['close'] - a['close']
        s = 1 if d > 0 else (-1 if d < 0 else 0)
        if s == 0 or (sign is not None and s != sign):
            break
        sign, n = s, n + 1
    return n * (sign or 0)


# ─────────────────────────── 진입점 ───────────────────────────
def align_snapshot(conn, captured, log=print):
    """스냅샷을 **실제 장 세션 날짜**에 맞춘다.

    수집은 실행한 달력 날짜로 스냅을 쓴다. 거래일 장 마감 뒤에 돌리면 그 날짜가
    곧 세션 날짜라 문제가 없다. 그런데 휴장일(주말·공휴일)에 돌리면 네이버가
    돌려주는 값은 **직전 거래일 종가**인데 스냅은 오늘 날짜로 찍힌다.

    그러면 기준일(일봉의 마지막 날짜 = 금요일)보다 스냅이 뒤(토요일)에 있게 되고,
    `DB.snapshot()` 은 기준일 **이하**를 찾으므로 아무것도 못 찾는다. 2026-08-29
    토요일에 새로 적재한 DB 가 정확히 그래서 깨졌다(D-041).

    **이미 그 날짜의 스냅이 있으면 건드리지 않는다.** 장 마감 전 평일에 돌리면
    일봉의 마지막 날짜가 어제인데, 오늘 장중 값을 어제 종가 위에 덮어쓰면
    전일 종가가 오염된다. 그 경우는 `DB.snapshot()` 의 '이하' 규칙이 알아서
    어제 스냅을 골라 준다.
    """
    session = DB.last_asof(conn)
    if not session or session == captured:
        return None
    have = conn.execute('SELECT COUNT(*) FROM snap WHERE asof=?',
                        (session,)).fetchone()[0]
    if have:
        return None                    # 그 세션 스냅이 이미 있다. 덮지 않는다
    n = conn.execute('UPDATE snap SET asof=? WHERE asof=?',
                     (session, captured)).rowcount
    conn.commit()
    if n:
        log(f'  스냅샷 {n:,}종목을 {captured} → {session} 로 맞춤 '
            f'(휴장일 수집이라 값은 {session} 종가다)')
    return session if n else None


def init(conn, cfg, years=None, log=print):
    """최초 1회. 전 종목 일봉을 **상장 이후 전 구간** 받아 스칼라를 만든다.

    years 를 주면 그 구간만 받는다. 기본은 전 구간이다.

    왜 전 구간인가. 2년만 받으면 alltime 스칼라가 '2년 최고가'가 되고,
    '역사적 신고가' 라벨이 거짓말이 된다. 2026-08-27 실행에서 역사적 49 /
    52주 56 으로 거의 같게 나온 것이 그 증상이었다 — 정상이라면 역사적이
    훨씬 드물어야 한다.

    비용은 거의 같다. **HTTP 호출 수가 종목당 1회로 동일하고** 응답만 커진다.
    받은 뒤 최근 구간만 남기고 잘라내므로 DB 도 커지지 않는다 (CLAUDE.md 4장:
    스칼라로 저장하고 전체 일봉을 매일 재계산하지 않는다).
    """
    t = today_kst()
    asof = t.isoformat()
    day, why = krx_regular_day(conn, asof, log)
    if not day:
        log(f'  경고 — 정규장 확정 종가를 못 받았다: {why}')
        DB.log_step(conn, asof, 'close_krx', False, why)
    uni = sync_universe(conn, asof, log, krx_day=day)

    # 반드시 비우고 시작한다 (D-026). roll_alltime 은 last_date **이후** 행만
    # 반영하므로, 기존 스칼라가 남아 있으면 과거 구간을 아무리 받아도 전부
    # 걸러진다 — 전 구간을 내려받고도 값이 그대로인 채 끝난다. init 은 백필이고
    # 백필은 이 경로로만 된다.
    n_at = conn.execute('SELECT COUNT(*) FROM alltime').fetchone()[0]
    n_px = conn.execute('SELECT COUNT(*) FROM px').fetchone()[0]
    if n_at or n_px:
        log(f'  기존 스칼라 {n_at:,}종목 · 일봉 {n_px:,}행을 지우고 다시 쌓는다')
    conn.execute('DELETE FROM alltime')
    conn.execute('DELETE FROM px')
    conn.commit()

    start = ((t - timedelta(days=365 * years + 30)).isoformat() if years
             else FIRST_DAY)
    keep_from = (t - timedelta(days=KEEP_DAYS)).isoformat()
    log(f'  일봉 구간 {start} ~ {t} ({"전 구간" if not years else f"{years}년"}) · '
        f'{keep_from} 이후만 보관')
    ok, bad = sync_px(conn, uni.keys(), start, t.isoformat(), cfg, log=log,
                      keep_from=keep_from, krx_day=day)
    sync_sectors(conn, asof, log)
    if bad:
        DB.log_step(conn, asof, 'px', False, f'{len(bad)}종목 일봉 실패')
    else:
        DB.log_step(conn, asof, 'px', True, f'{len(ok)}종목')
    align_snapshot(conn, asof, log)
    DB.set_meta(conn, 'data_version', DATA_VERSION)
    return len(ok), len(bad)


def stale_reason(conn):
    """DB 가 지금 코드의 규약과 맞는지. 어긋나면 사유, 맞으면 None."""
    if not conn.execute('SELECT COUNT(*) FROM alltime').fetchone()[0]:
        return None                    # 빈 DB 는 stale 이 아니라 그냥 없는 것
    got = DB.get_meta(conn, 'data_version')
    if got is None:
        return (f'DB 에 data_version 이 없다 — v{DATA_VERSION} 이전 코드로 쌓은 '
                'DB 다. 단위와 스칼라 의미가 달라 그대로 쓰면 값이 틀린다')
    if str(got) != str(DATA_VERSION):
        return (f'DB 규약 v{got} · 코드 v{DATA_VERSION} — 다르다. '
                '단위나 스칼라 의미가 바뀌었으므로 다시 쌓아야 한다')
    return None


# 의심 종목을 다시 받을 때 한 번에 훑을 상한. 12종목쯤이 정상이고, 이보다
# 많으면 소스나 적재가 통째로 이상한 것이라 다시 받아도 소용이 없다.
MAX_REFRESH = 60


def suspect_codes(conn, cfg, asof):
    """저장된 시계열에서 split_guard 에 걸리는 종목 코드."""
    out = []
    # 한 종목씩 보고 버린다 — 전 종목을 동시에 들고 있을 이유가 없다.
    for code, ser in DB.iter_series(conn, asof):
        if nh.split_guard(ser, cfg['integrity']['split_guard_ratio'])[0]:
            out.append(code)
    return out


def refresh_suspects(conn, cfg, asof, log=print, krx_day=None):
    """수정주가 의심 종목의 일봉을 소스에서 다시 받아 덮는다.

    daily 는 최근 back_days(15일)만 다시 받는다. 그런데 액면병합·감자·무상증자가
    생기면 소스는 **과거 전체를** 다시 조정한다. 우리 DB 는 15일 밖을 그대로
    두므로 조정된 최근 구간과 조정 전 과거 구간 사이에 실재하지 않는 계단이
    생기고, split_guard 가 그걸 분할로 보고 종목을 역사적 판정에서 뺀다.

    2026-09-01 verify-adjust 가 이걸 드러냈다. 의심 12종목 중 5종목(091810 ·
    417310 · 286750 · 090410 · 032860)은 소스에서 전 구간을 새로 받으면 점프가
    아예 없다. 소스가 틀린 게 아니라 우리가 들고 있던 값이 낡은 것이었다.

    보관 구간 전체를 다시 받아 덮고, 그래도 남는 종목만 진짜 의심으로 둔다.
    의심 종목만 대상이라 호출량은 십여 건이다.

    반환 (해소된 수, 남은 수). 다시 받을 게 없으면 (0, 0).
    """
    before = suspect_codes(conn, cfg, asof)
    if not before:
        return 0, 0
    if len(before) > MAX_REFRESH:
        # 조용히 넘기지 않는다. 이 수가 나오면 적재 자체를 봐야 한다.
        log(f'  수정주가 의심 {len(before):,}종목 — {MAX_REFRESH}종목을 넘어 '
            '다시 받지 않았습니다. 적재 경로를 확인하세요')
        DB.log_step(conn, asof, 'px_refresh', False,
                    f'의심 {len(before)}종목으로 상한 초과 — 재수집 생략')
        return 0, len(before)
    log(f'  수정주가 의심 {len(before)}종목 — 상장 이후 전 구간을 다시 받습니다')
    bad = _refetch_full(conn, cfg, asof, before, log, krx_day)
    after = suspect_codes(conn, cfg, asof)
    cleared = len(before) - len(after)
    note = f'{len(before)}종목 중 {cleared}종목 해소, {len(after)}종목 남음'
    if bad:
        note += f' / 재수집 실패 {len(bad)}'
    log(f'  → {note}')
    DB.log_step(conn, asof, 'px_refresh', True, note)
    return cleared, len(after)


def _refetch_full(conn, cfg, asof, codes, log=print, krx_day=None):
    """스칼라와 일봉을 지우고 상장 이후 전 구간을 받아 **한 fetch 로** 다시 세운다.

    보관 구간만 받으면 안 된다. roll_alltime 은 last_date 이후 행만 반영하므로
    (그 독스트링이 "백필은 --init 으로" 라고 적어 둔 그대로) 스칼라가 낡은 값
    그대로 남는다. 스칼라만 지워도 안 된다. INSERT OR REPLACE 는 새 응답에 있는
    날짜만 덮으므로 응답이 짧게 오면 옛 px 행이 남아, 스칼라는 짧은 구간의
    최고가가 되고 창 최고가는 옛 행에서 나온다 — '역사적 최고가 < 52주 최고가'
    라는 정의상 불가능한 상태다. 실측에서 090410 이 42% 낮았다(D-059).

    둘 다 지우고 한 번의 fetch 에서만 만들면 어긋날 자리가 없다.
    px 에는 보관 구간만 남긴다(keep_from) — 4장이 말한 구조 그대로다.
    """
    conn.executemany('DELETE FROM alltime WHERE code=?', [(c,) for c in codes])
    conn.executemany('DELETE FROM px WHERE code=?', [(c,) for c in codes])
    conn.commit()
    keep_from = (today_kst() - timedelta(days=KEEP_DAYS)).isoformat()
    # krx_day 를 같이 넘긴다. 안 넘기면 재수집이 방금 확정치로 덮어 둔 당일
    # 종가를 네이버 잠정치로 되돌린다 — 그 종목만 조용히 규칙이 달라진다.
    _, bad = sync_px(conn, codes, FIRST_DAY, asof, cfg, workers=4, log=log,
                     keep_from=keep_from, krx_day=krx_day)
    if bad:
        # 지웠는데 못 받았으면 그 종목은 그날 보드에서 빠진다. 조용히 넘기면
        # 어제까지 있던 종목이 이유 없이 사라진다.
        names = ', '.join(str(c) for c, _ in bad[:5])
        DB.log_step(conn, asof, 'px_refresh_lost', False,
                    f'{len(bad)}종목을 다시 받지 못해 일봉이 비었습니다: {names}')
        log(f'  경고 — {len(bad)}종목 재수집 실패, 일봉이 비었습니다: {names}')
    return bad


def broken_alltime_codes(conn):
    """스칼라가 자기 일봉과 모순인 종목 — alltime.hi < px 최고가.

    상장 이후 전체 최고가가 보관 중인 어떤 봉의 고가보다 낮을 수는 없다.
    낮다면 스칼라와 일봉이 서로 다른 fetch 에서 나온 것이다.
    """
    return [r['code'] for r in conn.execute(
        'SELECT a.code FROM alltime a '
        'JOIN (SELECT code, MAX(high) mx FROM px GROUP BY code) p '
        '  ON p.code = a.code '
        'WHERE a.hi IS NOT NULL AND p.mx > a.hi * 1.000001')]


def repair_alltime(conn, cfg, asof, log=print, krx_day=None):
    """모순인 스칼라를 재수집으로 고친다.

    D-059 를 고친 뒤에도 세 종목이 남았다 — 고치기 **전에** 이미 어긋나 버린
    스칼라는 refresh_suspects 가 다시 보지 않는다. 그 함수는 지금 계단이 있는
    종목만 보는데, 이들은 계단이 이미 지워져 안 걸리고, 낡은 스칼라만 캐시
    DB 에 영구히 남는다. 원인이 무엇이었든 **어긋남 자체**를 수리 트리거로
    삼아야 과거의 손상이 스스로 낫는다.

    반환 (고친 수, 남은 수).
    """
    codes = broken_alltime_codes(conn)
    if not codes:
        return 0, 0
    if len(codes) > MAX_REFRESH:
        log(f'  스칼라 모순 {len(codes):,}종목 — {MAX_REFRESH}종목을 넘어 다시 '
            '받지 않았습니다. 적재 경로를 확인하세요')
        DB.log_step(conn, asof, 'alltime_repair', False,
                    f'모순 {len(codes)}종목으로 상한 초과 — 재수집 생략')
        return 0, len(codes)
    log(f'  역사적 최고가 스칼라가 일봉과 모순인 {len(codes)}종목 — 다시 세웁니다')
    _refetch_full(conn, cfg, asof, codes, log, krx_day)
    left = broken_alltime_codes(conn)
    note = f'{len(codes)}종목 중 {len(codes) - len(left)}종목 수리, {len(left)}종목 남음'
    log(f'  → {note}')
    DB.log_step(conn, asof, 'alltime_repair', not left, note)
    return len(codes) - len(left), len(left)


def confirm_suspects(conn, cfg, asof, log=print):
    """다시 받고도 남은 의심 종목에 대해 공시로 사유를 확정한다 (D-056).

    시세만 봐서는 갈리지 않는다. `split_guard` 는 하루 ±31% 를 잡을 뿐,
    그게 주식 수가 바뀐 계단인지 그날 실제로 크게 움직인 것인지 모른다.
    2026-09-01 실측에서 12종목 중 5종목은 감자·병합·무상증자 공시가 있었고
    007610 은 두 소스가 완전히 일치하는데 아무 공시도 없었다.

    공시가 있으면 가드를 유지하고, 없으면 풀어 준다. **조회에 실패하면 풀지
    않는다** — 못 본 것과 없는 것은 다르다.

    반환 (공시 있음, 공시 없음, 판단 못 함).
    """
    from . import dart
    left = suspect_codes(conn, cfg, asof)
    if not left:
        return 0, 0, 0
    done = {r['code']: r['jump_date'] for r in conn.execute(
        'SELECT code, jump_date FROM split_check')}
    # 의심 종목만 다시 읽는다. left 는 보통 열 몇 개다.
    ser_all = DB.series_for(conn, left, asof)
    ratio = cfg['integrity']['split_guard_ratio']
    rows, n_act, n_none, n_unk = [], 0, 0, 0
    for code in left:
        _, day, note, _ = nh.split_guard(ser_all.get(code) or [], ratio)
        if not day:
            continue
        if done.get(code) == day:
            continue                      # 같은 점프는 다시 묻지 않는다
        try:
            acts = dart.stock_actions(code, *_action_window(day))
        except Exception as ex:           # noqa: BLE001
            rows.append((code, day, 'unknown', f'공시 조회 실패: {str(ex)[:120]}'))
            n_unk += 1
            continue
        if acts:
            rows.append((code, day, 'action',
                         ' / '.join(f'{a["date"]} {a["title"]}' for a in acts[:3])))
            n_act += 1
        else:
            rows.append((code, day, 'none',
                         f'{note} — ±{dart.ACTION_WINDOW}일 안에 분할·병합·감자·'
                         '무상증자 공시가 없다'))
            n_none += 1
    if rows:
        DB.put_split_check(conn, rows)
        DB.log_step(conn, asof, 'split_check', True,
                    f'공시 있음 {n_act} · 없음 {n_none} · 판단 못 함 {n_unk}')
        log(f'  의심 종목 공시 대조 — 있음 {n_act} · 없음 {n_none} · '
            f'판단 못 함 {n_unk}')
    return n_act, n_none, n_unk


def _action_window(day):
    """점프일 앞뒤로 볼 구간. 결정 공시가 앞서고 변경상장이 뒤따른다."""
    from . import dart
    d = date.fromisoformat(day)
    w = timedelta(days=dart.ACTION_WINDOW)
    return (d - w).isoformat(), (d + w).isoformat()


def daily(conn, cfg, back_days=15, log=print):
    """매 영업일. 스냅샷 + 최근 일봉만 증분 적재한다."""
    t = today_kst()
    asof = t.isoformat()
    day, why = krx_regular_day(conn, asof, log)
    if not day:
        # 삼키지 않는다. 이 메모가 보드 배너에 '잠정' 으로 그대로 올라간다.
        log(f'  경고 — 정규장 확정 종가를 못 받았다: {why}')
        DB.log_step(conn, asof, 'close_krx', False, why)
    uni = sync_universe(conn, asof, log, krx_day=day)
    start = (t - timedelta(days=back_days)).isoformat()
    ok, bad = sync_px(conn, uni.keys(), start, t.isoformat(), cfg, log=log,
                      krx_day=day)
    DB.log_step(conn, asof, 'px', not bad,
                f'{len(ok)}종목' + (f' / 실패 {len(bad)}' if bad else ''))
    align_snapshot(conn, asof, log)
    # 자르기 전에 한다. prune 뒤에 하면 다시 받은 구간이 곧바로 잘린다.
    refresh_suspects(conn, cfg, asof, log, krx_day=day)
    # 예전 재수집이 남긴 모순(스칼라 < 일봉 최고가)도 고친다. 계단은 이미
    # 지워져 refresh_suspects 에 안 걸리는데 스칼라만 낡은 채 남은 경우다.
    repair_alltime(conn, cfg, asof, log, krx_day=day)
    # 다시 받고도 남은 것만 공시에 묻는다. 낡은 값이 원인이면 위에서 이미
    # 사라지므로 DART 호출은 진짜 의심 종목에만 든다.
    confirm_suspects(conn, cfg, asof, log)
    prune(conn)
    return len(ok), len(bad)

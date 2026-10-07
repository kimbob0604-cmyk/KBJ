#!/usr/bin/env python3
"""
미국장 수집 파이프라인 — 소스 → DB.

  --us-init   유니버스 전 종목의 과거 일봉을 한 번 쌓는다 (2년, 30~60분)
  --us-daily  스냅샷 + 최근 구간 일봉만 갱신한다

기준일은 **받은 일봉이 알려 준다.** 달력으로 '어제' 를 계산하면 휴장일·서머타임
·한국 시각 기준의 날짜 밀림이 전부 여기서 터진다. 종목마다 마지막 봉 날짜를
세어 가장 많은 날짜를 기준일로 삼고, 그 날짜를 쓰는 종목 수를 함께 남긴다.
"""
import concurrent.futures as cf
import time
from collections import Counter

from ..ingest import http as H
from . import db as DB
from . import sectors as SEC
from . import sources as S


def universe(cfg, log=print, limit=None):
    """스크리너 스냅샷 → 섹터 배정 → 하한 통과 종목.

    limit 은 **첫 실행·시험용 상한**이다. 거래대금 상위부터 자른다 — 무작위로
    자르면 표가 아무 뜻도 없어진다. 자른 사실은 호출자가 배너에 올린다.
    유니버스를 줄이면 브레드스·중앙값이 전체 시장이 아니라 그 부분집합의 것이
    되므로, 자른 채로 낸 브리프는 '전체 시장' 이라고 읽으면 안 된다.
    """
    rows, drop = S.screener(cfg)
    SEC.apply(rows)
    u = cfg['universe']
    keep = [r for r in rows
            if (r.get('mktcap') or 0) >= u['min_mktcap_usd']
            and (r.get('close') or 0) >= u['min_price_usd']
            and (r.get('turnover') or 0) >= u['min_turnover_usd']]
    # 미분류는 **쓰는 유니버스 기준**으로 센다. 3,129종목 전체로 세면 표에
    # 오르지도 않는 종목이 배너를 채운다 — run #3 의 '161종목' 이 그랬다.
    # 원인도 가른다: 소스가 분류를 안 준 것과 우리 규칙이 못 푼 것은 할 일이 다르다.
    n_unmapped = sum(1 for r in keep if r.get('sector') == SEC.UNMAPPED)
    n_blank = sum(1 for r in keep if r.get('sector') == SEC.UNMAPPED
                  and not (r.get('industry_raw') or r.get('sector_raw')))
    log(f'  스크리너 {len(rows)}종목 → 하한 통과 {len(keep)} '
        f'(제외 {drop} · 섹터 미분류 {n_unmapped} 중 소스 공란 {n_blank})')
    truncated = None
    if limit and len(keep) > limit:
        keep.sort(key=lambda r: -(r.get('turnover') or 0))
        truncated = dict(full=len(keep), kept=limit)
        keep = keep[:limit]
        log(f'  ** 유니버스를 거래대금 상위 {limit}종목으로 잘랐다 '
            f'(원래 {truncated["full"]}종목) — 전체 시장 수치가 아니다')
    return rows, keep, dict(dropped=drop, unmapped=n_unmapped, blank=n_blank,
                            truncated=truncated, by_source=SEC.by_source(keep),
                            unmapped_rows=SEC.unmapped_industries(keep),
                            unmapped_all=SEC.unmapped_industries(rows))


def fetch_history(tickers, cfg, start=None, log=print):
    """일봉 병렬 수집. 실패는 삼키지 않고 사유와 함께 모아 돌려준다."""
    out, fails = {}, []
    workers = cfg['sources'].get('workers', 8)
    pause = cfg['sources'].get('pause_sec', 0.0)
    retries = cfg['sources'].get('retries', 2)

    def one(t):
        for attempt in range(retries + 1):
            try:
                sess = H.session()
                return t, S.history(t, cfg, sess, start), None
            except Exception as e:                    # noqa: BLE001
                if attempt >= retries:
                    return t, None, H.scrub(e)[:160]
                time.sleep(pause * (attempt + 1) + 0.5)
        return t, None, '재시도 소진'

    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for t, bars, why in ex.map(one, tickers):
            done += 1
            if bars:
                out[t] = bars
            else:
                fails.append((t, why or '빈 응답'))
            if done % 200 == 0:
                log(f'    {done}/{len(tickers)} 수집 (실패 {len(fails)})')
    return out, fails


def asof_from(series):
    """가장 많은 종목이 공유하는 마지막 봉 날짜. (기준일, 그 날짜를 쓰는 종목 수)"""
    c = Counter(bars[-1]['asof'] for bars in series.values() if bars)
    if not c:
        return None, 0
    asof, n = c.most_common(1)[0]
    return asof, n


def store(conn, series, snaps, asof, log=print):
    for t, bars in series.items():
        DB.put_px(conn, t, bars)
    conn.commit()
    if snaps:
        DB.put_snap(conn, asof, snaps)
    log(f'  DB 적재 — 일봉 {sum(len(b) for b in series.values()):,}행 · '
        f'스냅샷 {len(snaps or [])}종목')


def init(conn, cfg, years=2, log=print, limit=None):
    """최초 1회. 유니버스 전 종목의 과거 일봉."""
    from datetime import date, timedelta
    all_rows, keep, meta = universe(cfg, log, limit)
    start = (date.today() - timedelta(days=int(365.25 * years) + 30)).isoformat()
    log(f'  일봉 수집 {len(keep)}종목 · {start} 이후')
    series, fails = fetch_history([r['ticker'] for r in keep], cfg, start, log)
    asof, n = asof_from(series)
    if not asof:
        raise SystemExit('일봉을 한 종목도 못 받았다. --us-check 로 소스를 확인하라.')
    log(f'  기준일 {asof} ({n}/{len(series)}종목이 이 날짜를 마지막 봉으로 가진다)')
    snaps = {r['ticker']: r for r in keep}
    store(conn, series, list(snaps.values()), asof, log)
    DB.set_meta(conn, 'data_version', DB.DATA_VERSION)
    DB.set_meta(conn, 'init_at', DB.now_utc())
    DB.log_step(conn, asof, 'init', not fails,
                f'{len(series)}종목 · 실패 {len(fails)}')
    return asof, fails, meta


def daily(conn, cfg, log=print, limit=None):
    """매일. 스냅샷 + 일봉.

    이미 이력을 가진 종목은 **최근 구간만** 다시 받는다. 소스가 지난 며칠 값을
    사후에 고치는 일이 있어서, 겹쳐 받아 덮어쓰면 DB 가 스스로 복구된다.

    **처음 보는 종목은 전 구간을 받는다.** 안 그러면 90일치만 들고 52주(252거래일)
    판정을 못 해 그 종목은 며칠이고 라벨이 안 뜬다. run #4 가 정확히 그랬다 —
    유니버스를 300 → 1,719 로 늘린 날, 새로 들어온 1,400여 종목이 90일치뿐이라
    ②의 5일 추이가 날짜마다 다른 집합을 세고 오늘만 91, 어제는 8 이 됐다.
    같은 표의 칸들이 서로 다른 유니버스를 세면 그 표는 추이가 아니다.
    """
    from datetime import date, timedelta
    all_rows, keep, meta = universe(cfg, log, limit)
    tickers = [r['ticker'] for r in keep]

    # 52주 창(252거래일) + 5일 추이 + 여유. 이만큼 없으면 새로 받는다.
    need = cfg['newhigh']['lookback']['w52'] + cfg['brief']['trend_days'] + 10
    have = DB.bar_counts(conn)
    fresh = [t for t in tickers if (have.get(t) or {}).get('n', 0) < need]
    known = [t for t in tickers if t not in set(fresh)]
    recent = (date.today() - timedelta(days=90)).isoformat()
    deep = (date.today() - timedelta(days=760)).isoformat()
    log(f'  일봉 — 최근 구간 {len(known)}종목 · 전 구간 {len(fresh)}종목'
        f'(이력 {need}봉 미만)')

    series, fails = {}, []
    for group, start in ((known, recent), (fresh, deep)):
        if not group:
            continue
        got, bad = fetch_history(group, cfg, start, log)
        series.update(got)
        fails.extend(bad)
    asof, n = asof_from(series)
    if not asof:
        raise SystemExit('일봉을 한 종목도 못 받았다.')
    log(f'  기준일 {asof} ({n}/{len(series)}종목)')
    store(conn, series, keep, asof, log)
    # 아직도 창을 못 채운 종목은 52주 판정이 보류된다. 숨기지 않는다.
    #
    # **DB 를 보고 센다.** 이번에 받은 `series` 로 세면 안 된다 — 이미 이력을
    # 가진 종목은 최근 90일만 겹쳐 받으므로 전부 '이력 부족'으로 잡힌다.
    # run #9 가 그랬다: 적재 직후인데 1,721종목 전부가 보류라고 적혔고,
    # 정작 같은 브리프의 ②는 52주 신고가 63종목을 세고 있었다.
    have_after = DB.bar_counts(conn)
    short = sum(1 for t in tickers if (have_after.get(t) or {}).get('n', 0) < need)
    DB.log_step(conn, asof, 'daily', not fails,
                f'{len(series)}종목 · 실패 {len(fails)}')
    missing = []
    if fails:
        head = ', '.join(f'{t}: {why}' for t, why in fails[:3])
        more = f' 외 {len(fails) - 3}종목' if len(fails) > 3 else ''
        missing.append(f'일봉 수집 실패 {len(fails)}종목 — {head}{more}')
    if meta['unmapped']:
        blank = meta.get('blank') or 0
        why = (f' (그중 {blank}종목은 소스가 섹터·산업을 비워서 보냈다 — 규칙으로는 못 푼다)'
               if blank else '')
        missing.append(f'섹터 미분류 {meta["unmapped"]}종목{why}')
    if short:
        missing.append(f'이력이 짧아 52주 판정 보류 {short}종목 '
                       f'(상장 후 {need}거래일을 못 채웠거나 소스가 과거를 안 준다)')
    if meta.get('truncated'):
        t = meta['truncated']
        missing.append(f'**유니버스를 거래대금 상위 {t["kept"]}종목으로 잘랐다** '
                       f'(하한 통과는 {t["full"]}종목). 브레드스·중앙값은 전체 시장이 아니다')
    return asof, missing, meta

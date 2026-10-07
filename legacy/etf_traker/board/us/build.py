#!/usr/bin/env python3
"""
미국장 state 조립 — DB → state_us/YYYYMMDD/*.json.

국장과 같은 규약이다. 단계 사이는 파일로만 넘어가고, 다음 단계(브리프·HTML)는
DB 를 전혀 모른다 (CLAUDE.md 3장).

  universe.json   유니버스 전 종목의 기준일 지표
  board.json      브리프 ①~⑧ 이 읽는 값 전부. 화면도 이 파일 하나만 읽는다

②·③ 의 '추이' 는 저장된 라벨 표가 아니라 **일봉에서 그날그날 다시 판정해서**
만든다. 라벨 표에 기대면 보드를 처음 돌린 날에는 추이가 빈칸이고, 하루라도
거르면 그 자리가 영원히 빈다. 일봉은 지나간 날도 다시 판정할 수 있다.
다만 유니버스는 **기준일 기준 하나**다 — 과거 각 날짜의 시총·거래대금을
따로 들고 있지 않으므로 그날의 유니버스를 복원할 수 없다. 이 사실을
board.json 의 `trend_note` 에 적는다 (2장 1번).
"""
import json
import os
from datetime import datetime, timezone

from ..engine.config import ROOT
from . import engine as E

STATE = os.path.join(ROOT, 'state_us')


def state_dir(asof, make=True):
    d = os.path.join(STATE, asof.replace('-', ''))
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def write(asof, name, payload):
    p = os.path.join(state_dir(asof), name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return p


def write_text(asof, name, text):
    p = os.path.join(state_dir(asof), name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


def read_text(asof, name):
    p = os.path.join(state_dir(asof, make=False), name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return f.read()


def read(asof, name):
    p = os.path.join(state_dir(asof, make=False), name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ─────────────────────────── 추이 ───────────────────────────
def day_stats(series, tickers, asof, cfg, floors=None):
    """그 하루의 브레드스·신고가 수. 일봉만 읽는다.

    floors 는 티커별 수정주가 가드 지점이다. 날짜마다 다시 계산하면 5일치를
    다섯 번 훑게 되고, 무엇보다 ①과 같은 값을 써야 두 절이 같은 수를 말한다.
    """
    floors = floors if floors is not None else {}
    nh = w52 = low = 0
    chgs = []
    for t in tickers:
        rows = series.get(t) or []
        i = next((k for k, r in enumerate(rows) if r['asof'] == asof), None)
        if i is None:
            continue
        h = E.hits_on(rows, i, cfg, floor=floors.setdefault(t, E.guard_floor(rows, cfg)))
        if h['kind']:
            nh += 1
        if h['kind'] == 'w52':
            w52 += 1
        if h['low52']:
            low += 1
        if i > 0 and rows[i - 1].get('close') and rows[i].get('close') is not None:
            chgs.append((rows[i]['close'] / rows[i - 1]['close'] - 1) * 100)
    up = sum(1 for c in chgs if c > 0)
    dn = sum(1 for c in chgs if c < 0)
    known = up + dn + sum(1 for c in chgs if c == 0)
    return dict(asof=asof, newhigh=nh, w52_high=w52, w52_low=low,
                up=up, down=dn,
                up_ratio=round(up / known * 100, 1) if known else None,
                median=E.median(chgs))


def trend(series, tickers, days, cfg):
    """② 5일 흐름. 최근 days 거래일을 옛날→오늘 순으로."""
    seen = set()
    for t in tickers:
        for r in (series.get(t) or [])[-days * 3:]:
            seen.add(r['asof'])
    last = sorted(seen)[-days:]
    floors = {}
    return [day_stats(series, tickers, d, cfg, floors) for d in last]


def sector_trend(series, rows, days, cfg):
    """③ 섹터별 신고가 수 추이. 섹터는 기준일 배정을 그대로 쓴다."""
    sec_of = {r['ticker']: (r.get('sector') or '미분류') for r in rows}
    seen = set()
    for t in sec_of:
        for r in (series.get(t) or [])[-days * 3:]:
            seen.add(r['asof'])
    last = sorted(seen)[-days:]
    out, floors = {}, {}
    for d in last:
        for t, sec in sec_of.items():
            bars = series.get(t) or []
            i = next((k for k, r in enumerate(bars) if r['asof'] == d), None)
            if i is None:
                continue
            floor = floors.setdefault(t, E.guard_floor(bars, cfg))
            if E.hits_on(bars, i, cfg, floor=floor)['kind']:
                out.setdefault(sec, {}).setdefault(d, 0)
                out[sec][d] += 1
    return {'days': last,
            'counts': {sec: [out[sec].get(d, 0) for d in last] for sec in out}}


# ─────────────────────────── 조립 ───────────────────────────
def build(series, snaps, asof, cfg, missing=None, log=print):
    """계산 전부. 파일 입출력은 run() 이 한다 — 시험이 이 함수를 그대로 부른다."""
    rows, skipped = E.evaluate_all(series, snaps, asof, cfg, log=log)
    full = rows
    rows = [r for r in rows if E.in_universe(r, cfg)]
    log(f'  유니버스 {len(rows)}종목 (수집 {len(full)} · 하한 미달 {len(full) - len(rows)})')

    tickers = [r['ticker'] for r in rows]
    days = cfg['brief']['trend_days']
    tr = trend(series, tickers, days, cfg)
    prev = tr[-2] if len(tr) >= 2 else None

    secs = E.sector_table(rows, cfg)
    st = sector_trend(series, rows, days, cfg)
    for s in secs:
        s['trend'] = st['counts'].get(s['sector'], [0] * len(st['days']))

    my = cfg['brief'].get('my_tickers') or []
    mine = [r for r in rows if r['ticker'] in set(my)]
    my_nh = [r for r in rows if r.get('label') and r['ticker'] in set(my)]

    board = dict(
        asof=asof, generated_at=now_iso(),
        source=dict(universe=cfg['sources']['universe'],
                    history=cfg['sources']['history']),
        basis=cfg['newhigh']['default_basis'],
        universe=dict(n=len(rows), collected=len(full), skipped=skipped,
                      filters=dict(min_mktcap_usd=cfg['universe']['min_mktcap_usd'],
                                   min_turnover_usd=cfg['universe']['min_turnover_usd'],
                                   min_price_usd=cfg['universe']['min_price_usd'])),
        summary=E.summary(rows, cfg, prev),
        trend=tr,
        trend_note=('추이의 유니버스는 기준일 하나다 — 과거 날짜의 시총·거래대금을 '
                    '따로 들고 있지 않아 그날의 유니버스를 복원하지 않는다'),
        sectors=secs,
        sector_trend_days=st['days'],
        tiers=E.cap_tiers(rows, cfg),
        mega=E.mega_movers(rows, cfg),
        leaders=E.leaders(rows, cfg),
        streaks=E.streaks(rows, cfg),
        fresh52=E.fresh52(rows, cfg),
        movers=dict(up=E.industry_groups(rows, cfg, 'up'),
                    down=E.industry_groups(rows, cfg, 'down')),
        my=dict(label=cfg['brief'].get('my_label'), tickers=my,
                n_newhigh=len(my_nh), rows=mine),
        missing=list(missing or []))
    return rows, board


def run(db_path, asof=None, cfg=None, log=print):
    from ..engine.config import load
    from . import db as DB
    cfg = cfg or load(os.path.join(ROOT, 'config', 'us.yaml'))
    conn = DB.connect(db_path)
    asof = asof or DB.last_asof(conn)
    if not asof:
        raise SystemExit('일봉이 비어 있다. --us-init 을 먼저 돌려라.')
    log(f'미국장 state 조립 — 기준일 {asof}')

    snaps = DB.snapshot(conn, asof)
    series = DB.series_for(conn, None, asof)
    missing = []
    if not snaps:
        missing.append(f'{asof} 스냅샷 없음 — 시총·섹터를 모른다. 유니버스 하한을 걸 수 없다')
    rows, board = build(series, snaps, asof, cfg, missing=missing, log=log)

    write(asof, 'universe.json', dict(asof=asof, generated_at=now_iso(),
                                      source='us-board', rows=rows))
    write(asof, 'board.json', board)

    # 라벨을 DB 에도 남긴다. 화면은 state 만 읽지만 '어제 라벨' 질의는 DB 가 빠르다.
    basis = cfg['newhigh']['default_basis']
    DB.put_labels(conn, asof, basis,
                  [(r['ticker'], r['label'],
                    cfg['newhigh']['priority'].index(r['label']))
                   for r in rows if r.get('label')])
    DB.put_lows(conn, asof, basis, [r['ticker'] for r in rows if r.get('low52')])
    DB.log_step(conn, asof, 'build', True, f'{len(rows)}종목')
    log(f'  state_us/{asof.replace("-", "")}/ 에 기록')
    return asof, board

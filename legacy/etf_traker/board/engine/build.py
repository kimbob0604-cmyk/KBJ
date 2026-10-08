#!/usr/bin/env python3
"""
엔진 오케스트레이션 — DB 를 읽어 state/YYYYMMDD/*.json 을 만든다.

KBJ P3 묶음 E1(docs/p3_design.md §1.3·§4.1, D-P3-10): **계산은 `kbj.engines.board.build.compute_day`
한 곳이다.** 이 모듈은 legacy 의 I/O 만 남긴 shim 이다 — sqlite(`DB`)에서 엔진이 읽던 것을 모아
`BoardInputs` 를 만들고, `compute_day` 를 부르고, 결과를 label 표·run_log·state 파일로 쓴다.
골든(tests/golden/board)은 이 shim 전의 계산으로 캡처했고 kbj 산출이 그것과 같음을 시험한다.

CLAUDE.md 3장 계약: 각 단계는 JSON 파일로 결과를 남기고 다음 단계는 그 파일만
읽는다. 단계 간 함수 호출로 데이터를 넘기지 않는다. 그래서 render 는 DB 를
전혀 모르고 JSON 만 본다. 재실행·디버깅이 쉬워지는 대신 파일이 늘어난다.

산출:
  universe.json  전 종목 시세·시총·거래대금 + 업종·테마 매핑
  newhigh.json   신고가 달성·근접 (이 대시보드의 본체)
  sectors.json   업종·테마 집계와 히트맵 입력
  events.json    가격·거래량 기반 탐지기 출력
  rankings.json  랭킹 표
"""
import json
import os
from datetime import timedelta, timezone

from kbj.core.time import now_kst as _kbj_now_kst
from kbj.engines.board.build import (  # noqa: F401 — legacy 모듈·시험이 예전 이름으로 쓴다
    BIG_CAP_EOK, CLOSE_CONFIRM_MIN, CONSIST_EX, KRX_SOURCE, MAX_PREV_GAP_DAYS,
    MIN_BIG_CAP_TURNOVER, BoardInputs, achieved_rows, by_mktcap, close_label, close_provenance,
    compute_day, consistency_notes, high_label, prev_near_from, unit_sanity)

from . import db as DB
from . import newhigh as nh
from . import rankings as RK
from .config import ROOT, load, themes as load_themes

KST = timezone(timedelta(hours=9))
STATE = os.path.join(ROOT, 'state')


def now_kst():
    return _kbj_now_kst().isoformat(timespec='seconds')  # KBJ P2(설계 §7.2): 벽시계는 kbj.core.time 한 곳


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


def sync_label_kinds(conn, cfg):
    """라벨 테이블을 현재 우선순위 구성에 맞춘다.

    d120 을 빼면서(D-060) 두 가지가 남는다.

      1. 어제까지 쌓인 kind='d120' 행 — 지금 구성에 없는 라벨이다. 지운다.
      2. 남은 행의 rank 정수 — 옛 우선순위(hist=0, w52=1, d120=2, d60=3)로
         찍혀 있다. 새 구성에서 d60 은 2 인데 어제 행은 3 이라, 숫자로
         비교하는 신규/이어감 판정이 하루 동안 '이어감'을 '신규'로 적는다.
         kind 가 저장돼 있으므로 kind 로 rank 를 다시 매긴다.

    구성에서 라벨을 넣고 뺄 때마다 알아서 맞으므로 일회성 마이그레이션이 아니라
    매 build 마다 돈다 — 맞아 있으면 아무것도 바꾸지 않는다.

    반환 (지운 행 수, 고친 행 수).
    """
    rank = nh.rank_of(cfg)
    ph = ','.join('?' * len(rank))
    n_del = conn.execute(
        f'DELETE FROM label WHERE kind NOT IN ({ph})', list(rank)).rowcount
    n_fix = 0
    for k, r in rank.items():
        n_fix += conn.execute(
            'UPDATE label SET rank=? WHERE kind=? AND rank<>?', (r, k, r)).rowcount
    conn.commit()
    return n_del, n_fix


def _prev_labels(conn, prev_asof, basis):
    if not prev_asof:
        return {}
    return {r['code']: r['rank'] for r in conn.execute(
        'SELECT code, rank FROM label WHERE asof=? AND basis=?', (prev_asof, basis))}


def run(db_path, asof=None, cfg=None, log=print):
    """sqlite 읽기 → kbj `compute_day` → label 표·run_log·state 파일 쓰기."""
    cfg = cfg or load()
    conn = DB.connect(db_path)
    try:
        asof = asof or DB.last_asof(conn)
        if not asof:
            raise RuntimeError('일봉이 비었다. 먼저 ingest 를 돌려라.')

        days = DB.trading_days(conn, asof, 2)
        prev_asof = days[1] if len(days) > 1 else None
        basis = cfg['newhigh']['default_basis']

        snap, snap_asof = DB.snapshot(conn, asof)
        n_del, n_fix = sync_label_kinds(conn, cfg)
        if n_del or n_fix:
            log(f'  라벨 테이블 정리 — 구성에서 빠진 라벨 {n_del:,}행 삭제, '
                f'순위 {n_fix:,}행 갱신')
        # 공시 대조로 '분할이 아니다' 가 확인된 종목. 조회에 실패한 것은 안 들어온다.
        cleared = DB.split_cleared(conn)
        # 공시를 못 물어본 종목. 가드는 유지되지만 사유가 '공시가 있어서' 가 아니라
        # '물어보지 못해서' 다. 같은 말로 적으면 안 된다 (D-057).
        unknown_split = DB.split_unknown(conn)
        taxes = conn.execute(
            'SELECT taxonomy, COUNT(*) c FROM sector_map GROUP BY taxonomy '
            'ORDER BY c DESC').fetchall()
        # 수집 단계가 run_log 에 남긴 실패 사유 — 엔진이 배너로 올린다(2장 6번).
        collect = [(m['step'], m['note']) for m in DB.missing(conn, asof)]
        pn = read(prev_asof, 'newhigh.json') if prev_asof else None
        inp = BoardInputs(
            asof=asof, prev_asof=prev_asof,
            series=DB.all_series(conn, asof) if snap else {},
            snapshot=snap, snap_asof=snap_asof,
            generated_at=now_kst(),
            alltime={r['code']: dict(r) for r in conn.execute('SELECT * FROM alltime')},
            sectors=DB.sector_of(conn),
            taxonomy_counts=[(r[0], r[1]) for r in taxes],
            prev_ranks=_prev_labels(conn, prev_asof, basis),
            split_cleared=frozenset(cleared),
            split_unknown=list(unknown_split),
            prev_near=prev_near_from(pn),
            collect_notes=collect,
            close_note=DB.note(conn, asof, 'close_krx'),
            funds_excluded=DB.note(conn, asof, 'funds_excluded'),
            themes_yaml=load_themes(),
            taxonomy=RK.load_taxonomy(),
            prev_rankings=read(prev_asof, 'rankings.json') if prev_asof else None,
            market=read(asof, 'market.json'))
        day = compute_day(inp, cfg)

        conn.executemany('INSERT OR REPLACE INTO label VALUES(?,?,?,?,?)',
                         [(x.code, asof, x.basis, x.kind, x.rank) for x in day.labels])
        conn.commit()
        for step, note in day.steps:
            DB.log_step(conn, asof, step, False, note)
        for line in day.log:
            log(line)
        for name, payload in day.payloads().items():
            write(asof, f'{name}.json', payload)

        # ── market.json 은 ingest 가 쓴다. 없으면 결손으로 남긴다 ──
        if read(asof, 'market.json') is None:
            DB.log_step(conn, asof, 'market', False, '지수·수급·환율 미수집')
        return asof
    finally:
        conn.close()

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

STATE = os.path.join(ROOT, 'state_us')
# 설정은 레포 루트 config/us.yaml(KBJ P5 — git mv)
from kbj.engines.us.config import us_config_path  # noqa: E402

US_CFG = str(us_config_path())


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


# ─────────────────────────── 조립 ───────────────────────────
# KBJ P5 묶음 US(docs/p5_design.md §1.6): 계산(day_stats·trend·sector_trend·build)은
# `kbj.engines.us.board` 정본을 쓴다. 여기 남은 것은 파일·sqlite 입출력(run·write·read)뿐이다.
from kbj.engines.us.board import day_stats, sector_trend, trend  # noqa: E402,F401
from kbj.engines.us.board import build as _kbj_build  # noqa: E402


def build(series, snaps, asof, cfg, missing=None, log=print):
    """kbj `board.build` 에 만든 시각만 넣어 부른다(예전 호출 모양 그대로)."""
    return _kbj_build(series, snaps, asof, cfg, missing=missing, log=log, generated_at=now_iso())


def run(db_path, asof=None, cfg=None, log=print):
    from ..engine.config import load
    from . import db as DB
    cfg = cfg or load(US_CFG)
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

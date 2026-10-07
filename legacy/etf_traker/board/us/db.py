#!/usr/bin/env python3
"""
미국장 저장소 — sqlite3. 국장(board/engine/db.py)과 같은 규약이고 파일만 다르다.

DB 는 일봉 캐시일 뿐이고 단계 간 계약이 아니다. 계약은 state_us/YYYYMMDD/*.json
이다 (CLAUDE.md 3장).

국장 스키마와 다른 점 셋.
  · 코드가 아니라 티커다 (문자열이라는 점은 같다)
  · 역사적 최고가 스칼라(alltime)가 없다 — 무료 소스가 상장 이후 전 구간을
    주지 않아 '사상 최고가' 를 단정할 수 없다 (config/us.yaml 의 newhigh 주석)
  · 52주 신저가 라벨을 따로 쌓는다. 신고가의 거울이라 표가 한 줄로 읽힌다
"""
import sqlite3
from datetime import datetime, timezone

DATA_VERSION = '1'

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS px(
  ticker TEXT, asof TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  source TEXT, PRIMARY KEY(ticker, asof));
CREATE INDEX IF NOT EXISTS ix_uspx_asof ON px(asof);

CREATE TABLE IF NOT EXISTS snap(
  ticker TEXT, asof TEXT, name TEXT, exchange TEXT, close REAL, chg_pct REAL,
  volume REAL, turnover REAL, mktcap REAL, sector TEXT, industry TEXT,
  sector_raw TEXT, industry_raw TEXT, source TEXT,
  PRIMARY KEY(ticker, asof));
CREATE INDEX IF NOT EXISTS ix_ussnap_asof ON snap(asof);

-- 일자별 신고가 라벨. '신규 / 이어감' 과 '연속 N일' 이 이 표를 읽는다.
CREATE TABLE IF NOT EXISTS label(
  ticker TEXT, asof TEXT, basis TEXT, kind TEXT, rank INT,
  PRIMARY KEY(ticker, asof, basis));
CREATE INDEX IF NOT EXISTS ix_uslabel_asof ON label(asof, basis);

-- 52주 신저가. 라벨이 하나뿐이라 kind 열을 두지 않는다.
CREATE TABLE IF NOT EXISTS lowlabel(
  ticker TEXT, asof TEXT, basis TEXT, PRIMARY KEY(ticker, asof, basis));
CREATE INDEX IF NOT EXISTS ix_uslow_asof ON lowlabel(asof, basis);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS run_log(
  asof TEXT, step TEXT, ok INT, note TEXT, ts TEXT);
"""


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(path):
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def get_meta(conn, k, default=None):
    r = conn.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
    return r['v'] if r else default


def set_meta(conn, k, v):
    conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, str(v)))
    conn.commit()


def log_step(conn, asof, step, ok, note=''):
    conn.execute('INSERT INTO run_log VALUES(?,?,?,?,?)',
                 (asof, step, 1 if ok else 0, note, now_utc()))
    conn.commit()


def put_px(conn, ticker, rows):
    conn.executemany(
        'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
        [(ticker, r['asof'], r.get('open'), r.get('high'), r.get('low'),
          r.get('close'), r.get('volume'), r.get('source')) for r in rows])


def put_snap(conn, asof, rows):
    conn.executemany(
        'INSERT OR REPLACE INTO snap VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [(r['ticker'], asof, r.get('name'), r.get('exchange'), r.get('close'),
          r.get('chg_pct'), r.get('volume'), r.get('turnover'), r.get('mktcap'),
          r.get('sector'), r.get('industry'), r.get('sector_raw'),
          r.get('industry_raw'), r.get('source')) for r in rows])
    conn.commit()


def put_labels(conn, asof, basis, rows):
    """rows: [(ticker, kind, rank)]. 그날 것을 통째로 갈아 끼운다."""
    conn.execute('DELETE FROM label WHERE asof=? AND basis=?', (asof, basis))
    conn.executemany('INSERT INTO label VALUES(?,?,?,?,?)',
                     [(t, asof, basis, k, rk) for t, k, rk in rows])
    conn.commit()


def put_lows(conn, asof, basis, tickers):
    conn.execute('DELETE FROM lowlabel WHERE asof=? AND basis=?', (asof, basis))
    conn.executemany('INSERT INTO lowlabel VALUES(?,?,?)',
                     [(t, asof, basis) for t in tickers])
    conn.commit()


def series_for(conn, tickers, upto):
    """티커별 오름차순 일봉. 기준일까지만 읽는다 — 미래 봉이 판정에 섞이면 안 된다."""
    out = {}
    q = ('SELECT ticker, asof, open, high, low, close, volume FROM px '
         'WHERE asof<=? ORDER BY ticker, asof')
    want = set(tickers) if tickers is not None else None
    for r in conn.execute(q, (upto,)):
        if want is not None and r['ticker'] not in want:
            continue
        out.setdefault(r['ticker'], []).append(dict(r))
    return out


def bar_counts(conn):
    """티커별 보유 일봉 수와 첫 날짜. 어디까지 받아야 하는지 판단하는 근거다."""
    return {r['ticker']: dict(n=r['n'], first=r['first'], last=r['last'])
            for r in conn.execute(
                'SELECT ticker, COUNT(*) n, MIN(asof) first, MAX(asof) last '
                'FROM px GROUP BY ticker')}


def snapshot(conn, asof):
    return {r['ticker']: dict(r)
            for r in conn.execute('SELECT * FROM snap WHERE asof=?', (asof,))}


def last_asof(conn):
    r = conn.execute('SELECT MAX(asof) a FROM px').fetchone()
    return r['a'] if r else None


def label_days(conn, upto, n, basis):
    """기준일 포함 직전 n영업일의 라벨 일자. 연속·신규 판정이 읽는다."""
    return [r['asof'] for r in conn.execute(
        'SELECT DISTINCT asof FROM label WHERE asof<=? AND basis=? '
        'ORDER BY asof DESC LIMIT ?', (upto, basis, n))]


def labels_on(conn, asof, basis):
    return {r['ticker']: dict(kind=r['kind'], rank=r['rank'])
            for r in conn.execute(
                'SELECT ticker, kind, rank FROM label WHERE asof=? AND basis=?',
                (asof, basis))}


def trading_days(conn, upto, n):
    return [r['asof'] for r in conn.execute(
        'SELECT DISTINCT asof FROM px WHERE asof<=? ORDER BY asof DESC LIMIT ?',
        (upto, n))]

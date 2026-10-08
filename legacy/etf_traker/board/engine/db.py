#!/usr/bin/env python3
"""
저장소 스키마 — sqlite3.

CLAUDE.md 3장은 engine 을 DuckDB 로 적어 두었으나 v0.1 은 표준 라이브러리
sqlite3 로 간다. 근거와 되돌리는 조건은 docs/DECISIONS.md D-002 참고.
단계 간 데이터는 여전히 state/YYYYMMDD/*.json 으로만 넘긴다. DB 는 일봉 캐시와
역사적 최고가 스칼라를 들고 있는 저장소일 뿐이고, 엔진의 입력 계약이 아니다.
"""
import sqlite3
from datetime import timedelta, timezone

from kbj.core.time import now_kst as _kbj_now_kst

KST = timezone(timedelta(hours=9))


def now_kst():
    """DB 에 남기는 시각. **오프셋을 붙인 KST 로 통일한다.**

    같은 sector_map.updated_at 을 pipeline 은 KST 로, classify 와
    restore_sectors 는 naive `datetime.now()` 로 썼다. 러너는 UTC 라 두 값이
    9시간 어긋나고, naive 쪽은 오프셋이 없어 사후에 어느 쪽인지 구분조차
    안 된다. '어느 배정이 더 최신인가' 를 이 컬럼으로 판단하는 순간 틀린다.
    """
    return _kbj_now_kst().isoformat(timespec='seconds')  # KBJ P2(설계 §7.2): 벽시계는 kbj.core.time 한 곳

DDL = """
PRAGMA journal_mode=WAL;

-- 일봉. 신고가 판정의 유일한 원천.
CREATE TABLE IF NOT EXISTS px(
  code TEXT, asof TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  source TEXT, PRIMARY KEY(code, asof));
CREATE INDEX IF NOT EXISTS ix_px_asof ON px(asof);

-- 일자별 종목 스냅샷. 시총·거래대금처럼 일봉에 없는 값.
CREATE TABLE IF NOT EXISTS snap(
  code TEXT, asof TEXT, name TEXT, market TEXT, close REAL, chg_pct REAL,
  volume REAL, turnover REAL, mktcap REAL, turnover_is_estimate INT,
  source TEXT, PRIMARY KEY(code, asof));
CREATE INDEX IF NOT EXISTS ix_snap_asof ON snap(asof);

-- 역사적 최고가는 스칼라로 들고 매일 당일 고가와만 비교한다 (CLAUDE.md 4장).
-- 전체 일봉을 매일 재계산하지 않는다.
CREATE TABLE IF NOT EXISTS alltime(
  code TEXT PRIMARY KEY,
  hi REAL, hi_date TEXT,          -- 고가 기준 사상 최고가 (당일 포함)
  cl REAL, cl_date TEXT,          -- 종가 기준 사상 최고가 (당일 포함)
  prev_hi REAL, prev_cl REAL,     -- 직전 처리일까지의 최고가. 당일 갱신 판정의 기준
  first_date TEXT, last_date TEXT, n_days INT,
  suspect INT DEFAULT 0,          -- 수정주가 미반영 의심 (split_guard)
  suspect_date TEXT, suspect_note TEXT,
  updated_at TEXT);

-- 일자별 신고가 라벨. '신규 / 이어감' 판정이 전일 행을 읽는다.
CREATE TABLE IF NOT EXISTS label(
  code TEXT, asof TEXT, basis TEXT,   -- basis: high | close
  kind TEXT,                          -- hist | w52 | d120 (KBJ ADR 0017 — 예전 d60)
  rank INT,                           -- 0=hist 1=w52 2=d120. 작을수록 상위
  PRIMARY KEY(code, asof, basis));
CREATE INDEX IF NOT EXISTS ix_label_asof ON label(asof, basis);

-- 종목 → 업종 (1층). 커버리지 보장용이라 종목당 정확히 하나.
CREATE TABLE IF NOT EXISTS sector_map(
  code TEXT PRIMARY KEY, sector TEXT, taxonomy TEXT, updated_at TEXT);

-- DB 를 어떤 규약으로 만들었는지. 수집 단위나 스칼라 의미가 바뀌면 이 값을 올리고,
-- daily 는 값이 다르면 다시 쌓는다. '파일이 있느냐'로 판단하면 옛 규약으로 만든
-- DB 위에서 새 코드가 돈다 — 2026-08-29 에 실제로 그랬다 (D-039).
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

-- split_guard 에 걸린 종목의 공시 대조 결과 (D-056).
-- 시세만 봐서는 '주식 수가 바뀐 계단'과 '실제로 크게 움직인 날'이 갈리지 않는다.
-- 공시가 그 답을 갖고 있으므로 종목별로 물어보고 결과를 남긴다.
--   action    분할·병합·감자·무상증자 공시가 있다 → 가드 유지
--   none      그런 공시가 없다 → 실제 등락이다. 가드를 풀어 준다
--   unknown   조회 실패·corp_code 없음 → **가드를 풀지 않는다**
-- 못 본 것을 없다고 적으면 그 다음 판단이 통째로 틀린다 (CLAUDE.md 2장 6번).
CREATE TABLE IF NOT EXISTS split_check(
  code TEXT PRIMARY KEY, jump_date TEXT, verdict TEXT, note TEXT, updated_at TEXT);

-- 단계별 실행 기록. 무엇이 빠졌는지 리포트 상단에 그대로 올린다.
CREATE TABLE IF NOT EXISTS run_log(
  asof TEXT, step TEXT, ok INT, note TEXT, ts TEXT);
"""


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
                 (asof, step, 1 if ok else 0, note,
                  now_kst()))
    conn.commit()


def split_cleared(conn):
    """공시 대조로 '분할이 아니다' 가 확인된 코드 집합.

    verdict 가 'none' 인 것만이다. 'unknown'(조회 실패)은 포함하지 않는다 —
    모르는 것과 아닌 것은 다르다.
    """
    return {r['code'] for r in conn.execute(
        "SELECT code FROM split_check WHERE verdict='none'")}


def split_unknown(conn):
    """공시 대조를 못 한 종목 [(code, note)].

    가드는 유지되지만 **왜 유지되는지가 다르다** — 공시가 있어서가 아니라
    물어보지 못해서다. 화면이 그 둘을 같은 말로 적으면 안 된다.
    """
    return [(r['code'], r['note']) for r in conn.execute(
        "SELECT code, note FROM split_check WHERE verdict='unknown' ORDER BY code")]


def put_split_check(conn, rows):
    """[(code, jump_date, verdict, note)] 를 기록한다."""
    conn.executemany(
        'INSERT OR REPLACE INTO split_check VALUES(?,?,?,?,?)',
        [(c, d, v, n, now_kst()) for c, d, v, n in rows])
    conn.commit()


def missing(conn, asof):
    """해당 일자에 실패한 단계. 리포트 상단 결손 배너의 입력."""
    return [dict(step=r['step'], note=r['note']) for r in conn.execute(
        'SELECT step, note FROM run_log WHERE asof=? AND ok=0 ORDER BY ts', (asof,))]


def note(conn, asof, step):
    """run_log 에서 한 단계의 메모를 꺼낸다. 성공한 단계의 부가 정보용."""
    r = conn.execute(
        'SELECT note FROM run_log WHERE asof=? AND step=? ORDER BY ts DESC LIMIT 1',
        (asof, step)).fetchone()
    return r[0] if r else None


_PX_COLS = 'SELECT code,asof,open,high,low,close,volume FROM px '


def iter_series(conn, upto):
    """(code, 일봉) 를 코드 순서로 하나씩 흘려보낸다.

    한 종목씩 보고 버리면 되는 자리에서 `all_series` 를 쓰면 120만 행
    (420일 x 2,800종목)이 통째로 메모리에 남는다. 쿼리는 이미 `ORDER BY code`
    라 커서를 그대로 끊어 주기만 하면 된다 — 쿼리 횟수는 여전히 한 번이다.
    """
    code, buf = None, []
    for r in conn.execute(_PX_COLS + 'WHERE asof<=? ORDER BY code, asof', (upto,)):
        if r['code'] != code:
            if code is not None:
                yield code, buf
            code, buf = r['code'], []
        buf.append(dict(r))
    if code is not None:
        yield code, buf


def series_for(conn, codes, upto):
    """지정한 종목의 일봉만 {code: [행]}. 전 종목을 올리지 않는다.

    의심 종목 열두 개를 보려고 전 종목을 적재하던 자리를 위한 것이다.
    """
    codes = list(dict.fromkeys(codes))
    if not codes:
        return {}
    out = {}
    # SQLite 의 변수 한도(기본 999)를 넘지 않게 나눠 묻는다.
    for i in range(0, len(codes), 500):
        chunk = codes[i:i + 500]
        q = (_PX_COLS + f'WHERE asof<=? AND code IN ({",".join("?" * len(chunk))}) '
             'ORDER BY code, asof')
        for r in conn.execute(q, (upto, *chunk)):
            out.setdefault(r['code'], []).append(dict(r))
    return out


def all_series(conn, upto):
    """전 종목 일봉을 코드별로 묶어 한 번에.

    전 종목을 동시에 들고 있어야 하는 자리(engine/build)만 쓴다. 한 종목씩
    보고 버리는 자리는 `iter_series`, 일부만 필요한 자리는 `series_for` 다.
    """
    return dict(iter_series(conn, upto))


def trading_days(conn, upto, n):
    """upto 이하 거래일을 최신순 n개. 전 종목 합집합이라 개별 종목 휴장은 무시된다."""
    return [r[0] for r in conn.execute(
        'SELECT DISTINCT asof FROM px WHERE asof<=? ORDER BY asof DESC LIMIT ?', (upto, n))]


def last_asof(conn):
    return conn.execute('SELECT MAX(asof) FROM px').fetchone()[0]


def snapshot(conn, asof):
    """기준일의 종목 스냅샷 {code: dict}.

    스냅이 기준일과 다른 날짜로 들어가 있으면 **기준일 이하의 가장 최근 스냅**을
    쓴다. 수집은 실행한 날짜로 스냅을 쓰는데 기준일은 일봉의 마지막 날짜라,
    휴장일이나 장 마감 전에 돌리면 둘이 어긋난다. 정확히 일치만 보면 그때
    엔진 입력이 통째로 비어 리포트가 조용히 빈 채로 나간다.

    반환 (스냅 dict, 실제 사용한 날짜). 스냅이 아예 없으면 ({}, None).
    """
    row = conn.execute(
        'SELECT MAX(asof) FROM snap WHERE asof<=?', (asof,)).fetchone()
    used = row[0] if row else None
    if not used:
        return {}, None
    return ({r['code']: dict(r) for r in conn.execute(
        'SELECT * FROM snap WHERE asof=?', (used,))}, used)


def sector_of(conn):
    return {r['code']: r['sector'] for r in conn.execute(
        'SELECT code, sector FROM sector_map')}

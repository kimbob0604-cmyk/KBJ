"""SYNTHETIC 옛 SQLite·JSON 원본 생성기 — 묶음 G 이관 시험용(시드 고정, 실데이터 아님).

옛 DB(SD dashboard.db, ET board.db·us_board.db·backtest.db·us_backtest.db)와 JSON(ET inbox.json·
state/<날짜>/stockflows.json, monitor/kr cache/flows.json)은 로그인 등급·개인 데이터가 섞여 레포에
넣지 않는다(CLAUDE.md §2, ADR 0001 U3, .gitignore `*.db`). 이 스크립트는 **원본의 DDL 을
글자 그대로**
(아래 출처 줄) 써서 빈 DB 를 만들고, 시드 고정 난수와 손으로 정한 경우(네이버 행·출처 빈 행·정수가
아닌 거래량·잘못된 날짜·앞자리 0 이 빠진 코드·겹치는 날짜·비밀처럼 보이는 키 …)만 넣는다.
종목·이름은 가짜(코드 99xxxx, `합성…`, 미국 티커 ZZ…).

    uv run python tests/fixtures/synthetic/sqlite/make_legacy_db.py --out <폴더>

시험은 `build_all(tmp_path)` 를 부른다(파일은 시험 임시 폴더에만 생긴다 — 커밋하지 않는다).
DDL 출처(원본 스냅샷 /home/user/p0src 기준 — ET 0014f57, SD f46178c):
- ET `board/engine/db.py:DDL`(board.db·backtest.db),
  ET `board/us/db.py:DDL`(us_board.db·us_backtest.db)
- SD `db/schema.sql`(ops_state·flow_cache), `migrations/004_step4_schema.py`(fetch_progress),
  `migrations/005_step4_7_earnings.py`(index_universe)
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

SEED = "kbj-p2-legacy-sqlite-v1"
DAYS = ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02")
OLD_DAYS = ("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25")

# ── 원본 DDL (글자 그대로) ─────────────────────────────────────────────────────────────────

ET_BOARD_DDL = """
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

CREATE TABLE IF NOT EXISTS alltime(
  code TEXT PRIMARY KEY,
  hi REAL, hi_date TEXT,          -- 고가 기준 사상 최고가 (당일 포함)
  cl REAL, cl_date TEXT,          -- 종가 기준 사상 최고가 (당일 포함)
  prev_hi REAL, prev_cl REAL,     -- 직전 처리일까지의 최고가. 당일 갱신 판정의 기준
  first_date TEXT, last_date TEXT, n_days INT,
  suspect INT DEFAULT 0,          -- 수정주가 미반영 의심 (split_guard)
  suspect_date TEXT, suspect_note TEXT,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS label(
  code TEXT, asof TEXT, basis TEXT,   -- basis: high | close
  kind TEXT,                          -- hist | w52 | d60
  rank INT,                           -- 0=hist 1=w52 2=d60. 작을수록 상위
  PRIMARY KEY(code, asof, basis));
CREATE INDEX IF NOT EXISTS ix_label_asof ON label(asof, basis);

CREATE TABLE IF NOT EXISTS sector_map(
  code TEXT PRIMARY KEY, sector TEXT, taxonomy TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS split_check(
  code TEXT PRIMARY KEY, jump_date TEXT, verdict TEXT, note TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS run_log(
  asof TEXT, step TEXT, ok INT, note TEXT, ts TEXT);
"""

ET_US_DDL = """
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

CREATE TABLE IF NOT EXISTS label(
  ticker TEXT, asof TEXT, basis TEXT, kind TEXT, rank INT,
  PRIMARY KEY(ticker, asof, basis));
CREATE INDEX IF NOT EXISTS ix_uslabel_asof ON label(asof, basis);

CREATE TABLE IF NOT EXISTS lowlabel(
  ticker TEXT, asof TEXT, basis TEXT, PRIMARY KEY(ticker, asof, basis));
CREATE INDEX IF NOT EXISTS ix_uslow_asof ON lowlabel(asof, basis);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS run_log(
  asof TEXT, step TEXT, ok INT, note TEXT, ts TEXT);
"""

SD_DDL = """
-- 수급 데이터 (캐시 단위: 종목별 20일 블록)
CREATE TABLE IF NOT EXISTS flow_cache (
    code TEXT PRIMARY KEY,
    name TEXT,
    dates_json TEXT,
    close_json TEXT,
    foreign_shares_json TEXT,
    inst_shares_json TEXT,
    foreign_value_json TEXT,
    inst_value_json TEXT,
    foreign_sum_20 REAL,
    inst_sum_20 REAL,
    source TEXT,
    fetched_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ops_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fetch_progress (
  stock_code TEXT PRIMARY KEY,

  quarterly_last_year INTEGER,
  quarterly_last_quarter INTEGER,
  quarterly_fetched_count INTEGER DEFAULT 0,
  quarterly_target_count INTEGER DEFAULT 20,
  quarterly_status TEXT DEFAULT 'PENDING',
  quarterly_last_attempt TEXT,
  quarterly_error TEXT,

  valuation_band_status TEXT DEFAULT 'PENDING',
  valuation_band_calculated_at TEXT,

  consensus_status TEXT DEFAULT 'PENDING',
  consensus_last_fetch TEXT,

  overhang_status TEXT DEFAULT 'PENDING',
  overhang_last_fetch TEXT,

  updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS index_universe (
    stock_code TEXT NOT NULL,
    source TEXT NOT NULL,
    rank INTEGER,
    market_cap REAL,

    added_date TEXT NOT NULL,
    removed_date TEXT,
    is_active INTEGER DEFAULT 1,

    PRIMARY KEY (stock_code, source)
);
"""


def _rng(name: str) -> random.Random:
    return random.Random(f"{SEED}:{name}")  # noqa: S311 — 암호용이 아니라 재현 가능한 합성 값


def _ohlcv(r: random.Random, base: float) -> tuple[float, float, float, float, float]:
    o = round(base * (1 + r.uniform(-0.02, 0.02)))
    c = round(base * (1 + r.uniform(-0.03, 0.03)))
    h = max(o, c) + r.randint(0, 300)
    lo = min(o, c) - r.randint(0, 300)
    return float(o), float(h), float(lo), float(c), float(r.randint(10_000, 900_000))


def _connect(path: Path, ddl: str) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    return conn


def _close(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


# ── ET board.db·backtest.db ──────────────────────────────────────────────────────────────


def board_px_rows() -> list[tuple[object, ...]]:
    """(code, asof, open, high, low, close, volume, source). 코드 9900 은 정수로 넣는다(앞자리 0 이
    빠진 코드 — TEXT 친화로 '9900' 으로 저장된다)."""
    r = _rng("board.px")
    out: list[tuple[object, ...]] = []
    for code, base in (("990010", 51_000.0), ("990020", 8_200.0), ("990030", 132_500.0)):
        for i, d in enumerate(DAYS):
            src = "naver" if code == "990020" and i < 3 else "krx"
            out.append((code, d, *_ohlcv(r, base), src))
    for d in DAYS:
        out.append((9900, d, *_ohlcv(r, 4_100.0), "datago"))  # 숫자로 저장된 코드
    out.append(("990040", DAYS[0], *_ohlcv(r, 1_000.0), None))  # 출처 빈 행
    o, h, lo, c, _ = _ohlcv(r, 2_000.0)
    out.append(("990050", DAYS[0], o, h, lo, c, 1234.5, "krx"))  # 정수가 아닌 거래량
    out.append(("990060", "n/a", *_ohlcv(r, 3_000.0), "krx"))  # 잘못된 날짜
    return out


def backtest_px_rows() -> list[tuple[object, ...]]:
    """board.db 와 겹치는 날(990010 의 마지막 두 날 — 값은 다르게)과 board 에 없는 앞 날짜들."""
    r = _rng("backtest.px")
    out: list[tuple[object, ...]] = []
    for d in DAYS[3:]:
        out.append(("990010", d, *_ohlcv(r, 49_000.0), "krx"))  # board 우선 → other_origin
    for d in OLD_DAYS:
        out.append(("990010", d, *_ohlcv(r, 50_000.0), "krx"))
        out.append(("990030", d, *_ohlcv(r, 130_000.0), "naver"))
    return out


def board_snap_rows() -> list[tuple[object, ...]]:
    """(code, asof, name, market, close, chg_pct, volume, turnover(억원), mktcap(억원),
    turnover_is_estimate, source)."""
    r = _rng("board.snap")
    out: list[tuple[object, ...]] = []
    for d in DAYS[3:]:
        for code, name, mkt, src in (
            ("990010", "합성전자", "KOSPI", "krx"),
            ("990020", "합성바이오", "KOSDAQ", "naver"),
            ("990030", "합성화학", "KOSPI", "krx"),
        ):
            close = float(r.randint(5_000, 150_000))
            vol = float(r.randint(10_000, 900_000))
            turnover = round(close * vol / 1e8, 6)
            out.append(
                (code, d, name, mkt, close, round(r.uniform(-5, 5), 2), vol, turnover,
                 round(r.uniform(1_000, 90_000), 2), 0, src)
            )  # fmt: skip
    out.append(("990040", DAYS[4], "합성정지", "KOSDAQ", None, None, 0.0, None, 512.25, 1, "krx"))
    return out


def board_meta_rows() -> list[tuple[str, str]]:
    return [
        ("data_version", "3"),
        ("last_daily", DAYS[4]),
        ("kis_token_cache", "SYNTHETIC-NOT-A-TOKEN"),  # 비밀처럼 보이는 키 → 버림
    ]


def board_run_log_rows() -> list[tuple[object, ...]]:
    return [
        (DAYS[3], "universe", 1, "2800종목", "2026-10-01T15:41:02+09:00"),
        (DAYS[3], "close_krx", 0, "KRX 확정치 지연", "2026-10-01T15:41:05+09:00"),
        (DAYS[4], "universe", 1, "2801종목", "2026-10-02T15:40:59+09:00"),
        (DAYS[4], "universe", 1, "2801종목", "2026-10-02T15:40:59+09:00"),  # 완전히 같은 줄
        (DAYS[4], "px", 1, "", "2026-10-02 15:42:00"),  # 오프셋 없는 옛 시각
        (None, "orphan", 1, "", "2026-10-02T15:43:00+09:00"),  # asof 없음
    ]


# ── ET us_board.db·us_backtest.db ────────────────────────────────────────────────────────


def us_px_rows(name: str, days: tuple[str, ...], base: float) -> list[tuple[object, ...]]:
    r = _rng(name)
    out: list[tuple[object, ...]] = []
    for ticker, src in (("ZZA", "nasdaq"), ("ZZB", "yahoo")):
        for d in days:
            o, h, lo, c, v = _ohlcv(r, base)
            out.append((ticker, d, o / 100, h / 100, lo / 100, c / 100, v, src))
    return out


def us_snap_rows() -> list[tuple[object, ...]]:
    return [
        ("ZZA", DAYS[4], "Synthetic Alpha", "NASDAQ", 101.25, 1.5, 120000.0, 12150000.0, 2.1e9,
         "Technology", "Software", "Computer Software", "Prepackaged Software", "nasdaq-screener"),
        ("ZZB", DAYS[4], "Synthetic Beta", "NYSE", 55.5, -0.75, 80000.0, 4440000.0, 9.5e8,
         "Health Care", None, "", None, "nasdaq-screener"),
    ]  # fmt: skip


# ── SD dashboard.db ──────────────────────────────────────────────────────────────────────


def sd_rows() -> dict[str, list[tuple[object, ...]]]:
    kis_dates = [DAYS[2], DAYS[3], DAYS[4]]
    return {
        "ops_state": [
            ("closing_brief_date", DAYS[4], "2026-10-02 07:00:00"),
            ("watchdog_alerted", "0", "2026-10-02 06:10:00"),
            ("telegram_token_hint", "SYNTHETIC", "2026-10-02 06:10:00"),  # 비밀처럼 보이는 키
        ],
        "fetch_progress": [
            ("990010", 2026, 2, 10, 20, "DONE", "2026-10-01 20:00:00", None, "DONE", None,
             "PENDING", None, "PENDING", None, "2026-10-01 20:00:00"),
            (5930, None, None, 0, 20, "PENDING", None, None, "PENDING", None, "PENDING", None,
             "PENDING", None, "2026-10-01 20:00:00"),
        ],
        "index_universe": [
            ("990010", "VALUECHAIN_V2", 1, 3.2e13, "2026-06-01", None, 1),
            ("990030", "VALUECHAIN_V2", 2, 1.1e13, "2026-06-01", "2026-09-01", 0),
            ("990070", "VALUECHAIN_V2", 3, 9.0e11, "not-a-date", None, 1),  # 날짜 오류
        ],
        "flow_cache": [
            ("990010", "합성전자", json.dumps(kis_dates), json.dumps([51000, 51500, 52000]),
             json.dumps([1200, -300, 450]), json.dumps([-800, 100, 0]),
             json.dumps([61200000, -15450000, 23400000]), json.dumps([-40800000, 5150000, 0]),
             68400000.0, -35650000.0, "kis", "2026-10-02 16:00:00"),
            ("990020", "합성바이오", json.dumps(kis_dates), json.dumps([8200, 8300, 8250]),
             json.dumps([10, 20, 30]), json.dumps([1, 2, 3]), json.dumps([82000, 166000, 247500]),
             json.dumps([8200, 16600, 24750]), 495500.0, 49550.0, "naver_mobile_api",
             "2026-10-02 16:00:00"),
            ("990030", "합성화학", json.dumps(kis_dates[:2]), json.dumps([1, 2]),
             json.dumps([1, 2, 3]), json.dumps([1, 2]), json.dumps([1, 2]), json.dumps([1, 2]),
             0.0, 0.0, "kis", "2026-10-02 16:00:00"),  # 배열 길이 불일치
        ],
    }  # fmt: skip


# ── JSON ─────────────────────────────────────────────────────────────────────────────────


def inbox_doc() -> dict[str, object]:
    return {
        "source": "telegram",
        "updated_at": "2026-10-02T08:00:00+09:00",
        "chat_ids": [-1009900000001],
        "items": [
            {"update_id": 900001, "chat_id": -1009900000001, "date": "2026-10-01T21:10:00+09:00",
             "text": "합성 게시물 https://x.com/synthetic_acct/status/1990000000000000001",
             "urls": ["https://x.com/synthetic_acct/status/1990000000000000001"],
             "x_ids": ["1990000000000000001"], "author": "synthetic_acct", "kind": "x",
             "text_via": "message"},
            {"update_id": 900002, "chat_id": -1009900000001, "date": "2026-10-02T07:30:00+09:00",
             "text": "https://example.invalid/report", "urls": ["https://example.invalid/report"],
             "x_ids": [], "author": None, "kind": "other", "text_via": None},
            {"chat_id": -1009900000001, "date": "2026-10-02T07:31:00+09:00", "text": "id 없음",
             "urls": [], "x_ids": [], "author": None, "kind": "other", "text_via": "message"},
        ],
    }  # fmt: skip


def stockflows_docs() -> dict[str, dict[str, object]]:
    """{YYYYMMDD 폴더: stockflows.json}. 두 파일에 같은 (990010, 10-01) 이 있다 — 최근 파일 우선.
    990010 10-01 은 monitor/kr 캐시(krflows — 우선)와도 겹친다."""
    return {
        "20261001": {
            "source": "kis",
            "by_code": {
                "990010": {"code": "990010", "name": "합성전자", "as_of": DAYS[3], "unit": "억원",
                           "source": "kis", "기관": 12.5, "외국인": -3.25, "개인": -9.25},
                "990020": {"code": "990020", "name": "합성바이오", "as_of": DAYS[3], "unit": "주",
                           "source": "naver", "기관": 100, "외국인": -50},
            },
        },
        "20261002": {
            "source": "mixed",
            "by_code": {
                "990010": {"code": "990010", "name": "합성전자", "as_of": DAYS[3], "unit": "억원",
                           "source": "kis", "기관": 12.75, "외국인": -3.25, "개인": -9.5},
                "990030": {"code": "990030", "name": "합성화학", "as_of": DAYS[3], "unit": "주",
                           "source": "kis", "기관": 1500, "외국인": -700},
            },
        },
    }  # fmt: skip


def krflows_doc() -> dict[str, object]:
    return {
        "source": "KIS stock_investor",
        "unit": "억원",
        "updated_at": DAYS[4],
        "by_code": {
            "990010": {"20261001": {"f": -3.25, "o": 12.5, "p": -9.25},
                       "20261002": {"f": 0.0, "o": 4.75, "p": -4.75}},
            "990030": {"20261002": {"f": -21.0, "o": 15.5, "p": 5.5}},
        },
    }  # fmt: skip


def build_all(out: Path) -> dict[str, Path]:
    """합성 원본을 모두 만들고 {원본 이름: 경로}(legacy_import --source/--json 이름)를 돌려준다."""
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "sd": out / "dashboard.db",
        "board": out / "board.db",
        "backtest": out / "backtest.db",
        "us": out / "us_board.db",
        "us_backtest": out / "us_backtest.db",
        "inbox": out / "inbox.json",
        "stockflows": out / "state",
        "krflows": out / "flows.json",
    }
    c = _connect(paths["board"], ET_BOARD_DDL)
    c.executemany("INSERT INTO px VALUES(?,?,?,?,?,?,?,?)", board_px_rows())
    c.executemany("INSERT INTO snap VALUES(?,?,?,?,?,?,?,?,?,?,?)", board_snap_rows())
    c.executemany("INSERT INTO meta VALUES(?,?)", board_meta_rows())
    c.executemany("INSERT INTO run_log VALUES(?,?,?,?,?)", board_run_log_rows())
    _close(c)
    c = _connect(paths["backtest"], ET_BOARD_DDL)
    c.executemany("INSERT INTO px VALUES(?,?,?,?,?,?,?,?)", backtest_px_rows())
    _close(c)
    c = _connect(paths["us"], ET_US_DDL)
    c.executemany("INSERT INTO px VALUES(?,?,?,?,?,?,?,?)", us_px_rows("us.px", DAYS[2:], 10_000))
    c.executemany("INSERT INTO snap VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", us_snap_rows())
    c.executemany("INSERT INTO meta VALUES(?,?)", [("data_version", "1")])
    c.executemany(
        "INSERT INTO run_log VALUES(?,?,?,?,?)",
        [(DAYS[4], "universe", 1, "2 tickers", "2026-10-02T21:05:00+00:00")],
    )
    _close(c)
    c = _connect(paths["us_backtest"], ET_US_DDL)
    c.executemany(
        "INSERT INTO px VALUES(?,?,?,?,?,?,?,?)",
        us_px_rows("us_backtest.px", DAYS[3:] + OLD_DAYS, 9_000),
    )
    _close(c)
    c = _connect(paths["sd"], SD_DDL)
    rows = sd_rows()
    c.executemany("INSERT INTO ops_state VALUES(?,?,?)", rows["ops_state"])
    c.executemany(
        f"INSERT INTO fetch_progress VALUES({','.join('?' * 15)})", rows["fetch_progress"]
    )
    c.executemany("INSERT INTO index_universe VALUES(?,?,?,?,?,?,?)", rows["index_universe"])
    c.executemany(
        "INSERT INTO flow_cache (code, name, dates_json, close_json, foreign_shares_json, "
        "inst_shares_json, foreign_value_json, inst_value_json, foreign_sum_20, inst_sum_20, "
        "source, fetched_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (*r, "2026-10-02 07:00:01") for r in rows["flow_cache"]
        ],  # 기본값(datetime('now')) 대신 고정
    )
    _close(c)
    paths["inbox"].write_text(json.dumps(inbox_doc(), ensure_ascii=False, indent=1), "utf-8")
    for day, doc in stockflows_docs().items():
        d = paths["stockflows"] / day
        d.mkdir(parents=True, exist_ok=True)
        (d / "stockflows.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
    paths["krflows"].write_text(json.dumps(krflows_doc(), ensure_ascii=False), "utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--out", required=True, type=Path, help="만들 폴더(레포 밖 임시 폴더 권장)")
    args = p.parse_args(argv)
    for name, path in build_all(args.out).items():
        print(f"{name}={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

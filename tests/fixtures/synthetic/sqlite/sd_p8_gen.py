"""SYNTHETIC SD 개인 기록 이관 원본 생성기 — P8 묶음 JNL 시험용(실데이터 아님, 값 고정).

SD `dashboard.db` 의 `analysis_journal`·`trade_journal`·`recommendation_history`,
`cache/alert_rules.json`·`cache/server_portfolio.json` 은 개인 데이터라 레포에 넣지 않는다
(CLAUDE.md §2). 이 스크립트는 **원본 DDL 을 글자 그대로**(아래 출처) 써서 빈 DB 를 만들고 손으로
정한 경우만 넣는다: 정상 행·앞자리 0 이 빠진 코드·미국 종목·틀린 코드·틀린 action·KR 이 아닌
추천·변환 표 밖 규칙 종류·매매 기록과 겹치는 포지션·수량이 틀린 포지션. 종목·이름은 가짜(코드
`99xxxx`, `합성…`, 미국 티커 `ZZ…`).

    uv run python tests/fixtures/synthetic/sqlite/sd_p8_gen.py --out <폴더>

시험은 `build_sd_p8(tmp_path)` 를 부른다(파일은 시험 임시 폴더에만 생긴다 — 커밋하지 않는다).
DDL 출처(SD f46178c): `migrations/004_step4_schema.py`(analysis_journal),
`server.py:_init_recommendation_history`·`_init_trade_journal`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ANALYSIS_DDL = """
        CREATE TABLE IF NOT EXISTS analysis_journal (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          stock_code TEXT NOT NULL,
          stock_name TEXT,
          analysis_date TEXT NOT NULL,

          -- Step 1: 밸류체인 위치 (자동)
          theme_id TEXT,
          layer_id TEXT,
          segment_id TEXT,
          is_bottleneck INTEGER DEFAULT 0,

          -- Step 2: 개별 요인 (자동 + 수동 덮어쓰기)
          current_price REAL,
          return_52w REAL,
          fwd_per REAL,
          fwd_per_band_pct REAL,
          ev_ebitda REAL,
          ev_ebitda_band_pct REAL,
          market_cap REAL,
          opm_estimate REAL,
          opm_source TEXT,

          -- Step 4: TAM 모델링
          bear_eps REAL, bear_eps_source TEXT,
          bear_per REAL, bear_per_source TEXT,
          bear_tp REAL,

          base_eps REAL, base_eps_source TEXT,
          base_per REAL, base_per_source TEXT,
          base_tp REAL,

          bull_eps REAL, bull_eps_source TEXT,
          bull_per REAL, bull_per_source TEXT,
          bull_tp REAL,

          -- Step 5: 반영도
          reflection_score REAL,
          reflection_label TEXT,

          -- 메모/결론
          memo TEXT,
          tags TEXT,
          conclusion TEXT,

          created_at TEXT DEFAULT (datetime('now', 'localtime')),
          updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
"""

REC_DDL = """
                CREATE TABLE IF NOT EXISTS recommendation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    rank INTEGER,
                    code TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    score REAL,
                    price_at_rec REAL,
                    tags_json TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(date, source, code)
                )
"""

TRADE_DDL = """
                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty INTEGER NOT NULL,
                    total_amount REAL,
                    fee REAL DEFAULT 0,
                    tax REAL DEFAULT 0,
                    strategy TEXT,
                    memo TEXT,
                    trade_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    linked_buy_id INTEGER,
                    realized_pnl REAL,
                    realized_pnl_pct REAL,
                    hold_days INTEGER
                )
"""

ANALYSES = (
    # stock_code, name, date, theme, current_price, return_52w, fwd_per, band, market_cap(백만원),
    # opm, bear_eps, base_eps, bull_eps, bear_tp, base_tp, bull_tp, label, memo, tags, conclusion
    ("990001", "합성전자", "2026-09-01", "T1", 70000.0, 12.5, 11.0, 35.0, 1234.0, 15.0,
     5000.0, 6000.0, 7000.0, 60000.0, 80000.0, 100000.0, "중", "합성 메모", '["반도체", "HBM"]',
     "보유"),
    ("990002", "합성화학", "2026-09-02 10:00:00", None, None, None, None, None, None, None,
     None, None, None, None, None, None, None, None, "", None),
    (990004, "합성바이오", "2026-09-03", None, 1000.0, None, None, None, 50.0, None,
     None, 100.0, None, None, 1500.0, None, "하", None, None, None),  # 숫자 코드 → 0 채움
    ("ZZ", "합성틀림", "2026-09-04", None, None, None, None, None, None, None,
     None, None, None, None, None, None, None, None, None, None),  # bad_code
)  # fmt: skip

TRADES = (
    # code, name, market, action, price, qty, fee, tax, strategy, memo, trade_date
    ("990001", "합성전자", "kr", "buy", 70000.0, 10, 105.0, 0.0, "스윙", None, "2026-09-01"),
    ("990001", "합성전자", "kr", "buy", 68000.0, 5, 51.0, 0.0, None, None, "2026-09-05"),
    ("990001", "합성전자", "kr", "sell", 75000.0, 3, 34.0, 405.0, None, "일부 익절", "2026-09-10"),
    ("ZZAA", "합성미국", "us", "buy", 10.0, 1, 0.0, 0.0, None, None, "2026-09-01"),  # not_kr
    ("990002", "합성화학", "kr", "hold", 1.0, 1, 0.0, 0.0, None, None, "2026-09-01"),  # bad_side
)  # fmt: skip

PICKS = (
    # date, source, rank, code, name, market, score, price_at_rec, tags_json
    ("2026-09-01", "합성추천", 1, "990001", "합성전자", "kr", 0.9, 70000.0, '["모멘텀"]'),
    ("2026-09-01", "합성추천", 2, "990002", "합성화학", "kr", 0.8, 50000.0, '{"why": "합성"}'),
    ("2026-09-02", "합성추천", 1, "990001", "합성전자", "kr", 0.7, 71000.0, None),
    ("2026-09-02", "합성발굴", None, "990003", "합성기계", "kr", None, None, "not json"),
    ("2026-09-02", "합성추천", 3, "ZZBB", "합성미국", "us", 0.5, 10.0, None),  # not_kr
)

RULES = {
    "rules": [
        {"id": 1, "code": "990001", "market": "kr", "type": "rsi_oversold", "value": 25,
         "enabled": True, "name": "합성전자", "triggered_at": "2026-09-01T10:00:00"},
        {"id": 2, "code": 990002, "type": "foreign_cum_buy", "value": 50, "enabled": False},
        {"id": 3, "code": "990001", "market": "kr", "type": "macd_golden", "name": "합성전자"},
        {"id": 4, "code": "ZZAA", "market": "us", "type": "price_above", "value": 10},
        {"id": 5, "code": "990001", "type": "volume_spike", "value": 2},
    ]
}  # fmt: skip

PORTFOLIO = {
    "positions": [
        {"code": "990001", "name": "합성전자", "market": "kr", "buy_price": 69000,
         "quantity": 12, "buy_date": "2026-08-01"},  # 매매 기록과 겹침 → covered_by_trades
        {"code": "990003", "name": "합성기계", "market": "kr", "buy_price": 20000.5,
         "quantity": 7.0, "buy_date": "2026-08-15", "memo": "합성"},
        {"code": "990005", "name": "합성틀림", "buy_price": 100, "quantity": 0,
         "buy_date": "2026-08-15"},  # bad_number
    ]
}  # fmt: skip


def build_sd_p8(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    db = out / "dashboard.db"
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.execute(ANALYSIS_DDL)
        conn.execute(REC_DDL)
        conn.execute(TRADE_DDL)
        conn.executemany(
            "INSERT INTO analysis_journal (stock_code, stock_name, analysis_date, theme_id, "
            "current_price, return_52w, fwd_per, fwd_per_band_pct, market_cap, opm_estimate, "
            "bear_eps, base_eps, bull_eps, bear_tp, base_tp, bull_tp, reflection_label, memo, "
            "tags, conclusion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ANALYSES,
        )
        conn.executemany(
            "INSERT INTO trade_journal (code, name, market, action, price, qty, fee, tax, "
            "strategy, memo, trade_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            TRADES,
        )
        conn.executemany(
            "INSERT INTO recommendation_history (date, source, rank, code, name, market, score, "
            "price_at_rec, tags_json) VALUES (?,?,?,?,?,?,?,?,?)",
            PICKS,
        )
        conn.commit()
    finally:
        conn.close()
    rules = out / "alert_rules.json"
    rules.write_text(json.dumps(RULES, ensure_ascii=False), encoding="utf-8")
    pf = out / "server_portfolio.json"
    pf.write_text(json.dumps(PORTFOLIO, ensure_ascii=False), encoding="utf-8")
    return {"db": db, "rules": rules, "portfolio": pf}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    for name, p in build_sd_p8(args.out).items():
        print(f"{name}: {p}", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

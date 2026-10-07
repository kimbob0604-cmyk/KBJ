"""과거 날짜 state 를 여러 개 만들어 flows.json 을 누적시킨다.

엔진 코드는 건드리지 않는다. 데모 DB 를 flowlab/cache 로 **복사**한 뒤 그 복사본에
날짜별 종목 스냅샷(snap)을 채우고, 엔진의 `board.engine.build.run(db, asof)` 을
날짜마다 호출한다. state 는 엔진이 직접 쓴다 — 여기서 newhigh.json 을 손으로
만들지 않는다. 그 위에 `flowlab.flows` 를 날짜순으로 돌려 flows.json 을 쌓는다.

스냅샷 근사 (원본 DB 에는 마지막 날 것만 있다)
  거래대금 = 그날 종가 × 거래량              → turnover_is_estimate=1
  시가총액 = 최신 상장주식수(최신 시총/최신 종가) × 그날 종가
합성 데이터에 붙는 근사이므로 산출물은 전부 source="demo" 로 남는다.
"""
from __future__ import annotations

import shutil
import sqlite3

from . import config as C

WORK_DB = C.CACHE / "backfill.db.demo"


def _trading_days(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT asof FROM px ORDER BY asof")]


def _latest_snap(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT code, name, market, close, mktcap FROM snap "
        "WHERE asof=(SELECT MAX(asof) FROM snap)").fetchall()
    out = {}
    for code, name, market, close, mktcap in rows:
        shares = (mktcap * 1e8 / close) if (close and mktcap) else None
        out[code] = dict(name=name, market=market, shares=shares)
    return out


def _fill_snaps(conn, dates: list[str], meta: dict, log=print) -> int:
    """날짜별 snap 행을 px 에서 만들어 넣는다. 이미 있는 (code, asof) 는 건너뛴다."""
    have = {(c, a) for c, a in conn.execute("SELECT code, asof FROM snap")}
    rows = []
    for d in dates:
        px = conn.execute(
            "SELECT p.code, p.close, p.volume, "
            "(SELECT close FROM px q WHERE q.code=p.code AND q.asof<p.asof "
            " ORDER BY q.asof DESC LIMIT 1) AS prev "
            "FROM px p WHERE p.asof=?", (d,)).fetchall()
        for code, close, vol, prev in px:
            m = meta.get(code)
            if not m or (code, d) in have or not close:
                continue
            chg = round((close / prev - 1) * 100, 2) if prev else None
            mktcap = round(m["shares"] * close / 1e8, 1) if m["shares"] else None
            rows.append((code, d, m["name"], m["market"], close, chg, vol,
                         round(close * (vol or 0) / 1e8, 1), mktcap, 1, "demo"))
    conn.executemany(
        "INSERT OR REPLACE INTO snap(code,asof,name,market,close,chg_pct,volume,"
        "turnover,mktcap,turnover_is_estimate,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def run(days: int = 60, source: str = C.SOURCE_DEMO, run_flows: bool = True,
        include_last: bool = True, log=print) -> list[str]:
    if source != C.SOURCE_DEMO:
        raise RuntimeError("backfill 은 합성(demo) 소스 전용이다. 실데이터 state 는 엔진 --daily 가 쌓는다")
    from . import demo
    if not demo.available():
        raise FileNotFoundError(f"{demo.DB} 없음 — `python3 -m board.run --demo` 를 먼저 돌려라")

    from board.engine import build as B
    from board.engine.config import load as load_cfg

    shutil.copyfile(demo.DB, WORK_DB)
    conn = sqlite3.connect(WORK_DB)
    all_days = _trading_days(conn)
    last = all_days[-1]
    picked = all_days[-(days + 1):-1] if days > 0 else []
    if include_last:
        picked = picked + [last]
    meta = _latest_snap(conn)
    n = _fill_snaps(conn, picked, meta, log=log)
    conn.close()
    log(f"작업 DB {WORK_DB.name} · 날짜 {len(picked)}개 ({picked[0]}~{picked[-1]}) "
        f"· 스냅샷 {n:,}행 보강")

    cfg = load_cfg()
    made = []
    for i, d in enumerate(picked, 1):
        # 엔진이 state/{날짜}/ 를 직접 쓴다. 날짜순이라 다음 날의 신규/이어감 판정이 선다.
        B.run(str(WORK_DB), asof=d, cfg=cfg, log=lambda *_: None)
        made.append(d.replace("-", ""))
        if i % 10 == 0 or i == len(picked):
            log(f"  엔진 {i}/{len(picked)} — {d}")

    if run_flows:
        from . import flows
        for i, d in enumerate(made, 1):
            flows.run(date=d, source=source, write_report=False, log=lambda *_: None)
            if i % 10 == 0 or i == len(made):
                log(f"  flows {i}/{len(made)} — {d}")
    return made

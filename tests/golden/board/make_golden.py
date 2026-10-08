"""신고가 보드 골든 — 합성 입력을 만들고 **legacy board 엔진으로** 기대 산출을 캡처한다.

docs/p3_design.md §4.1 '골든 비교 방법'·§8.1, D-P3-10(골든 먼저 → 이식 → 비교 → shim).

쓰는 법(레포 루트, shim 전 legacy 엔진이 있을 때만 의미가 있다)::

    uv run python tests/golden/board/make_golden.py --seed 20260826 --days 30 --stocks 300

- 입력은 ET `board/tests/demo.py:_series`:41 방식의 랜덤워크(시드 고정)에 사례를 일부러
  심는다: 돌파·근접(추세가 있는 종목), 분할 계단(공시 대조 action·none·unknown), 거래정지 공백,
  상장 60일 미만, 시총 하한 미달·모름, 종가/고가 기준 차이, 거래대금 없음·0, 등락률 없음,
  KRX 확정·KIS 잠정·일부만 확정, 하루 늦은 스냅, 거래대금 단위 뒤집힘 하루, 테마 시드
  비상장·개명·오타, 섹터 분류 혼재, 거래량 이상.
- legacy 실행: 임시 sqlite 에 ET `pipeline.sync_px` 순서대로(수집 → `roll_alltime` → `build.run`)
  30일을 연속으로 돌리고 `state/<날짜>/{universe,newhigh,sectors,events,rankings}.json` 을
  `expected/day_XX.json.gz` 로 모은다. 같은 순간의 DB 상태(엔진이 읽는 것 전부)를
  `inputs/day_XX.json.gz` 로 남긴다 — kbj `compute_day` 는 이 입력만으로 같은 산출을 내야 한다.
- 숫자는 전부 가짜다(공개 레포에 넣어도 된다 — CLAUDE.md 2장, U3).
- shim(D-P3-10 ④) 뒤에는 legacy 가 kbj 를 부르므로 다시 돌려도 의미가 없다. 골든은 그 뒤
  고치지 않는다(`tests/golden/test_board_golden_meta.py` 가 체크섬을 고정한다). 바꿀 일이 생기면
  새 골든 세트 + ADR.

legacy 패키지(`board.*`)는 정적 import 하지 않는다(pyright·ruff 대상 밖 — ADR 0003). importlib 로
불러 몇 군데(state 폴더·테마·분류 사전)만 바꿔 끼운다. 난수는 합성 데이터용(암호 용도 아님).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import random
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LEGACY_ET = REPO / "legacy" / "etf_traker"
FILES = ("universe", "newhigh", "sectors", "events", "rankings")

SECTORS = [
    "반도체장비", "반도체소재부품", "전자제품", "2차전지", "자동차부품", "조선",
    "방산", "건설", "화학", "제약", "은행", "소프트웨어",
]  # fmt: skip
TAXONOMY_KNOWN = SECTORS[:10]  # 분류 사전에 없는 섹터 둘 → 랭킹 '미배정'
THEMES = [
    ("t_ai", "AI반도체", "chain", ["설계", "장비", "소재"]),
    ("t_bat", "2차전지", "chain", ["소재", "셀"]),
    ("t_ship", "조선기자재", "flat", None),
    ("t_def", "방산수출", "flat", None),
    ("t_bio", "바이오시밀러", "flat", None),
    ("t_grid", "전력망", "chain", ["송전", "변압기"]),
]


# ─────────────────────────── 합성 입력 ───────────────────────────
def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _walk(
    rng: random.Random, days: list[date], drift: float, vol: float, base: float
) -> list[dict[str, Any]]:
    px = base
    rows: list[dict[str, Any]] = []
    for d in days:
        px *= 1 + rng.gauss(drift, vol)
        px = max(px, 500.0)
        hi = px * (1 + abs(rng.gauss(0, 0.012)))
        lo = px * (1 - abs(rng.gauss(0, 0.012)))
        op = min(max(px * (1 + rng.gauss(0, 0.006)), lo), hi)
        rows.append(
            dict(
                asof=d.isoformat(),
                open=round(op, 0),
                high=round(hi, 0),
                low=round(lo, 0),
                close=round(px, 0),
                volume=round(rng.uniform(3e4, 4e6), 0),
            )
        )
    return rows


def _split_step(rows: list[dict[str, Any]], at: int, ratio: float) -> None:
    """at 번째 봉부터 가격을 ratio 로 나눈다(수정주가 미반영 계단)."""
    for r in rows[at:]:
        for k in ("open", "high", "low", "close"):
            r[k] = round(r[k] / ratio, 0)


def _source(di: int, i: int) -> str:
    """그날 종가 원천 — 짝수 날 KRX 확정, 홀수 날 KIS 잠정, 7일째는 60% 만 확정(일부 확정 안내)."""
    if di % 2 == 0:
        return "krx"
    if di == 7:
        return "krx" if i % 5 < 3 else "kis"
    return "kis"


def generate(seed: int, n_days: int, n_stocks: int) -> dict[str, Any]:
    """합성 세계 하나 — 시세·스냅·사전·실행 기록. 결정적(같은 시드면 같은 결과)."""
    rng = random.Random(seed)  # noqa: S311 — 합성 데이터(재현용 시드)
    pre = 320  # 골든 첫날 전 이력(252영업일 창 + 여유)
    all_days = _weekdays(date(2025, 6, 2), pre + n_days)
    golden = all_days[pre:]

    stocks: list[dict[str, Any]] = []
    for i in range(n_stocks):
        code = f"{900000 + i * 10:06d}"
        name = f"합성종목{i:03d}"
        stocks.append(dict(code=code, name=name, i=i))
    # 종목 종류 — 우선주(코드 끝자리 ≠ 0 + 이름 '우')·스팩·리츠
    stocks[5]["code"], stocks[5]["name"] = "900055", "합성종목004우"
    stocks[6]["code"], stocks[6]["name"] = "900067", "합성종목0042우B"
    stocks[7]["name"] = "합성제1호스팩"
    stocks[8]["name"] = "합성인프라리츠"

    series: dict[str, list[dict[str, Any]]] = {}
    old_hist: set[str] = set()
    listed: dict[str, int] = {}
    for s in stocks:
        i = s["i"]
        drift = 0.0
        r = rng.random()
        if r < 0.22:
            drift = rng.uniform(0.002, 0.006)  # 추세 — 돌파·근접이 창 안에서 나온다
        elif r < 0.30:
            drift = rng.uniform(-0.004, -0.001)
        rows = _walk(rng, all_days, drift, rng.uniform(0.012, 0.026), 10000 * rng.uniform(0.3, 8))
        if i in (10, 11):  # 상장 60일 미만으로 시작 → 창 안에서 60일을 넘긴다
            listed[s["code"]] = pre - 40 + (i - 10) * 5
            rows = rows[listed[s["code"]] :]
        if i in (12, 13, 14):  # 분할 계단(5:1) — 252일 창 안
            _split_step(rows, pre - 120 + (i - 12) * 7, 5.0)
        if i == 15:  # 골든 창 안 거래정지 8영업일 → 재개일 등락률은 공백을 삼킨다
            rows = rows[: pre + 6] + rows[pre + 14 :]
        if i == 16:  # 기준일 봉이 없는 날(정지 중) → no_price_series
            rows = rows[: pre + 20] + rows[pre + 23 :]
        if i == 17:  # 고가로만 뚫는 날: 마지막 열흘 고가를 크게
            for rr in rows[-12:]:
                rr["high"] = round(rr["high"] * 1.06, 0)
        if i == 18:  # 고저가 없는 봉 — 저항두께가 종가 한 점으로 본다
            for rr in rows[-80:]:
                rr["low"] = None
                rr["high"] = None
        if 40 <= i < 45:  # 거래량 이상(탐지기 4) — 골든 창 안 하루
            spike = rows[pre + (i - 40) * 5 + 2]
            spike["volume"] = round(spike["volume"] * 9, 0)
        if i == 19:  # 거래량 모름(None)
            for rr in rows[-5:]:
                rr["volume"] = None
        if i % 2 == 0 and i not in (10, 11):
            old_hist.add(s["code"])  # 사상 최고가를 과거에(역사적 신고가가 아무 데서나 안 뜨게)
        series[s["code"]] = rows

    # 섹터 사전 — 대부분 board48, 셋은 다른 분류(혼재 안내), 넷은 미분류
    sector_map: dict[str, tuple[str, str]] = {}
    for s in stocks:
        i = s["i"]
        if i in (20, 21, 22, 23):
            continue
        tax = "naver_upjong" if i in (24, 25, 26) else "board48"
        sector_map[s["code"]] = (SECTORS[i % len(SECTORS)], tax)

    # 테마 사전 — 시드는 종목명, 비상장·개명·오타가 섞인다
    # 덧붙일 시드: 비상장(알려진 것)·개명(표로 찾는다)·오타(near 후보가 나온다)
    extra_seed = {
        0: ("설계", "합성비상장반도체"),
        1: ("소재", "옛합성종목100"),
        2: ("", "합성종목99"),
    }
    themes: list[dict[str, Any]] = []
    for k, (tid, tname, axis, stages) in enumerate(THEMES):
        members = [s["name"] for s in stocks if s["i"] % 9 == k][:14]
        stage_x, name_x = extra_seed.get(k, ("", ""))
        if stages:
            staged = {st: members[j :: len(stages)] for j, st in enumerate(stages)}
            if name_x:
                staged[stage_x].append(name_x)
            themes.append(dict(id=tid, name=tname, axis=axis, parent=None, seeds=staged))
        else:
            flat = [*members, name_x] if name_x else list(members)
            themes.append(dict(id=tid, name=tname, axis=axis, parent=None, seeds=flat))
    themes_yaml = dict(
        axes=dict(chain=dict(stages=["설계", "장비", "소재", "셀", "송전", "변압기"])),
        unlisted={"합성비상장반도체": "비상장(합성 사례)"},
        renames={"옛합성종목100": "합성종목100"},
        themes=themes,
    )
    taxonomy = dict(
        meta=dict(taxonomy="board48"),
        sectors=[dict(name=n) for n in TAXONOMY_KNOWN],
    )

    # 공시 대조 — 분할 계단 셋: action(가드 유지)·none(가드 해제)·unknown(물어보지 못함)
    split_check = [
        [stocks[12]["code"], golden[0].isoformat(), "action", "주식분할결정(합성)"],
        [stocks[13]["code"], golden[0].isoformat(), "none", "공시 없음(합성)"],
        [stocks[14]["code"], golden[0].isoformat(), "unknown", "공시 조회 실패: corp_code 없음"],
    ]

    # 날마다 스냅 — 시총(억원)·거래대금(억원)·등락률·원천
    caps: dict[str, float | None] = {}
    for s in stocks:
        i = s["i"]
        if i in (30, 31):
            caps[s["code"]] = None  # 시총 모름
        elif i % 7 == 0:
            caps[s["code"]] = round(rng.uniform(200, 900), 1)  # 하한 미달
        elif i % 5 == 0:
            caps[s["code"]] = round(rng.uniform(12000, 400000), 1)  # 1조 이상
        else:
            caps[s["code"]] = round(rng.uniform(1000, 12000), 1)
    snaps: dict[str, dict[str, Any] | None] = {}
    for di, d in enumerate(golden):
        ds = d.isoformat()
        if di == 10:
            snaps[ds] = None  # 그날 스냅이 없다 → 전날 스냅을 쓴다(스냅 늦음 안내)
            continue
        day_rows: dict[str, Any] = {}
        for s in stocks:
            code, i = s["code"], s["i"]
            ser = series[code]
            upto = [r for r in ser if r["asof"] <= ds]
            bar = upto[-1] if upto and upto[-1]["asof"] == ds else None
            prev = upto[-2] if bar is not None and len(upto) > 1 else None
            close = upto[-1]["close"] if upto else None  # 정지 중이면 마지막 종가
            vol = bar["volume"] if bar else 0.0
            chg = None
            if bar and prev and prev["close"]:
                chg = round((bar["close"] / prev["close"] - 1) * 100, 2)
            if i % 11 == 0 or i == 15:
                chg = None  # 원천이 등락률을 안 줌 → 일봉 파생
            turnover: float | None = None
            if close and vol is not None:
                turnover = round(close * vol / 1e8 * rng.uniform(0.95, 1.05), 1)
            if i % 13 == 0:
                turnover = None  # 원천이 거래대금을 안 줌 → 종가×거래량 추정
            if i % 17 == 0 and vol:
                turnover = 0.0  # 거래량이 있는데 0 — 추정으로 바꾼다
            if di == 20 and turnover:
                turnover = round(turnover / 1e6, 6)  # 단위 뒤집힘(1e6 배 작게)
            cap = caps[code]
            if cap is not None and close:
                cap = round(cap * (1 + (di - 15) * 0.002), 1)
            day_rows[code] = dict(
                name=s["name"],
                market="KOSDAQ" if i % 3 == 0 else "KOSPI",
                close=close,
                chg_pct=chg,
                volume=vol,
                turnover=turnover,
                mktcap=cap,
                turnover_is_estimate=0,
                source=_source(di, i),
            )
        # 시세가 아예 없는 스냅 둘(상장 첫날 등)
        for extra in ("999990", "999980"):
            day_rows[extra] = dict(
                name=f"합성신규{extra[-2:]}", market="KOSPI", close=10000.0, chg_pct=1.0,
                volume=1000.0, turnover=1.0, mktcap=1500.0, turnover_is_estimate=0,
                source=_source(di, 0),
            )  # fmt: skip
        snaps[ds] = day_rows

    # 실행 기록(run_log) — 펀드 제외(성공 메모)·KRX 잠정 사유(성공 메모)·수집 실패(배너로 간다)
    run_log: dict[str, list[list[Any]]] = {}
    for di, d in enumerate(golden):
        ds = d.isoformat()
        logs: list[list[Any]] = []
        if di != 3:
            logs.append(
                ["funds_excluded", 1, f"{5 + di % 3}종목 제외 / 이름 판정 {5 + di % 3}종목"]
            )
        if di % 2 == 1:
            logs.append(["close_krx", 1, "KRX 정규장 일별 시세 공표 전(합성)"])
        if di in (4, 18):
            logs.append(["px_refresh_lost", 0, "일봉 갱신 일부 실패 — 3종목(합성)"])
        if di == 18:
            logs.append(["sector", 0, "섹터 사전 동기화 실패(합성)"])
        run_log[ds] = logs

    return dict(
        seed=seed,
        days=[d.isoformat() for d in golden],
        series=series,
        old_hist=sorted(old_hist),
        sector_map={c: list(v) for c, v in sector_map.items()},
        themes_yaml=themes_yaml,
        taxonomy=taxonomy,
        split_check=split_check,
        snaps=snaps,
        run_log=run_log,
    )


# ─────────────────────────── legacy 실행 ───────────────────────────
def _legacy() -> dict[str, Any]:
    if str(LEGACY_ET) not in sys.path:
        sys.path.insert(0, str(LEGACY_ET))
    mods = {n: importlib.import_module(f"board.engine.{n}") for n in ("build", "db", "newhigh")}
    mods["rankings"] = importlib.import_module("board.engine.rankings")
    mods["config"] = importlib.import_module("board.engine.config")
    return mods


def _write_gz(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 키 순서를 바꾸지 않는다 — 스냅·테마 단계의 순서가 산출 순서를 정한다(sqlite·yaml 읽은 순서)
    raw = json.dumps(obj, ensure_ascii=False, indent=None).encode()
    # mtime=0 — 같은 내용이면 같은 바이트(체크섬 고정)
    with path.open("wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(raw)


def capture(world: dict[str, Any], out: Path) -> dict[str, Any]:
    """legacy 엔진으로 30일을 돌려 inputs·expected 를 쓴다. 반환: META."""
    m = _legacy()
    B, DB, nh, R, C = m["build"], m["db"], m["newhigh"], m["rankings"], m["config"]
    cfg = C.load()
    themes_yaml, taxonomy = world["themes_yaml"], world["taxonomy"]
    tmp = Path(tempfile.mkdtemp(prefix="kbj-golden-"))
    B.STATE = str(tmp / "state")
    B.load_themes = lambda: themes_yaml
    R.load_taxonomy = lambda: taxonomy

    conn = DB.connect(str(tmp / "board.db"))
    px = [
        (code, r["asof"], r["open"], r["high"], r["low"], r["close"], r["volume"], "synthetic")
        for code, rows in world["series"].items()
        for r in rows
    ]
    conn.executemany("INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)", px)
    conn.executemany(
        "INSERT OR REPLACE INTO sector_map VALUES(?,?,?,?)",
        [(c, s, t, "synthetic") for c, (s, t) in world["sector_map"].items()],
    )
    DB.put_split_check(conn, [tuple(x) for x in world["split_check"]])
    conn.commit()

    days: list[str] = world["days"]
    old_hist = set(world["old_hist"])
    # 골든 첫날 전까지의 스칼라(ET --init) — 절반은 사상 최고가를 과거로 올린다(demo.py 와 같다)
    alltime: dict[str, dict[str, Any]] = {}
    rng = random.Random(world["seed"] + 1)  # noqa: S311
    for code, rows in world["series"].items():
        before = [r for r in rows if r["asof"] < days[0]]
        a = nh.roll_alltime(None, before, cfg)
        if code in old_hist and a.get("hi"):
            k = rng.uniform(1.05, 2.0)
            a["hi"], a["cl"] = a["hi"] * k, a["cl"] * k
            a["prev_hi"], a["prev_cl"] = a["hi"], a["cl"]
            a["n_days"] = 1200
        alltime[code] = a

    meta_days = []
    for di, ds in enumerate(days):
        prev_ds = days[di - 1] if di else None
        snap = world["snaps"][ds]
        if snap is not None:
            conn.executemany(
                "INSERT OR REPLACE INTO snap VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (c, ds, x["name"], x["market"], x["close"], x["chg_pct"], x["volume"],
                     x["turnover"], x["mktcap"], x["turnover_is_estimate"], x["source"])
                    for c, x in snap.items()
                ],
            )  # fmt: skip
        # 수집 → 스칼라 갱신(ET pipeline.sync_px 의 ALLTIME_SQL 순서)
        for code, rows in world["series"].items():
            upto = [r for r in rows if r["asof"] <= ds]
            alltime[code] = nh.roll_alltime(alltime.get(code), upto, cfg)
        conn.executemany(
            "INSERT OR REPLACE INTO alltime(code,hi,hi_date,cl,cl_date,prev_hi,prev_cl,"
            "first_date,last_date,n_days,suspect,suspect_date,suspect_note,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (c, a["hi"], a["hi_date"], a["cl"], a["cl_date"], a["prev_hi"], a["prev_cl"],
                 a["first_date"], a["last_date"], a["n_days"], 1 if a["suspect"] else 0,
                 a["suspect_date"], a["suspect_note"], "synthetic")
                for c, a in alltime.items()
            ],
        )  # fmt: skip
        for step, ok, note in world["run_log"][ds]:
            DB.log_step(conn, ds, step, bool(ok), note)
        conn.commit()

        # 엔진이 읽는 DB 상태 전부(= kbj BoardInputs)
        snap_now, snap_asof = DB.snapshot(conn, ds)
        prev_newhigh = B.read(prev_ds, "newhigh.json") if prev_ds else None
        prev_rankings = B.read(prev_ds, "rankings.json") if prev_ds else None
        inputs = dict(
            asof=ds,
            prev_asof=[*DB.trading_days(conn, ds, 2), None, None][1],
            snapshot={c: {k: v for k, v in x.items() if k not in ("code", "asof")}
                      for c, x in snap_now.items()},
            snap_asof=snap_asof,
            alltime={r["code"]: {k: r[k] for k in r.keys() if k not in ("code", "updated_at")}
                     for r in conn.execute("SELECT * FROM alltime")},
            sectors=DB.sector_of(conn),
            taxonomy_counts=[list(r) for r in conn.execute(
                "SELECT taxonomy, COUNT(*) c FROM sector_map GROUP BY taxonomy ORDER BY c DESC")],
            prev_ranks=B._prev_labels(conn, prev_ds, cfg["newhigh"]["default_basis"]),
            split_cleared=sorted(DB.split_cleared(conn)),
            split_unknown=[list(x) for x in DB.split_unknown(conn)],
            collect_notes=[[x["step"], x["note"]] for x in DB.missing(conn, ds)],
            close_note=DB.note(conn, ds, "close_krx"),
            funds_excluded=DB.note(conn, ds, "funds_excluded"),
        )  # fmt: skip
        # 전일 newhigh·rankings(근접 기준·순위 변동의 입력)는 전날 expected 에 있다 —
        # 시험이 거기서 꺼낸다.
        # 여기서는 legacy 가 실제로 읽은 것과 같은지만 확인한다.
        assert (prev_newhigh is None) == (prev_rankings is None) == (prev_ds is None)
        logs: list[str] = []
        B.run(str(tmp / "board.db"), asof=ds, cfg=cfg, log=logs.append)
        expected = {n: B.read(ds, f"{n}.json") for n in FILES}
        _write_gz(out / "inputs" / f"day_{di:02d}.json.gz", inputs)
        _write_gz(out / "expected" / f"day_{di:02d}.json.gz", expected)
        meta_days.append(ds)

    dates = sorted({r["asof"] for rows in world["series"].values() for r in rows})
    pos = {d: k for k, d in enumerate(dates)}
    common = dict(
        # 일봉은 [날짜 번호, 시가, 고가, 저가, 종가, 거래량] 으로 줄여 담는다(크기)
        dates=dates,
        series={c: [[pos[r["asof"]], r["open"], r["high"], r["low"], r["close"], r["volume"]]
                    for r in rows] for c, rows in world["series"].items()},
        themes_yaml=themes_yaml,
        taxonomy=taxonomy,
        config={k: cfg[k] for k in (
            "newhigh", "proximity", "volume", "resistance", "giveback", "themes", "display",
            "integrity", "detect", "rankings")},
    )  # fmt: skip
    _write_gz(out / "inputs" / "common.json.gz", common)
    conn.close()
    return dict(days=meta_days)


def checksums(out: Path) -> dict[str, str]:
    return {
        str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.glob("*/*.json.gz"))
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--seed", type=int, default=20260826)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--stocks", type=int, default=300)
    ap.add_argument("--out", type=Path, default=HERE)
    ap.add_argument(
        "--allow-shim",
        action="store_true",
        help="legacy 엔진이 이미 kbj shim 이어도 돌린다(시험용 — 골든 폴더에는 쓰지 말 것)",
    )
    a = ap.parse_args(argv)
    shim = "compute_day" in (LEGACY_ET / "board" / "engine" / "build.py").read_text(
        encoding="utf-8"
    )
    if shim and not a.allow_shim:
        print(
            "legacy board 엔진이 이미 kbj 를 부르는 shim 이다 — 지금 캡처하면 kbj 가 자기 자신과 "
            "비교된다(D-P3-10). 골든은 고치지 않는다(바꿀 일은 새 골든 세트 + ADR).",
            file=sys.stderr,
        )
        return 2
    if shim and a.out.resolve() == HERE:
        print("--allow-shim 으로는 골든 폴더에 쓰지 않는다 — --out 을 다른 곳으로", file=sys.stderr)
        return 2
    world = generate(a.seed, a.days, a.stocks)
    meta = capture(world, a.out)
    commit = subprocess.run(  # noqa: S603 — 고정 인자
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],  # noqa: S607
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    meta_doc = dict(
        legacy_commit=commit,
        legacy_engine="legacy/etf_traker/board/engine (shim 전 — D-P3-10 ①)",
        seed=a.seed,
        n_days=a.days,
        n_stocks=a.stocks,
        days=meta["days"],
        files=list(FILES),
        compare=dict(rel_tol=1e-9, abs_tol=1e-6, ignore=["generated_at"]),
        checksums=checksums(a.out),
    )
    (a.out / "META.json").write_text(
        json.dumps(meta_doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"골든 {len(meta['days'])}일 · 종목 {a.stocks} · legacy {commit} → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

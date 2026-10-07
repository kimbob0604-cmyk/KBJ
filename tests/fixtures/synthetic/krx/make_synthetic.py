"""SYNTHETIC KRX·금융위 시세 fixture 생성기 — 묶음 D 시험용(시드 고정, 실측 아님).

KRX OpenAPI·금융위 주식/지수 시세는 로그인 등급이라 원본 응답과 그것으로 만든 fixture 를 레포에
넣지 않는다(CLAUDE.md §2, DATA_TIERS §4, ADR 0001 U3). 이 스크립트는 **실측을 읽지 않고** 시드 고정
난수만으로 응답 행 **모양**(필드 이름·문자열 숫자·빈 값)을 흉내 낸 합성 데이터를 만든다. 필드 이름은
KRX OpenAPI 카탈로그·금융위 V1 문서 기준이라 그 자체가 [실측 필요]다(probe_results §7 #8·#14).

    uv run python tests/fixtures/synthetic/krx/make_synthetic.py          # 커밋본과 비교(다르면 1)
    uv run python tests/fixtures/synthetic/krx/make_synthetic.py --write  # 다시 쓴다

만드는 것(이 폴더):
- `stock_daily.json`   — `sto/stk_bydd_trd`·`ksq_bydd_trd` 두 거래일(2026-09-29·30). 종목·코드·
  이름은 가짜(`합성…`, 코드 99xxxx·영숫자 단축코드 1개). 둘째 날 전일 대비는 첫날 종가 기준으로
  맞춘다. 거래정지 꼴 한 행(거래량 0, 시·고·저 0 [추정 — KRX 표기 실측 필요])
- `etf_daily.json`     — `etp/etf_bydd_trd` 두 거래일. 순자산 = 상장좌수 × NAV(원 단위 반올림),
  설정·환매로 좌수가 바뀐다. 둘째 날 신규 상장 1종목(전날 행 없음 — metrics §4 의 3번)
- `etn_daily.json`     — `etp/etn_bydd_trd` 하루
- `index_daily.json`   — `idx/kospi_dd_trd`·`kosdaq_dd_trd` 하루(지수 이름은 실제 계열 이름, 값은
  합성)
- `base_info.json`     — `sto/stk_isu_base_info`·`ksq_isu_base_info`(표준코드는 합성 ISIN + 검사
  숫자)
- `fsc_stock_price.json` — 금융위 주식시세(15094808) `item` 하루 — `stock_daily.json` 같은 날과 값이
  같다(교차검증 시험용)
- `fsc_index_price.json` — 금융위 지수시세 `item` 두 거래일(코스피·코스닥)

opt_daily.json·fut_daily.json 은 여기서 만들지 않는다 — legacy GX 생성기
(`legacy/gexlab/scripts/make_synthetic_fixtures.py`)의 출력 복사본이다(README.md).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SEED = "kbj-p2-krx-synthetic-v1"
SYNTH = (
    "SYNTHETIC — tests/fixtures/synthetic/krx/make_synthetic.py(시드 고정)가 만든 합성 데이터. "
    "실측 아님"
)
DAYS = ("20260929", "20260930")

Row = dict[str, str]
Json = dict[str, Any]


def rng(name: str) -> random.Random:
    """fixture 마다 따로 시드한 난수 — 한 fixture 를 고쳐도 다른 fixture 값이 바뀌지 않는다."""
    return random.Random(f"{SEED}:{name}")  # noqa: S311 — 암호용이 아니라 재현 가능한 합성 값


def isin(short: str) -> str:
    """합성 ISIN `KR7<단축코드>00<검사 숫자>` — 글자는 A=10…Z=35 로 바꿔 Luhn 검사 숫자를 붙인다."""
    body = f"KR7{short}00"
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return body + str((10 - total % 10) % 10)


def tick(price: float) -> int:
    """호가 단위로 반올림(합성 규칙 — 실제 KRX 호가 단위표를 단순화했다)."""
    unit = 1 if price < 2_000 else 5 if price < 20_000 else 50 if price < 200_000 else 500
    return max(unit, round(price / unit) * unit)


def pct(chg: float, base: float) -> str:
    return f"{100 * chg / base:.2f}"


# ── 주식 ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    market: str  # KOSPI·KOSDAQ
    sect: str  # 소속부(코스닥) — 코스피는 빈 값
    kind: str  # 보통주·우선주
    shares: int
    base: float  # 첫날 전일 종가
    suspended: bool = False


STOCKS: tuple[Stock, ...] = (
    Stock("990010", "합성전자", "KOSPI", "", "보통주", 5_969_782_550, 61_300),
    Stock("990015", "합성전자우", "KOSPI", "", "우선주", 822_886_700, 50_100),
    Stock("990020", "합성화학", "KOSPI", "", "보통주", 70_592_343, 402_000),
    Stock("990030", "합성금융지주", "KOSPI", "", "보통주", 389_634_335, 59_800),
    Stock("9900K0", "합성신규상장", "KOSPI", "", "보통주", 12_000_000, 18_250),
    Stock("990040", "합성건설", "KOSPI", "", "보통주", 35_000_000, 1_985, suspended=True),
    Stock("998010", "가상바이오", "KOSDAQ", "우량기업부", "보통주", 151_200_000, 92_400),
    Stock("998020", "가상소재", "KOSDAQ", "벤처기업부", "보통주", 48_300_000, 7_430),
    Stock("998030", "가상게임즈", "KOSDAQ", "중견기업부", "보통주", 24_150_000, 31_850),
    Stock("998040", "가상로봇", "KOSDAQ", "기술성장기업부", "보통주", 19_870_000, 15_620),
)


def stock_rows() -> dict[str, dict[str, list[Row]]]:
    """시장 → 날짜 → 행. 둘째 날 대비는 첫날 종가 기준."""
    r = rng("stock_daily")
    out: dict[str, dict[str, list[Row]]] = {"kospi": {}, "kosdaq": {}}
    prev = {s.code: tick(s.base) for s in STOCKS}
    for day in DAYS:
        for s in STOCKS:
            p0 = prev[s.code]
            if s.suspended:
                close, op, hi, lo, vol, val = p0, 0, 0, 0, 0, 0
            else:
                close = tick(p0 * (1 + r.uniform(-0.045, 0.045)))
                op = tick(p0 * (1 + r.uniform(-0.01, 0.01)))
                hi = max(close, op, tick(max(close, op) * (1 + r.uniform(0.0, 0.02))))
                lo = min(close, op, tick(min(close, op) * (1 - r.uniform(0.0, 0.02))))
                vol = int(s.shares * r.uniform(0.0008, 0.006))
                vwap = (op + hi + lo + close) / 4
                val = int(vol * vwap)
            prev[s.code] = close
            row: Row = {
                "BAS_DD": day,
                "ISU_CD": s.code,
                "ISU_NM": s.name,
                "MKT_NM": s.market,
                "SECT_TP_NM": s.sect,
                "TDD_CLSPRC": str(close),
                "CMPPREVDD_PRC": str(close - p0),
                "FLUC_RT": pct(close - p0, p0),
                "TDD_OPNPRC": str(op),
                "TDD_HGPRC": str(hi),
                "TDD_LWPRC": str(lo),
                "ACC_TRDVOL": str(vol),
                "ACC_TRDVAL": str(val),
                "MKTCAP": str(close * s.shares),
                "LIST_SHRS": str(s.shares),
            }
            out[s.market.lower()].setdefault(day, []).append(row)
    return out


def base_info_rows() -> dict[str, list[Row]]:
    """종목기본정보 — 응답에 기준일이 있는지 [실측 필요]라 `BAS_DD` 를 넣지 않는다."""
    out: dict[str, list[Row]] = {"kospi": [], "kosdaq": []}
    for i, s in enumerate(STOCKS):
        out[s.market.lower()].append(
            {
                "ISU_CD": isin(s.code),
                "ISU_SRT_CD": s.code,
                "ISU_NM": f"{s.name}{'보통주' if s.kind == '보통주' else ''}",
                "ISU_ABBRV": s.name,
                "ISU_ENG_NM": f"Synthetic Co {i + 1:02d}",
                "LIST_DD": f"{1990 + 3 * i:04d}0{1 + i % 9}15",
                "MKT_TP_NM": s.market,
                "SECUGRP_NM": "주권",
                "SECT_TP_NM": s.sect,
                "KIND_STKCERT_TP_NM": s.kind,
                "PARVAL": "무액면" if i == 3 else str(100 if s.market == "KOSPI" else 500),
                "LIST_SHRS": str(s.shares),
            }
        )
    return out


def fsc_stock_items(stocks: dict[str, dict[str, list[Row]]], day: str) -> list[Row]:
    """금융위 주식시세 `item` — KRX 같은 날 행과 값이 같다(시장 순서는 코스피·코스닥)."""
    out: list[Row] = []
    for market in ("kospi", "kosdaq"):
        for k in stocks[market][day]:
            out.append(
                {
                    "basDt": day,
                    "srtnCd": f"A{k['ISU_CD']}" if k["ISU_CD"].endswith("5") else k["ISU_CD"],
                    "isinCd": isin(k["ISU_CD"]),
                    "itmsNm": k["ISU_NM"],
                    "mrktCtg": k["MKT_NM"],
                    "clpr": k["TDD_CLSPRC"],
                    "vs": k["CMPPREVDD_PRC"],
                    "fltRt": k["FLUC_RT"],
                    "mkp": k["TDD_OPNPRC"],
                    "hipr": k["TDD_HGPRC"],
                    "lopr": k["TDD_LWPRC"],
                    "trqu": k["ACC_TRDVOL"],
                    "trPrc": k["ACC_TRDVAL"],
                    "lstgStCnt": k["LIST_SHRS"],
                    "mrktTotAmt": k["MKTCAP"],
                }
            )
    return out


# ── ETF·ETN ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Etf:
    code: str
    name: str
    index: str
    nav: float  # 첫날 전일 NAV
    shares: int  # 첫날 전일 상장좌수
    cu: int  # 설정·환매 단위(좌)
    lev: float  # 기초지수 대비 배율
    listed_on: str = DAYS[0]


ETFS: tuple[Etf, ...] = (
    Etf("995010", "합성 200", "코스피 200", 52_310.25, 92_150_000, 100_000, 1.0),
    Etf("995020", "합성 200레버리지", "코스피 200", 21_840.10, 166_400_000, 200_000, 2.0),
    Etf("995030", "합성 국고채3년", "KTB 3Y 지수", 104_520.77, 6_830_000, 50_000, 0.02),
    Etf("995040", "합성 미국S&P500", "S&P 500", 19_970.42, 98_300_000, 100_000, 0.6),
    Etf("995050", "합성 신규상장 테마", "합성 테마 지수", 10_000.00, 0, 100_000, 1.0, DAYS[1]),
)


def etf_rows() -> dict[str, list[Row]]:
    r = rng("etf_daily")
    out: dict[str, list[Row]] = {}
    idx_level = {"코스피 200": 410.55, "KTB 3Y 지수": 1_032.18, "S&P 500": 6_520.40}
    idx_level["합성 테마 지수"] = 1_000.00
    state = {e.code: (e.nav, e.shares, round(e.nav * (1 + r.uniform(-0.002, 0.002)))) for e in ETFS}
    for day in DAYS:
        moves = {k: r.uniform(-0.02, 0.02) for k in idx_level}
        rows: list[Row] = []
        for e in ETFS:
            if day < e.listed_on:
                continue
            nav0, sh0, close0 = state[e.code]
            nav = round(nav0 * (1 + e.lev * moves[e.index]), 2)
            if e.shares == 0 and day == e.listed_on:
                sh = e.cu * 30  # 첫날 상장좌수(신규) — 전날 행 없음
            else:
                sh = max(e.cu, sh0 + e.cu * r.randint(-8, 12))
            close = tick(nav * (1 + r.uniform(-0.003, 0.003)))
            op = tick(nav0 * (1 + r.uniform(-0.004, 0.004)))
            hi = tick(max(close, op) * (1 + r.uniform(0.0005, 0.004)))
            lo = tick(min(close, op) * (1 - r.uniform(0.0005, 0.004)))
            vol = int(sh * r.uniform(0.002, 0.03))
            val = int(vol * (op + hi + lo + close) / 4)
            lvl0 = idx_level[e.index]
            lvl = round(lvl0 * (1 + moves[e.index]), 2)
            rows.append(
                {
                    "BAS_DD": day,
                    "ISU_CD": e.code,
                    "ISU_NM": e.name,
                    "TDD_CLSPRC": str(close),
                    "CMPPREVDD_PRC": str(close - close0),
                    "FLUC_RT": pct(close - close0, close0),
                    "NAV": f"{nav:.2f}",
                    "TDD_OPNPRC": str(op),
                    "TDD_HGPRC": str(hi),
                    "TDD_LWPRC": str(lo),
                    "ACC_TRDVOL": str(vol),
                    "ACC_TRDVAL": str(val),
                    "MKTCAP": str(close * sh),
                    "INVSTASST_NETASST_TOTAMT": str(round(sh * nav)),
                    "LIST_SHRS": str(sh),
                    "IDX_IND_NM": e.index,
                    "OBJ_STKPRC_IDX": f"{lvl:.2f}",
                    "CMPPREVDD_IDX": f"{lvl - lvl0:.2f}",
                    "FLUC_RT_IDX": pct(lvl - lvl0, lvl0),
                }
            )
            state[e.code] = (nav, sh, close)
        for k in idx_level:
            idx_level[k] = round(idx_level[k] * (1 + moves[k]), 2)
        out[day] = rows
    return out


def etn_rows() -> dict[str, list[Row]]:
    r = rng("etn_daily")
    rows: list[Row] = []
    for code, name, base, shares in (
        ("996010", "합성 인버스 2X 원유 ETN", 4_310.0, 20_000_000),
        ("996020", "합성 반도체 TOP5 ETN", 13_870.0, 4_000_000),
    ):
        iv = round(base * (1 + r.uniform(-0.03, 0.03)), 2)
        close = tick(iv * (1 + r.uniform(-0.004, 0.004)))
        vol = int(shares * r.uniform(0.01, 0.05))
        rows.append(
            {
                "BAS_DD": DAYS[1],
                "ISU_CD": code,
                "ISU_NM": name,
                "TDD_CLSPRC": str(close),
                "CMPPREVDD_PRC": str(close - tick(base)),
                "FLUC_RT": pct(close - tick(base), tick(base)),
                "PER1SECU_INDIC_VAL": f"{iv:.2f}",
                "TDD_OPNPRC": str(tick(base)),
                "TDD_HGPRC": str(max(close, tick(base)) + 5),
                "TDD_LWPRC": str(min(close, tick(base)) - 5),
                "ACC_TRDVOL": str(vol),
                "ACC_TRDVAL": str(int(vol * close)),
                "MKTCAP": str(close * shares),
                "INDIC_VAL_AMT": str(round(iv * shares)),
                "LIST_SHRS": str(shares),
                "IDX_IND_NM": f"{name.removesuffix(' ETN')} 지수",
            }
        )
    return {DAYS[1]: rows}


# ── 지수 ─────────────────────────────────────────────────────────────────────────────────

INDEXES: dict[str, tuple[tuple[str, str, float], ...]] = {
    "kospi": (
        ("KOSPI", "코스피", 3_412.57),
        ("KOSPI", "코스피 200", 457.80),
        ("KOSPI", "코스피 대형주", 3_390.12),
        ("KOSPI", "전기·전자", 1_103.44),
    ),
    "kosdaq": (
        ("KOSDAQ", "코스닥", 872.35),
        ("KOSDAQ", "코스닥 150", 1_405.92),
    ),
}


def index_series(name: str) -> list[tuple[str, float, float, float, float, float]]:
    """(날짜, 전일, 시, 고, 저, 종) — 두 거래일 이어지게."""
    r = rng(f"index:{name}")
    base = next(b for s in INDEXES.values() for _, n, b in s if n == name)
    out: list[tuple[str, float, float, float, float, float]] = []
    prev = base
    for day in DAYS:
        close = round(prev * (1 + r.uniform(-0.02, 0.02)), 2)
        op = round(prev * (1 + r.uniform(-0.005, 0.005)), 2)
        hi = round(max(close, op) * (1 + r.uniform(0, 0.006)), 2)
        lo = round(min(close, op) * (1 - r.uniform(0, 0.006)), 2)
        out.append((day, prev, op, hi, lo, close))
        prev = close
    return out


def index_rows() -> dict[str, dict[str, list[Row]]]:
    r = rng("index_volume")
    out: dict[str, dict[str, list[Row]]] = {}
    for series, items in INDEXES.items():
        rows: list[Row] = []
        for clss, name, _ in items:
            day, prev, op, hi, lo, close = index_series(name)[-1]
            vol = r.randint(100_000_000, 900_000_000)
            rows.append(
                {
                    "BAS_DD": day,
                    "IDX_CLSS": clss,
                    "IDX_NM": name,
                    "CLSPRC_IDX": f"{close:.2f}",
                    "CMPPREVDD_IDX": f"{close - prev:.2f}",
                    "FLUC_RT": pct(close - prev, prev),
                    "OPNPRC_IDX": f"{op:.2f}",
                    "HGPRC_IDX": f"{hi:.2f}",
                    "LWPRC_IDX": f"{lo:.2f}",
                    "ACC_TRDVOL": str(vol),
                    "ACC_TRDVAL": str(vol * r.randint(20_000, 60_000)),
                    "MKTCAP": str(r.randint(300, 3_000) * 10**12),
                }
            )
        out[series] = {DAYS[1]: rows}
    return out


def fsc_index_items() -> list[Row]:
    r = rng("fsc_index")
    out: list[Row] = []
    for name, csf in (("코스피", "KOSPI시리즈"), ("코스닥", "KOSDAQ시리즈")):
        for day, prev, op, hi, lo, close in index_series(name):
            vol = r.randint(100_000_000, 900_000_000)
            out.append(
                {
                    "basDt": day,
                    "idxNm": name,
                    "idxCsf": csf,
                    "clpr": f"{close:.2f}",
                    "vs": f"{close - prev:.2f}",
                    "fltRt": pct(close - prev, prev),
                    "mkp": f"{op:.2f}",
                    "hipr": f"{hi:.2f}",
                    "lopr": f"{lo:.2f}",
                    "trqu": str(vol),
                    "trPrc": str(vol * r.randint(20_000, 60_000)),
                    "lstgMrktTotAmt": str(r.randint(300, 3_000) * 10**12),
                }
            )
    return out


# ── 묶기·쓰기 ─────────────────────────────────────────────────────────────────────────────


def _doc(what: str, body: Json) -> Json:
    return {"_source": f"{SYNTH} — {what}", **body}


def build() -> dict[str, Json]:
    """파일 이름 → 내용. 같은 시드면 언제나 같은 값."""
    stocks = stock_rows()
    return {
        "stock_daily.json": _doc(
            "KRX /sto/stk_bydd_trd·ksq_bydd_trd 응답 행 형태(시장 → basDd → 행)", dict(stocks)
        ),
        "base_info.json": _doc(
            "KRX /sto/stk_isu_base_info·ksq_isu_base_info 응답 행 형태(시장 → 행)",
            dict(base_info_rows()),
        ),
        "etf_daily.json": _doc("KRX /etp/etf_bydd_trd 응답 행 형태(basDd → 행)", dict(etf_rows())),
        "etn_daily.json": _doc("KRX /etp/etn_bydd_trd 응답 행 형태(basDd → 행)", dict(etn_rows())),
        "index_daily.json": _doc(
            "KRX /idx/kospi_dd_trd·kosdaq_dd_trd 응답 행 형태(시리즈 → basDd → 행)",
            dict(index_rows()),
        ),
        "fsc_stock_price.json": _doc(
            "금융위 주식시세(15094808) item 형태 — stock_daily.json 같은 날과 같은 값",
            {DAYS[1]: fsc_stock_items(stocks, DAYS[1])},
        ),
        "fsc_index_price.json": _doc(
            "금융위 지수시세 item 형태(코스피·코스닥 두 거래일)", {"items": fsc_index_items()}
        ),
    }


def dump(doc: Json) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(description="SYNTHETIC KRX·금융위 시세 시험 fixture 생성")
    ap.add_argument("--write", action="store_true", help="파일을 다시 쓴다(없으면 비교만)")
    args = ap.parse_args(argv)
    differ = 0
    for name, doc in build().items():
        path = HERE / name
        text = dump(doc)
        if args.write:
            path.write_text(text, encoding="utf-8")
            out(f"썼다 {name}")
        elif not path.exists() or path.read_text(encoding="utf-8") != text:
            differ += 1
            out(f"다르다 {name}")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())

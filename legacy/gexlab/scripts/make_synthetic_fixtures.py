"""SYNTHETIC 시험 fixture 생성기 — KIS·KRX 실측 발췌 fixture 를 같은 형태의 합성 데이터로 대신한다.

KBJ 공개 레포 규칙(docs/DATA_TIERS.md §4, ADR 0001 U3): KIS·KRX(로그인 등급) 원본 응답과 그것으로
만든 fixture 는 레포에 넣지 않는다. 이 스크립트는 그 fixture 를 **실측을 읽지 않고**
시드 고정 난수와
모형(Black-76)만으로 다시 만든다 — 키·필드·자료형·
행 수·행 순서는 원래 fixture 와 같고 값은 합성이다.

    uv run python -m scripts.make_synthetic_fixtures            # 커밋된 fixture 와 비교만(다르면 1)
    uv run python -m scripts.make_synthetic_fixtures --write    # 다시 쓴다
    uv run python -m scripts.make_synthetic_fixtures --explain  # 시험 기대값 독립 계산(아래)

만드는 것(모두 tests/fixtures 아래):
- kis/master_lines.json — 지수선물옵션 마스터 줄 110개(형식 `data/kis/master.py`). 옵션 단축코드의
  행사가 자리 3글자는 합성 규칙(`Z` + 행사가/2.5 의 36진 두 자리), 표준코드(ISIN)는 합성 본문 +
  ISIN 검사 숫자. 선물·스프레드 코드는 상품·결제월 규칙 그대로(A01612 등 — 손으로 쓴 시험이 쓴다)
- kis/callput_202610.json·callput_wkm_260904.json·price_options.json·futures_board.json —
  아래 합성 체인 스냅샷(14:27)에서 원래 fixture 와 같은 자리(앞 10행 + 뒤 10행, 인덱스 46~65,
  단건 6종목, 선물 앞 2행)를 옮긴 것
- validation/chain_snapshot_synthetic_20260928_{1427,1452}_small.json — 합성 전체 스냅샷 두 장을
  프로젝트 규칙(`scripts.make_golden.chain_subset` + `dump_chain`)으로 자른 것
- kis/investor.json·minute_day.json·option_list.json, krx/opt_daily.json·fut_daily.json

남긴 것(시나리오 뼈대 — 손으로 쓴 시험·가짜 서버가 입력으로 쓰는 값): 날짜·조회 시각, 상품·만기·
행사가
격자, 근월물 선물가(14:27 1095.10·14:52 1092.50, 호가 1095.00/1095.10)와 원월물 1085.00, 전광판
ATM 표시 1125.0, 월물리스트(상장 규칙). 행 단위 값(가격·호가·거래량·OI·IV·그릭스·투자자 수급·KRX
일별)과 수준(합성 F·KIS 이론 선도·현물·전 세션 수준·역사적 변동성)은 모두 이 파일의 매개변수와
시드에서 나온다. 시험이 확인하는 행 성질(전 세션 가격 행, 내재가치 밑 last 로 KIS IV 폴백이 되는 행,
KIS IV 0 행, 표기 없는 코스닥 위클리 중복 행 등)은 일부러 같은 자리에 둔다.

KIS 그릭스·IV 관례는 docs/validation_greeks.md·docs/metrics.md §1.7 의 결론을 그대로 모형으로 쓴다:
월물 Δ·Γ = Black(σ = hist_vltl, S = KIS 이론가 패리티 선도, T = 달력일/365), KIS IV = Black 역산
(last, S = 선물가, T = max(달력일, 0.5)/365), 위클리 무거래 행은
전 세션 수준·기초 HV, 0DTE 는 0.5일.

`--explain` 은 시험이 값으로 확인하는 기대값을 이 파일의 독립 계산(math 만 쓰는 Black-76·중앙값 —
`core`·`scripts` 의 계산 코드를 부르지 않는다)으로 찍는다. 합성 교체로 바뀐 기대값은 여기서 나온
값으로 고쳤다(legacy/gexlab/MIGRATION.md).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
SEED = "kbj-p1-gexlab-synthetic-v1"
KST = timezone(timedelta(hours=9))
SYNTH = "SYNTHETIC — scripts/make_synthetic_fixtures.py(시드 고정)가 만든 합성 데이터. 실측 아님"

Row = dict[str, str]
Json = dict[str, Any]


def rng(name: str) -> random.Random:
    """fixture 마다 따로 시드한 난수 — 한 fixture 를 고쳐도 다른 fixture 값이 바뀌지 않는다."""
    return random.Random(f"{SEED}:{name}")  # noqa: S311 — 암호용이 아니라 재현 가능한 합성 값


# ── 수학 (Black-76, r = 0 이 기본 — core 를 쓰지 않는 독립 구현) ─────────────────────


def ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(F: float, K: float, T: float, s: float) -> float:
    v = s * math.sqrt(T)
    return (math.log(F / K) + 0.5 * v * v) / v


def bprice(cp: str, F: float, K: float, T: float, s: float, disc: float = 1.0) -> float:
    """Black-76 가격(pt). cp 'C'·'P'. disc 는 할인 계수 e^(−rT)."""
    if T <= 0 or s <= 0:
        return disc * max(F - K if cp == "C" else K - F, 0.0)
    d1 = _d1(F, K, T, s)
    d2 = d1 - s * math.sqrt(T)
    if cp == "C":
        return disc * (F * ncdf(d1) - K * ncdf(d2))
    return disc * (K * ncdf(-d2) - F * ncdf(-d1))


def bdelta(cp: str, F: float, K: float, T: float, s: float) -> float:
    d1 = _d1(F, K, T, s)
    return ncdf(d1) if cp == "C" else ncdf(d1) - 1.0


def bgamma(F: float, K: float, T: float, s: float) -> float:
    return npdf(_d1(F, K, T, s)) / (F * s * math.sqrt(T))


def bvega(F: float, K: float, T: float, s: float) -> float:
    """IV 1%p 당 가격 변화."""
    return F * math.sqrt(T) * npdf(_d1(F, K, T, s)) / 100.0


def btheta(F: float, K: float, T: float, s: float) -> float:
    """달력 1일 경과당 가격 변화(r = 0)."""
    return -F * npdf(_d1(F, K, T, s)) * s / (2.0 * math.sqrt(T)) / 365.0


def brho(cp: str, F: float, K: float, T: float, s: float) -> float:
    """금리 1%p 당 가격 변화(현물 Black-Scholes 꼴 — KIS 표시용 칸, 시험은 값을 보지 않는다)."""
    d2 = _d1(F, K, T, s) - s * math.sqrt(T)
    return K * T * (ncdf(d2) if cp == "C" else -ncdf(-d2)) / 100.0


def implied_vol(cp: str, price: float, F: float, K: float, T: float) -> float | None:
    """가격 → σ (이분법). 무차익 범위 밖이면 None."""
    lo_p = max(F - K if cp == "C" else K - F, 0.0)
    if not price > lo_p + 1e-12 or T <= 0:
        return None
    lo, hi = 1e-4, 6.0
    if bprice(cp, F, K, T, hi) < price:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if bprice(cp, F, K, T, mid) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def implied_underlying(cp: str, price: float, K: float, T: float, s: float) -> float:
    """가격·σ·T → 기초자산 S (이분법, [K/4, 4K])."""
    lo, hi = K / 4, K * 4
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        p = bprice(cp, mid, K, T, s)
        if (p < price) == (cp == "C"):
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ── 표기 ─────────────────────────────────────────────────────────────────────


def dec(x: float | Decimal, places: int) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP)


def fmt(x: float | Decimal, places: int) -> str:
    v = dec(x, places)
    if v == 0:
        v = abs(v)  # -0.00 → 0.00
    return f"{v:.{places}f}"


def tick(p: float) -> Decimal:
    """코스피200 옵션 호가단위 — 10pt 미만 0.01, 이상 0.05."""
    return Decimal("0.05") if p >= 10 else Decimal("0.01")


def to_tick(p: float, mode: str = "nearest") -> Decimal:
    t = tick(p)
    rounding = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[mode]
    v = (Decimal(str(p)) / t).quantize(Decimal(1), rounding=rounding) * t
    return max(v, Decimal("0.01"))


def fut_tick(p: float) -> Decimal:
    return (Decimal(str(p)) / Decimal("0.05")).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    ) * Decimal("0.05")


def kst(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ── 종목코드 (합성 규칙) ──────────────────────────────────────────────────────

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 상품·만기 → 단축코드 앞 6글자(콜 기준 — 풋은 첫 글자 C). 상품·결제월 부호는
# 공개 규칙 표기를 따르고,
# 행사가 자리(뒤 3글자)만 합성이다
OPT_PREFIX: Mapping[tuple[str, str], str] = {
    ("kospi200", "202610"): "B01610",
    ("kospi200", "202611"): "B01611",
    ("mini", "202610"): "B05610",
    ("weekly_thu", "2610W1"): "B09FFW",
    ("weekly_mon", "2609W4"): "BAFBZW",
    ("weekly_mon", "2610W1"): "BAFC0W",
    ("kosdaq150", "202610"): "B06610",
    ("kosdaq_weekly_thu", "2610W1"): "BAJ37W",
    ("kosdaq_weekly_mon", "2609W4"): "BAK48W",
}


def opt_code(family: str, token: str, cp: str, strike: float) -> str:
    """합성 단축코드 — 앞 6글자 상품·만기, 뒤 3글자 `Z` + round(K/2.5) 의 36진 두 자리."""
    n = round(strike / 2.5) % (36 * 36)
    body = OPT_PREFIX[(family, token)][1:] + "Z" + _B36[n // 36] + _B36[n % 36]
    return ("B" if cp == "C" else "C") + body


def isin_check(body: str) -> str:
    """ISIN 검사 숫자(문자 → 10~35 로 펼친 뒤 Luhn)."""
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def isin(code: str) -> str:
    """합성 표준코드 — `KR4` + (첫 글자 + 셋째부터) 8자리 + 검사 숫자."""
    body = "KR4" + (code[0] + code[2:]).ljust(8, "0")[:8]
    return body + isin_check(body)


# ── 시나리오 뼈대 (원래 fixture·손으로 쓴 시험과 같은 자리) ─────────────────────

DAY = date(2026, 9, 28)
DISPLAY_ATM = 1125.0  # 전광판 atm_cls_name 'ATM' (전날 수준 기준 — 선물가 ATM 과 다르다, #11)
EXPIRY: Mapping[str, date] = {
    "202610": date(2026, 10, 8),
    "202611": date(2026, 11, 12),
    "260904": date(2026, 9, 28),
    "261001": date(2026, 10, 6),
}
MONTH_STRIKES = [745.0 + 2.5 * i for i in range(341)]  # 202610·202611 745.0~1595.0
WEEK_STRIKES = [970.0 + 2.5 * i for i in range(111)]  # 2609W4 970.0~1245.0
BOARD_ROWS = 100  # 전광판은 행사가 내림차순 앞 100행(#11)

# ── 합성 매개변수 (실측 값을 옮긴 것이 아니다) ─────────────────────────────────

PREV_LEVEL = 1124.20  # 전 세션 기초자산 수준 — 무거래 행 가격·KIS 그릭스, 마스터 ATM 구분 기준
KQ_LEVEL = 1421.30  # 코스닥150 마스터 ATM 구분 기준
R_KIS = 0.0265  # KIS 이론가 할인 금리(연속복리)
UNCH = 6.3047  # 전광판 unch_prpr = 행사가 × UNCH (환산 칸)


@dataclass(frozen=True)
class Market:
    """스냅샷 한 장의 합성 시장."""

    # KIS hist_vltl(%) — 전광판 행. 보강 행은 조회 시각마다 소수 셋째 자리가 조금씩. 월물 ATM±5 감마
    # (≈ 0.0027)가 KIS 4자리 표시 반올림으로 1% 를 넘지 않는
    # 수준으로 골랐다(재현 검사 ATM±5 전 행 통과)
    hv: float
    fwd610: float  # 월물 202610 시장 선도 — 가격이 패리티를 지켜 합성 F 로 그대로 나온다
    fwd611: float
    fwd0: float  # 0DTE WKM 260904 시장 선도
    fwd_wk: float  # WKM 261001 당일 거래 행의 시장 수준
    kis610: float  # KIS 이론가 패리티 선도(월물 202610)
    kis611: float
    spot: Mapping[str, float]  # 전광판 내재가치(invl_val)의 현물 S
    g0_med: float  # 0DTE KIS 그릭스 기초자산 중앙(행마다 ± — 계산 시점이 다르다)
    vol0: float  # 0DTE 스마일 ATM σ(달력 분 T 기준)


@dataclass(frozen=True)
class Snap:
    name: str
    started: str
    s_ref: str  # 근월물 A01612 전광판 현재가 — 뼈대
    fut_ts: str
    boards: Mapping[str, str]  # 라벨 → 전광판 조회 시각
    fill_t0: tuple[str, str]  # 보강 콜·풋 첫 조회 시각
    fills: tuple[float, ...]  # 보강 행사가 (ATM±5)
    stale_puts: tuple[float, ...]  # 202610 보강 풋 중 당일 거래 없는 행사가 — 전 세션 last
    market: Market


SNAP_A = Snap(
    name="1427",
    started="2026-09-28T14:27:12.848713+09:00",
    s_ref="1095.10",
    fut_ts="2026-09-28T14:27:13.030853+09:00",
    boards={
        "MONTH:202610": "2026-09-28T14:27:18.149318+09:00",
        "MONTH:202611": "2026-09-28T14:27:19.257682+09:00",
        "WKM:260904": "2026-09-28T14:27:20.308950+09:00",
        "WKM:261001": "2026-09-28T14:27:22.438099+09:00",
    },
    fill_t0=("2026-09-28T14:27:28.250000+09:00", "2026-09-28T14:27:38.650000+09:00"),
    fills=tuple(1082.5 + 2.5 * i for i in range(11)),
    stale_puts=(1097.5, 1102.5, 1107.5),
    market=Market(
        hv=81.05,
        fwd610=1089.55,
        fwd611=1091.95,
        fwd0=1094.42,
        fwd_wk=1094.20,
        kis610=1095.16,
        kis611=1098.72,
        spot={
            "MONTH:202610": 1094.55,
            "MONTH:202611": 1094.52,
            "WKM:260904": 1094.87,
            "WKM:261001": 1094.47,
        },
        g0_med=1094.58,
        vol0=0.505,
    ),
)
SNAP_B = Snap(
    name="1452",
    started="2026-09-28T14:52:36.745683+09:00",
    s_ref="1092.50",
    fut_ts="2026-09-28T14:52:36.912000+09:00",
    boards={
        "MONTH:202610": "2026-09-28T14:52:42.043734+09:00",
        "MONTH:202611": "2026-09-28T14:52:43.164576+09:00",
        "WKM:260904": "2026-09-28T14:52:44.226146+09:00",
        "WKM:261001": "2026-09-28T14:52:46.327346+09:00",
    },
    fill_t0=("2026-09-28T14:52:52.600000+09:00", "2026-09-28T14:53:03.000000+09:00"),
    fills=tuple(1080.0 + 2.5 * i for i in range(11)),
    stale_puts=(1097.5, 1102.5),
    market=Market(
        hv=81.07,
        fwd610=1087.65,
        fwd611=1090.05,
        fwd0=1091.88,
        fwd_wk=1091.70,
        kis610=1092.77,
        kis611=1096.05,
        spot={
            "MONTH:202610": 1092.31,
            "MONTH:202611": 1091.86,
            "WKM:260904": 1091.83,
            "WKM:261001": 1092.02,
        },
        g0_med=1091.95,
        vol0=0.552,
    ),
)
SNAPS = (SNAP_A, SNAP_B)


# ── 시간 ─────────────────────────────────────────────────────────────────────

MINUTES_PER_YEAR = 365 * 24 * 60


def expiry_at(d: date) -> datetime:
    """만기 시각 — 만기일 15:20 KST."""
    return datetime(d.year, d.month, d.day, 15, 20, tzinfo=KST)


def t_market(now: datetime, expiry: str) -> float:
    """시장 가격의 T — 달력 분(하한 5분) / 1년."""
    minutes = (expiry_at(EXPIRY[expiry]) - now).total_seconds() / 60
    return max(minutes, 5.0) / MINUTES_PER_YEAR


def kis_days(expiry: str) -> int:
    """KIS 잔존 달력일 D(만기일 − 오늘)."""
    return (EXPIRY[expiry] - DAY).days


def t_kis(expiry: str) -> float:
    """KIS 관례 T = max(D, 0.5)/365 (docs/metrics.md §1.7)."""
    return max(kis_days(expiry), 0.5) / 365


# ── 스마일 ───────────────────────────────────────────────────────────────────


def smile_month(K: float, F: float, base: float = 0.408) -> float:
    """월물 σ(달력 분 T) — 로그 머니니스 2차식."""
    m = math.log(K / F)
    return base + 0.48 * m + 0.80 * m * m


def smile_0dte(K: float, F: float, T: float, atm: float) -> float:
    """0DTE σ(달력 분 T) — 표준편차 단위 머니니스 2차식, 상한 3.0."""
    x = math.log(K / F) / (atm * math.sqrt(T))
    return min(atm * (1.0 + 0.055 * x * x - 0.012 * x), 3.0)


# ── 옵션 한 행 ───────────────────────────────────────────────────────────────


@dataclass
class Opt:
    """합성 옵션 한 종목 — 전광판 41칸·단건 30칸으로 그린다."""

    family: str
    token: str
    series: str  # 단건 series 표기 ('C 202610')
    name_prefix: str  # 단건 종목명 앞부분 ('C 202610')
    cp: str
    K: float
    last: Decimal
    vol: int
    oi: int
    oi_chg: int
    base: Decimal  # 기준가(전일 종가 — nmix_sdpr·futs_sdpr)
    prdy_clpr: Decimal | None = None  # 단건 futs_prdy_clpr (없으면 기준가)
    bid: Decimal | None = None
    ask: Decimal | None = None
    iv: float = 0.0  # KIS hts_ints_vltl %, 0 은 값 없음
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    hist: float = 0.0
    thpr: float = 0.0
    invl: float = 0.0
    tr_value: int = 0  # 누적 거래대금(천원)
    ohl: tuple[Decimal, Decimal, Decimal] = (Decimal(0), Decimal(0), Decimal(0))
    ask_q: int = 0
    bid_q: int = 0
    rgbf: int = 0
    rmnn: int = 0
    last_tr: str = ""
    extra: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def code(self) -> str:
        return opt_code(self.family, self.token, self.cp, self.K)

    @property
    def strike_name(self) -> str:
        return f"{self.name_prefix} {self.K:,.1f}"

    def change(self) -> tuple[str, str, str]:
        """(전일 대비, 부호, 등락률) — 기준가 0 이면 등락률 0."""
        diff = self.last - self.base
        sign = "2" if diff > 0 else "5" if diff < 0 else "3"
        pct = Decimal(0) if self.base == 0 else diff / self.base * 100
        return fmt(diff, 2), sign, fmt(pct, 2)

    def board_row(self, atm_label: str) -> Row:
        vrss, sign, ctrt = self.change()
        thpr = dec(self.thpr, 2)
        dprt = "9999.99" if thpr == 0 else fmt((self.last - thpr) / thpr * 100, 2)
        o, h, lo = self.ohl
        return {
            "acpr": fmt(self.K, 2),
            "unch_prpr": fmt(self.K * UNCH, 2),
            "optn_shrn_iscd": self.code,
            "optn_prpr": fmt(self.last, 2),
            "optn_prdy_vrss": vrss,
            "prdy_vrss_sign": sign,
            "optn_prdy_ctrt": ctrt,
            "optn_bidp": fmt(self.bid or 0, 2),
            "optn_askp": fmt(self.ask or 0, 2),
            "tmvl_val": fmt(float(self.last) - self.invl, 2),
            "nmix_sdpr": fmt(self.base, 2),
            "acml_vol": str(self.vol),
            "seln_rsqn": str(self.ask_q),
            "shnu_rsqn": str(self.bid_q),
            "acml_tr_pbmn": str(self.tr_value),
            "hts_otst_stpl_qty": str(self.oi),
            "otst_stpl_qty_icdc": str(self.oi_chg),
            "delta_val": fmt(self.delta, 4),
            "gama": fmt(self.gamma, 4),
            "vega": fmt(self.vega, 4),
            "theta": fmt(self.theta, 4),
            "rho": fmt(self.rho, 4),
            "hts_ints_vltl": fmt(self.iv, 4),
            "invl_val": fmt(self.invl, 2),
            "esdg": fmt(float(self.last) - self.thpr, 2),
            "dprt": dprt,
            "hist_vltl": fmt(self.hist, 4),
            "hts_thpr": fmt(thpr, 2),
            "optn_oprc": fmt(o, 2),
            "optn_hgpr": fmt(h, 2),
            "optn_lwpr": fmt(lo, 2),
            "optn_mxpr": fmt(to_tick(float(self.base) + 0.0092 * self.K), 2),
            "optn_llam": "0.01",
            "atm_cls_name": atm_label,
            "rgbf_vrss_icdc": str(self.rgbf),
            "total_askp_rsqn": str(self.ask_q * 3 + self.rgbf * self.rgbf),
            "total_bidp_rsqn": str(self.bid_q * 3 + abs(self.rgbf)),
            "futs_antc_cnpr": "0.00",
            "futs_antc_cntg_vrss": "0.00",
            "antc_cntg_vrss_sign": "0",
            "antc_cntg_prdy_ctrt": "0.00",
        }

    def price_output(self) -> Row:
        """단건 현재가(FHMIF10000000) output1 — 30칸."""
        vrss, sign, ctrt = self.change()
        thpr = dec(self.thpr, 2)
        dprt = "9999.99" if thpr == 0 else fmt((self.last - thpr) / thpr * 100, 2)
        o, h, lo = self.ohl
        prdy = self.base if self.prdy_clpr is None else self.prdy_clpr
        lo_life = min(self.base, self.last) * Decimal("0.75")
        hi_life = max(self.base, self.last) * Decimal("1.70")
        return {
            "hts_kor_isnm": self.strike_name,
            "futs_prpr": fmt(self.last, 2),
            "futs_prdy_vrss": vrss,
            "prdy_vrss_sign": sign,
            "futs_prdy_clpr": fmt(prdy, 2),
            "futs_prdy_ctrt": ctrt,
            "acml_vol": str(self.vol),
            "acml_tr_pbmn": str(self.tr_value),
            "hts_otst_stpl_qty": str(self.oi),
            "otst_stpl_qty_icdc": str(self.oi_chg),
            "futs_oprc": fmt(o, 2),
            "futs_hgpr": fmt(h, 2),
            "futs_lwpr": fmt(lo, 2),
            "futs_mxpr": fmt(to_tick(float(self.base) + (91.40 if self.cp == "C" else 74.50)), 2),
            "futs_llam": "0.01",
            "futs_sdpr": fmt(self.base, 2),
            "hts_thpr": fmt(thpr, 2),
            "dprt": dprt,
            "futs_last_tr_date": self.last_tr,
            "hts_rmnn_dynu": str(self.rmnn),
            "futs_lstn_medm_hgpr": fmt(to_tick(float(hi_life)), 2),
            "futs_lstn_medm_lwpr": fmt(to_tick(float(lo_life)), 2),
            "delta_val": fmt(self.delta, 4),
            "gama": fmt(self.gamma, 4),
            "theta": fmt(self.theta, 4),
            "vega": fmt(self.vega, 4),
            "rho": fmt(self.rho, 4),
            "hist_vltl": fmt(self.hist, 4),
            "hts_ints_vltl": fmt(self.iv, 4),
            "acpr": fmt(self.K, 2),
        }


def atm_label(cp: str, K: float) -> str:
    """전광판 atm_cls_name — 전날 수준 기준 ATM(DISPLAY_ATM)에서 콜·풋 ITM/OTM."""
    if K == DISPLAY_ATM:
        return "ATM"
    itm = K < DISPLAY_ATM if cp == "C" else K > DISPLAY_ATM
    return "ITM" if itm else "OTM"


def intrinsic(cp: str, S: float, K: float) -> float:
    return max(S - K if cp == "C" else K - S, 0.0)


def kis_greeks(o: Opt, S: float, T: float, sigma: float) -> None:
    """KIS 그릭스 칸 — Black(S, σ, T). 표시는 소수 4자리."""
    flag = o.cp
    o.delta = bdelta(flag, S, o.K, T, sigma)
    o.gamma = bgamma(S, o.K, T, sigma)
    o.vega = bvega(S, o.K, T, sigma)
    o.theta = btheta(S, o.K, T, sigma)
    o.rho = brho(flag, S, o.K, T, sigma)


def kis_iv_pct(cp: str, last: Decimal, S: float, K: float, T: float) -> float:
    """KIS IV(%) — last 를 S·T 로 역산. 풀 수 없으면 0(KIS 는 없음을 0 으로 준다)."""
    s = implied_vol(cp, float(last), S, K, T)
    return 0.0 if s is None else s * 100


def quote(r: random.Random, p: float, ticks: int) -> tuple[Decimal, Decimal]:
    """이론가 p 둘레 호가 — 합 ticks 틱 폭. 아주 싼 종목은 0.00 / 0.01."""
    if p < 0.005:
        return Decimal("0.00"), Decimal("0.01")
    t = tick(p)
    below = ticks // 2 if r.random() < 0.5 else (ticks + 1) // 2
    mid = to_tick(p)
    bid = max(mid - t * below, Decimal("0.00"))
    ask = max(mid + t * (ticks - below), bid + t)
    return bid, ask


def ohl(
    r: random.Random, last: Decimal, vol: int, base: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """당일 시가·고가·저가 — 거래가 없으면 0."""
    if vol == 0:
        return Decimal(0), Decimal(0), Decimal(0)
    o = to_tick(float(base) * r.uniform(0.85, 1.15) if base > 0 else float(last))
    h = max(o, last) + tick(float(last)) * r.randint(0, 6)
    lo = max(min(o, last) - tick(float(last)) * r.randint(0, 6), Decimal("0.01"))
    return o, h, lo


def tr_value(r: random.Random, last: Decimal, vol: int) -> int:
    """누적 거래대금(천원) — 계약당 25만원 × 평균 체결가."""
    return 0 if vol == 0 else round(vol * float(last) * 250 * r.uniform(0.9, 1.1))


# ── 합성 체인 스냅샷 ──────────────────────────────────────────────────────────

M610, M611 = "MONTH:202610", "MONTH:202611"
W0, W1 = "WKM:260904", "WKM:261001"


def _board_strikes(grid: Sequence[float]) -> list[float]:
    return sorted(grid, reverse=True)[:BOARD_ROWS]


def _finish(o: Opt, r: random.Random) -> Opt:
    o.ohl = ohl(r, o.last, o.vol, o.base)
    o.tr_value = tr_value(r, o.last, o.vol)
    o.ask_q = r.randint(1, 60) if o.ask else 0
    o.bid_q = r.randint(1, 60) if o.bid else 0
    o.rgbf = r.randint(-3, 15)
    return o


def month_board(snap: Snap, label: str) -> tuple[list[Opt], list[Opt]]:
    """월물 전광판 — 행사가 내림차순 앞 100행(1595.0~1347.5, 깊은 OTM 콜·깊은 ITM 풋)."""
    m = snap.market
    expiry = label.split(":")[1]
    r = rng(f"board:{snap.name}:{label}")
    now = kst(snap.boards[label])
    T = t_market(now, expiry)
    F, kfwd = (m.fwd610, m.kis610) if expiry == "202610" else (m.fwd611, m.kis611)
    base_vol = 0.408 if expiry == "202610" else 0.418
    tk = t_kis(expiry)
    disc = math.exp(-R_KIS * tk)
    s_ref = float(snap.s_ref)
    spot = m.spot[label]
    t_prev = T + 5 / 365  # 전 거래일(09-23) 종가 시각
    calls: list[Opt] = []
    puts: list[Opt] = []
    for K in _board_strikes(MONTH_STRIKES):
        for cp, out in (("C", calls), ("P", puts)):
            sigma = smile_month(K, F, base_vol)
            theo = bprice(cp, F, K, T, sigma)
            prev = bprice(cp, PREV_LEVEL - 1.8, K, t_prev, sigma)
            base = to_tick(prev)
            if cp == "C":
                forced = {1352.5: 0, 1350.0: 3, 1347.5: 3} if expiry == "202611" else {}
                if K in forced:
                    vol = forced[K]
                elif r.random() < 0.7:
                    vol = max(1, int(math.exp(r.gauss(3.2 if expiry == "202610" else 1.0, 1.4))))
                else:
                    vol = 0
                if K == 1595.0 and expiry == "202610":
                    vol = r.randint(1800, 3200)  # 끝 행사가 — 거래가 몰린다
                last = to_tick(theo * (1 + r.gauss(0, 0.04))) if vol else base
                bid, ask = quote(r, theo, r.choice([1, 1, 2, 3]))
                if expiry == "202611":  # 원월물 호가는 넓다
                    bid = to_tick(theo - r.uniform(0.6, 1.4), "down")
                    ask = to_tick(theo + r.uniform(0.7, 1.4), "up")
                oi = (
                    int(math.exp(r.gauss(4.6, 1.3)))
                    if expiry == "202610"
                    else r.choice([0, 3, 146])
                )
                if K == 1595.0 and expiry == "202610":
                    oi = r.randint(5000, 8000)
                if expiry == "202611" and K in forced:
                    oi = {1352.5: 0, 1350.0: r.randint(100, 200), 1347.5: r.randint(1, 9)}[K]
            else:  # 깊은 ITM 풋 — 당일 거래 없음, last 는 전 세션 값(지금 내재가치 밑)
                vol = 0
                last = base
                bid = to_tick(theo - r.uniform(0.6, 2.0), "down")
                ask = to_tick(theo + r.uniform(5.0, 9.0), "up")
                oi = r.choice([0, 0, 0, r.randint(1, 30)]) if expiry == "202610" else 0
            o = Opt(
                family="kospi200",
                token=expiry,
                series=f"{cp} {expiry}",
                name_prefix=f"{cp} {expiry}",
                cp=cp,
                K=K,
                last=last,
                vol=vol,
                oi=oi,
                oi_chg=0 if oi == 0 else r.randint(-min(oi, 3), 6),
                base=base,
                bid=bid,
                ask=ask,
            )
            o.hist = round(m.hv + (0.0003 if expiry == "202611" else 0.0), 4)
            kis_greeks(o, kfwd, tk, o.hist / 100)
            o.thpr = bprice(cp, kfwd, K, tk, o.hist / 100, disc)
            o.iv = 0.0 if cp == "P" else kis_iv_pct(cp, o.last, s_ref, K, tk)
            o.invl = round(intrinsic(cp, spot, K), 2)
            out.append(_finish(o, r))
    return calls, puts


ZERO_PRDY = frozenset({(1092.5, "C"), (1097.5, "C"), (1092.5, "P")})  # 전일 종가가 없는 종목


def month_fills(snap: Snap) -> list[tuple[str, Opt]]:
    """월물 202610 단건 보강 행(ATM±5 콜·풋) — 호가 없음, last 는 패리티를 지킨다."""
    m = snap.market
    r = rng(f"fills:{snap.name}")
    tk = t_kis("202610")
    disc = math.exp(-R_KIS * tk)
    s_ref = float(snap.s_ref)
    F = Decimal(str(m.fwd610))
    out: list[tuple[str, Opt]] = []
    by_k: dict[float, tuple[Decimal, Decimal, int, int, int, int]] = {}
    for K in snap.fills:
        t0 = kst(snap.fill_t0[1])
        T = t_market(t0, "202610")
        sigma = smile_month(K, float(F)) + r.gauss(0, 0.012)
        put = to_tick(bprice("P", float(F), K, T, sigma))
        call = put + F - Decimal(str(K))  # C − P = F − K (r = 0) — 합성 F 가 F 그대로
        popular = K % 50 == 0
        vc = r.randint(250, 340) if popular else r.randint(1, 50)
        vp = r.randint(280, 360) if popular else r.randint(1, 50)
        oc = r.randint(2500, 3000) if popular else int(math.exp(r.gauss(4.9, 0.6)))
        op = r.randint(1500, 1900) if popular else int(math.exp(r.gauss(4.9, 0.6)))
        by_k[K] = (call, put, vc, vp, oc, op)
    for cp, t0s in (("C", snap.fill_t0[0]), ("P", snap.fill_t0[1])):
        t0 = kst(t0s)
        for i, K in enumerate(snap.fills):
            call, put, vc, vp, oc, op = by_k[K]
            ts = t0 + timedelta(seconds=0.255 * i)
            stale = cp == "P" and K in snap.stale_puts
            prev = to_tick(bprice(cp, PREV_LEVEL - 1.8, K, t_market(ts, "202610") + 5 / 365, 0.36))
            last = prev if stale else (call if cp == "C" else put)
            vol = 0 if stale else (vc if cp == "C" else vp)
            oi = oc if cp == "C" else op
            o = Opt(
                family="kospi200",
                token="202610",  # noqa: S106 — 시리즈 토큰(결제월), 비밀 아님
                series=f"{cp} 202610",
                name_prefix=f"{cp} 202610",
                cp=cp,
                K=K,
                last=last,
                vol=vol,
                oi=oi,
                oi_chg=r.randint(0, 6) if vol else 0,
                base=prev,
                prdy_clpr=Decimal(0) if (K, cp) in ZERO_PRDY else None,
                rmnn=kis_days("202610") + 1,
                last_tr=EXPIRY["202610"].strftime("%Y%m%d"),
            )
            o.hist = round(m.hv + r.gauss(0, 0.0012), 4)
            kis_greeks(o, m.kis610, tk, o.hist / 100)
            o.thpr = bprice(cp, m.kis610, K, tk, o.hist / 100, disc)
            o.iv = kis_iv_pct(cp, o.last, s_ref, K, tk)
            o.extra["ts_kst"] = ts.isoformat()
            out.append((ts.isoformat(), _finish(o, r)))
    return out


# 0DTE 행 성질 — 스냅샷별. 폴백: last 가 합성 F 기준 내재가치 밑이라 자체 역산이 안 되고 KIS IV 로
# 넘어가는 행(§1.5). KIS IV 없음: hts_ints_vltl 0
FALLBACK_0DTE: Mapping[str, tuple[float, str, float]] = {
    "1427": (1102.5, "P", 1.55),  # (행사가, 콜풋, 내재가치 밑으로 내린 pt)
    "1452": (1100.0, "P", 0.30),
}
IV_MISSING_0DTE: Mapping[str, frozenset[tuple[float, str]]] = {
    "1452": frozenset({(1087.5, "C")}),
}
FALLBACK_D1 = -1.08  # 폴백 행 KIS 그릭스의 d1 — 풋 델타 N(d1) − 1 ≈ −0.86
FALLBACK_SIGMA_RATIO = (
    0.80  # 폴백 행 KIS 그릭스 σ / KIS IV — 그 행 IV 보다 작은 σ 로 그릭스를 매겼다
)
# 0DTE KIS 그릭스 기초자산 = g0_med + 아래(작은 발췌 행 — 대칭이라 중앙이 g0_med), σ 배율(1 + η)
G0_OFFSET: Mapping[str, Mapping[tuple[float, str], tuple[float, float]]] = {
    "1427": {
        (1087.5, "P"): (-0.24, 0.0),
        (1090.0, "C"): (0.30, 0.0),
        (1090.0, "P"): (-0.12, 0.0),
        (1092.5, "C"): (0.18, 0.06),
        (1092.5, "P"): (-0.36, 0.0),
        (1095.0, "C"): (0.05, 0.0),
        (1095.0, "P"): (-0.30, -0.05),
        (1097.5, "C"): (0.24, 0.0),
        (1097.5, "P"): (-0.05, 0.0),
        (1100.0, "C"): (0.36, 0.0),
        (1100.0, "P"): (-0.18, 0.04),
        (1102.5, "P"): (0.12, 0.0),
    },
    "1452": {
        (1085.0, "P"): (-0.24, 0.0),
        (1087.5, "C"): (0.30, 0.0),
        (1087.5, "P"): (-0.12, 0.0),
        (1090.0, "C"): (0.18, 0.06),
        (1090.0, "P"): (-0.36, 0.0),
        (1092.5, "C"): (0.05, 0.0),
        (1092.5, "P"): (-0.30, -0.05),
        (1095.0, "C"): (0.24, 0.0),
        (1095.0, "P"): (-0.05, 0.0),
        (1097.5, "C"): (0.36, 0.0),
        (1097.5, "P"): (-0.18, 0.04),
        (1100.0, "P"): (0.12, 0.0),
    },
}
T_KIS_0DTE = 0.5 / 365
# 0DTE ATM 둘레 OI 범위 — 14:52 는 풋 쪽이 무거워
# ±5% 안에 감마 Flip 교차가 없다(골든이 이 갈래를 덮는다)
OI_0DTE: Mapping[str, Mapping[str, tuple[int, int]]] = {
    "1427": {"C": (150, 2300), "P": (150, 2300)},
    "1452": {"C": (40, 420), "P": (600, 2300)},
}


def week0_board(snap: Snap, label: str = W0) -> tuple[list[Opt], list[Opt]]:
    """0DTE WKM 260904 전광판 — 앞 100행(1245.0~997.5). 그날 15:20 만기."""
    m = snap.market
    r = rng(f"board:{snap.name}:{label}")
    now = kst(snap.boards[label])
    T = t_market(now, "260904")
    F = m.fwd0
    spot = m.spot[label]
    t_prev = T + 5 / 365
    fb_k, fb_cp, fb_drop = FALLBACK_0DTE[snap.name]
    missing = IV_MISSING_0DTE.get(snap.name, frozenset())
    offsets = G0_OFFSET[snap.name]
    calls: list[Opt] = []
    puts: list[Opt] = []
    for K in _board_strikes(WEEK_STRIKES):
        for cp, out in (("C", calls), ("P", puts)):
            sigma = smile_0dte(K, F, T, m.vol0)
            theo = bprice(cp, F, K, T, sigma)
            base = to_tick(bprice(cp, PREV_LEVEL - 1.8, K, t_prev, 0.30))
            near = abs(K - F) < 40
            vol = r.randint(1000, 30000) if near else max(1, int(math.exp(r.gauss(6.0, 1.5))))
            lo_oi, hi_oi = OI_0DTE[snap.name][cp]
            oi = r.randint(lo_oi, hi_oi) if near else int(math.exp(r.gauss(5.5, 1.2)))
            bid, ask = quote(r, theo, r.choice([1, 2, 2, 3, 3, 4, 6, 10, 12]))
            intr = intrinsic(cp, F, K)
            if theo < 0.005:
                last = Decimal("0.01")
            else:
                last = to_tick(max(theo * (1 + r.gauss(0, 0.05)), intr + 0.03))
            is_fb = (K, cp) == (fb_k, fb_cp)
            if is_fb:  # 내재가치 밑 last + 넓은 호가 → §1.1 last → 자체 역산 실패 → KIS IV 폴백
                last = to_tick(intr - fb_drop)
                bid = to_tick(intr - 0.45, "down")
                ask = to_tick(intr + 1.85, "up")
            o = Opt(
                family="weekly_mon",
                token="2609W4",  # noqa: S106 — 시리즈 토큰(결제월), 비밀 아님
                series=f"{cp} 260904",
                name_prefix=f"위클리M {cp} 2609W4",
                cp=cp,
                K=K,
                last=last,
                vol=vol,
                oi=oi,
                oi_chg=r.randint(-min(oi, 40), 400),
                base=base,
                bid=bid,
                ask=ask,
            )
            if (K, cp) in missing:
                o.iv = 0.0
            elif is_fb:  # KIS 는 다른 시점의 기초자산으로 매겨 역산이 된다
                o.iv = kis_iv_pct(cp, last, float(K) - float(last) + 0.22, K, T_KIS_0DTE)
            else:
                o.iv = kis_iv_pct(cp, last, F + r.gauss(0, 0.08), K, T_KIS_0DTE)
            off, eta = offsets.get((K, cp), (r.uniform(-0.4, 0.4), 0.0))
            if o.iv > 0:
                sg = o.iv / 100 * (FALLBACK_SIGMA_RATIO if is_fb else 1.0 + eta)
            else:  # KIS IV 가 없는 행 — 시장 σ 를 0.5일 기준으로 옮긴 값
                sg = sigma * math.sqrt(T / T_KIS_0DTE)
            s_g = m.g0_med + off
            if (
                is_fb
            ):  # 폴백 행 그릭스는 다른 시점 기초자산 — 풋 델타 ≈ −0.86 이 되는 S(대칭 중앙 위)
                v = sg * math.sqrt(T_KIS_0DTE)
                s_g = K * math.exp(FALLBACK_D1 * v - 0.5 * v * v)
            kis_greeks(o, s_g, T_KIS_0DTE, sg)
            o.hist = 0.0
            o.invl = round(intrinsic(cp, spot, K), 2)
            o.thpr = o.invl + r.uniform(0.1, 0.4) if o.invl > 0 else 0.0
            out.append(_finish(o, r))
    return calls, puts


WK_TRADED: frozenset[tuple[float, str]] = frozenset({(1092.5, "C")})  # WKM 261001 당일 거래 행


def week1_board(snap: Snap, label: str = W1) -> tuple[list[Opt], list[Opt]]:
    """WKM 261001(D = 8) 전광판 — 거의 모든 행이 당일 거래 없이 전 세션 수준(PREV_LEVEL) 가격."""
    m = snap.market
    r = rng(f"board:{snap.name}:{label}")
    now = kst(snap.boards[label])
    T = t_market(now, "261001")
    tk = t_kis("261001")
    spot = m.spot[label]
    calls: list[Opt] = []
    puts: list[Opt] = []
    for K in _board_strikes(WEEK_STRIKES):
        for cp, out in (("C", calls), ("P", puts)):
            cur = bprice(cp, m.fwd_wk, K, T, 0.355)
            prev_sigma = (0.372 if cp == "C" else 0.415) + r.gauss(0, 0.006)
            prev = to_tick(bprice(cp, PREV_LEVEL, K, tk + 1 / 365, prev_sigma))
            traded = (K, cp) in WK_TRADED
            last = to_tick(cur * (1 + r.gauss(0, 0.03))) if traded else prev
            o = Opt(
                family="weekly_mon",
                token="2610W1",  # noqa: S106 — 시리즈 토큰(결제월), 비밀 아님
                series=f"{cp} 261001",
                name_prefix=f"위클리M {cp} 2610W1",
                cp=cp,
                K=K,
                last=last,
                vol=2 if traded else 0,
                oi=2 if traded else 0,
                oi_chg=2 if traded else 0,
                base=prev,
                bid=to_tick(cur - r.uniform(4.2, 6.0), "down"),
                ask=to_tick(cur + r.uniform(4.0, 6.0), "up"),
            )
            o.hist = 0.0
            o.invl = round(intrinsic(cp, spot, K), 2)
            if traded:
                o.iv = kis_iv_pct(cp, last, m.fwd_wk - 0.2, K, tk)
                kis_greeks(o, m.fwd_wk - 0.35, tk, o.iv / 100)
                o.thpr = o.invl + 0.85
            else:  # 무거래 — IV 는 전 세션 last 를 오늘 T·전 세션 수준으로, 그릭스는 기초 HV 로
                o.iv = kis_iv_pct(cp, last, PREV_LEVEL, K, tk)
                kis_greeks(o, PREV_LEVEL, tk, m.hv / 100)
                o.thpr = bprice(cp, PREV_LEVEL, K, tk, m.hv / 100)
            out.append(_finish(o, r))
    return calls, puts


def _board_json(ts: str, calls: Sequence[Opt], puts: Sequence[Opt]) -> Json:
    return {
        "ts_kst": ts,
        "rt_cd": "0",
        "output1": [c.board_row(atm_label("C", c.K)) for c in calls],
        "output2": [p.board_row(atm_label("P", p.K)) for p in puts],
    }


def full_snapshot(snap: Snap) -> Json:
    """probe `chain_snapshot.json` 꼴의 합성 전체 스냅샷(이 시험들이 읽는 부분)."""
    boards: Json = {}
    for label in (M610, M611):
        boards[label] = _board_json(snap.boards[label], *month_board(snap, label))
    boards[W0] = _board_json(snap.boards[W0], *week0_board(snap))
    boards[W1] = _board_json(snap.boards[W1], *week1_board(snap))
    fills = [
        {"ts_kst": ts, "series": o.series, "rt_cd": "0", "code": o.code, "row": o.price_output()}
        for ts, o in month_fills(snap)
    ]
    return {
        "started_kst": snap.started,
        "atm_ref": {"code": "A01612", "price": float(snap.s_ref)},
        "boards": boards,
        "fills": fills,
    }


# ── KIS 선물 전광판·분봉 ─────────────────────────────────────────────────────

R_FUT = 0.0262  # 선물 이론가(현물 × e^(rT))의 금리
FUT_PREV_CLOSE = 1126.15  # 근월물 전일 종가
FUT_DAY_HIGH, FUT_DAY_LOW, FUT_DAY_OPEN = 1123.70, 1090.10, 1121.40


@dataclass(frozen=True)
class FutLeg:
    code: str
    name: str
    price: str
    bid: str
    ask: str
    prev: float
    rmnn: int
    high: float
    low: float


FUT_LEGS = (
    FutLeg(
        "A01612", "F 202612", "1095.10", "1095.00", "1095.10", FUT_PREV_CLOSE, 74, 1123.70, 1090.10
    ),
    FutLeg("A01703", "F 202703", "1085.00", "1083.85", "1086.35", 1112.60, 165, 1111.50, 1085.00),
)


def futures_board() -> Json:
    """선물 전광판(FHPIF05030200) 14:27 앞 2행 — 근월물 가격·호가와 원월물 가격은 뼈대."""
    r = rng("futures_board")
    spot = SNAP_A.market.spot[W0]
    rows: list[Row] = []
    for leg, vol, oi in (
        (FUT_LEGS[0], r.randint(70000, 90000), r.randint(130000, 140000)),
        (FUT_LEGS[1], r.randint(3, 12), r.randint(4000, 5000)),
    ):
        price = Decimal(leg.price)
        diff = price - Decimal(str(leg.prev))
        rows.append(
            {
                "futs_shrn_iscd": leg.code,
                "hts_kor_isnm": leg.name,
                "futs_prpr": leg.price,
                "futs_prdy_vrss": fmt(diff, 2),
                "prdy_vrss_sign": "2" if diff > 0 else "5" if diff < 0 else "3",
                "futs_prdy_ctrt": fmt(diff / Decimal(str(leg.prev)) * 100, 2),
                "hts_thpr": fmt(spot * math.exp(R_FUT * leg.rmnn / 365), 2),
                "acml_vol": str(vol),
                "futs_askp": leg.ask,
                "futs_bidp": leg.bid,
                "hts_otst_stpl_qty": str(oi),
                "futs_hgpr": fmt(leg.high, 2),
                "futs_lwpr": fmt(leg.low, 2),
                "hts_rmnn_dynu": str(leg.rmnn),
                "total_askp_rsqn": str(r.randint(1, 1500) if vol > 100 else r.randint(1, 5)),
                "total_bidp_rsqn": str(r.randint(1, 1500) if vol > 100 else r.randint(1, 5)),
                "futs_antc_cnpr": "0.00",
                "futs_antc_cntg_vrss": "0.00",
                "antc_cntg_vrss_sign": "0",
                "antc_cntg_prdy_ctrt": "0.00",
            }
        )
    return {
        "_source": f"{SYNTH} — 선물 전광판(FHPIF05030200) output 앞 2행 형태, 14:27",
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "output": rows,
    }


# 14:01 분봉 조회 첫 응답(근월물 A01612, 시장 F) — 합성 수준
MIN_INDEX = 1097.62  # kospi200_nmix
MIN_PRICE = 1098.05  # futs_prpr


def minute_day() -> Json:
    """분봉 조회(FHKIF03020200) 첫 응답 output1(28칸) + output2 앞 3봉."""
    r = rng("minute_day")
    prev = Decimal(str(FUT_PREV_CLOSE))
    price = Decimal(str(MIN_PRICE))
    index = Decimal(str(MIN_INDEX))
    theory = dec(MIN_INDEX * math.exp(R_FUT * 74 / 365), 2)
    diff = price - prev
    vol = r.randint(74000, 80000)
    cum = round(vol * 1107.3 * 250)
    oi = r.randint(134000, 138000)
    out1: Row = {
        "futs_prdy_vrss": fmt(diff, 2),
        "prdy_vrss_sign": "5" if diff < 0 else "2",
        "futs_prdy_ctrt": fmt(diff / prev * 100, 2),
        "futs_prdy_clpr": fmt(prev, 2),
        "prdy_nmix": fmt(prev, 2),
        "acml_vol": str(vol),
        "acml_tr_pbmn": str(cum),
        "hts_kor_isnm": "F 202612",
        "futs_prpr": fmt(price, 2),
        "futs_shrn_iscd": "A01612",
        "prdy_vol": str(r.randint(78000, 86000)),
        "futs_mxpr": fmt(fut_tick(FUT_PREV_CLOSE * 1.08), 2),
        "futs_llam": fmt(fut_tick(FUT_PREV_CLOSE * 0.92), 2),
        "futs_oprc": fmt(FUT_DAY_OPEN, 2),
        "futs_hgpr": fmt(FUT_DAY_HIGH, 2),
        "futs_lwpr": fmt(FUT_DAY_LOW, 2),
        "futs_prdy_oprc": "1133.85",
        "futs_prdy_hgpr": "1137.20",
        "futs_prdy_lwpr": "1114.35",
        "futs_askp": fmt(price + Decimal("0.05"), 2),
        "futs_bidp": fmt(price - Decimal("0.05"), 2),
        "basis": fmt(theory - index, 2),  # KIS basis = 이론가 − 지수(이론 베이시스)
        "kospi200_nmix": fmt(index, 2),
        "hts_otst_stpl_qty": str(oi),
        "otst_stpl_qty_icdc": str(r.randint(1200, 2200)),
        "tday_rltv": fmt(r.uniform(90, 100), 2),
        "hts_thpr": fmt(theory, 2),
        "dprt": fmt((price - theory) / theory * 100, 2),
    }
    bars: list[Row] = []
    close = price
    for hhmm in ("140100", "140000", "135900"):
        bar_vol = r.randint(40, 450)
        o = fut_tick(float(close) + r.uniform(-2.5, 0.5))
        hi = max(o, close) + Decimal("0.05") * r.randint(0, 4)
        lo = min(o, close) - Decimal("0.05") * r.randint(0, 4)
        bars.append(
            {
                "stck_bsop_date": DAY.strftime("%Y%m%d"),
                "stck_cntg_hour": hhmm,
                "futs_prpr": fmt(close, 2),
                "futs_oprc": fmt(o, 2),
                "futs_hgpr": fmt(hi, 2),
                "futs_lwpr": fmt(lo, 2),
                "cntg_vol": str(bar_vol),
                "acml_tr_pbmn": str(cum),
            }
        )
        cum -= round(bar_vol * float(close) * 250)
        close = o  # 앞 봉 종가 ≈ 이 봉 시가
    return {
        "_source": f"{SYNTH} — 분봉 조회 첫 응답 형태(A01612 시장 F, 14:01), output2 앞 3봉",
        "rt_cd": "0",
        "output1": out1,
        "output2": bars,
    }


# ── KIS 투자자별(FHPTJ04030000) ───────────────────────────────────────────────

INVESTOR_KEYS: tuple[str, ...] = (
    *(
        f"{p}_{k}"
        for p in ("frgn", "prsn", "orgn", "scrt", "ivtr")
        for k in (
            "seln_vol",
            "shnu_vol",
            "ntby_qty",
            "seln_tr_pbmn",
            "shnu_tr_pbmn",
            "ntby_tr_pbmn",
        )
    ),
    # 원문 필드 순서 그대로(사모펀드는 순서·이름이 다르다 — 순매수 수량이 `_ntby_vol`)
    "pe_fund_seln_tr_pbmn",
    "pe_fund_seln_vol",
    "pe_fund_ntby_vol",
    "pe_fund_shnu_tr_pbmn",
    "pe_fund_shnu_vol",
    "pe_fund_ntby_tr_pbmn",
    *(
        f"{p}_{k}"
        for p in ("bank", "insu", "mrbn", "fund")
        for k in (
            "seln_vol",
            "shnu_vol",
            "ntby_qty",
            "seln_tr_pbmn",
            "shnu_tr_pbmn",
            "ntby_tr_pbmn",
        )
    ),
    *(
        f"{p}_{k}"
        for p in ("etc_orgt", "etc_corp")
        for k in (
            "seln_vol",
            "shnu_vol",
            "ntby_vol",
            "seln_tr_pbmn",
            "shnu_tr_pbmn",
            "ntby_tr_pbmn",
        )
    ),
)
LEAVES = (
    "frgn",
    "prsn",
    "scrt",
    "ivtr",
    "pe_fund",
    "bank",
    "insu",
    "mrbn",
    "fund",
    "etc_orgt",
    "etc_corp",
)
ORGN = ("scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund")  # 기관계 = 이 일곱의 합

# 조합 → (양쪽 체결 수량 합, 평균 체결가 pt, 투자자별 (매도 비중, 매수 비중))
INVESTOR_PAIRS: Mapping[str, tuple[int, float, Mapping[str, tuple[float, float]]]] = {
    "K2I/F001": (
        71500,
        1104.0,
        {
            "frgn": (0.74, 0.69),
            "prsn": (0.12, 0.11),
            "scrt": (0.08, 0.15),
            "ivtr": (0.004, 0.011),
            "bank": (0.0004, 0.0006),
            "insu": (0.0, 0.003),
            "mrbn": (0.0001, 0.0002),
            "fund": (0.001, 0.003),
            "etc_orgt": (0.008, 0.006),
            "etc_corp": (0.04, 0.035),
        },
    ),
    "K2I/OC01": (
        35800,
        4.2,
        {
            "frgn": (0.61, 0.56),
            "prsn": (0.34, 0.35),
            "scrt": (0.022, 0.058),
            "ivtr": (0.003, 0.004),
            "etc_orgt": (0.004, 0.006),
            "etc_corp": (0.021, 0.022),
        },
    ),
    "K2I/OP01": (
        19600,
        4.9,
        {
            "frgn": (0.60, 0.57),
            "prsn": (0.33, 0.33),
            "scrt": (0.035, 0.07),
            "ivtr": (0.011, 0.012),
            "etc_orgt": (0.004, 0.004),
            "etc_corp": (0.02, 0.014),
        },
    ),
    "WKM/OC05": (
        224000,
        0.95,
        {
            "frgn": (0.60, 0.58),
            "prsn": (0.27, 0.35),
            "scrt": (0.11, 0.048),
            "fund": (0.0008, 0.0002),
            "etc_orgt": (0.009, 0.012),
            "etc_corp": (0.0102, 0.0098),
        },
    ),
    "WKM/OP05": (
        189500,
        1.05,
        {
            "frgn": (0.72, 0.73),
            "prsn": (0.22, 0.225),
            "scrt": (0.052, 0.036),
            "fund": (0.0012, 0.0005),
            "etc_orgt": (0.003, 0.004),
            "etc_corp": (0.0038, 0.0045),
        },
    ),
    "WKI/OC04": (
        1950,
        6.3,
        {
            "frgn": (0.39, 0.57),
            "prsn": (0.58, 0.40),
            "scrt": (0.026, 0.023),
            "etc_orgt": (0.002, 0.004),
            "etc_corp": (0.002, 0.003),
        },
    ),
    "WKI/OP04": (
        1150,
        3.9,
        {
            "frgn": (0.42, 0.71),
            "prsn": (0.58, 0.29),
        },
    ),
}


def _split(total: int, weights: Mapping[str, float], r: random.Random) -> dict[str, int]:
    """총 수량을 비중(+잡음)으로 정수로 나눈다 — 합은 정확히 total(최대 나머지 방식)."""
    w = {k: v * r.uniform(0.9, 1.1) for k, v in weights.items() if v > 0}
    s = sum(w.values())
    raw = {k: total * v / s for k, v in w.items()}
    out = {k: int(v) for k, v in raw.items()}
    rest = total - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:rest]:
        out[k] += 1
    return {k: out.get(k, 0) for k in LEAVES}


def investor() -> Json:
    """투자자별 7조합 × 1행 × 72칸 — 매수·매도 합이 같고, 기관계 = 일곱 기관 합,
    순매수 = 매수 − 매도
    (대금은 따로 반올림해 ±1). 값은 백만원, 계약당 25만원."""
    r = rng("investor")
    pairs: Json = {}
    for key, (total, px, weights) in INVESTOR_PAIRS.items():
        sells = _split(total, {k: v[0] for k, v in weights.items()}, r)
        buys = _split(total, {k: v[1] for k, v in weights.items()}, r)
        val_s = {k: sells[k] * px * r.uniform(0.97, 1.03) * 0.25 for k in LEAVES}
        val_b = {k: buys[k] * px * r.uniform(0.97, 1.03) * 0.25 for k in LEAVES}
        for d in (sells, buys, val_s, val_b):
            d["orgn"] = sum(d[k] for k in ORGN)  # type: ignore[assignment]
        row: Row = {}
        for k in INVESTOR_KEYS:
            p, _, kind = next(
                (p, "_", k[len(p) + 1 :])
                for p in ("pe_fund", "etc_orgt", "etc_corp", *LEAVES, "orgn")
                if k.startswith(p + "_")
            )
            if kind == "seln_vol":
                v = sells[p]
            elif kind == "shnu_vol":
                v = buys[p]
            elif kind in ("ntby_qty", "ntby_vol"):
                v = buys[p] - sells[p]
            elif kind == "seln_tr_pbmn":
                v = round(val_s[p])
            elif kind == "shnu_tr_pbmn":
                v = round(val_b[p])
            else:  # ntby_tr_pbmn
                v = round(val_b[p] - val_s[p])
            row[k] = str(v)
        pairs[key] = {"rt_cd": "0", "output": [row]}
    return {
        "_source": f"{SYNTH} — 시장별 투자자매매동향 형태(13:25), 7조합 × 1행 × 72칸",
        "pairs": pairs,
    }


# ── 월물리스트 (상장 규칙) ─────────────────────────────────────────────────────


def _months(y: int, m: int, n: int) -> list[tuple[int, int]]:
    out = []
    for _ in range(n):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def option_list() -> Json:
    """월물리스트(FHPIO056104C0) — 코스피200 월물: 연속 6개월 +
    그 뒤 분기월 2개 + 반기월(6·12월) 3개,
    미니: 연속 6개월, 위클리: 그날 상장된 주. 상장 규칙에서 나온다(시세가 아니다)."""
    near = _months(2026, 10, 6)
    after = _months(*_months(near[-1][0], near[-1][1], 2)[1], 30)
    quarterly = [ym for ym in after if ym[1] in (3, 6, 9, 12)][:2]
    semi = [ym for ym in after if ym[1] in (6, 12) and ym > quarterly[-1]][:3]

    def rows(yms: Iterable[tuple[int, int]]) -> list[Row]:
        return [{"mtrt_yymm_code": f"0{y % 10}{m:02d}", "mtrt_yymm": f"{y}{m:02d}"} for y, m in yms]

    def weekly(codes: Iterable[str]) -> list[Row]:
        return [{"mtrt_yymm_code": c[2:], "mtrt_yymm": c} for c in codes]

    return {
        "_source": f"{SYNTH} — 월물리스트 형태(2026-09-28 13:33), 상장 규칙으로 만든 목록",
        "by_class": {
            "(blank)": {"rt_cd": "0", "output": rows([*near, *quarterly, *semi])},
            "K21": {"rt_cd": "0", "output": []},
            "WKM": {"rt_cd": "0", "output": weekly(["260904", "261001"])},
            "WKI": {"rt_cd": "0", "output": weekly(["261001"])},
            "MKI": {"rt_cd": "0", "output": rows(near)},
        },
    }


# ── 마스터 줄 ────────────────────────────────────────────────────────────────

# (종류, 코드, 이름, 기초자산코드, 기초자산명) — 선물·스프레드 (선물 순번 1)
MASTER_FUTURES = (
    ("1", "A01612", "F 202612", "2001", "KOSPI200"),
    ("B", "A05610", "미니F 202610", "2001", "KOSPI200"),
    ("3", "A06612", "코스닥150F 202612", "3003", "KSQ150"),
    ("7", "A04610", "변동성F 202610", "0503", "VKOSPI"),
    ("9", "AA4612", "고배당50   F 202612", "0163", "고배당50"),
    ("2", "D0161201", "SP 2612-2703", "2001", "KOSPI200"),
)
# 상품군 → (콜 종류, 풋 종류, 이름 앞부분, 기초자산 코스닥?)
MASTER_FAMILY: Mapping[str, tuple[str, str, str, bool]] = {
    "kospi200": ("5", "6", "", False),
    "mini": ("D", "E", "미니", False),
    "weekly_thu": ("L", "M", "위클리", False),
    "weekly_mon": ("N", "O", "위클리M ", False),
    "kosdaq150": ("J", "K", "코스닥150", True),
    "kosdaq_weekly_thu": ("P", "Q", "코스닥위클리", True),
    "kosdaq_weekly_mon": ("R", "S", "코스닥위클리M ", True),
}
# 종류별 표본 — (상품군, 만기 표기, 콜풋, 행사가)
MASTER_SAMPLES: tuple[tuple[str, str, str, float], ...] = (
    ("kospi200", "202610", "C", 745.0),
    ("kospi200", "202610", "P", 745.0),
    ("kospi200", "202610", "C", 1125.0),
    ("kospi200", "202610", "P", 1125.0),
    ("kospi200", "202610", "C", 1127.5),
    ("kospi200", "202610", "P", 1127.5),
    ("mini", "202610", "C", 752.5),
    ("mini", "202610", "C", 1125.0),
    ("mini", "202610", "P", 1125.0),
    ("weekly_thu", "2610W1", "C", 1125.0),
    ("weekly_thu", "2610W1", "P", 1125.0),
    ("weekly_thu", "2610W1", "C", 992.5),
    ("weekly_mon", "2610W1", "C", 1125.0),
    ("kosdaq150", "202610", "C", 1275.0),
    ("kosdaq150", "202610", "P", 950.0),
    ("kosdaq150", "202610", "C", 1425.0),
    ("kosdaq_weekly_thu", "2610W1", "C", 1425.0),
    ("kosdaq_weekly_mon", "2609W4", "P", 1300.0),
)


def moneyness(cp: str, K: float, level: float, grid: float) -> str:
    """마스터 ATM 구분(1 ATM · 2 ITM · 3 OTM) —
    작성 시점 기준 수준에 가장 가까운 격자 행사가가 ATM."""
    atm = round(level / grid) * grid
    if K == atm:
        return "1"
    itm = K < atm if cp == "C" else K > atm
    return "2" if itm else "3"


def master_line(family: str, token: str, cp: str, K: float) -> str:
    kc, kp, prefix, kq = MASTER_FAMILY[family]
    code = opt_code(family, token, cp, K)
    strike = f"{int(K):5,d}" if kq else f"{K:7,.1f}"
    name = f"{prefix}{cp} {token} {strike}"
    flag = moneyness(cp, K, KQ_LEVEL if kq else PREV_LEVEL, 25.0 if kq else 2.5)
    under = "3003|KSQ150" if kq else "2001|KOSPI200"
    return f"{kc if cp == 'C' else kp}|{code}|{isin(code)}|{name}|{flag}|{K:08.2f}| |{under}"


def master_lines() -> Json:
    lines = [f"{k}|{c}|{isin(c)}|{n}| |00000.00|1|{uc}|{un}" for k, c, n, uc, un in MASTER_FUTURES]
    lines += [master_line(*s) for s in MASTER_SAMPLES]
    month = _board_strikes(MONTH_STRIKES)
    board_610 = [*month[:10], *month[-10:]]  # callput_202610.json 의 행사가
    for cp in ("C", "P"):
        lines += [master_line("kospi200", "202610", cp, k) for k in board_610]
    week = _board_strikes(WEEK_STRIKES)[46:66]  # callput_wkm_260904.json 의 행사가
    for cp in ("C", "P"):
        lines += [master_line("weekly_mon", "2609W4", cp, k) for k in week]
    for cp in ("C", "P"):  # price_options.json 의 종목
        lines += [master_line("kospi200", "202610", cp, k) for k in PRICE_STRIKES]
    return {
        "_source": f"{SYNTH} — 지수선물옵션 마스터(fo_idx_code_mts.mst) 줄 형식. 전광판·단건 "
        "fixture 종목 전부 + 종류별 표본. 옵션 코드·표준코드는 합성 규칙",
        "lines": lines,
    }


PRICE_STRIKES = (1092.5, 1095.0, 1097.5)


# ── KRX 파생 일별 (/drv/opt_bydd_trd·/drv/fut_bydd_trd) ──────────────────────────

KRX_DAY = "20260923"
KRX_SPOT = 1124.85  # 2026-09-23 코스피200 (합성)
KRX_FWD = 1123.40  # 그날 주간 202610 옵션의 선도
KRX_T = 15.0 / 365  # 09-23 마감 → 10-08 만기
KQ_SPOT = 1426.90  # 코스닥150 (합성)


def krx_code(prefix5: str, K: float) -> str:
    """합성 KRX ISU_CD — 앞 5글자 상품·만기, 뒤 3글자 `Z` + round(K/2.5) 의 36진 두 자리."""
    n = round(K / 2.5) % (36 * 36)
    return prefix5 + "Z" + _B36[n // 36] + _B36[n % 36]


def _krx_iv(x: float) -> str:
    """KRX IMP_VOLT — 0.1%p 단위로 적힌다(소수 둘째 자리 0)."""
    return fmt(round(x * 10) / 10, 2)


@dataclass(frozen=True)
class KrxOpt:
    prod: str
    cp: str
    prefix5: str
    name: str  # ISU_NM 앞부분(행사가 앞까지)
    K: float
    strike_txt: str
    session: str  # '(정규)'·'(야간)'·''
    traded: bool
    iv: float | None  # IMP_VOLT %(None 이면 0.00)
    price: float  # 이론가(종가·기준가 바탕)
    vol: int = 0
    oi: int = 0
    next_base: str | None = None  # NXTDD_BAS_PRC 를 직접 줄 때('' = 빈칸)
    base_from_day: float | None = None  # 야간 행 NXTDD = 주간 종가


def krx_opt_daily() -> Json:
    r = rng("krx_opt")
    F, T = KRX_FWD, KRX_T
    c1100 = to_tick(bprice("C", F, 1100, T, 0.352))
    p1100 = to_tick(bprice("P", F, 1100, T, 0.398))
    specs = [
        KrxOpt(
            "코스피200 옵션",
            "C",
            "B016A",
            "코스피200 C 202610",
            1100,
            "1,100.0",
            "(정규)",
            True,
            35.2,
            float(c1100),
            r.randint(60, 120),
            r.randint(2600, 3000),
        ),
        KrxOpt(
            "코스피200 옵션",
            "C",
            "B016A",
            "코스피200 C 202610",
            1100,
            "1,100.0",
            "(야간)",
            True,
            None,
            float(c1100) + 4.65,
            r.randint(1, 6),
            r.randint(2600, 3000),
            base_from_day=float(c1100),
        ),
        KrxOpt(
            "코스피200 옵션",
            "P",
            "C016A",
            "코스피200 P 202610",
            1100,
            "1,100.0",
            "(정규)",
            True,
            39.8,
            float(p1100),
            r.randint(700, 950),
            r.randint(1500, 1900),
        ),
        KrxOpt(
            "코스피200 옵션",
            "P",
            "C016A",
            "코스피200 P 202610",
            1100,
            "1,100.0",
            "(야간)",
            True,
            None,
            float(p1100) - 3.95,
            r.randint(15, 40),
            r.randint(1100, 1400),
            base_from_day=float(p1100),
        ),
        KrxOpt(
            "코스피200 옵션",
            "C",
            "B016A",
            "코스피200 C 202610",
            745,
            "  745.0",
            "(정규)",
            False,
            31.5,
            F - 745 + 0.05,
            0,
            r.randint(30, 60),
        ),
        KrxOpt(
            "코스피200 옵션",
            "P",
            "C018C",
            "코스피200 P 202812",
            1000,
            "1,000.0",
            "(정규)",
            False,
            41.3,
            bprice("P", F + 9.5, 1000, 2.2, 0.413),
            0,
            r.randint(250, 340),
        ),
        KrxOpt(
            "미니코스피200 옵션",
            "C",
            "B056A",
            "미니코스피 C 202610",
            752.5,
            "  752.5",
            "(야간)",
            False,
            None,
            F - 752.5 + 0.05,
            0,
            0,
        ),
        KrxOpt(
            "미니코스피200 옵션",
            "C",
            "B056A",
            "미니코스피 C 202610",
            940,
            "  940.0",
            "(야간)",
            True,
            None,
            F - 940 + 2.1,
            r.randint(3, 9),
            r.randint(3, 9),
        ),
        KrxOpt(
            "미니코스피200 옵션",
            "P",
            "C056A",
            "미니코스피 P 202610",
            1100,
            "1,100.0",
            "(정규)",
            True,
            39.8,
            float(p1100) + 0.25,
            r.randint(25, 50),
            r.randint(220, 320),
        ),
        KrxOpt(
            "코스피200 위클리(목) 옵션",
            "C",
            "B09FE",
            "코스피위클리 C 2609W4",
            1100,
            "1,100.0",
            "(정규)",
            True,
            26.6,
            F - 1100 + 1.85,
            r.randint(150, 260),
            0,
            next_base="",
        ),
        KrxOpt(
            "코스피200 위클리(목) 옵션",
            "P",
            "C09FF",
            "코스피위클리 P 2610W1",
            1100,
            "1,100.0",
            "(야간)",
            False,
            None,
            15.95,
            0,
            0,
        ),
        KrxOpt(
            "코스피200 위클리(월) 옵션",
            "C",
            "BAFBZ",
            "코스피위클리M C 2609W4",
            1100,
            "1,100.0",
            "(정규)",
            True,
            24.1,
            F - 1100 + 4.3,
            r.randint(2, 5),
            r.randint(3, 8),
        ),
        KrxOpt(
            "코스피200 위클리(월) 옵션",
            "P",
            "CAFBZ",
            "코스피위클리M P 2609W4",
            970,
            "  970.0",
            "(야간)",
            True,
            None,
            0.10,
            r.randint(40, 80),
            r.randint(150, 230),
        ),
        KrxOpt(
            "코스닥150 옵션",
            "C",
            "B066A",
            "코스닥150 C 202610",
            1000,
            "1,000",
            "(정규)",
            False,
            29.6,
            KQ_SPOT - 1000 + 0.1,
            0,
            0,
        ),
        KrxOpt(
            "코스닥150 옵션",
            "P",
            "C066A",
            "코스닥150 P 202610",
            875,
            "  875",
            "(야간)",
            False,
            None,
            0.30,
            0,
            r.randint(30, 60),
        ),
        KrxOpt(
            "코스닥150 위클리(목) 옵션",
            "C",
            "BAJ36",
            "코스닥위클리 C 2609W4",
            1175,
            "1,175",
            "",
            False,
            None,
            0.0,
            0,
            0,
            next_base="",
        ),
        KrxOpt(
            "코스닥150 위클리(월) 옵션",
            "C",
            "BAK48",
            "코스닥위클리M C 2609W4",
            1200,
            "1,200",
            "",
            False,
            7.6,
            KQ_SPOT - 1200 - 0.4,
            0,
            0,
        ),
        # 표기 없는 코스닥 위클리 — 같은 ISU_CD 두 행, 하나는 IMP_VOLT 0.00(값 없음)
        KrxOpt(
            "코스닥150 위클리(목) 옵션",
            "C",
            "BAJ36",
            "코스닥위클리 C 2609W4",
            1175,
            "1,175",
            "",
            False,
            23.9,
            0.0,
            0,
            0,
            next_base="",
        ),
    ]
    old = [
        KrxOpt(
            "코스피200 옵션",
            "C",
            "201E1",
            "코스피200 C 201001",
            185,
            "185.0",
            "(정규)",
            True,
            63.5,
            37.85,
            r.randint(2, 6),
            r.randint(3800, 4300),
        ),
        KrxOpt(
            "코스피200 옵션",
            "P",
            "301E3",
            "코스피200 P 201003",
            240,
            "240.0",
            "(정규)",
            True,
            20.8,
            18.45,
            r.randint(150, 220),
            r.randint(900, 1200),
        ),
        KrxOpt(
            "미국달러 옵션",
            "C",
            "275E1",
            "미국달러 C 201001",
            1120,
            "1,120.0",
            "",
            False,
            8.4,
            45.35,
            0,
            0,
        ),
    ]

    def row(day: str, s: KrxOpt) -> Row:
        name = f"{s.name} {s.strike_txt}" + (f" {s.session}" if s.session else "")
        close = to_tick(s.price) if s.traded else None
        prev = None if close is None else to_tick(float(close) * r.uniform(0.75, 1.25))
        if close is not None and prev is not None:
            hi = max(close, prev) + tick(float(close)) * r.randint(0, 20)
            lo = max(min(close, prev) - tick(float(close)) * r.randint(0, 20), Decimal("0.01"))
            op = min(max(prev, lo), hi)
        else:
            hi = lo = op = None
        if s.next_base is not None:
            nxt = s.next_base
        elif s.base_from_day is not None:
            nxt = fmt(to_tick(s.base_from_day), 2)
        else:
            nxt = fmt(close if close is not None else to_tick(s.price), 2)
        mult = 250000 if "코스피200" in s.prod and "미니" not in s.prod else 50000
        val = (
            0
            if close is None
            else round(s.vol * float(close) * mult * r.uniform(0.9, 1.1) / 2500) * 2500
        )
        return {
            "BAS_DD": day,
            "PROD_NM": s.prod,
            "RGHT_TP_NM": "CALL" if s.cp == "C" else "PUT",
            "ISU_CD": krx_code(s.prefix5, s.K),
            "ISU_NM": name,
            "TDD_CLSPRC": "" if close is None else fmt(close, 2),
            "CMPPREVDD_PRC": "" if close is None or prev is None else fmt(close - prev, 2),
            "TDD_OPNPRC": "" if op is None else fmt(op, 2),
            "TDD_HGPRC": "" if hi is None else fmt(hi, 2),
            "TDD_LWPRC": "" if lo is None else fmt(lo, 2),
            "IMP_VOLT": "0.00" if s.iv is None else _krx_iv(s.iv),
            "NXTDD_BAS_PRC": nxt,
            "ACC_TRDVOL": str(s.vol if close is not None else 0),
            "ACC_TRDVAL": str(val),
            "ACC_OPNINT_QTY": str(s.oi),
        }

    return {
        "_source": f"{SYNTH} — KRX /drv/opt_bydd_trd 응답 행 형태(basDd 20260923·20100104), "
        "이름 형식마다 한두 행",
        KRX_DAY: [row(KRX_DAY, s) for s in specs],
        "20100104": [row("20100104", s) for s in old],
    }


def krx_fut_daily() -> Json:
    r = rng("krx_fut")
    legs = [
        ("야간", "A056A000", "미니코스피 F 202610 (야간)", 1131.40, 25.10, None, 33800, 63150),
        ("정규", "A056A000", "미니코스피 F 202610 (주간)", 1120.35, 14.05, 1120.35, 119400, 63210),
        ("야간", "A056B000", "미니코스피 F 202611 (야간)", 1135.30, 26.05, None, 230, 2510),
    ]
    rows: list[Row] = []
    for mkt, code, name, close, chg, setl, vol, oi in legs:
        op = close - chg + r.uniform(-1.5, 1.5)
        hi = max(close, op) + r.uniform(0.5, 4.0)
        lo = min(close, op) - r.uniform(0.0, 1.0)
        rows.append(
            {
                "BAS_DD": KRX_DAY,
                "PROD_NM": "미니코스피200 선물",
                "MKT_NM": mkt,
                "ISU_CD": code,
                "ISU_NM": name,
                "TDD_CLSPRC": fmt(close, 2),
                "CMPPREVDD_PRC": fmt(chg, 2),
                "TDD_OPNPRC": fmt(op, 2),
                "TDD_HGPRC": fmt(hi, 2),
                "TDD_LWPRC": fmt(lo, 2),
                "SPOT_PRC": fmt(KRX_SPOT, 2),
                "SETL_PRC": "" if setl is None else fmt(setl, 2),
                "ACC_TRDVOL": str(vol + r.randint(0, 400)),
                "ACC_TRDVAL": str(round(vol * close * 50000 / 1000) * 1000),
                "ACC_OPNINT_QTY": str(oi + r.randint(0, 40)),
            }
        )
    return {
        "_source": f"{SYNTH} — KRX /drv/fut_bydd_trd 응답 행 형태(basDd 20260923), 미니 선물 3행",
        "rows": rows,
    }


# ── 출력 ─────────────────────────────────────────────────────────────────────


def _is_flat(v: object) -> bool:
    return isinstance(v, dict) and all(
        not isinstance(x, (dict, list))
        for x in v.values()  # type: ignore[union-attr]
    )


def dump(obj: object, ind: int = 0) -> str:
    """fixture 표기 — 한 칸 들여쓰기, 값이 모두 스칼라인 객체는 한 줄(행 단위 diff)."""
    pad = " " * ind
    if isinstance(obj, dict) and obj and not (_is_flat(obj) and ind > 0):
        items = [
            f"{pad} {json.dumps(k, ensure_ascii=False)}: {dump(v, ind + 1)}"
            for k, v in obj.items()  # type: ignore[union-attr]
        ]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(obj, list) and obj:
        items = [pad + " " + dump(v, ind + 1) for v in obj]  # type: ignore[union-attr]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)


def _board_slice(board: Json, idx: Sequence[int], source: str) -> Json:
    return {
        "_source": source,
        "rt_cd": "0",
        "output1": [board["output1"][i] for i in idx],
        "output2": [board["output2"][i] for i in idx],
    }


def price_options(full: Json) -> Json:
    items = []
    for f in full["fills"]:
        if float(f["row"]["acpr"]) in PRICE_STRIKES:
            items.append(
                {
                    "ts_kst": f["ts_kst"],
                    "code": f["code"],
                    "series": f["series"],
                    "body": {"rt_cd": "0", "msg_cd": "MCA00000", "output1": f["row"]},
                }
            )
    return {
        "_source": f"{SYNTH} — 옵션 단건 현재가(FHMIF10000000 시장 O) output1 형태, 14:27 합성 "
        "스냅샷의 보강 행 — 행사가 1092.5·1095.0·1097.5 콜·풋",
        "items": items,
    }


def small_name(snap: Snap) -> str:
    return f"chain_snapshot_synthetic_20260928_{snap.name}_small.json"


def outputs() -> dict[Path, str]:
    """경로 → 내용. 같은 시드면 바이트까지 같다."""
    from scripts.make_golden import chain_subset, dump_chain  # 작은 체인은 프로젝트 규칙으로 자른다

    full = {s.name: full_snapshot(s) for s in SNAPS}
    a = full[SNAP_A.name]
    out: dict[Path, str] = {}
    for s in SNAPS:
        out[FIX / "validation" / small_name(s)] = dump_chain(chain_subset(full[s.name]))
    out[FIX / "kis" / "callput_202610.json"] = dump(
        _board_slice(
            a["boards"][M610],
            [*range(10), *range(BOARD_ROWS - 10, BOARD_ROWS)],
            f"{SYNTH} — 전광판 콜/풋(FHPIF05030100) MONTH:202610 14:27, output1·output2 앞 10행 + "
            "뒤 10행",
        )
    )
    out[FIX / "kis" / "callput_wkm_260904.json"] = dump(
        _board_slice(
            a["boards"][W0],
            range(46, 66),
            f"{SYNTH} — 전광판 콜/풋(FHPIF05030100) WKM:260904 14:27, 인덱스 46~65 (1130.0~1082.5)",
        )
    )
    out[FIX / "kis" / "price_options.json"] = dump(price_options(a))
    out[FIX / "kis" / "futures_board.json"] = dump(futures_board())
    out[FIX / "kis" / "investor.json"] = dump(investor())
    out[FIX / "kis" / "minute_day.json"] = dump(minute_day())
    out[FIX / "kis" / "option_list.json"] = dump(option_list())
    out[FIX / "kis" / "master_lines.json"] = dump(master_lines())
    out[FIX / "krx" / "opt_daily.json"] = dump(krx_opt_daily())
    out[FIX / "krx" / "fut_daily.json"] = dump(krx_fut_daily())
    return {p: t if t.endswith("\n") else t + "\n" for p, t in out.items()}


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SYNTHETIC KIS·KRX 시험 fixture 생성")
    ap.add_argument("--write", action="store_true", help="fixture 를 다시 쓴다(없으면 비교만)")
    ap.add_argument("--explain", action="store_true", help="시험 기대값 독립 계산을 찍는다")
    args = ap.parse_args(argv)
    if args.explain:
        sys.stdout.write(explain())
        return 0
    rc = 0
    for path, text in outputs().items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"썼다: {_rel(path)}")
        elif not path.exists():
            print(f"없다: {_rel(path)} — 만들려면 --write")
            rc = 1
        elif path.read_text(encoding="utf-8") != text:
            print(f"다르다: {_rel(path)} — 의도한 변경이면 --write 로 다시 쓴다")
            rc = 1
        else:
            print(f"같다: {_rel(path)}")
    return rc


# ── --explain: 시험 기대값의 독립 계산 ─────────────────────────────────────────────
#
# 아래는 core·scripts 의 계산 코드를 부르지 않는다 — 생성한 fixture 의 입력 값과 이 파일의 수학
# (Black-76·이분법·중앙값·최소제곱)만 쓴다. 합성 교체로 바뀐 시험 기대값은 여기 찍힌 값이다.

REL_TOL = 0.01  # §6.2 재현 판정 — 평이 상대오차 1%
HALF_ULP = 0.00005  # KIS 4자리 표시의 반올림 반폭


def _num(v: str) -> float:
    return float(v) if v.strip() else 0.0


def _ticks(b: float, a: float) -> float:
    """b → a 사이 호가 틱 수(10pt 아래 0.01·이상 0.05)."""
    if a <= 10 or b >= 10:
        t = 0.01 if b < 10 else 0.05
        return (a - b) / t
    return (10 - b) / 0.01 + (a - 10) / 0.05


def _price_choice(bid: float, ask: float, last: float, vol: int | None) -> tuple[float, str, bool]:
    """§1.1 — 호가가 둘 다 있고 3틱 안이면 mid, 아니면 last(당일 거래량 0 이면 전 세션)."""
    if bid > 0 and ask > 0 and ask >= bid and _ticks(bid, ask) <= 3 + 1e-9:
        return (bid + ask) / 2, "mid", False
    return last, "last", vol == 0


@dataclass(frozen=True)
class Q:
    K: float
    cp: str
    price: float
    kind: str
    prev: bool
    row: Row


def _quotes(snap_json: Json, label: str, fills_series: str | None) -> list[Q]:
    """작은 스냅샷의 한 시리즈 → 가격 고른 종목(겹치면 전광판 행)."""
    b = snap_json["boards"][label]
    out: dict[tuple[float, str], Q] = {}
    for cp, side in (("C", "output1"), ("P", "output2")):
        for r in b[side]:
            px, kind, prev = _price_choice(
                _num(r["optn_bidp"]), _num(r["optn_askp"]), _num(r["optn_prpr"]), int(r["acml_vol"])
            )
            out[(float(r["acpr"]), cp)] = Q(float(r["acpr"]), cp, px, kind, prev, r)
    for f in snap_json["fills"]:
        if fills_series is None or f["series"].split()[1] != fills_series:
            continue
        r = f["row"]
        cp = f["series"][0]
        key = (float(r["acpr"]), cp)
        if key not in out:
            vol = int(r["acml_vol"])
            out[key] = Q(key[0], cp, _num(r["futs_prpr"]), "last", vol == 0, r)
    return list(out.values())


def _forward(qs: Sequence[Q], s_ref: float) -> tuple[float | None, list[float], list[float]]:
    """§1.3 — S_ref 에 가장 가까운 행사가(동률 낮은 쪽) ± 2 칸,
    전 세션 가격 행사가 빼고 C − P + K 중앙값."""
    ks = sorted({q.K for q in qs})
    i = min(range(len(ks)), key=lambda j: (abs(ks[j] - s_ref), ks[j]))
    by = {(q.K, q.cp): q for q in qs}
    used: list[float] = []
    skipped: list[float] = []
    vals: list[float] = []
    for k in ks[max(0, i - 2) : i + 3]:
        c, p = by.get((k, "C")), by.get((k, "P"))
        if c is None or p is None or not c.price or not p.price:
            continue
        if c.prev or p.prev:
            skipped.append(k)
            continue
        used.append(k)
        vals.append(c.price - p.price + k)
    return (statistics.median(vals) if vals else None), used, skipped


def _parity_forward(rows: Iterable[tuple[float, str, float]]) -> tuple[float, int]:
    """KIS 이론가 thpr_C − thpr_P = A − D·K 최소제곱 → 선도 A/D, 행사가 수."""
    by: dict[float, dict[str, float]] = {}
    for k, cp, th in rows:
        if th > 0:
            by.setdefault(k, {})[cp] = th
    pts = [(k, v["C"] - v["P"]) for k, v in by.items() if "C" in v and "P" in v]
    n = len(pts)
    mx = sum(k for k, _ in pts) / n
    my = sum(y for _, y in pts) / n
    slope = sum((k - mx) * (y - my) for k, y in pts) / sum((k - mx) ** 2 for k, _ in pts)
    a = my - slope * mx
    return a / -slope, n


def _plain(ours: float, kis: float) -> float:
    return ours / kis - 1


def _rounded(ours: float, kis: float) -> float:
    gap = abs(ours - kis) - HALF_ULP
    return 0.0 if gap <= 0 else math.copysign(gap / abs(kis), ours - kis)


def _ok(ours: float, kis: float, rounding: bool) -> bool:
    return abs((_rounded if rounding else _plain)(ours, kis)) <= REL_TOL


def _norm_ppf(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ncdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _solve_dg(K: float, cp: str, delta: float, gamma: float) -> tuple[float, float] | None:
    """KIS Δ·Γ 한 쌍 → (S*, x* = σ√T). N(d1) 이 0.02~0.98 밖이면 None."""
    nd1 = delta if cp == "C" else delta + 1
    if not 0.02 <= nd1 <= 0.98 or gamma <= 0:
        return None
    d1 = _norm_ppf(nd1)

    def f(x: float) -> float:
        S = npdf(d1) / (gamma * x)
        return (math.log(S / K) + 0.5 * x * x) / x - d1

    lo, hi = 1e-5, 2.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    x = 0.5 * (lo + hi)
    return npdf(d1) / (gamma * x), x


def _kis_rows(
    qs: Sequence[Q], hist_ref: float | None
) -> list[tuple[float, str, float, float | None, float | None, int]]:
    """재현 검사 대상 — (K, 콜풋, σ, KIS Δ, KIS Γ, 거래량). σ 관례:
    hist → (거래 있음) KIS IV → hist_ref."""
    out = []
    for q in sorted(qs, key=lambda q: (q.K, q.cp)):
        r = q.row
        hist, iv = _num(r["hist_vltl"]), _num(r["hts_ints_vltl"])
        vol = int(r["acml_vol"])
        delta = _num(r["delta_val"])
        gamma = _num(r["gama"]) or None
        if hist > 0:
            sigma: float | None = hist / 100
        elif vol != 0:
            sigma = iv / 100 if iv > 0 else None
        else:
            sigma = None if hist_ref is None else hist_ref / 100
        if sigma is None or (not delta and gamma is None):
            continue
        out.append((q.K, q.cp, sigma, delta or None, gamma, vol))
    return out


def _counts(
    rows: Sequence[tuple[float, str, float, float | None, float | None, int]],
    S: float,
    T: float,
    rounding: bool = False,
) -> tuple[int, int, int, int]:
    d = [(_ok(bdelta(cp, S, K, T, s), kd, rounding)) for K, cp, s, kd, _, _ in rows if kd]
    g = [(_ok(bgamma(S, K, T, s), kg, rounding)) for K, _, s, _, kg, _ in rows if kg is not None]
    return sum(d), len(d), sum(g), len(g)


def _causes_rounding(
    rows: Sequence[tuple[float, str, float, float | None, float | None, int]], S: float, T: float
) -> int:
    """평이 1% 밖인데 반올림 보정으로는 1% 안인 행 수(원인 `반올림`)."""
    n = 0
    for K, cp, s, kd, kg, _ in rows:
        pairs = [(bdelta(cp, S, K, T, s), kd)] if kd else []
        if kg is not None:
            pairs.append((bgamma(S, K, T, s), kg))
        if not all(_ok(o, k, False) for o, k in pairs) and all(_ok(o, k, True) for o, k in pairs):
            n += 1
    return n


def explain() -> str:
    from scripts.make_golden import chain_subset  # 작은 스냅샷 = 커밋된 fixture 와 같은 자름

    lines: list[str] = []

    def out(*a: object) -> None:
        lines.append(" ".join(str(x) for x in a))

    full = {s.name: full_snapshot(s) for s in SNAPS}
    small = {s.name: chain_subset(full[s.name]) for s in SNAPS}
    A = small[SNAP_A.name]
    s_ref = float(SNAP_A.s_ref)
    m = SNAP_A.market

    out("# KIS fixture 입력 값 (test_kis_models·test_store_sql·test_poller_fake_kis)")
    top = full[SNAP_A.name]["boards"][M610]["output1"][0]
    out(
        "callput_202610 output1[0]:",
        {
            k: top[k]
            for k in (
                "optn_shrn_iscd",
                "acpr",
                "hts_otst_stpl_qty",
                "otst_stpl_qty_icdc",
                "gama",
                "delta_val",
                "hts_ints_vltl",
                "acml_tr_pbmn",
            )
        },
    )
    fb = futures_board()["output"][0]
    out("futures_board output[0]:", {k: fb[k] for k in ("hts_otst_stpl_qty", "acml_vol")})
    po = price_options(full[SNAP_A.name])["items"]
    out(
        "price_options items[0]:",
        po[0]["code"],
        po[0]["body"]["output1"]["futs_prpr"],
        "| C 1095.0 코드:",
        next(
            i["code"]
            for i in po
            if i["series"] == "C 202610" and i["body"]["output1"]["acpr"] == "1095.00"
        ),
    )
    inv = investor()["pairs"]["K2I/F001"]["output"][0]
    out(
        "investor K2I/F001: etc_corp_ntby_vol",
        inv["etc_corp_ntby_vol"],
        "frgn_ntby_qty",
        inv["frgn_ntby_qty"],
    )
    md = minute_day()
    b0 = md["output2"][0]
    out(
        "minute_day output2[0]:",
        {
            k: b0[k]
            for k in (
                "futs_oprc",
                "futs_hgpr",
                "futs_lwpr",
                "futs_prpr",
                "cntg_vol",
                "acml_tr_pbmn",
            )
        },
    )
    o1 = md["output1"]
    prpr, nmix = Decimal(o1["futs_prpr"]), Decimal(o1["kospi200_nmix"])
    thpr, basis = Decimal(o1["hts_thpr"]), Decimal(o1["basis"])
    out(
        "minute_day output1:",
        {
            k: o1[k]
            for k in (
                "futs_prpr",
                "kospi200_nmix",
                "hts_thpr",
                "basis",
                "dprt",
                "otst_stpl_qty_icdc",
                "tday_rltv",
            )
        },
    )
    out(
        "  시장 베이시스 = 선물가 − 지수 =",
        prpr - nmix,
        "· 이론 베이시스 = 이론가 − 지수 =",
        thpr - nmix,
        "· KIS − 이론 =",
        basis - (thpr - nmix),
        "· KIS − 시장 =",
        f"{basis - (prpr - nmix):+}",
        "· 경계 basis(이론 + 0.05) =",
        thpr - nmix + Decimal("0.05"),
    )
    out("  KIS basis 를 시장 베이시스로 바꾸면 이론 − KIS =", (thpr - nmix) - (prpr - nmix))

    out("\n# 마스터 (test_kis_master·test_store_sql)")
    out("A01612 표준코드:", isin("A01612"))
    for fam, tok, cp, k in (
        ("kospi200", "202610", "C", 1125.0),
        ("kospi200", "202610", "P", 1125.0),
        ("kospi200", "202610", "C", 1127.5),
        ("kospi200", "202610", "P", 1127.5),
        ("kospi200", "202610", "C", 745.0),
        ("mini", "202610", "C", 1125.0),
        ("weekly_thu", "2610W1", "C", 1125.0),
        ("weekly_thu", "2610W1", "P", 1125.0),
        ("weekly_mon", "2609W4", "C", 1125.0),
        ("weekly_mon", "2610W1", "C", 1125.0),
        ("kosdaq150", "202610", "C", 1275.0),
        ("kosdaq150", "202610", "P", 950.0),
        ("kosdaq_weekly_thu", "2610W1", "C", 1425.0),
        ("kosdaq_weekly_mon", "2609W4", "P", 1300.0),
        ("weekly_thu", "2610W1", "C", 992.5),
    ):
        out(f"  {fam} {tok} {cp} {k}: {opt_code(fam, tok, cp, k)}")
    out("  마스터 ATM 구분 기준", PREV_LEVEL, "→ ATM", round(PREV_LEVEL / 2.5) * 2.5)

    out("\n# KRX (test_krx_models·test_store_sql·test_metric_report)")
    od = krx_opt_daily()
    day = od[KRX_DAY]
    by_name = {}
    for r in day:
        by_name.setdefault(r["ISU_NM"], []).append(r)
    for nm in (
        "코스피200 C 202610 1,100.0 (정규)",
        "코스피200 C 202610 1,100.0 (야간)",
        "코스피200 P 202610 1,100.0 (정규)",
        "코스피200 C 202610   745.0 (정규)",
        "코스피200 P 202812 1,000.0 (정규)",
        "코스피위클리M P 2609W4   970.0 (야간)",
        "코스닥위클리 C 2609W4 1,175",
    ):
        for r in by_name[nm]:
            out(
                f"  {nm}:",
                {
                    k: r[k]
                    for k in (
                        "ISU_CD",
                        "TDD_CLSPRC",
                        "IMP_VOLT",
                        "NXTDD_BAS_PRC",
                        "ACC_TRDVOL",
                        "ACC_TRDVAL",
                        "ACC_OPNINT_QTY",
                    )
                },
            )
    for r in od["20100104"]:
        out("  20100104", r["ISU_NM"], r["IMP_VOLT"], r["ACC_TRDVOL"])
    fut = krx_fut_daily()["rows"]
    out(
        "  fut_daily:",
        [
            (
                r["MKT_NM"],
                r["ISU_CD"],
                r["SETL_PRC"],
                r["SPOT_PRC"],
                r["ACC_TRDVOL"],
                r["ACC_TRDVAL"],
                r["ACC_OPNINT_QTY"],
            )
            for r in fut
        ],
    )
    # 손계산 — PCR·맥스페인·ATM IV (metric_report 교차 확인과 같은 식)
    legs = {}
    for r in day:
        nm = r["ISU_NM"]
        if r["PROD_NM"] != "코스피200 옵션" or not nm.endswith("(정규)"):
            continue
        parts = nm.split()
        legs.setdefault(parts[2], {})[(float(parts[3].replace(",", "")), parts[1])] = (
            int(r["ACC_OPNINT_QTY"]),
            int(r["ACC_TRDVOL"]),
        )
    out("  legs:", legs)
    l610 = legs["202610"]
    po_ = sum(v[0] for (k, cp), v in l610.items() if cp == "P")
    co_ = sum(v[0] for (k, cp), v in l610.items() if cp == "C")
    pv_ = sum(v[1] for (k, cp), v in l610.items() if cp == "P")
    cv_ = sum(v[1] for (k, cp), v in l610.items() if cp == "C")
    out(
        f"  PCR 202610: | 20260923 | 202610 | {po_:,}/{co_:,} | {po_ / co_:.4f} | · 거래량 "
        f"{pv_}/{cv_} = {pv_ / cv_:.4f}"
    )
    cands = sorted({k for k, _ in l610})
    pains = {
        x: sum(
            oi * max(x - k, 0) if cp == "C" else oi * max(k - x, 0)
            for (k, cp), (oi, _) in l610.items()
        )
        for x in cands
    }
    mp = min(cands, key=lambda x: (pains[x], x))
    out(f"  맥스페인 202610: 후보 {len(cands)} → {mp:.2f}")
    p812 = legs["202812"]
    out(f"  202812: {sum(v[0] for (k, cp), v in p812.items() if cp == 'P'):,}/0")
    c1 = next(r for r in by_name["코스피200 C 202610 1,100.0 (정규)"])
    p1 = next(r for r in by_name["코스피200 P 202610 1,100.0 (정규)"])
    f_h = 1100 + float(c1["TDD_CLSPRC"]) - float(p1["TDD_CLSPRC"])
    iv_h = (float(c1["IMP_VOLT"]) + float(p1["IMP_VOLT"])) / 2
    out(
        f"  ATM: | {f_h:.2f} | {f_h:.2f} · estimated | {iv_h:.2f}% | {iv_h:.2f}% · estimated "
        f"(one_side) |  (F = 1100 + {c1['TDD_CLSPRC']} − {p1['TDD_CLSPRC']}, "
        f"IV = ({c1['IMP_VOLT']} "
        f"+ {p1['IMP_VOLT']}) / 2)"
    )
    pairs = investor()["pairs"]
    sc = {k: int(v["output"][0]["scrt_ntby_qty"]) for k, v in pairs.items()}
    calls = [sc["K2I/OC01"], sc["WKM/OC05"], sc["WKI/OC04"]]
    puts = [sc["K2I/OP01"], sc["WKM/OP05"], sc["WKI/OP04"]]
    out(
        f"  딜러(증권) 콜 {calls} = {sum(calls):+,} · 풋 {puts} = {sum(puts):+,} → 손 판정 "
        f"{'일치' if sum(calls) > 0 and sum(puts) < 0 else '불일치'}"
    )

    out("\n# validate_greeks (14:27 작은 스냅샷)")
    hist_vals = [
        _num(r["hist_vltl"])
        for b in A["boards"].values()
        for r in (*b["output1"], *b["output2"])
        if _num(r["hist_vltl"]) > 0
    ] + [_num(f["row"]["hist_vltl"]) for f in A["fills"] if _num(f["row"]["hist_vltl"]) > 0]
    hist_ref = statistics.median(hist_vals)
    out("snapshot_hist(중앙값):", hist_ref)
    out("전광판 현물 S(내재가치 — 매개변수):", dict(m.spot))
    q610 = _quotes(A, M610, "202610")
    F, used, skipped = _forward(q610, s_ref)
    assert F is not None  # noqa: S101 — 독립 계산의 내부 불변식
    out(f"합성 F 202610 = {F:.4f} · 쓴 {used} · 뺀 {skipped} · F − S_ref = {F - s_ref:+.2f}")
    f1100 = next(
        f["row"] for f in A["fills"] if f["series"] == "C 202610" and f["row"]["acpr"] == "1100.00"
    )
    out(
        "보강 1100 C:",
        {
            k: f1100[k]
            for k in (
                "hts_ints_vltl",
                "gama",
                "delta_val",
                "hist_vltl",
                "hts_rmnn_dynu",
                "acml_vol",
            )
        },
    )
    now610 = kst(SNAP_A.boards[M610])
    T610 = t_market(now610, "202610")
    out(f"T(분) 202610 = {T610 * MINUTES_PER_YEAR:.0f}")
    # 재현 검사 202610 — S = KIS 이론가 패리티 선도, T = 10/365, σ = 행 hist
    S_par, n_par = _parity_forward((q.K, q.cp, _num(q.row["hts_thpr"])) for q in q610)
    tk = t_kis("202610")
    rows610 = _kis_rows(q610, hist_ref)
    ks = sorted({q.K for q in q610})
    ia = min(range(len(ks)), key=lambda j: (abs(ks[j] - s_ref), ks[j]))
    win = set(ks[max(0, ia - 5) : ia + 6])
    atm_rows = [r for r in rows610 if r[0] in win]
    out(f"패리티 선도 202610 = {S_par:.4f} (행사가 {n_par}) · 재현 T = {tk * 365:.0f}/365")
    out(
        "  ATM±5 평이(Δ통과, n, Γ통과, n):",
        _counts(atm_rows, S_par, tk),
        "전체:",
        _counts(rows610, S_par, tk),
        "· 반올림 보정 ATM:",
        _counts(atm_rows, S_par, tk, True),
        "전체:",
        _counts(rows610, S_par, tk, True),
    )
    g_err = [abs(_plain(bgamma(S_par, K, tk, s), kg)) for K, _, s, _, kg, _ in atm_rows if kg]
    out(
        f"  ATM±5 감마 |평이 오차| 최대 = {max(g_err):.5f} · 원인 반올림(전체) = "
        f"{_causes_rounding(rows610, S_par, tk)} · ATM = {_causes_rounding(atm_rows, S_par, tk)}"
    )
    out(
        "  후보 T=달력일+1(11/365) ATM:",
        _counts(atm_rows, S_par, 11 / 365),
        "전체:",
        _counts(rows610, S_par, 11 / 365),
        "· 후보 S=합성 F 전체:",
        _counts(rows610, F, tk),
    )
    # 자체 감마 vs KIS — ATM±5, 자체 = Black(F, 달력 분 T, 자체 역산 σ)
    rel_g = []
    for q in q610:
        if q.K not in win:
            continue
        sig = implied_vol(q.cp, q.price, F, q.K, T610)
        kg = _num(q.row["gama"])
        if sig is not None and kg > 0:
            rel_g.append(_plain(bgamma(F, q.K, T610, sig), kg))
    out(
        f"  자체/KIS 감마 − 1 (ATM±5): n {len(rel_g)} · 편향 {statistics.median(rel_g) * 100:+.1f}%"
    )
    # ATM IV(§3.7)·±1σ(§3.8) — F 를 끼는 두 행사가의 콜·풋 자체 IV 평균을 F 로 선형보간
    ivk: dict[float, list[float]] = {}
    for q in q610:
        sig = implied_vol(q.cp, q.price, F, q.K, T610) if q.price else None
        if sig is not None:
            ivk.setdefault(q.K, []).append(sig)
    k1 = max(k for k in ivk if k <= F)
    k2 = min(k for k in ivk if k >= F)
    v1, v2 = sum(ivk[k1]) / len(ivk[k1]), sum(ivk[k2]) / len(ivk[k2])
    atm = v1 + (F - k1) / (k2 - k1) * (v2 - v1)
    sess = (datetime(2026, 9, 28, 15, 45, tzinfo=KST) - now610).total_seconds() / 60
    cal_m = F * atm * math.sqrt(sess / MINUTES_PER_YEAR)
    trd_m = F * atm * math.sqrt(sess / (252 * 420))
    out(
        f"  ATM IV = {atm * 100:.2f}% · 남은 세션 {sess:.0f}분 · ±1σ 달력 {cal_m:.2f} · 거래 "
        f"{trd_m:.2f} · 비 {trd_m / cal_m:.2f}"
    )
    # 202611 — 패리티 선도(3 행사가), T = 45/365
    q611 = _quotes(A, M611, None)
    S611, n611 = _parity_forward((q.K, q.cp, _num(q.row["hts_thpr"])) for q in q611)
    t611 = t_kis("202611")
    rows611 = _kis_rows(q611, hist_ref)
    out(
        f"202611: 패리티 선도 {S611:.4f} (S − S_ref {S611 - s_ref:+.2f}, 행사가 {n611}) · 평이",
        _counts(rows611, S611, t611),
        "· 반올림",
        _counts(rows611, S611, t611, True),
        "· 원인 반올림",
        _causes_rounding(rows611, S611, t611),
        "· S=선물",
        _counts(rows611, s_ref, t611),
        "· T=달력일+1",
        _counts(rows611, S611, 46 / 365),
    )
    # 0DTE — T 훑기(σ = 행 KIS IV, S = S_ref), KIS Δ·Γ 가 함의하는 S
    q0 = _quotes(A, W0, None)
    rows0 = _kis_rows(q0, hist_ref)
    sweep = {}
    for i in range(2, 21):
        d = round(0.05 * i, 2)
        errs = [abs(_plain(bgamma(s_ref, K, d / 365, s), kg)) for K, _, s, _, kg, _ in rows0 if kg]
        sweep[d] = statistics.median(errs)
    best = min(sweep, key=lambda d: sweep[d])
    implied = [
        x[0] for K, cp, _, kd, kg, _ in rows0 if kd and kg and (x := _solve_dg(K, cp, kd, kg))
    ]
    out(
        f"0DTE: 행 {len(rows0)} · T 훑기 중앙 0.5일 {sweep[0.5]:.4f} · 최소 {best}일 · 0.1일 "
        f"{sweep[0.1]:.3f} · 1.0일 {sweep[1.0]:.3f} · 함의 S 중앙 {statistics.median(implied):.2f}"
    )
    F0, used0, _ = _forward(q0, s_ref)
    assert F0 is not None  # noqa: S101 — 독립 계산의 내부 불변식
    fbr = next(q.row for q in q0 if (q.K, q.cp) == (1102.5, "P"))
    iv_k, g_k, d_k = _num(fbr["hts_ints_vltl"]), _num(fbr["gama"]), _num(fbr["delta_val"])
    tk0 = T_KIS_0DTE
    g_ours = bgamma(F0, 1102.5, tk0, iv_k / 100)  # 총분산 보존 — σ_KIS·√(T_KIS/T) 를 T 로
    dg = _solve_dg(1102.5, "P", d_k, g_k)
    assert dg is not None  # noqa: S101 — 독립 계산의 내부 불변식
    out(
        f"  0DTE 합성 F = {F0:.4f} (행사가 {used0}) · 폴백 1102.5 P: KIS IV {iv_k} · Γ {g_k} · Δ "
        f"{d_k} · OI {fbr['hts_otst_stpl_qty']} · last {fbr['optn_prpr']}"
    )
    out(
        f"  자체(T 맞춤) Γ / KIS Γ = {g_ours / g_k:.4f} · KIS Δ·Γ 의 σ = x*/√T_KIS = "
        f"{dg[1] / math.sqrt(tk0):.4f} (시나리오 {FALLBACK_SIGMA_RATIO} × KIS IV = "
        f"{FALLBACK_SIGMA_RATIO * iv_k / 100:.4f})"
    )
    # 위클리 무거래
    q1 = _quotes(A, W1, None)
    for q in q1:
        r = q.row
        if int(r["acml_vol"]) == 0:
            u = implied_underlying(
                q.cp, float(r["optn_prpr"]), q.K, t_kis("261001"), _num(r["hts_ints_vltl"]) / 100
            )
            dgw = _solve_dg(q.K, q.cp, _num(r["delta_val"]), _num(r["gama"]))
            out(
                f"  WKM 261001 무거래 {q.K} {q.cp}: last·IV 의 S {u:.2f} · Δ·Γ 의 S "
                f"{'—' if dgw is None else f'{dgw[0]:.2f}'} σ "
                f"{'—' if dgw is None else f'{dgw[1] / math.sqrt(t_kis("261001")):.4f}'}"
            )
    out(f"  전 세션 수준 {PREV_LEVEL} · 기초 HV {m.hv}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

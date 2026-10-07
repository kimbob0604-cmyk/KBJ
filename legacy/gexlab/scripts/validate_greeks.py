"""PLAN §6.2 KIS 관례 재현 검사 + 자체 vs KIS 기록·KIS 관례 역산(docs/validation_greeks.md).

실행(로컬 녹화만 읽는다 — 네트워크·.env 를 쓰지 않는다):

    uv run python -m scripts.validate_greeks probe_out/runs/<런>/chain_snapshot.json [...] \
        --detail [--expiry WKI:261008=20261008 ...]

마크다운 표를 stdout 에 쓴다. 시리즈 하나(만기일 모름·빈 전광판 등)나 스냅샷 하나가 실패해도
나머지는 계속 보고, 실패는 사유와 함께 '건너뜀' 줄로 남는다(CLAUDE.md 예외 격리).

§6.2 재현 검사(2026-09-28 사용자 결정, metrics §1.7): KIS 가 쓰는 입력 — σ(월물 `hist_vltl`,
위클리 당일 거래 행 KIS IV·무거래 행 기초 HV), S(KIS 이론가 패리티 선도, 안 서면 선물 근월물),
T(`max(달력일, 0.5)/365`) — 을 자체 `core.greeks`(Black-76, r = 0)에 넣어 KIS `delta_val`·`gama`
를 다시 낸다. 목적은 자체 공식 구현의 검증이다. 판정은 결정대로 상대오차 `|자체/KIS − 1|`
≤ 1% [확인 필요]. KIS 값이 소수 4자리라, 반올림 구간(±0.00005) 밖 상대오차(반올림 보정 —
[확인 필요] 제안)도 나란히 센다. 허용 밖 종목은 반올림 보정으로 통과하면 원인 `반올림`, 아니면
KIS Δ·Γ 한 쌍이 함의하는 (S*, σ*)로 입력을 하나씩 바꿔 원인(S·σ√T)을 가린다. KIS 그릭스는 GEX 에
쓰지 않는다 — 이 검사에서만 읽는다.

기록(옛 §6.2, 판정 아님): 실제 코어 파이프라인 — 종목 → `core.gex.OptionQuote` →
`core.gex.evaluate_expiry`(§1.1 가격·§1.3 합성 F·§1.4 T·§1.5 IV·§1.6 그릭스), 기준가 S_ref 는
스냅샷의 선물 근월물 현재가(`atm_ref.price`) — 의 자체 IV·감마를 ATM±5 에서 KIS 값과 나란히 둔다.
월물은 전광판 행과 단건 보강(`fills`) 행을 합쳐 ATM 구간을 채운다(#11a — 같은 종목이면 호가가 있는
전광판 행을 쓴다).
- 감마 상대오차 `자체 / KIS gama − 1`(옛 허용 5%). GEX 에 든 종목(KIS IV 폴백 포함)
- IV 차 `자체 − KIS hts_ints_vltl` (%p, 옛 허용 1.0%p). 자체 역산(`source=model`)만 — KIS 폴백
  (`estimated`)은 KIS 값 그대로라 비교가 아니다
KIS 는 값 없음을 0 으로 준다 — KIS IV·감마 0 은 비교에서 뺀다.

관례 역산(진단): KIS 값을 어떤 σ·기초자산 S·T 로 매겼는지 다시 계산해 잔차를 잰다.
- 감마·델타 후보: σ(KIS IV·KIS hist_vltl·자체 IV) × S × T 기준 — `core.greeks`(Black-76, r=0)
- IV 후보: 가격(last·mid) × S × T 기준 — `core.iv.implied_vol`(KIS 폴백 없이)
- IV 가 함의하는 T: r=0 Black 가격은 σ√T 로만 정해진다 — 가격·S·K 로 푼 총변동 w 와 KIS IV 로
  `T = (w / σ_KIS)²`
- IV 가 함의하는 S: KIS IV·last·T(KIS 관례)로 가격이 맞는 S — 종목별로 쓴 S 가 드러난다
- 델타 곡면: KIS 델타가 종목 공통 σ·T 의 Black 델타면 `ln K = ln S + x²/2 − x·z`
  (z = N⁻¹(콜 Δ, 풋 Δ + 1), x = σ√T)가 직선이다 — 회귀로 S·x, 잔차로 공통 σ 가정을 본다
- 델타·감마 한 쌍: 종목마다 KIS 델타·감마를 동시에 맞추는 (S, x) — 종목별로 쓴 S 가 드러난다
- 이론가 패리티: `thpr_C − thpr_P = A − D·K` 회귀 → 선도 A/D, 할인 D
- 내재가치: 전광판 `invl_val` 이 함의하는 현물 S(`board_spot`)

`--summary`(검증 수정 4 전후 비교): 위 보고서 대신 파이프라인 요약만 쓴다 — 시리즈마다 합성 F·
F − S_ref·§1.3 선물 기준가와 F − 기준가·ATM IV(§3.7)·자체 vs KIS IV·감마(ATM±5)·§1.5 KIS IV 폴백과
T 환산 행 수·순GEX(§2.2)·Flip(§3.4), 범위 all·0dte 순GEX·Flip, §6.2 재현 통과 수, §3.8 ±1σ(달력·
거래시간 기준). 스냅샷은 시각 순으로 돌고 만기별 확정 베이시스(`core.forward.confirm_basis`)를 다음
스냅샷에 넘긴다(운영 engine 몫을 여기서 흉내 낸다).
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from core import black76
from core.black76 import Flag
from core.calendar import KST, TradingCalendar, expiry_at, monthly_expiry, state_at
from core.chain import atm_window, covers
from core.forward import MINUTES_PER_YEAR, confirm_basis, kis_time_to_expiry, time_to_expiry
from core.gex import (
    CallPut,
    ExpiryEval,
    Exposure,
    OptionQuote,
    QuoteSource,
    evaluate_expiry,
    net_gex,
    option_gex,
    to_eok,
)
from core.greeks import greeks
from core.iv import IvResult, implied_vol, rescale_sigma, usable
from core.levels import (
    AtmIv,
    DeltaBasis,
    ExpectedMove,
    GammaFlip,
    atm_iv,
    expected_move,
    gamma_flip,
)
from core.preprocess import PriceKind

REPRO_REL_TOL = (
    0.01  # PLAN §6.2 KIS 관례 재현 |자체/KIS − 1| 1% [확인 필요] — 2026-09-28 사용자 결정
)
GAMMA_REL_TOL = 0.05  # 옛 §6.2 감마 상대오차 5% — 기록용(판정 아님)
IV_DIFF_TOL = 1.0  # 옛 §6.2 IV 1.0%p — 기록용(판정 아님)
WINDOW = 5  # ATM±5
REPRO_HEADER = "§6.2 KIS 관례 재현 검사 — KIS delta_val·gama 를 core.greeks 로 다시 낸다"
RECORD_HEADER = "기록: 자체 vs KIS (옛 §6.2 — 판정 아님) — 감마 %(자체/KIS − 1), IV %p(자체 − KIS)"
DELTA_FIT_RANGE = (0.05, 0.95)  # 델타 곡면 회귀의 N(d1) 범위 — 4자리 반올림이 z 를 흔드는 끝은 뺀다
DELTA_GAMMA_RANGE = (0.02, 0.98)  # 델타·감마 한 쌍 풀이의 N(d1) 범위
FIT_RESID_MAX = 1.0  # pt — 델타 곡면·패리티 잔차가 이보다 작을 때만 S 후보로 쓴다
PARITY_MIN_STRIKES = 3  # 재현 검사 S(이론가 패리티 선도)에 쓸 최소 행사가 수
BOOT_N = 500  # 델타 곡면 T 붓스트랩 반복
BOOT_SEED = 0  # 시드 고정 — 같은 스냅샷이면 같은 구간
DELTA_DAYS_TOL = 0.02  # 일 — 델타 곡면 T 와 KIS 관례(달력일) 차 허용, 재현 검사 권고안 [확인 필요]
STALE_S_GAP = 10.0  # pt — 델타·감마가 함의하는 S 가 S_ref 에서 이만큼 멀면 '다른 S' 로 센다
KIS_GAMMA_DP = 4  # KIS gama·delta_val 소수 자릿수(실측 문자열 "0.0027")
KIS_HALF_ULP = 0.5 * 10**-KIS_GAMMA_DP  # KIS 4자리 표시의 반올림 반폭
DAYS_PER_YEAR = 365
TRADING_DAYS_PER_YEAR = 252
DAY_FLOOR = 0.5  # days_min_half 기준의 만기일 날수 — 0DTE KIS IV 역산값(약 0.5일) 확인용
# 만기일(D = 0) 시리즈의 T 훑기(일) — 0.10~1.00, 0.05 간격. KIS 만기일 0.5일 관례를 가른다
T_SWEEP_DAYS: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(2, 21))

# 만기일 — KIS 단건 `futs_last_tr_date` 실측(probe_results #19 14:42 런). 라벨(YYMMWW·YYYYMM)은 한
# 시리즈를 가리키므로 다른 날 스냅샷에도 맞다. 순서: 스냅샷 fills → `--expiry` → 이 표 → 월물 계산.
# 위클리는 계산하지 않는다(코드 WW 의 뜻이 실측되지 않았다) — 모르면 그 시리즈만 건너뛴다
KIS_EXPIRY: Mapping[str, date] = {
    "MONTH:202610": date(2026, 10, 8),
    "WKM:260904": date(2026, 9, 28),
    "WKI:261001": date(2026, 10, 1),
    "WKM:261001": date(2026, 10, 6),
}

TBasis = Literal["minutes", "days", "days_incl", "days_min_half", "trading_incl"]
T_BASES: tuple[TBasis, ...] = get_args(TBasis)
SigmaSource = Literal["kis_iv", "hist", "ours"]
SIGMA_SOURCES: tuple[SigmaSource, ...] = get_args(SigmaSource)
PriceSource = Literal["last", "mid"]
PRICE_SOURCES: tuple[PriceSource, ...] = get_args(PriceSource)
SigmaConv = Literal["hist", "kis_iv", "hist_ref"]  # KIS 그릭스 σ 관례 출처(kis_sigma)
UnderlyingConv = Literal["parity", "fut"]  # KIS 그릭스 S 관례 출처(kis_underlying)
ReproCause = Literal["반올림", "S", "σ√T", "S+σ√T", "풀이 없음"]  # 재현 실패 원인(reproduce_row)
REPRO_CAUSES: tuple[ReproCause, ...] = get_args(ReproCause)
# 재현 오차: plain = |자체/KIS − 1|(PLAN §6.2 결정), rounding = KIS 4자리 반올림 구간 밖
# 상대오차([확인 필요] 제안 — 나란히 기록)
ErrMetric = Literal["plain", "rounding"]
ExpirySource = Literal["kis", "arg", "computed"]  # arg: --expiry
EXPIRY_TAG: Mapping[ExpirySource, str] = {"kis": "", "arg": " (인자)", "computed": " (계산)"}


# ── 입력 (녹화 스냅샷, pydantic 검증) ─────────────────────────────────────────


def _num(v: object) -> object:
    """KIS 숫자 문자열 → float. 빈 값은 None."""
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        return float(s) if s else None
    return v


def _int_or_none(v: object) -> object:
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        return int(s) if s else None
    return v


KisNum = Annotated[float | None, BeforeValidator(_num)]
KisInt = Annotated[int | None, BeforeValidator(_int_or_none)]


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    acpr: str
    hts_otst_stpl_qty: int = Field(ge=0)
    acml_vol: KisInt = None
    hts_ints_vltl: KisNum = None
    gama: KisNum = None
    delta_val: KisNum = None
    hts_thpr: KisNum = None
    hist_vltl: KisNum = None


class BoardRow(_Row):
    """전광판 콜(`output1`)·풋(`output2`) 한 행. invl_val: KIS 내재가치(pt) — 현물 S 가 드러난다."""

    optn_bidp: str = ""
    optn_askp: str = ""
    optn_prpr: str = ""
    invl_val: KisNum = None


class FillRow(_Row):
    """옵션 단건 현재가 한 행 — 호가가 없다(#11a)."""

    futs_prpr: str = ""
    futs_last_tr_date: str = Field(default="", pattern=r"^([0-9]{8})?$")
    hts_rmnn_dynu: KisInt = None


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    ts_kst: AwareDatetime
    # 마스터 종목명에서 행사가를 뗀 것 — 월물 "C 202610". 위클리는 마스터 이름("2609W4" 꼴)이라
    # 전광판 코드(YYMMWW)와 안 맞을 수 있어 형식을 묶지 않는다 — 안 맞는 행은 `unmatched_fills`
    series: str = Field(pattern=r"^[CP] \S+$")
    rt_cd: str = ""
    row: FillRow | None = None


class Board(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    ts_kst: AwareDatetime
    output1: list[BoardRow] = Field(default_factory=list[BoardRow])
    output2: list[BoardRow] = Field(default_factory=list[BoardRow])


class AtmRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str
    price: float = Field(gt=0)


class Snapshot(BaseModel):
    """`chain_snapshot.json` 에서 이 검증이 쓰는 부분(나머지 필드는 무시)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    started_kst: AwareDatetime
    atm_ref: AtmRef
    boards: dict[str, Board]
    fills: list[Fill] = Field(default_factory=list[Fill])


def load_snapshot(path: Path) -> Snapshot:
    return Snapshot.model_validate_json(path.read_bytes())


# ── 시리즈 → OptionQuote ─────────────────────────────────────────────────────


def _positive(v: float | None) -> float | None:
    """KIS 는 없음을 0 으로 준다 — 0 이하는 None."""
    return v if v is not None and v > 0 else None


@dataclass(frozen=True, slots=True)
class KisRow:
    """KIS 가 같은 종목에 준 값. iv·hist 는 %, gamma 1/pt, volume 은 당일 누적 거래량.

    0 은 없음(None)으로 바꿔 둔다 — 델타·거래량은 0 이 값이라 그대로 둔다.
    """

    iv_pct: float | None
    gamma: float | None
    delta: float | None
    hist_pct: float | None
    thpr: float | None
    volume: int | None = None
    rmnn: int | None = None

    @classmethod
    def of(cls, r: _Row, rmnn: int | None = None) -> KisRow:
        return cls(
            iv_pct=_positive(r.hts_ints_vltl),
            gamma=_positive(r.gama),
            delta=r.delta_val,
            hist_pct=_positive(r.hist_vltl),
            thpr=_positive(r.hts_thpr),
            volume=r.acml_vol,
            rmnn=rmnn,
        )


Key = tuple[Decimal, CallPut]


@dataclass(frozen=True, slots=True)
class Series:
    """만기 하나의 입력. now 는 전광판 조회 시각, kis 는 (행사가, 콜/풋) → KIS 값.

    spot: 전광판 KIS 내재가치(`invl_val`)가 함의하는 기초자산(콜 K + 내재가치, 풋 K − 내재가치)의
    중앙값 — 전광판 조회 시점의 현물 수준. 내재가치가 있는 행이 없으면 None.
    """

    label: str
    expiry: str
    expiry_date: date
    expiry_source: ExpirySource
    now: datetime
    quotes: tuple[OptionQuote, ...]
    kis: Mapping[Key, KisRow]
    spot: float | None = None


def _code(label: str) -> tuple[str, str]:
    kind, _, code = label.partition(":")
    if not kind or len(code) != 6 or not code.isdigit():
        raise ValueError(f"전광판 라벨은 '<구분>:<6자리 만기 코드>': {label!r}")
    return kind, code


def _ymd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _series_code(series: str) -> str:
    return series.partition(" ")[2]


def _known_expiry(label: str, given: Mapping[str, date] | None) -> tuple[date, ExpirySource] | None:
    """스냅샷 밖에서 아는 만기일: `--expiry`(given) → `KIS_EXPIRY`."""
    if given and label in given:
        return given[label], "arg"
    if label in KIS_EXPIRY:
        return KIS_EXPIRY[label], "kis"
    return None


def _fills_of(
    snap: Snapshot, label: str, known: date | None = None
) -> list[tuple[CallPut, FillRow]]:
    """이 라벨의 단건 보강 행.

    보강 행 `series` 에는 만기 코드만 있어('C 261001') WKI·WKM 261001 처럼 코드가 같은 전광판이 둘
    이상이면 코드로는 못 가른다 — 그때는 `futs_last_tr_date` 가 이 라벨의 아는 만기일(known)과 같은
    행만 쓴다. known 이 없으면 ValueError — 말없이 두 시리즈에 섞지 않는다.
    """
    _, code = _code(label)
    out: list[tuple[CallPut, FillRow]] = []
    for f in snap.fills:
        cp, _, _ = f.series.partition(" ")
        if _series_code(f.series) == code and f.row is not None and f.rt_cd in ("", "0"):
            out.append(("C" if cp == "C" else "P", f.row))
    sharing = sorted(b for b in snap.boards if b.partition(":")[2] == code)
    if not out or len(sharing) < 2:
        return out
    if known is None:
        raise ValueError(
            f"{label}: 보강 행 {code} 를 {sharing} 중 어디에 붙일지 모른다 — "
            f"--expiry {label}=YYYYMMDD 를 주면 futs_last_tr_date 로 가른다"
        )
    ymd = f"{known:%Y%m%d}"
    return [(cp, r) for cp, r in out if r.futs_last_tr_date == ymd]


def unmatched_fills(snap: Snapshot) -> dict[str, int]:
    """전광판 만기 코드 어디에도 안 붙는 보강 행 수(series 별) — 보고서에 남긴다."""
    codes = {b.partition(":")[2] for b in snap.boards}
    out: dict[str, int] = {}
    for f in snap.fills:
        if _series_code(f.series) not in codes:
            out[f.series] = out.get(f.series, 0) + 1
    return out


def resolve_expiry(
    label: str,
    fill_dates: Iterable[str],
    cal: TradingCalendar,
    given: Mapping[str, date] | None = None,
) -> tuple[date, ExpirySource]:
    """만기일: fills `futs_last_tr_date` → `--expiry`(given) → `KIS_EXPIRY` → 월물이면 계산.

    fills 끼리, 또는 fills 와 아는 만기일이 갈리면 ValueError. 위클리를 모르면 ValueError.
    """
    dates = {_ymd(d) for d in fill_dates if d}
    if len(dates) > 1:
        raise ValueError(f"{label}: futs_last_tr_date 가 여러 개: {sorted(dates)}")
    known = _known_expiry(label, given)
    if dates:
        d = dates.pop()
        if known is not None and known[0] != d:
            raise ValueError(
                f"{label}: futs_last_tr_date {d} 와 {known[1]} 만기일 {known[0]} 이 다르다"
            )
        return d, "kis"
    if known is not None:
        return known
    kind, code = _code(label)
    if kind == "MONTH":
        return monthly_expiry(int(code[:4]), int(code[4:]), cal), "computed"
    raise ValueError(f"{label}: 만기일을 모른다 — --expiry {label}=YYYYMMDD")


def series_rows(
    snap: Snapshot, label: str, cal: TradingCalendar, given: Mapping[str, date] | None = None
) -> Series:
    """전광판 한 시리즈(+ 같은 만기의 단건 보강 행)를 OptionQuote·KisRow 로. 겹치면 전광판 행.

    given: `--expiry` 로 받은 만기일(라벨 → 날짜).
    """
    _, code = _code(label)
    board = snap.boards[label]
    known = _known_expiry(label, given)
    fills = _fills_of(snap, label, None if known is None else known[0])
    expiry_date, source = resolve_expiry(label, (r.futs_last_tr_date for _, r in fills), cal, given)
    quotes: list[OptionQuote] = []
    kis: dict[Key, KisRow] = {}
    base = {"expiry": code, "expiry_date": expiry_date}
    sides: tuple[tuple[CallPut, list[BoardRow]], ...] = (
        ("C", board.output1),
        ("P", board.output2),
    )
    for cp, rows in sides:
        for r in rows:
            # KIS 문자열 그대로 넘긴다 — OptionQuote 가 빈 값·0 을 없음으로 검증한다
            q = OptionQuote.model_validate(
                base
                | {
                    "strike": r.acpr,
                    "cp": cp,
                    "bid": r.optn_bidp,
                    "ask": r.optn_askp,
                    "last": r.optn_prpr,
                    "oi": r.hts_otst_stpl_qty,
                    "kis_iv_pct": r.hts_ints_vltl,
                    "volume": r.acml_vol,
                    "source": "board",
                }
            )
            if (q.strike, q.cp) in kis:
                raise ValueError(f"{label}: 전광판에 같은 종목이 두 번: {q.strike} {q.cp}")
            quotes.append(q)
            kis[(q.strike, q.cp)] = KisRow.of(r)
    for cp, r in fills:
        q = OptionQuote.model_validate(
            base
            | {
                "strike": r.acpr,
                "cp": cp,
                "last": r.futs_prpr,
                "oi": r.hts_otst_stpl_qty,
                "kis_iv_pct": r.hts_ints_vltl,
                "volume": r.acml_vol,
                "source": "fill",
            }
        )
        if (q.strike, q.cp) in kis:
            continue  # 호가가 있는 전광판 행이 낫다
        quotes.append(q)
        kis[(q.strike, q.cp)] = KisRow.of(r, r.hts_rmnn_dynu)
    return Series(
        label, code, expiry_date, source, board.ts_kst, tuple(quotes), kis, board_spot(board)
    )


def board_spot(board: Board) -> float | None:
    """전광판 KIS 내재가치가 함의하는 기초자산 S(중앙값). 내재가치 > 0 인 행이 없으면 None.

    콜 `invl_val = S − K`, 풋 `K − S` 라 콜 `K + invl`, 풋 `K − invl`. 2026-09-28 두 스냅샷에서 한
    전광판 안의 값은 모두 같았다(행마다 0.01pt 안) — 조회 시점의 현물 지수로 보인다.
    """
    implied = [float(r.acpr) + r.invl_val for r in board.output1 if r.invl_val and r.invl_val > 0]
    implied += [float(r.acpr) - r.invl_val for r in board.output2 if r.invl_val and r.invl_val > 0]
    return statistics.median(implied) if implied else None


def evaluate(series: Series, s_ref: float, forward_basis: Decimal | None = None) -> ExpiryEval:
    """실제 파이프라인(§1.1~§1.6) 그대로. forward_basis: 이 만기의 확정 베이시스(§1.3 선물 교차
    확인 기준가 = s_ref + 베이시스 — `core.forward.confirm_basis`, 없으면 확인 건너뜀)."""
    return evaluate_expiry(series.quotes, series.now, s_ref, forward_basis=forward_basis)


# ── §6.2 비교 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompareRow:
    """ATM±n 한 종목. iv_ours_pct 는 자체 역산(model)일 때만, gamma_ours 는 GEX 에 든 종목만.
    iv_reason 은 파이프라인 IV 사유(`IvResult.reason` — ok 면 None)."""

    strike: Decimal
    cp: CallPut
    source: QuoteSource
    price_kind: PriceKind | None
    iv_quality: str | None
    iv_ours_pct: float | None
    iv_kis_pct: float | None
    gamma_ours: float | None
    gamma_kis: float | None
    volume: int | None = None
    iv_reason: str | None = None

    @property
    def iv_diff(self) -> float | None:
        """자체 − KIS (%p)."""
        if self.iv_ours_pct is None or self.iv_kis_pct is None:
            return None
        return self.iv_ours_pct - self.iv_kis_pct

    @property
    def gamma_rel(self) -> float | None:
        """자체 / KIS − 1."""
        if self.gamma_ours is None or self.gamma_kis is None:
            return None
        return self.gamma_ours / self.gamma_kis - 1

    @property
    def stale_last(self) -> bool:
        """당일 거래가 없는데 last 로 역산했다 — last 는 전 세션 값이다."""
        return self.price_kind == "last" and self.volume == 0


def window_strikes(ev: ExpiryEval, s_ref: float, n: int = WINDOW) -> list[Decimal] | None:
    """ATM±n 행사가. 받은 행사가가 참 ATM±n 을 못 덮으면(전광판 잘림 #11) None."""
    strikes = sorted({o.quote.strike for o in ev.options})
    if not covers(strikes, s_ref, n):
        return None
    return atm_window(strikes, s_ref, n)


def _own_iv_pct(iv: IvResult | None) -> float | None:
    """자체 역산 IV(%) — KIS 폴백·invalid 는 None."""
    if iv is not None and iv.source == "model" and iv.sigma is not None:
        return iv.sigma * 100
    return None


def compare(
    ev: ExpiryEval, kis: Mapping[Key, KisRow], s_ref: float, n: int = WINDOW
) -> tuple[CompareRow, ...] | None:
    """ATM±n 종목의 자체 vs KIS. 창을 못 덮으면 None."""
    win = window_strikes(ev, s_ref, n)
    if win is None:
        return None
    wanted = set(win)
    out: list[CompareRow] = []
    for o in ev.options:
        if o.quote.strike not in wanted:
            continue
        k = kis[(o.quote.strike, o.quote.cp)]
        out.append(
            CompareRow(
                strike=o.quote.strike,
                cp=o.quote.cp,
                source=o.quote.source,
                price_kind=o.choice.kind,
                iv_quality=None if o.iv is None else o.iv.quality,
                iv_ours_pct=_own_iv_pct(o.iv),
                iv_kis_pct=k.iv_pct,
                gamma_ours=None if o.greeks is None else o.greeks.gamma,
                gamma_kis=k.gamma,
                volume=k.volume,
                iv_reason=None if o.iv is None else o.iv.reason,
            )
        )
    return tuple(out)


def percentile(xs: Sequence[float], q: float) -> float:
    """선형 보간 백분위(numpy 기본 'linear' 와 같다). q 는 0~100."""
    if not xs:
        raise ValueError("빈 표본")
    if not 0 <= q <= 100:
        raise ValueError(f"q 는 0~100: {q!r}")
    s = sorted(xs)
    pos = (len(s) - 1) * q / 100
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


@dataclass(frozen=True, slots=True)
class ErrorStats:
    """오차 분포. median·p90·max 는 |오차|, bias 는 부호 있는 오차의 중앙값, within 은 ≤ tol."""

    n: int
    bias: float
    median: float
    p90: float
    max: float
    within: int

    @property
    def pass_rate(self) -> float:
        return self.within / self.n

    @property
    def exceed(self) -> int:
        return self.n - self.within


def error_stats(errors: Iterable[float], tol: float) -> ErrorStats | None:
    """표본이 없으면 None. nan 은 ValueError."""
    es = list(errors)
    if not es:
        return None
    if any(math.isnan(e) for e in es):
        raise ValueError("nan 오차")
    ab = [abs(e) for e in es]
    return ErrorStats(
        n=len(es),
        bias=statistics.median(es),
        median=statistics.median(ab),
        p90=percentile(ab, 90),
        max=max(ab),
        within=sum(1 for a in ab if a <= tol),
    )


# ── 관례 역산 ────────────────────────────────────────────────────────────────


def t_years(basis: TBasis, now: datetime, expiry_date: date, cal: TradingCalendar) -> float | None:
    """T 후보(년). 0 이하가 되는 기준이면 None.

    - minutes: 자체 §1.4 — 만기일 15:20 KST 까지 달력 분 / (365×24×60), 하한 5분
    - days: (만기일 − 오늘) 달력일 / 365
    - days_incl: (만기일 − 오늘 + 1) / 365 — KIS `hts_rmnn_dynu`(오늘·만기일 모두 셈)와 같은 날수
    - days_min_half: max(달력일, 0.5) / 365 — 만기일(0일)만 0.5일
    - trading_incl: 오늘~만기일 거래일 수(양끝 포함) / 252
    """
    today = now.astimezone(KST).date()
    days = (expiry_date - today).days
    if basis == "minutes":
        return time_to_expiry(now, expiry_at(expiry_date))
    if basis == "days":
        return days / DAYS_PER_YEAR if days > 0 else None
    if basis == "days_incl":
        return (days + 1) / DAYS_PER_YEAR if days >= 0 else None
    if basis == "days_min_half":
        return max(days, DAY_FLOOR) / DAYS_PER_YEAR if days >= 0 else None
    if basis == "trading_incl":
        n = sum(1 for i in range(days + 1) if cal.is_trading_day(today + timedelta(days=i)))
        return n / TRADING_DAYS_PER_YEAR if n else None
    raise ValueError(f"T 기준: {basis!r}")


def t_candidates(
    now: datetime, expiry_date: date, cal: TradingCalendar
) -> dict[TBasis, float | None]:
    """모든 T 기준의 값(년)."""
    return {b: t_years(b, now, expiry_date, cal) for b in T_BASES}


def _flag(cp: CallPut) -> Flag:
    return "c" if cp == "C" else "p"


def _call_delta(cp: CallPut, delta: float) -> float:
    """풋 Δ + 1 = 콜 Δ = N(d1) (r = 0 Black)."""
    return delta if cp == "C" else delta + 1


@dataclass(frozen=True, slots=True)
class DeltaFit:
    """KIS 델타를 종목 공통 σ·T 의 Black 델타로 본 회귀.

    S: 델타가 함의하는 기초자산(할인이 있으면 선도 쪽), x: σ√T. resid_pt: 행사가 잔차 RMS(pt) —
    KIS 가 종목마다 다른 σ·S 를 쓰면 커진다.
    """

    S: float
    x: float
    n: int
    resid_pt: float

    def implied_days(self, sigma: float) -> float:
        """σ(소수)로 매겼다면 T 가 며칠(365일 기준)인가."""
        return (self.x / sigma) ** 2 * DAYS_PER_YEAR


def fit_delta_surface(
    rows: Iterable[tuple[float, CallPut, float]],
    lo: float = DELTA_FIT_RANGE[0],
    hi: float = DELTA_FIT_RANGE[1],
) -> DeltaFit | None:
    """(행사가, 콜/풋, KIS 델타) → S·x. N(d1) 이 [lo, hi] 인 행만. 점 부족·x ≤ 0 이면 None."""
    nd = statistics.NormalDist()
    pts: list[tuple[float, float]] = []
    for k, cp, delta in rows:
        p = _call_delta(cp, delta)
        if lo <= p <= hi and k > 0:
            pts.append((nd.inv_cdf(p), math.log(k)))
    if len({z for z, _ in pts}) < 2:
        return None
    fit = statistics.linear_regression([z for z, _ in pts], [y for _, y in pts])
    x = -fit.slope
    if x <= 0:
        return None
    S = math.exp(fit.intercept - x * x / 2)
    resid = [math.exp(y) - math.exp(fit.intercept + fit.slope * z) for z, y in pts]
    rms = math.sqrt(sum(r * r for r in resid) / len(resid))
    return DeltaFit(S=S, x=x, n=len(pts), resid_pt=rms)


def bootstrap_delta_days(
    rows: Iterable[tuple[float, CallPut, float]],
    sigma: float,
    n_boot: int = BOOT_N,
    seed: int = BOOT_SEED,
    level: float = 0.95,
) -> tuple[float, float] | None:
    """델타 곡면 T(일, σ 로 환산)의 붓스트랩 백분위 구간. 행사가 3개 미만·적합 반 넘게 실패면 None.

    곡면 회귀에 드는 행(N(d1) 이 DELTA_FIT_RANGE 안)만 복원추출해 다시 적합한다 — KIS 델타 4자리
    반올림과 행별 잡음이 T 추정에 주는 폭. 시드가 고정이라 같은 입력이면 같은 구간이다. 같은
    행사가의 콜·풋은 z 가 같아, 행사가 2개면 회귀가 늘 딱 맞아 폭 0 인 가짜 구간이 된다 — 3개부터.
    """
    if n_boot < 1 or not 0 < level < 1 or sigma <= 0:
        raise ValueError(f"n_boot ≥ 1, 0 < level < 1, σ > 0: {n_boot!r} {level!r} {sigma!r}")
    lo, hi = DELTA_FIT_RANGE
    pts: list[tuple[float, CallPut, float]] = [
        (k, cp, d) for k, cp, d in rows if k > 0 and lo <= _call_delta(cp, d) <= hi
    ]
    if len({k for k, _, _ in pts}) < 3:
        return None
    rng = random.Random(seed)  # noqa: S311 — 통계 재표본, 암호 용도 아님
    days: list[float] = []
    for _ in range(n_boot):
        fit = fit_delta_surface(rng.choices(pts, k=len(pts)))
        if fit is not None:
            days.append(fit.implied_days(sigma))
    if len(days) * 2 < n_boot:
        return None
    a = (1 - level) / 2 * 100
    return percentile(days, a), percentile(days, 100 - a)


def t_bases_within(
    lo: float, hi: float, t_values: Mapping[TBasis, float | None]
) -> tuple[TBasis, ...]:
    """T 후보 중 [lo, hi](일) 안에 드는 기준 — 델타 곡면 구간이 가르는(또는 못 가르는) 관례."""
    return tuple(b for b, t in t_values.items() if t is not None and lo <= t * DAYS_PER_YEAR <= hi)


def solve_delta_gamma(
    strike: float,
    cp: CallPut,
    delta: float,
    gamma: float,
    lo: float = DELTA_GAMMA_RANGE[0],
    hi: float = DELTA_GAMMA_RANGE[1],
) -> tuple[float, float] | None:
    """KIS 델타·감마를 함께 맞추는 (S, x = σ√T). 풀 수 없으면 None.

    N(d1) = 콜 Δ 로 d1 을, `Γ = n(d1) / (S·x)` 로 S·x 를 얻고 `f(x) = ln(S/K)/x + x/2 − d1 = 0`
    을 x 로 푼다. f 는 x → 0 에서 + 이고 큰 x 에서 다시 + 가 될 수 있어(S → 0 인 가짜 근), x 를
    1e-4 부터 1.2 배씩 키우며 처음 + → − 로 바뀌는 구간을 이분법으로 좁힌다 — 물리적인 근.
    """
    p = _call_delta(cp, delta)
    if not (lo <= p <= hi) or gamma <= 0 or strike <= 0:
        return None
    nd = statistics.NormalDist()
    d1 = nd.inv_cdf(p)
    sx = nd.pdf(d1) / gamma

    def f(x: float) -> float:
        return math.log(sx / x / strike) / x + x / 2 - d1

    a = 1e-4
    fa = f(a)
    while a < 3.0:
        b = a * 1.2
        fb = f(b)
        if fa > 0 >= fb:
            break
        a, fa = b, fb
    else:
        return None
    for _ in range(100):
        m = (a + b) / 2
        if f(m) > 0:
            a = m
        else:
            b = m
    x = (a + b) / 2
    return sx / x, x


def implied_underlying(
    price: float, strike: float, T: float, sigma: float, cp: CallPut
) -> float | None:
    """가격·σ·T 로 매긴 Black 가격이 `price` 가 되는 기초자산 S. 없으면 None.

    콜 가격은 S 에 대해 증가, 풋은 감소한다 — [K/4, 4K] 에서 이분법. KIS IV 와 last 로 풀면 KIS 가
    그 IV 를 어떤 S 로 매겼는지 드러난다.
    """
    if not (price > 0 and strike > 0 and T > 0 and sigma > 0):
        return None
    flag = _flag(cp)
    sign = 1.0 if cp == "C" else -1.0

    def g(s: float) -> float:
        return sign * (black76.price(flag, s, strike, T, sigma) - price)

    a, b = strike / 4, strike * 4
    if g(a) > 0 or g(b) < 0:
        return None
    for _ in range(200):
        m = (a + b) / 2
        if g(m) > 0:
            b = m
        else:
            a = m
    return (a + b) / 2


@dataclass(frozen=True, slots=True)
class ParityFit:
    """KIS 이론가 패리티 `thpr_C − thpr_P = A − D·K` 회귀. 선도 = A/D, 최대 잔차 resid(pt)."""

    A: float
    D: float
    n: int
    resid: float

    @property
    def forward(self) -> float:
        return self.A / self.D

    def rate(self, T: float) -> float:
        """할인 D 를 T(년) 기준 연속복리 금리로: −ln D / T."""
        return -math.log(self.D) / T


def fit_parity(kis: Mapping[Key, KisRow]) -> ParityFit | None:
    """콜·풋 이론가가 둘 다 있는 행사가로 회귀. 행사가 2개 미만이면 None."""
    ks: list[float] = []
    ys: list[float] = []
    for (k, cp), row in kis.items():
        put = kis.get((k, "P"))
        if cp != "C" or row.thpr is None or put is None or put.thpr is None:
            continue
        ks.append(float(k))
        ys.append(row.thpr - put.thpr)
    if len(set(ks)) < 2:
        return None
    fit = statistics.linear_regression(ks, ys)
    resid = max(abs(y - (fit.intercept + fit.slope * k)) for k, y in zip(ks, ys, strict=True))
    return ParityFit(A=fit.intercept, D=-fit.slope, n=len(ks), resid=resid)


@dataclass(frozen=True, slots=True)
class FitInput:
    """관례 후보를 매길 종목 하나. 가격은 pt, iv 는 %."""

    strike: float
    cp: CallPut
    kis: KisRow
    iv_ours_pct: float | None
    last: float | None
    mid: float | None

    def price(self, src: PriceSource) -> float | None:
        return self.last if src == "last" else self.mid


def fit_inputs(
    ev: ExpiryEval, kis: Mapping[Key, KisRow], strikes: Iterable[Decimal]
) -> list[FitInput]:
    """평가 결과에서 행사가 `strikes` 의 종목들. mid 는 전광판 호가가 둘 다 있고 역전이 아닐 때."""
    wanted = set(strikes)
    out: list[FitInput] = []
    for o in ev.options:
        q = o.quote
        if q.strike not in wanted:
            continue
        mid = None
        if q.source == "board" and q.bid and q.ask and q.ask >= q.bid:
            mid = float((q.bid + q.ask) / 2)
        out.append(
            FitInput(
                strike=float(q.strike),
                cp=q.cp,
                kis=kis[(q.strike, q.cp)],
                iv_ours_pct=_own_iv_pct(o.iv),
                last=float(q.last) if q.last else None,
                mid=mid,
            )
        )
    return out


def _sigma(src: SigmaSource, x: FitInput) -> float | None:
    pct = {"kis_iv": x.kis.iv_pct, "hist": x.kis.hist_pct, "ours": x.iv_ours_pct}[src]
    return None if pct is None else pct / 100


def total_vol(x: FitInput, S: float, src: PriceSource = "last") -> float | None:
    """가격이 함의하는 총변동 w = σ√T (r = 0 Black 가격은 w 로만 정해진다). 못 풀면 None."""
    price = x.price(src)
    if price is None:
        return None
    iv = implied_vol(price, S, x.strike, 1.0, _flag(x.cp))
    return iv.sigma if iv.source == "model" else None


def implied_t_days(x: FitInput, S: float, src: PriceSource = "last") -> float | None:
    """KIS IV 와 같은 가격이 나오려면 T 가 며칠이어야 하나: 365·(w / σ_KIS)²."""
    w = total_vol(x, S, src)
    if w is None or x.kis.iv_pct is None:
        return None
    return DAYS_PER_YEAR * (w / (x.kis.iv_pct / 100)) ** 2


@dataclass(frozen=True, slots=True)
class FitResult:
    """후보 하나의 잔차. main·p90: 감마 |상대오차| 또는 IV |차|(%p). round_match: KIS 자릿수로
    반올림해 KIS 와 같은 비율, delta_abs: 델타 |차| 중앙값 — 둘 다 감마 후보만."""

    name: str
    n: int
    main: float
    p90: float
    round_match: float | None = None
    delta_abs: float | None = None


def fit_gamma(
    xs: Sequence[FitInput],
    underlyings: Mapping[str, float],
    t_values: Mapping[TBasis, float | None],
    sigmas: Sequence[SigmaSource] = SIGMA_SOURCES,
) -> list[FitResult]:
    """σ × S × T 후보마다 Black-76 감마·델타를 다시 매겨 KIS gama·delta_val 과 비교. main 오름차순.

    KIS 감마가 없는(0) 종목·σ 가 없는 종목은 그 후보에서 빠진다.
    """
    out: list[FitResult] = []
    for src in sigmas:
        for uname, S in underlyings.items():
            for basis, T in t_values.items():
                if T is None:
                    continue
                rel: list[float] = []
                match = 0
                dabs: list[float] = []
                for x in xs:
                    sigma = _sigma(src, x)
                    if sigma is None or x.kis.gamma is None:
                        continue
                    g = greeks(_flag(x.cp), S, x.strike, T, sigma)
                    rel.append(g.gamma / x.kis.gamma - 1)
                    match += round(g.gamma, KIS_GAMMA_DP) == round(x.kis.gamma, KIS_GAMMA_DP)
                    if x.kis.delta is not None:
                        dabs.append(abs(g.delta - x.kis.delta))
                if not rel:
                    continue
                ab = [abs(r) for r in rel]
                out.append(
                    FitResult(
                        name=f"σ={src} · S={uname} · T={basis}",
                        n=len(rel),
                        main=statistics.median(ab),
                        p90=percentile(ab, 90),
                        round_match=match / len(rel),
                        delta_abs=statistics.median(dabs) if dabs else None,
                    )
                )
    return sorted(out, key=lambda r: r.main)


def fit_iv(
    xs: Sequence[FitInput],
    underlyings: Mapping[str, float],
    t_values: Mapping[TBasis, float | None],
    prices: Sequence[PriceSource] = PRICE_SOURCES,
) -> list[FitResult]:
    """가격 × S × T 후보마다 IV 를 역산(KIS 폴백 없이)해 KIS hts_ints_vltl 과 비교. main 오름차순.

    역산이 안 되는(내재가치 아래 등) 종목은 그 후보에서 빠진다.
    """
    out: list[FitResult] = []
    for src in prices:
        for uname, S in underlyings.items():
            for basis, T in t_values.items():
                if T is None:
                    continue
                diffs: list[float] = []
                for x in xs:
                    w = total_vol(x, S, src)
                    if w is None or x.kis.iv_pct is None:
                        continue
                    diffs.append(w / math.sqrt(T) * 100 - x.kis.iv_pct)
                if not diffs:
                    continue
                ab = [abs(d) for d in diffs]
                out.append(
                    FitResult(
                        name=f"가격={src} · S={uname} · T={basis}",
                        n=len(diffs),
                        main=statistics.median(ab),
                        p90=percentile(ab, 90),
                    )
                )
    return sorted(out, key=lambda r: r.main)


# ── KIS 관례 재현 검사 (PLAN §6.2, metrics §1.7) ─────────────────────────────


def rel_err(ours: float, kis: float) -> float:
    """재현 검사 판정 오차(PLAN §6.2, 2026-09-28 사용자 결정): 평이 상대오차 `(ours − kis) / |kis|`.

    크기는 `|ours / kis − 1|` 그대로다. 부호는 `ours − kis` 를 따른다 — 풋(KIS 음수)에서도
    `kis_rel_err` 와 같은 부호라 둘을 나란히 쓸 수 있다. kis 0(값 없음)은 ValueError.
    """
    if kis == 0:
        raise ValueError("KIS 값 0 은 비교할 수 없다")
    return (ours - kis) / abs(kis)


def kis_rel_err(ours: float, kis: float, half_ulp: float = KIS_HALF_ULP) -> float:
    """반올림 보정 오차([확인 필요] 제안 — 판정은 `rel_err`): KIS 표시값의 반올림 구간
    [kis − half_ulp, kis + half_ulp] 밖으로 벗어난 만큼의 상대오차.

    부호는 `ours − kis` 를 따른다. KIS `delta_val`·`gama` 는 소수 4자리라 작은 값은 반올림만으로
    1% 를 넘을 수 있다 — 반올림 구간이 감마 0.0029 면 ±1.7%, 0.0011 이면 ±4.5%, 0.0002 면 ±25%,
    델타 0.0016 이면 ±3.1%. 실측(2026-09-28)에서 평이 1% 를 넘은 월물 행은 ATM±5 밖의 이런 작은
    값이었다(ATM±5 는 평이 1% 로 전 행 통과). 구간 안이면 0. kis 가 0 이면 ValueError.
    """
    if kis == 0:
        raise ValueError("KIS 값 0 은 비교할 수 없다")
    gap = max(abs(ours - kis) - half_ulp, 0.0)
    return math.copysign(gap, ours - kis) / abs(kis)


def repro_err(ours: float, kis: float, metric: ErrMetric = "plain") -> float:
    return rel_err(ours, kis) if metric == "plain" else kis_rel_err(ours, kis)


def kis_sigma(k: KisRow, hist_ref: float | None) -> tuple[float, SigmaConv] | None:
    """KIS 그릭스의 σ 관례(metrics §1.7) — (σ 소수, 출처). 정할 수 없으면 None.

    - 행 `hist_vltl` 이 있으면 그것(`hist` — 월물)
    - 없으면(위클리 전광판은 0) 당일 거래가 있거나 거래량을 모르는 행은 그 행 KIS IV(`kis_iv`),
      거래가 없는 행(`acml_vol` 0)은 같은 스냅샷 월물의 `hist_vltl`(`hist_ref` — 기초 HV)
    """
    if k.hist_pct is not None:
        return k.hist_pct / 100, "hist"
    if k.volume != 0:
        return None if k.iv_pct is None else (k.iv_pct / 100, "kis_iv")
    return None if hist_ref is None else (hist_ref / 100, "hist_ref")


def kis_underlying(
    parity: ParityFit | None,
    s_ref: float,
    resid_max: float = FIT_RESID_MAX,
    min_strikes: int = PARITY_MIN_STRIKES,
) -> tuple[float, UnderlyingConv]:
    """KIS 그릭스의 S 관례(metrics §1.7) — KIS 이론가 패리티 선도, 안 서면 선물 근월물 S_ref.

    패리티 `thpr_C − thpr_P = A − D·K` 는 행사가 min_strikes 개 이상·최대 잔차 < resid_max 일 때만
    쓴다(행사가 2개면 늘 딱 맞는다). 월물은 이론가(`hts_thpr`)가 콜·풋 모두 있어 직선이다(잔차
    ≤ 0.4pt). 위클리 이론가는 대부분 0·들쭉날쭉이라(잔차 38~62pt) 패리티가 안 선다.
    """
    if parity is not None and parity.n >= min_strikes and parity.resid < resid_max:
        return parity.forward, "parity"
    return s_ref, "fut"


@dataclass(frozen=True, slots=True)
class ReproRow:
    """종목 하나의 재현. delta·gamma 는 KIS 관례 입력으로 `core.greeks` 가 낸 값.

    implied: KIS Δ·Γ 한 쌍이 함의하는 (S*, x* = σ√T) — 풀 수 없으면 None. cause: 판정(평이
    상대오차) 허용 밖이면 왜인지 — `반올림`: 같은 입력이 반올림 보정 오차로는 허용 안(1% 를 넘은
    몫이 KIS 4자리 반올림 폭 안 — 입력은 맞다). 아니면 어느 입력을 바꿔야 KIS 값(반올림 구간까지)이
    나오는지 — `S`: S* 로 바꾸면 통과, `σ√T`: σ 를 x*/√T 로 바꾸면 통과, `S+σ√T`: 둘 다,
    `풀이 없음`: Δ·Γ 한 쌍을 못 풂. 통과면 None.
    """

    strike: Decimal
    cp: CallPut
    volume: int | None
    sigma: float
    sigma_src: SigmaConv
    delta: float
    gamma: float
    kis_delta: float | None
    kis_gamma: float | None
    implied: tuple[float, float] | None
    cause: ReproCause | None

    def err(self, greek: Literal["delta", "gamma"], metric: ErrMetric = "plain") -> float | None:
        """KIS 값이 없으면(0) None."""
        if greek == "delta":
            return None if not self.kis_delta else repro_err(self.delta, self.kis_delta, metric)
        return None if self.kis_gamma is None else repro_err(self.gamma, self.kis_gamma, metric)

    @property
    def delta_err(self) -> float | None:
        """판정 오차(평이 상대오차)."""
        return self.err("delta")

    @property
    def gamma_err(self) -> float | None:
        """판정 오차(평이 상대오차)."""
        return self.err("gamma")

    def implied_sigma(self, T: float) -> float | None:
        """KIS Δ·Γ 의 x* 를 T 로 나눈 σ* — T 를 KIS 관례로 두면 KIS 가 쓴 σ."""
        return None if self.implied is None else self.implied[1] / math.sqrt(T)


def _within(
    cp: CallPut,
    S: float,
    K: float,
    T: float,
    sigma: float,
    kis_delta: float | None,
    kis_gamma: float | None,
    tol: float,
    metric: ErrMetric = "plain",
) -> tuple[float, float, bool]:
    """(Δ, Γ, KIS 값이 있는 쪽이 모두 허용 안인가)."""
    g = greeks(_flag(cp), S, K, T, sigma)
    errs = []
    if kis_delta:
        errs.append(repro_err(g.delta, kis_delta, metric))
    if kis_gamma is not None:
        errs.append(repro_err(g.gamma, kis_gamma, metric))
    return g.delta, g.gamma, all(abs(e) <= tol for e in errs)


def reproduce_row(
    strike: Decimal,
    cp: CallPut,
    k: KisRow,
    sigma: tuple[float, SigmaConv],
    S: float,
    T: float,
    tol: float = REPRO_REL_TOL,
) -> ReproRow:
    """KIS 관례 입력(σ·S·T)으로 Black-76(r = 0) 델타·감마를 내 KIS 값과 비교한다.

    판정은 평이 상대오차(`rel_err`) ≤ tol. 허용 밖이면 원인을 가린다: 같은 입력이 반올림 보정
    오차(`kis_rel_err`)로는 통과하면 `반올림`(1% 를 넘은 몫이 KIS 4자리 표시 폭 안). 아니면 KIS Δ·Γ
    한 쌍이 함의하는 (S*, x*) 로 입력을 하나씩 바꿔 반올림 구간까지 맞는지 본다: S 만 S* 로 → 통과면
    `S`, σ 만 x*/√T 로 → 통과면 `σ√T`(시리즈 공통 T 는 따로 본다 — `Reproduction.t_implied_days`),
    아니면 `S+σ√T`.
    """
    K = float(strike)
    s, src = sigma
    delta, gamma, ok = _within(cp, S, K, T, s, k.delta, k.gamma, tol)
    implied = None
    if k.delta is not None and k.gamma is not None:
        implied = solve_delta_gamma(K, cp, k.delta, k.gamma)
    cause: ReproCause | None = None
    if not ok:
        if _within(cp, S, K, T, s, k.delta, k.gamma, tol, "rounding")[2]:
            cause = "반올림"
        elif implied is None:
            cause = "풀이 없음"
        elif _within(cp, implied[0], K, T, s, k.delta, k.gamma, tol, "rounding")[2]:
            cause = "S"
        elif _within(cp, S, K, T, implied[1] / math.sqrt(T), k.delta, k.gamma, tol, "rounding")[2]:
            cause = "σ√T"
        else:
            cause = "S+σ√T"
    return ReproRow(strike, cp, k.volume, s, src, delta, gamma, k.delta, k.gamma, implied, cause)


@dataclass(frozen=True, slots=True)
class Reproduction:
    """시리즈 하나의 KIS 관례 재현 검사(PLAN §6.2).

    T: KIS 관례 T(년, `core.forward.kis_time_to_expiry`). S·s_src: KIS 관례 S. rows: KIS
    델타·감마가 있고 σ 를 정할 수 있는 모든 종목(행사가 오름차순). window: ATM±5 행사가 — 받은
    행사가가 못 덮으면(전광판 잘림) None. candidates: S·T 후보 이름 → 그 입력 하나만 바꿔 다시 낸
    행(`candidate_counts`).
    delta_days: 델타 곡면 T — 감마 4자리 반올림에 덜 흔들려 T 관례를 가장 좁게 가른다.
    t_sweep: 만기일(D = 0) 시리즈만 — 관례 S·σ 그대로 T 만 `T_SWEEP_DAYS` 로 바꿨을 때 감마 평이
    오차(`t_sweep_atm` 이면 ATM±5, 아니면 전체 행). 그 밖의 시리즈는 빈 튜플.
    """

    T: float
    S: float
    s_src: UnderlyingConv
    rows: tuple[ReproRow, ...]
    window: frozenset[Decimal] | None
    candidates: Mapping[str, tuple[ReproRow, ...]]
    # 델타 곡면 T(일)·붓스트랩 95% 구간 — 시리즈 전체 행, σ = 행 hist_vltl 중앙값(월물만)
    delta_days: tuple[float, float, float] | None = None
    t_sweep: tuple[TSweepPoint, ...] = ()
    t_sweep_atm: bool = False

    @property
    def t_sweep_best(self) -> float | None:
        """T 훑기에서 감마 |평이 오차| 중앙이 가장 작은 날수. 훑기가 없으면 None."""
        if not self.t_sweep:
            return None
        return min(self.t_sweep, key=lambda p: p.median).days

    def scoped(
        self, atm_only: bool, rows: tuple[ReproRow, ...] | None = None
    ) -> tuple[ReproRow, ...] | None:
        """ATM±5(atm_only)나 전체 행. rows: 관례 행 대신 후보 행을 거를 때."""
        rows = self.rows if rows is None else rows
        if not atm_only:
            return rows
        if self.window is None:
            return None
        return tuple(r for r in rows if r.strike in self.window)

    def stats(
        self, greek: Literal["delta", "gamma"], atm_only: bool, metric: ErrMetric = "plain"
    ) -> ErrorStats | None:
        rows = self.scoped(atm_only)
        if rows is None:
            return None
        errs = (r.err(greek, metric) for r in rows)
        return error_stats((e for e in errs if e is not None), REPRO_REL_TOL)

    def counts(self, atm_only: bool, metric: ErrMetric = "plain") -> PassCounts | None:
        rows = self.scoped(atm_only)
        return None if rows is None else pass_counts(rows, metric)

    def candidate_counts(
        self, name: str, atm_only: bool = False, metric: ErrMetric = "plain"
    ) -> PassCounts | None:
        """후보 입력 하나만 바꿨을 때 통과 수. 없는 후보·ATM±5 없음이면 None."""
        rows = self.candidates.get(name)
        scoped = None if rows is None else self.scoped(atm_only, rows)
        return None if scoped is None else pass_counts(scoped, metric)

    def causes(self, atm_only: bool = False) -> dict[ReproCause, int]:
        out: dict[ReproCause, int] = {}
        for r in self.scoped(atm_only) or ():
            if r.cause is not None:
                out[r.cause] = out.get(r.cause, 0) + 1
        return out

    @property
    def t_implied_days(self) -> float | None:
        """행마다 KIS Δ·Γ 의 x* 와 관례 σ 로 푼 T((x*/σ)², 일)의 중앙값 — 관례 T 와 비교한다."""
        ts = [(r.implied[1] / r.sigma) ** 2 * DAYS_PER_YEAR for r in self.rows if r.implied]
        return statistics.median(ts) if ts else None

    def sigma_sources(self) -> dict[SigmaConv, int]:
        out: dict[SigmaConv, int] = {}
        for r in self.rows:
            out[r.sigma_src] = out.get(r.sigma_src, 0) + 1
        return out


@dataclass(frozen=True, slots=True)
class TSweepPoint:
    """T 훑기 한 점 — T(일)에서 감마 |평이 오차| 중앙과 허용(1%) 안 행 수 / 행 수."""

    days: float
    median: float
    within: int
    n: int


def t_sweep(
    series: Series,
    hist_ref: float | None,
    S: float,
    window: frozenset[Decimal] | None,
    days: Sequence[float] = T_SWEEP_DAYS,
) -> tuple[TSweepPoint, ...]:
    """관례 S·σ 는 두고 T 만 days 로 바꿔 KIS 감마를 다시 낸다 — 만기일 T 관례(0.5일) 확인용.

    window 가 있으면 ATM±5 행만, 없으면 KIS 감마가 있는 전체 행. 오차는 판정과 같은 평이 상대오차.
    """
    rows: list[tuple[float, CallPut, float, float]] = [
        (float(k), cp, sg[0], row.gamma)
        for (k, cp), row in sorted(series.kis.items())
        if row.gamma is not None
        and (window is None or k in window)
        and (sg := kis_sigma(row, hist_ref)) is not None
    ]
    out: list[TSweepPoint] = []
    for d in days:
        errs = [
            abs(rel_err(greeks(_flag(cp), S, K, d / DAYS_PER_YEAR, s).gamma, g))
            for K, cp, s, g in rows
        ]
        if errs:
            within = sum(e <= REPRO_REL_TOL for e in errs)
            out.append(TSweepPoint(d, statistics.median(errs), within, len(errs)))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class PassCounts:
    """허용 안 행 수 / KIS 값이 있는 행 수 — 델타·감마."""

    delta: int
    delta_n: int
    gamma: int
    gamma_n: int

    def cell(self) -> str:
        return f"Δ {self.delta}/{self.delta_n} · Γ {self.gamma}/{self.gamma_n}"


def pass_counts(rows: Sequence[ReproRow], metric: ErrMetric = "plain") -> PassCounts:
    d = [e for r in rows if (e := r.err("delta", metric)) is not None]
    g = [e for r in rows if (e := r.err("gamma", metric)) is not None]
    return PassCounts(
        sum(abs(e) <= REPRO_REL_TOL for e in d),
        len(d),
        sum(abs(e) <= REPRO_REL_TOL for e in g),
        len(g),
    )


def reproduce(
    series: Series,
    s_ref: float,
    hist_ref: float | None,
    F: float | None = None,
    cal: TradingCalendar | None = None,
    n: int = WINDOW,
) -> Reproduction | None:
    """KIS 관례 재현 검사. 만기가 지났거나 비교할 종목이 없으면 None.

    hist_ref: 위클리 무거래 행의 σ(같은 스냅샷 월물 `hist_vltl`, %). F: 후보 비교용 합성 F.
    cal: 후보 비교에 T = 달력 분·달력일+1 도 넣을 때(`t_years`).
    """
    try:
        T = kis_time_to_expiry(series.now, series.expiry_date)
    except ValueError:
        return None
    S, s_src = kis_underlying(fit_parity(series.kis), s_ref)

    def run(S_: float, T_: float) -> tuple[ReproRow, ...]:
        rows: list[ReproRow] = []
        for (k, cp), row in sorted(series.kis.items()):
            sigma = kis_sigma(row, hist_ref)
            if sigma is None or (not row.delta and row.gamma is None):
                continue
            rows.append(reproduce_row(k, cp, row, sigma, S_, T_))
        return tuple(rows)

    rows = run(S, T)
    if not rows:
        return None
    strikes = sorted({k for k, _ in series.kis})
    window = frozenset(atm_window(strikes, s_ref, n)) if covers(strikes, s_ref, n) else None
    cands: dict[str, tuple[ReproRow, ...]] = {}
    for name, alt in (("S=선물", s_ref), ("S=합성 F", F), ("S=현물", series.spot)):
        if alt is not None:
            cands[name] = run(alt, T)
    if cal is not None:
        t_alts: tuple[tuple[str, TBasis], ...] = (
            ("T=달력 분", "minutes"),
            ("T=달력일+1", "days_incl"),
        )
        for name, basis in t_alts:
            t = t_years(basis, series.now, series.expiry_date, cal)
            if t is not None:
                cands[name] = run(S, t)
    hists = [r.hist_pct for r in series.kis.values() if r.hist_pct is not None]
    _, delta_days = delta_surface_days(series.kis, statistics.median(hists) if hists else None)
    # 만기일(D = 0) — kis_time_to_expiry 가 max(D, 0.5)/365 로 낸 값과 같은 식이라 정확히 같다
    sweep = t_sweep(series, hist_ref, S, window) if T == DAY_FLOOR / DAYS_PER_YEAR else ()
    return Reproduction(
        T, S, s_src, rows, window, cands, delta_days, sweep, t_sweep_atm=window is not None
    )


# ── 시리즈 분석 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DgGroup:
    """델타·감마 한 쌍 풀이 묶음: 종목 수, S 중앙값, x = σ√T 중앙값."""

    n: int
    S: float | None
    x: float | None

    @classmethod
    def of(cls, solved: Sequence[tuple[float, float]]) -> DgGroup:
        if not solved:
            return cls(0, None, None)
        return cls(
            len(solved),
            statistics.median(s for s, _ in solved),
            statistics.median(x for _, x in solved),
        )


def split_by_s(
    solved: Iterable[tuple[float, float]], s_ref: float, gap: float = STALE_S_GAP
) -> tuple[DgGroup, DgGroup]:
    """(S, x) 풀이를 |S − s_ref| ≤ gap(가까움)과 그 밖(멂)으로 나눈다."""
    near: list[tuple[float, float]] = []
    far: list[tuple[float, float]] = []
    for s, x in solved:
        (near if abs(s - s_ref) <= gap else far).append((s, x))
    return DgGroup.of(near), DgGroup.of(far)


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """시리즈 하나의 진단 수치(보고서 표 한 줄)."""

    f_gap: float | None  # 합성 F − S_ref (pt)
    stale_last: int  # ATM±5 에서 당일 거래 없이 last 로 역산한 종목 수
    priced: int  # ATM±5 에서 가격이 있는 종목 수
    fallback: int  # ATM±5 에서 KIS IV 폴백(estimated)
    # 자체 역산이 실패해 KIS IV 로 폴백하는 행 중 KIS 감마가 있는 행 — 그대로 vs σ T 맞춤
    fallback_gamma: FallbackGamma | None
    cp_gap_ours: float | None  # 같은 행사가 콜·풋 IV |차| 중앙값(%p) — 자체
    cp_gap_kis: float | None  # 같은 행사가 콜·풋 IV |차| 중앙값(%p) — KIS
    t_iv_days: tuple[float, float, float] | None  # KIS IV 가 함의하는 T(일) p10·중앙·p90 (S=S_ref)
    dg_near: DgGroup  # ATM±5 에서 KIS Δ·Γ 가 함의하는 S 가 S_ref ±STALE_S_GAP 안인 종목
    dg_far: DgGroup  # 그 밖인 종목 — KIS 가 다른(지난) 기초자산으로 매긴 값
    iv_near: DgGroup  # KIS IV·last·T(days_min_half)가 함의하는 S 로 같은 구분(x = σ_KIS·√T)
    iv_far: DgGroup
    delta_fit: DeltaFit | None  # 시리즈 전체 행
    # 델타 곡면 T(일)·붓스트랩 95% 구간 — σ = hist, 곡면 잔차 < FIT_RESID_MAX 일 때만
    delta_days: tuple[float, float, float] | None
    parity: ParityFit | None  # 시리즈 전체 행
    hist_pct: float | None  # KIS hist_vltl 중앙값(ATM±5)


@dataclass(frozen=True, slots=True)
class SeriesReport:
    series: Series
    ev: ExpiryEval
    rows: tuple[CompareRow, ...] | None
    gamma: ErrorStats | None
    iv: ErrorStats | None
    diag: Diagnosis | None
    t_values: Mapping[TBasis, float | None]
    gamma_fits: tuple[FitResult, ...]
    iv_fits: tuple[FitResult, ...]
    repro: Reproduction | None = None  # §6.2 KIS 관례 재현 검사 — ATM±5 를 못 덮어도 돈다


# 자체 역산 실패가 아닌 IV 사유 — KIS 폴백 묶음에서 뺀다(§1.2 제외는 그릭스가 없고, 역산 이상치는
# 폴백하지 않는다 — core.iv)
_NOT_FALLBACK_REASONS = frozenset({"below_min_premium", "model_out_of_range"})


@dataclass(frozen=True, slots=True)
class FallbackGamma:
    """KIS IV 폴백 행의 감마 상대오차 `자체 / KIS gama − 1` 중앙값 — 검증 수정 3 전후 비교.

    n: 자체 역산이 실패해 KIS IV 로 폴백하는(쓰든 이상치로 버리든) 행 중 KIS IV·감마가 있는 행 수.
    as_is: KIS σ 를 자체 T 에 그대로 썼을 때(옮기기 전 파이프라인) — KIS σ 가 §6.1 안인 행.
    rescaled: σ_KIS·√(T_KIS/T) 로 옮겼을 때(지금 파이프라인) — 옮긴 σ 가 §6.1 안인 행.
    dropped: 옮긴 σ 가 §6.1 밖이라 파이프라인이 invalid 로 GEX 에서 뺀 행 수(n − rescaled 행).
    """

    n: int
    as_is: float | None
    rescaled: float | None
    dropped: int


def _own_inversion_failed(r: CompareRow) -> bool:
    """자체 역산이 실패해 KIS IV 폴백을 탄 행(파이프라인 품질 estimated·invalid 둘 다)."""
    return (
        r.iv_quality != "ok"
        and r.iv_reason is not None
        and r.iv_reason.split("/")[0] not in _NOT_FALLBACK_REASONS
    )


def fallback_gamma(ev: ExpiryEval, rows: Iterable[CompareRow]) -> FallbackGamma | None:
    """폴백 행의 감마를 KIS σ 그대로와 T_KIS→자체 T 로 옮긴 σ 로 각각 매겨 KIS gama 와 비교.

    자체 그릭스와 같은 S(합성 F)·T(달력 분)를 쓰고 바뀌는 것은 σ 하나다. T_KIS 는 파이프라인이 쓴
    `ev.T_kis`, 환산은 `core.iv.rescale_sigma` — 옮긴 σ 의 감마는 파이프라인 감마와 같다. 폴백 행이
    없거나 F·T_KIS 가 없으면 None.
    """
    if ev.F is None or ev.T_kis is None:
        return None
    n = 0
    as_is: list[float] = []
    rescaled: list[float] = []
    for r in rows:
        if (
            not _own_inversion_failed(r)
            or r.iv_kis_pct is None
            or r.iv_kis_pct <= 0
            or r.gamma_kis is None
        ):
            continue
        n += 1
        k = float(r.strike)
        for sigma, out in (
            (r.iv_kis_pct / 100, as_is),
            (rescale_sigma(r.iv_kis_pct / 100, ev.T_kis, ev.T), rescaled),
        ):
            if usable(sigma):  # §6.1 밖이면 그 파이프라인은 이 행을 GEX 에서 뺀다
                out.append(greeks(_flag(r.cp), ev.F, k, ev.T, sigma).gamma / r.gamma_kis - 1)
    if n == 0:
        return None
    return FallbackGamma(
        n,
        statistics.median(as_is) if as_is else None,
        statistics.median(rescaled) if rescaled else None,
        n - len(rescaled),
    )


def delta_surface_days(
    kis: Mapping[Key, KisRow], hist_pct: float | None
) -> tuple[DeltaFit | None, tuple[float, float, float] | None]:
    """시리즈 전체 행의 KIS 델타 곡면과, σ = hist_pct(%)로 환산한 T(일)·붓스트랩 95% 구간.

    T 는 곡면 잔차 < FIT_RESID_MAX(공통 σ·T 가정이 맞을 때)이고 hist_pct 가 있을 때만 낸다.
    """
    rows: list[tuple[float, CallPut, float]] = [
        (float(k), cp, r.delta) for (k, cp), r in kis.items() if r.delta is not None
    ]
    fit = fit_delta_surface(rows)
    if fit is None or hist_pct is None or fit.resid_pt >= FIT_RESID_MAX:
        return fit, None
    ci = bootstrap_delta_days(rows, hist_pct / 100)
    return fit, None if ci is None else (fit.implied_days(hist_pct / 100), *ci)


def _cp_gap(rows: Iterable[tuple[Decimal, CallPut, float | None]]) -> float | None:
    by_k: dict[Decimal, dict[CallPut, float]] = {}
    for k, cp, v in rows:
        if v is not None:
            by_k.setdefault(k, {})[cp] = v
    gaps = [abs(d["C"] - d["P"]) for d in by_k.values() if len(d) == 2]
    return statistics.median(gaps) if gaps else None


def _iv_underlyings(xs: Iterable[FitInput], T: float | None) -> list[tuple[float, float]]:
    """종목마다 KIS IV·last·T 가 함의하는 (S, σ_KIS·√T)."""
    if T is None:
        return []
    out: list[tuple[float, float]] = []
    for x in xs:
        if x.last is None or x.kis.iv_pct is None:
            continue
        sigma = x.kis.iv_pct / 100
        s = implied_underlying(x.last, x.strike, T, sigma, x.cp)
        if s is not None:
            out.append((s, sigma * math.sqrt(T)))
    return out


def _diagnose(
    series: Series,
    ev: ExpiryEval,
    rows: tuple[CompareRow, ...],
    xs: list[FitInput],
    s_ref: float,
    t_kis: float | None,
) -> Diagnosis:
    t_iv = [t for x in xs if (t := implied_t_days(x, s_ref)) is not None]
    solved = [
        sx
        for x in xs
        if x.kis.delta is not None
        and x.kis.gamma is not None
        and (sx := solve_delta_gamma(x.strike, x.cp, x.kis.delta, x.kis.gamma)) is not None
    ]
    near, far = split_by_s(solved, s_ref)
    iv_near, iv_far = split_by_s(_iv_underlyings(xs, t_kis), s_ref)
    hists = [x.kis.hist_pct for x in xs if x.kis.hist_pct is not None]
    hist = statistics.median(hists) if hists else None
    delta_fit, delta_days = delta_surface_days(series.kis, hist)
    return Diagnosis(
        f_gap=None if ev.F is None else ev.F - s_ref,
        stale_last=sum(r.stale_last for r in rows),
        priced=sum(r.price_kind is not None for r in rows),
        fallback=sum(r.iv_quality == "estimated" for r in rows),
        fallback_gamma=fallback_gamma(ev, rows),
        cp_gap_ours=_cp_gap((r.strike, r.cp, r.iv_ours_pct) for r in rows),
        cp_gap_kis=_cp_gap((r.strike, r.cp, r.iv_kis_pct) for r in rows),
        t_iv_days=(percentile(t_iv, 10), statistics.median(t_iv), percentile(t_iv, 90))
        if t_iv
        else None,
        dg_near=near,
        dg_far=far,
        iv_near=iv_near,
        iv_far=iv_far,
        delta_fit=delta_fit,
        delta_days=delta_days,
        parity=fit_parity(series.kis),
        hist_pct=hist,
    )


def analyze(
    series: Series, s_ref: float, cal: TradingCalendar, hist_ref: float | None = None
) -> SeriesReport:
    """시리즈 하나: §6.2 재현 검사 + 자체 vs KIS 기록·진단. hist_ref 는 `snapshot_hist`(%)."""
    ev = evaluate(series, s_ref)
    t_values = t_candidates(series.now, series.expiry_date, cal)
    repro = reproduce(series, s_ref, hist_ref, ev.F, cal)
    rows = compare(ev, series.kis, s_ref)
    if rows is None:
        return SeriesReport(series, ev, None, None, None, None, t_values, (), (), repro)
    gamma = error_stats((r.gamma_rel for r in rows if r.gamma_rel is not None), GAMMA_REL_TOL)
    iv = error_stats((r.iv_diff for r in rows if r.iv_diff is not None), IV_DIFF_TOL)
    xs = fit_inputs(ev, series.kis, {r.strike for r in rows})
    diag = _diagnose(series, ev, rows, xs, s_ref, t_values["days_min_half"])
    underlyings: dict[str, float] = {"fut": s_ref}
    if ev.F is not None:
        underlyings["F"] = ev.F
    if diag.delta_fit is not None and diag.delta_fit.resid_pt < FIT_RESID_MAX:
        underlyings["delta_fit"] = diag.delta_fit.S
    if diag.parity is not None and diag.parity.resid < FIT_RESID_MAX:
        underlyings["parity"] = diag.parity.forward
    return SeriesReport(
        series,
        ev,
        rows,
        gamma,
        iv,
        diag,
        t_values,
        tuple(fit_gamma(xs, underlyings, t_values)),
        tuple(fit_iv(xs, underlyings, t_values)),
        repro,
    )


# ── 보고서 ───────────────────────────────────────────────────────────────────


def _f(v: float | None, spec: str) -> str:
    return "—" if v is None else format(v, spec)


def _stats_cells(s: ErrorStats | None, scale: float, spec: str) -> str:
    if s is None:
        return "0 | — | — | — | — | —"
    return (
        f"{s.n} | {s.bias * scale:{spec}} | {s.median * scale:{spec}} | {s.p90 * scale:{spec}} | "
        f"{s.max * scale:{spec}} | {s.within}/{s.n}"
    )


@dataclass(frozen=True, slots=True)
class Skipped:
    """분석하지 못한 시리즈 — 한 시리즈의 실패가 보고서 전체를 막지 않는다(CLAUDE.md 예외 격리)."""

    label: str
    reason: str


def _reason(e: BaseException, limit: int = 200) -> str:
    """예외 → 표 한 칸에 들어갈 한 줄(줄바꿈·'|' 제거, 길이 제한)."""
    text = " ".join(f"{type(e).__name__}: {e}".split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def snapshot_hist(snap: Snapshot) -> float | None:
    """스냅샷의 KIS `hist_vltl`(기초자산 역사적 변동성, %) 중앙값 — 0 은 뺀다.

    월물 행에만 값이 있고 위클리 전광판은 0 으로 준다. 위클리 무거래 행의 KIS 그릭스 σ 로 쓴다
    (`kis_sigma` 의 `hist_ref`). 조회 시각마다 소수 셋째 자리가 달라진다(75.38~75.41).
    """
    rows: list[_Row] = [r for b in snap.boards.values() for r in (*b.output1, *b.output2)]
    rows += [f.row for f in snap.fills if f.row is not None]
    hs = [h for r in rows if (h := _positive(r.hist_vltl)) is not None]
    return statistics.median(hs) if hs else None


def analyze_label(
    snap: Snapshot,
    label: str,
    cal: TradingCalendar,
    given: Mapping[str, date] | None = None,
    hist_ref: float | None = None,
) -> SeriesReport | Skipped:
    """시리즈 하나를 적재·분석한다. 어떤 예외든 그 시리즈만 `Skipped` 로 남긴다."""
    try:
        return analyze(series_rows(snap, label, cal, given), snap.atm_ref.price, cal, hist_ref)
    except Exception as e:  # 시리즈 단위 격리 — 사유는 보고서 표에 남는다
        return Skipped(label, _reason(e))


def _summary(results: Sequence[SeriesReport | Skipped]) -> list[str]:
    out = [
        "| 시리즈 | 만기일 | T(분) | F (품질) | 감마 n | 편향 | 중앙 | p90 | 최대 | ≤5% "
        "| IV n | 편향 | 중앙 | p90 | 최대 | ≤1%p |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if isinstance(r, Skipped):
            out.append(f"| {r.label} | — | — | — | 건너뜀: {r.reason} |" + " |" * 11)
            continue
        s = r.series
        exp = f"{s.expiry_date}{EXPIRY_TAG[s.expiry_source]}"
        head = (
            f"| {s.label} | {exp} | {r.ev.T * MINUTES_PER_YEAR:.0f} | "
            f"{_f(r.ev.F, '.2f')} ({r.ev.forward.quality}) | "
        )
        if r.rows is None:
            out.append(head + "ATM±5 없음 |" + " |" * 11)
            continue
        out.append(
            head + f"{_stats_cells(r.gamma, 100, '+.1f')} | {_stats_cells(r.iv, 1, '+.2f')} |"
        )
    return out


def _dg_cell(g: DgGroup) -> str:
    return f"{g.n}·{_f(g.S, '.1f')}·{_f(g.x, '.4f')}"


def _x_days(g: DgGroup, hist_pct: float | None) -> str:
    """묶음의 x 를 hist σ 로 매겼다면 T 가 며칠인가."""
    if g.x is None or hist_pct is None:
        return ""
    return f" → {(g.x / (hist_pct / 100)) ** 2 * DAYS_PER_YEAR:.2f}일"


def _fallback_cell(d: Diagnosis) -> str:
    """폴백 행 수 (KIS 감마 있는 n 행의 감마 상대 중앙: 그대로 → σ T 맞춤[, T 맞춤으로 빠진 행])."""
    fg = d.fallback_gamma
    if fg is None:
        return str(d.fallback)

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:+.0f}%"

    drop = f", T 맞춤 σ > 300% 로 {fg.dropped}행 invalid" if fg.dropped else ""
    return f"{d.fallback} ({fg.n}행 감마 {pct(fg.as_is)} → T 맞춤 {pct(fg.rescaled)}{drop})"


def _diagnosis_tables(reports: Sequence[SeriesReport]) -> list[str]:
    # 위클리 전광판은 hist_vltl 이 0 이라, 같은 스냅샷 월물의 값을 기준 σ 로 쓴다
    hists = [r.diag.hist_pct for r in reports if r.diag and r.diag.hist_pct is not None]
    hist_ref = statistics.median(hists) if hists else None
    ours = [
        "| 시리즈 | F − S_ref | 전 세션 last / 가격 있음 | KIS IV 폴백 (감마: 그대로 → σ T 맞춤) "
        "| 같은 K 콜·풋 IV 차 자체 / KIS |",
        "|---|---|---|---|---|",
    ]
    kis = [
        "| 시리즈 | hist | KIS IV 의 T(일) p10·중앙·p90 | IV·last 의 S 가까움 n·S | 멂 n·S "
        "| Δ·Γ 풀이 가까움 n·S·x | 멂 n·S·x "
        f"(→ hist {_f(hist_ref, '.2f')}% 로 T) | 델타 곡면 S·x·잔차(pt) (→ hist 로 T) "
        "| 패리티 선도·D·r(days)·잔차(pt) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        d = r.diag
        if d is None:
            continue
        ours.append(
            f"| {r.series.label} | {_f(d.f_gap, '+.2f')} | {d.stale_last}/{d.priced} | "
            f"{_fallback_cell(d)} | {_f(d.cp_gap_ours, '.2f')} / {_f(d.cp_gap_kis, '.2f')} |"
        )
        t = "—" if d.t_iv_days is None else "·".join(f"{v:.2f}" for v in d.t_iv_days)
        df = d.delta_fit
        dfs = "—"
        if df is not None:
            dfs = f"{df.S:.2f}·{df.x:.4f}·{df.resid_pt:.2f}"
            if d.hist_pct is not None and df.resid_pt < FIT_RESID_MAX:
                dfs += f" → {df.implied_days(d.hist_pct / 100):.2f}일"
        pf = d.parity
        t_days = r.t_values["days"]
        pfs = "—"
        if pf is not None:
            ok = t_days is not None and pf.resid < FIT_RESID_MAX
            rate = f"{pf.rate(t_days) * 100:.2f}%" if ok and t_days is not None else "—"
            pfs = f"{pf.forward:.2f}·{pf.D:.6f}·{rate}·{pf.resid:.2f}"
        kis.append(
            f"| {r.series.label} | {_f(d.hist_pct, '.2f')} | {t} | "
            f"{d.iv_near.n}·{_f(d.iv_near.S, '.1f')} | {d.iv_far.n}·{_f(d.iv_far.S, '.1f')} | "
            f"{_dg_cell(d.dg_near)} | {_dg_cell(d.dg_far)}{_x_days(d.dg_far, hist_ref)} | "
            f"{dfs} | {pfs} |"
        )
    return [*ours, "", *kis]


def _t_basis_table(reports: Sequence[SeriesReport]) -> list[str]:
    """델타 곡면 T 로 KIS 의 T 관례를 가른다 — 감마·IV 잔차는 달력일과 달력 분을 못 가른다.

    곡면은 재현 검사(`Reproduction.delta_days`)의 것 — 시리즈 전체 행이라 ATM±5 를 못 덮는 202611
    도 나온다. 달력 분 − 달력일 = 오늘 15:20 까지 남은 시간이라, 이 폭이 구간 폭보다 작은(장 막판)
    스냅샷에서는 두 후보가 다 구간 안에 든다(판별 불가).
    """
    out = [
        "| 시리즈 | 델타 곡면 T(일) [95% 구간] | T_KIS(일) | 달력 분(일) | 구간 안 T 후보 "
        f"| \\|곡면 − T_KIS\\| ≤ {DELTA_DAYS_TOL} |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        if r.repro is None or r.repro.delta_days is None:
            continue
        est, lo, hi = r.repro.delta_days
        t_kis = r.repro.T * DAYS_PER_YEAR
        minutes = r.t_values["minutes"]
        within = ", ".join(t_bases_within(lo, hi, r.t_values)) or "없음"
        ok = "예" if abs(est - t_kis) <= DELTA_DAYS_TOL else "아니오"
        out.append(
            f"| {r.series.label} | {est:.4f} [{lo:.4f}, {hi:.4f}] | {t_kis:.3f} | "
            f"{_f(None if minutes is None else minutes * DAYS_PER_YEAR, '.4f')} | {within} | {ok} |"
        )
    return out if len(out) > 2 else ["(델타 곡면 잔차가 작은 시리즈 없음)"]


def _fit_table(r: SeriesReport, top: int) -> list[str]:
    out = [
        f"#### {r.series.label}",
        "",
        "T 후보(일): "
        + ", ".join(
            f"{b} {_f(None if t is None else t * DAYS_PER_YEAR, '.3f')}"
            for b, t in r.t_values.items()
        ),
        "",
        "| 감마 후보 | n | 상대 중앙 | p90 | 4자리 일치 | 델타 차 중앙 |",
        "|---|---|---|---|---|---|",
    ]
    for f in r.gamma_fits[:top]:
        out.append(
            f"| {f.name} | {f.n} | {f.main * 100:.2f}% | {f.p90 * 100:.2f}% | "
            f"{_f(None if f.round_match is None else f.round_match * 100, '.0f')}% | "
            f"{_f(f.delta_abs, '.4f')} |"
        )
    out += ["", "| IV 후보 | n | 차 중앙(%p) | p90 |", "|---|---|---|---|"]
    out += [f"| {f.name} | {f.n} | {f.main:.2f} | {f.p90:.2f} |" for f in r.iv_fits[:top]]
    return [*out, ""]


def _detail(r: SeriesReport) -> list[str]:
    out = [
        f"#### {r.series.label} — ATM±5 종목",
        "",
        "| K | C/P | 원천 | 가격 | 거래량 | IV 품질 | 자체 IV | KIS IV | 차(%p) | 자체 감마 "
        "| KIS 감마 | 상대 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in r.rows or ():
        rel = None if c.gamma_rel is None else c.gamma_rel * 100
        out.append(
            f"| {c.strike} | {c.cp} | {c.source} | {c.price_kind or '—'} | "
            f"{'—' if c.volume is None else c.volume} | {c.iv_quality or '—'} | "
            f"{_f(c.iv_ours_pct, '.2f')} | {_f(c.iv_kis_pct, '.2f')} | {_f(c.iv_diff, '+.2f')} | "
            f"{_f(c.gamma_ours, '.5f')} | {_f(c.gamma_kis, '.4f')} | {_f(rel, '+.1f')}% |"
        )
    return [*out, ""]


_SIGMA_NAME: Mapping[SigmaConv, str] = {"hist": "hist", "kis_iv": "IV", "hist_ref": "HV"}
_S_NAME: Mapping[UnderlyingConv, str] = {"parity": "이론가 패리티", "fut": "선물 S_ref"}
REPRO_CANDIDATES = ("S=선물", "S=합성 F", "S=현물", "T=달력 분", "T=달력일+1")


def _pass_cell(s: ErrorStats | None) -> str:
    """통과/n (|오차| 중앙·p90 %)."""
    if s is None:
        return "—"
    return f"{s.within}/{s.n} ({s.median * 100:.2f}·{s.p90 * 100:.2f}%)"


def _repro_table(results: Sequence[SeriesReport | Skipped]) -> list[str]:
    """§6.2 재현 검사 요약 — 시리즈마다 한 줄. 판정 칸은 평이 상대오차, 반올림 보정 칸은 제안."""
    out = [
        "| 시리즈 | T_KIS(일) | 행별 T*(일) | S (출처) | σ 행 수 hist·IV·HV | Δ ATM±5 "
        "| Γ ATM±5 | Δ 전체 | Γ 전체 | 반올림 보정 ATM±5 | 반올림 보정 전체 "
        "| 실패 원인(전체) " + "·".join(REPRO_CAUSES) + " |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if isinstance(r, Skipped):
            out.append(f"| {r.label} | 건너뜀: {r.reason} |" + " |" * 10)
            continue
        rp = r.repro
        if rp is None:
            out.append(
                f"| {r.series.label} | 비교할 종목 없음(만기 지남·KIS 값 없음) |" + " |" * 10
            )
            continue
        src = rp.sigma_sources()
        causes = rp.causes()
        out.append(
            f"| {r.series.label} | {rp.T * DAYS_PER_YEAR:.1f} | {_f(rp.t_implied_days, '.2f')} | "
            f"{rp.S:.2f} ({_S_NAME[rp.s_src]}) | "
            f"{src.get('hist', 0)}·{src.get('kis_iv', 0)}·{src.get('hist_ref', 0)} | "
            f"{_pass_cell(rp.stats('delta', True))} | {_pass_cell(rp.stats('gamma', True))} | "
            f"{_pass_cell(rp.stats('delta', False))} | {_pass_cell(rp.stats('gamma', False))} | "
            f"{_count_cell(rp.counts(True, 'rounding'))} | "
            f"{_count_cell(rp.counts(False, 'rounding'))} | "
            f"{'·'.join(str(causes.get(c, 0)) for c in REPRO_CAUSES)} |"
        )
    return out


def _count_cell(c: PassCounts | None) -> str:
    return "—" if c is None else c.cell()


def _repro_candidates(
    reports: Sequence[SeriesReport], metric: ErrMetric, atm_only: bool = False
) -> list[str]:
    """관례 입력 하나만 후보로 바꿨을 때 통과 수(ATM±5 나 전체 행).

    관례가 데이터로 갈리는지 본다.
    """
    out = [
        "| 시리즈 | 관례 | " + " | ".join(REPRO_CANDIDATES) + " |",
        "|---|---|" + "---|" * len(REPRO_CANDIDATES),
    ]
    for r in reports:
        rp = r.repro
        if rp is None:
            continue
        cells = " | ".join(
            _count_cell(rp.candidate_counts(n, atm_only, metric)) for n in REPRO_CANDIDATES
        )
        conv = _count_cell(rp.counts(atm_only, metric))
        out.append(f"| {r.series.label} | {conv} | {cells} |")
    return out


def _t_sweep_lines(reports: Sequence[SeriesReport]) -> list[str]:
    """만기일(D = 0) 시리즈마다 한 줄 — T(일)별 감마 |평이 오차| 중앙."""
    out: list[str] = []
    for r in reports:
        rp = r.repro
        if rp is None or not rp.t_sweep:
            continue
        scope = "ATM±5" if rp.t_sweep_atm else "전체"
        best = min(rp.t_sweep, key=lambda p: p.median)
        pts = " · ".join(f"{p.days:.2f}일 {p.median * 100:.2f}%" for p in rp.t_sweep)
        out.append(
            f"- {r.series.label} — {scope} {rp.t_sweep[0].n}행, 최소 {best.days:.2f}일 "
            f"({best.median * 100:.2f}%, 통과 {best.within}/{best.n}): {pts}"
        )
    return out or ["- 만기일(D = 0) 시리즈 없음"]


def _repro_detail(r: SeriesReport) -> list[str]:
    """ATM±5 종목별 재현 — 오차는 평이 상대오차 %(괄호는 반올림 보정).

    S*·σ* 는 KIS Δ·Γ 가 함의하는 값.
    """
    rp = r.repro
    rows = None if rp is None else rp.scoped(atm_only=True)
    if rp is None or rows is None:
        return []
    out = [
        f"#### {r.series.label} — 재현 ATM±5 (S {rp.S:.2f}, T {rp.T * DAYS_PER_YEAR:.1f}일)",
        "",
        "| K | C/P | 거래량 | σ(출처) | KIS Δ | 재현 Δ | 오차(보정) | KIS Γ | 재현 Γ | 오차(보정) "
        "| S* | σ*(T_KIS) | 원인 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def pct(x: ReproRow, greek: Literal["delta", "gamma"]) -> str:
        e, e_r = x.err(greek), x.err(greek, "rounding")
        if e is None or e_r is None:
            return "—"
        return f"{e * 100:+.2f}% ({e_r * 100:+.2f}%)"

    for x in rows:
        ss = x.implied_sigma(rp.T)
        out.append(
            f"| {x.strike} | {x.cp} | {'—' if x.volume is None else x.volume} | "
            f"{x.sigma * 100:.2f}% ({_SIGMA_NAME[x.sigma_src]}) | {_f(x.kis_delta, '+.4f')} | "
            f"{x.delta:+.4f} | {pct(x, 'delta')} | "
            f"{_f(x.kis_gamma, '.4f')} | {x.gamma:.5f} | {pct(x, 'gamma')} | "
            f"{_f(None if x.implied is None else x.implied[0], '.2f')} | "
            f"{_f(None if ss is None else ss * 100, '.2f')}% | {x.cause or '통과'} |"
        )
    return [*out, ""]


def report(
    path: Path,
    cal: TradingCalendar,
    top: int = 3,
    detail: bool = False,
    given: Mapping[str, date] | None = None,
) -> str:
    """스냅샷 하나의 보고서(마크다운). given: `--expiry` 만기일. 시리즈 실패는 '건너뜀' 줄로."""
    snap = load_snapshot(path)
    s_ref = snap.atm_ref.price
    hist_ref = snapshot_hist(snap)
    results = [analyze_label(snap, label, cal, given, hist_ref) for label in snap.boards]
    reports = [r for r in results if isinstance(r, SeriesReport)]
    loose = unmatched_fills(snap)
    out = [
        f"## {path.parent.name} — {snap.started_kst.isoformat()} "
        f"(S_ref {snap.atm_ref.code} {s_ref})",
        "",
    ]
    if loose:
        out += [
            "전광판 만기 코드에 안 붙어 뺀 보강 행: "
            + ", ".join(f"{k} {v}건" for k, v in sorted(loose.items())),
            "",
        ]
    out += [
        f"### {REPRO_HEADER}",
        "",
        f"σ: 월물 행 `hist_vltl`, 위클리 당일 거래 행 KIS IV · 무거래 행 기초 HV "
        f"{_f(hist_ref, '.2f')}%(월물 `hist_vltl`) · S: KIS 이론가 패리티 선도"
        f"(안 서면 선물 S_ref) · T: max(달력일, 0.5)/365 (metrics §1.7). "
        f"판정(PLAN §6.2 결정): |자체/KIS − 1| ≤ {REPRO_REL_TOL:.0%} — "
        "칸: 통과/n (|오차| 중앙·p90). "
        f"반올림 보정([확인 필요] 제안): KIS 4자리 반올림 구간(±{KIS_HALF_ULP:.5f}) 밖 "
        f"상대오차 ≤ {REPRO_REL_TOL:.0%}. 원인: `반올림` = 같은 입력이 반올림 보정으로는 통과"
        "(1% 를 넘은 몫이 반올림 폭 안), 그 밖은 그 입력을 KIS Δ·Γ 가 함의하는 값(S*, σ*)으로 "
        "바꾸면 (반올림 구간까지) 통과",
        "",
        *_repro_table(results),
        "",
        "#### 후보 — ATM±5 통과 수, 관례 입력 하나만 바꿨을 때 "
        f"(|자체/KIS − 1| ≤ {REPRO_REL_TOL:.0%})",
        "",
        *_repro_candidates(reports, "plain", atm_only=True),
        "",
        f"#### 후보 — 같은 입력, 전체 행 통과 수 (|자체/KIS − 1| ≤ {REPRO_REL_TOL:.0%})",
        "",
        *_repro_candidates(reports, "plain"),
        "",
        "#### 후보 — 같은 입력, 전체 행, 반올림 보정 [확인 필요]",
        "",
        *_repro_candidates(reports, "rounding"),
        "",
        f"#### T 훑기 — 만기일(D = 0) 시리즈: 관례 S·σ 그대로 T 만 "
        f"{T_SWEEP_DAYS[0]:.2f}~{T_SWEEP_DAYS[-1]:.2f}일로 바꿨을 때 감마 |자체/KIS − 1| 중앙 "
        "(ATM±5, 못 덮으면 전체 행)",
        "",
        *_t_sweep_lines(reports),
        "",
        f"#### T 판별 — 델타 곡면(σ = hist)의 T, 붓스트랩 {BOOT_N}회 95% 구간",
        "",
        *_t_basis_table(reports),
        "",
        f"### {RECORD_HEADER}",
        "",
        *_summary(results),
        "",
        "### 진단 (ATM±5, 델타 곡면·패리티는 시리즈 전체)",
        "",
        *_diagnosis_tables(reports),
        "",
        "### KIS 관례 후보(상위)",
        "",
    ]
    for r in reports:
        if r.rows is not None:
            out += _fit_table(r, top)
    if detail:
        out += ["### 재현 검사 ATM±5 종목", ""]
        for r in reports:
            out += _repro_detail(r)
        out += ["### 기록 ATM±5 종목", ""]
        for r in reports:
            if r.rows is not None:
                out += _detail(r)
    return "\n".join(out)


# ── 파이프라인 요약 (--summary — 검증 수정 4 전후 비교) ─────────────────────────


SUMMARY_HEADER = "파이프라인 요약 — 합성 F·선물 기준가·ATM IV·KIS IV 폴백·순GEX·Flip"
_RESCALED_OUT = "/kis_out_of_range_rescaled"  # core.iv — 옮긴 σ 가 §6.1 밖이라 invalid
MOVE_BASES: tuple[DeltaBasis, DeltaBasis] = ("calendar", "trading")


@dataclass(frozen=True, slots=True)
class FallbackCounts:
    """§1.5 KIS IV 폴백 행 수 — 시리즈 전 종목, 파이프라인 `IvResult` 로 센다(검증 수정 3 기록).

    used: 폴백 σ 를 쓴 행(`source` kis — §1.2 제외 종목의 표시용 σ 포함). in_gex: 그중 GEX 에 든
    행. rescaled: 쓴 폴백 중 T 환산으로 σ 가 바뀐 행(`IvResult.rescaled`). dropped_gex·
    dropped_display: 옮긴 σ 가 §6.1 밖이라 invalid 가 된 행(사유 `…/kis_out_of_range_rescaled`) —
    GEX 에 들 행이었던 것과 §1.2 제외(표시용 σ)였던 것. dropped_oi: dropped_gex 행의 OI 합.
    """

    used: int
    in_gex: int
    rescaled: int
    dropped_gex: int
    dropped_display: int
    dropped_oi: int


def fallback_counts(ev: ExpiryEval) -> FallbackCounts:
    """만기 하나의 §1.5 폴백·T 환산 행 수(`FallbackCounts`)."""
    used = in_gex = rescaled = dropped_gex = dropped_display = dropped_oi = 0
    for o in ev.options:
        iv = o.iv
        if iv is None:
            continue
        if iv.source == "kis":
            used += 1
            in_gex += not o.excluded
            rescaled += iv.rescaled
        elif iv.reason is not None and iv.reason.endswith(_RESCALED_OUT):
            if iv.below_min_premium:
                dropped_display += 1
            else:
                dropped_gex += 1
                dropped_oi += o.quote.oi
    return FallbackCounts(used, in_gex, rescaled, dropped_gex, dropped_display, dropped_oi)


def fallback_gamma_rel(rows: Iterable[CompareRow]) -> tuple[int, float | None]:
    """GEX 에 든 KIS IV 폴백 행(IV estimated·자체 감마 있음) 중 KIS 감마가 있는 행의 수와 그 감마
    `자체/KIS − 1` 중앙값 — 파이프라인이 실제로 GEX 에 쓴 감마(옮기기 전 0DTE 는 −100%)."""
    rel = [r.gamma_rel for r in rows if r.iv_quality == "estimated" and r.gamma_rel is not None]
    return len(rel), statistics.median(rel) if rel else None


NoKisRow = tuple[Decimal, CallPut, int, float, float | None]


@dataclass(frozen=True, slots=True)
class FallbackGexKis:
    """GEX 에 든 KIS IV 폴백 행(시리즈 전 종목)의 자체 GEX 와 같은 행을 KIS `gama` 로 매긴 GEX
    (진단 — KIS 그릭스는 GEX 에 쓰지 않는다). 원/1%, 자체 F 로 `core.gex.option_gex`.

    n·ours·kis: KIS 감마가 있는 행의 수와 그 행들의 자체·KIS 감마 GEX 합 — 같은 행끼리라 비(`ratio`)
    가 뜻이 있다. rel_median: 그 행들의 감마 `자체/KIS − 1` 중앙값. no_kis: KIS 감마가 없는 행
    (KIS 0 = 값 없음 — 깊은 ITM 을 Δ ±1.0 으로 준 행 등) (행사가, 콜풋, OI, 자체 GEX, KIS 델타) —
    분모에 없으니 분자에서도 빼고 따로 적는다.
    """

    n: int
    ours: float
    kis: float
    rel_median: float | None
    no_kis: tuple[NoKisRow, ...]

    @property
    def ratio(self) -> float | None:
        """자체 / KIS 감마 GEX — KIS 감마가 있는 같은 행끼리. 그런 행이 없으면 None."""
        return self.ours / self.kis if self.kis else None

    @property
    def total(self) -> float:
        """GEX 에 든 폴백 행 전부의 자체 GEX 합(KIS 감마 없는 행 포함)."""
        return math.fsum((self.ours, *(r[3] for r in self.no_kis)))


def fallback_gex_vs_kis(ev: ExpiryEval, kis: Mapping[Key, KisRow]) -> FallbackGexKis:
    """만기 하나의 `FallbackGexKis` — GEX 에 든(제외 아님) 폴백(`IvResult.source` kis) 행만 본다."""
    ours: list[float] = []
    kis_gex: list[float] = []
    rel: list[float] = []
    no_kis: list[NoKisRow] = []
    F = ev.F
    for o in ev.options:
        if F is None or o.greeks is None or o.iv is None or o.iv.source != "kis":
            continue
        q = o.quote
        g = option_gex(q.cp, o.greeks.gamma, q.oi, F)
        k = kis.get((q.strike, q.cp))
        if k is None or k.gamma is None:
            no_kis.append((q.strike, q.cp, q.oi, g, None if k is None else k.delta))
            continue
        ours.append(g)
        kis_gex.append(option_gex(q.cp, k.gamma, q.oi, F))
        rel.append(o.greeks.gamma / k.gamma - 1)
    return FallbackGexKis(
        n=len(ours),
        ours=math.fsum(ours),
        kis=math.fsum(kis_gex),
        rel_median=statistics.median(rel) if rel else None,
        no_kis=tuple(no_kis),
    )


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    """시리즈(만기) 하나의 파이프라인 요약.

    s_ref: 선물 근월물 현재가(ATM 기준·기준가의 근월물). basis: 이 스냅샷에 넘긴 확정 베이시스
    (pt — 없으면 None). iv·gamma: ATM±5 자체 vs KIS(옛 §6.2 기록 — 창을 못 덮으면 None).
    fb_gamma: `fallback_gamma_rel`(ATM±5). fb_gex: `fallback_gex_vs_kis`(전 종목 — 진단). gex·flip:
    이 만기만의 §2.2 순GEX·§3.4 Flip. moves:
    §3.8 ±1σ 달력·거래시간 기준(`MOVE_BASES` 순서). repro: §6.2 재현 검사.
    """

    series: Series
    s_ref: float
    ev: ExpiryEval
    basis: Decimal | None
    atm: AtmIv
    iv: ErrorStats | None
    gamma: ErrorStats | None
    fallback: FallbackCounts
    fb_gamma: tuple[int, float | None]
    fb_gex: FallbackGexKis
    gex: Exposure
    flip: GammaFlip
    moves: tuple[ExpectedMove, ExpectedMove]
    repro: Reproduction | None


def summarize_series(
    series: Series,
    s_ref: float,
    cal: TradingCalendar,
    hist_ref: float | None = None,
    basis: Decimal | None = None,
) -> SeriesSummary:
    """시리즈 하나를 파이프라인(basis 는 §1.3 확정 베이시스)으로 평가해 요약한다."""
    ev = evaluate(series, s_ref, basis)
    rows = compare(ev, series.kis, s_ref) or ()
    atm = atm_iv(ev)
    cal_move, trade_move = (
        expected_move(ev.F, atm, series.now, cal=cal, basis=b) for b in MOVE_BASES
    )
    return SeriesSummary(
        series=series,
        s_ref=s_ref,
        ev=ev,
        basis=basis,
        atm=atm,
        iv=error_stats((r.iv_diff for r in rows if r.iv_diff is not None), IV_DIFF_TOL),
        gamma=error_stats((r.gamma_rel for r in rows if r.gamma_rel is not None), GAMMA_REL_TOL),
        fallback=fallback_counts(ev),
        fb_gamma=fallback_gamma_rel(rows),
        fb_gex=fallback_gex_vs_kis(ev, series.kis),
        gex=net_gex([ev]),
        flip=gamma_flip([ev]),
        moves=(cal_move, trade_move),
        repro=reproduce(series, s_ref, hist_ref),
    )


def scope_evals(summaries: Iterable[SeriesSummary | Skipped]) -> list[ExpiryEval]:
    """범위 합산용 만기들 — 만기 코드를 시리즈 라벨로 바꿔 넣는다.

    `core.gex.select_scope` 는 만기를 코드로 가려 같은 코드가 두 번이면 ValueError 인데, 위클리
    WKI·WKM 261001 처럼 만기일이 다른 두 시리즈가 코드(YYMMWW)를 같이 쓴다(probe_results #12).
    라벨은 전광판마다 하나라 겹치지 않는다. 만기일·종목·F 는 그대로라 합·Flip 값은 같다.
    """
    return [replace(s.ev, expiry=s.series.label) for s in summaries if isinstance(s, SeriesSummary)]


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    """스냅샷 하나의 요약. gex_0dte 는 스냅샷 시각이 세션 밖이면(귀속 거래일 없음) None."""

    name: str
    snap: Snapshot
    series: tuple[SeriesSummary | Skipped, ...]
    gex_all: Exposure
    flip_all: GammaFlip
    trade_date: date | None
    gex_0dte: Exposure | None


def summarize(
    snaps: Sequence[tuple[str, Snapshot]],
    cal: TradingCalendar,
    given: Mapping[str, date] | None = None,
) -> list[SnapshotSummary]:
    """스냅샷들(이름, 스냅샷)을 시각 순으로 요약한다.

    §1.3 선물 교차 확인(검증 수정 2): 라벨(만기)마다 품질 ok F 로 확정한 베이시스
    (`core.forward.confirm_basis`)를 다음 스냅샷에 넘긴다 — 운영 engine 이 할 일을 여기서 한다.
    근월물 코드(`atm_ref.code`)가 바뀌면 옛 근월물 기준이라 버린다. 첫 스냅샷은 베이시스가 없어
    교차 확인을 건너뛴다(`no_futures_ref`). 시리즈 실패는 `Skipped` 로 격리한다.
    """
    out: list[SnapshotSummary] = []
    basis: dict[str, Decimal] = {}
    near: str | None = None
    for name, snap in sorted(snaps, key=lambda x: x[1].started_kst):
        if snap.atm_ref.code != near:
            basis, near = {}, snap.atm_ref.code
        s_ref = snap.atm_ref.price
        hist_ref = snapshot_hist(snap)
        results: list[SeriesSummary | Skipped] = []
        for label in snap.boards:
            try:
                series = series_rows(snap, label, cal, given)
                s = summarize_series(series, s_ref, cal, hist_ref, basis.get(label))
            except Exception as e:  # 시리즈 단위 격리
                results.append(Skipped(label, _reason(e)))
                continue
            results.append(s)
            confirmed = confirm_basis(s.ev.forward, s_ref, basis.get(label))
            if confirmed is not None:
                basis[label] = confirmed
        evs = scope_evals(results)
        trade_date = state_at(snap.started_kst, cal).trade_date
        out.append(
            SnapshotSummary(
                name=name,
                snap=snap,
                series=tuple(results),
                gex_all=net_gex(evs),
                flip_all=gamma_flip(evs),
                trade_date=trade_date,
                gex_0dte=None if trade_date is None else net_gex(evs, "0dte", trade_date),
            )
        )
    return out


def _eok(v: float | None) -> str:
    return "—" if v is None else f"{to_eok(v):+,.2f}"


def _exposure_cell(x: Exposure) -> str:
    return f"{_eok(x.value)} ({x.quality}, 제외 OI {x.excluded_oi_ratio * 100:.1f}%)"


def _flip_cell(f: GammaFlip) -> str:
    if f.f_ref is None:
        return f"— (F 없음, {f.quality})"
    if f.level is None:
        return f"없음 ({f.quality})"
    dist = f.distance_pct
    return f"{f.level:,.2f} (거리 {_f(dist, '+.2f')}%, 교차 {len(f.crossings)}, {f.quality})"


def _forward_cell(ev: ExpiryEval) -> str:
    fwd = ev.forward
    why = f": {', '.join(fwd.reasons)}" if fwd.reasons else ""
    return f"{_f(fwd.F, '.2f')} ({fwd.quality}{why})"


def _reference_cell(s: SeriesSummary) -> str:
    ref = s.ev.forward.reference
    if ref is None:
        return "없음"
    if ref.basis is None:
        return f"{float(ref.price):.2f} (같은 결제월)"
    return f"{float(ref.price):.2f} (근월물 {float(ref.basis):+.2f})"


def _err_cell(e: ErrorStats | None, scale: float, spec: str, unit: str = "") -> str:
    if e is None:
        return "—"
    return (
        f"{e.n}·{e.bias * scale:{spec}}·{e.median * scale:{spec.lstrip('+')}}{unit}·"
        f"{e.within}/{e.n}"
    )


def _fb_gex_cells(x: FallbackGexKis) -> str:
    rows = x.n + len(x.no_kis)
    like = "—" if x.n == 0 else f"{_eok(x.ours)} · {_eok(x.kis)} ({x.n})"
    rel = "—" if x.rel_median is None else f"{x.rel_median * 100:+.0f}%"
    miss = "; ".join(
        f"{k} {cp} OI {oi} {_eok(g)} (KIS Δ {_f(d, '+.4f')})" for k, cp, oi, g, d in x.no_kis
    )
    total = "— (0)" if rows == 0 else f"{_eok(x.total)} ({rows})"
    return f"{total} | {like} | {_f(x.ratio, '.2f')} | {rel} | {miss or '없음'}"


def _summary_series_tables(summaries: Sequence[SeriesSummary | Skipped]) -> list[str]:
    """시리즈 표 세 개 — F·기준가·IV·감마, 폴백·순GEX·Flip, 폴백 GEX 행 vs KIS 감마(진단).
    건너뛴 시리즈는 사유 줄."""
    fwd = [
        "| 시리즈 | T(분) | F (품질: 사유) | 쓴·뺀 행사가 | F − S_ref | 기준가 (종류) "
        "| F − 기준가 | ATM IV (품질) | IV 자체 − KIS n·편향·\\|중앙\\|·≤1%p "
        "| 감마 자체/KIS − 1 n·편향·\\|중앙\\|·≤5% |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    gex = [
        "| 시리즈 | 폴백 쓴·GEX·T 환산 | 옮긴 탓 invalid GEX·표시 (GEX OI) "
        "| 폴백 GEX 행 감마 자체/KIS − 1 중앙 (n) | 순GEX 억원 (품질, 제외 OI) "
        "| Flip (거리·교차·품질) |",
        "|---|---|---|---|---|---|",
    ]
    fbk = [
        "| 시리즈 | 폴백 GEX 행 자체 GEX 억원 (행) | KIS 감마 있는 행 자체 · KIS 감마 억원 (n) "
        "| 자체/KIS | 감마 자체/KIS − 1 중앙 | KIS 감마 없는 행 (자체 억원) |",
        "|---|---|---|---|---|---|",
    ]
    for s in summaries:
        if isinstance(s, Skipped):
            fwd.append(f"| {s.label} | 건너뜀: {s.reason} |" + " |" * 8)
            gex.append(f"| {s.label} | 건너뜀: {s.reason} |" + " |" * 4)
            fbk.append(f"| {s.label} | 건너뜀: {s.reason} |" + " |" * 4)
            continue
        ev, fwd_r, a = s.ev, s.ev.forward, s.atm
        f_gap = None if ev.F is None else ev.F - s.s_ref
        atm = "—" if a.value is None else f"{a.value * 100:.2f}%"
        fwd.append(
            f"| {s.series.label} | {ev.T * MINUTES_PER_YEAR:.0f} | {_forward_cell(ev)} | "
            f"{len(fwd_r.strikes)}·{len(fwd_r.prev_session_skipped)} | {_f(f_gap, '+.2f')} | "
            f"{_reference_cell(s)} | {_f(fwd_r.futures_gap, '+.3f')} | {atm} ({a.quality}) | "
            f"{_err_cell(s.iv, 1, '+.2f')} | {_err_cell(s.gamma, 100, '+.1f', '%')} |"
        )
        fb, (n_fb, rel_fb) = s.fallback, s.fb_gamma
        fb_rel = "—" if rel_fb is None else f"{rel_fb * 100:+.0f}% ({n_fb})"
        gex.append(
            f"| {s.series.label} | {fb.used}·{fb.in_gex}·{fb.rescaled} | "
            f"{fb.dropped_gex}·{fb.dropped_display} ({fb.dropped_oi}) | {fb_rel} | "
            f"{_exposure_cell(s.gex)} | {_flip_cell(s.flip)} |"
        )
        fbk.append(f"| {s.series.label} | {_fb_gex_cells(s.fb_gex)} |")
    return [
        *fwd,
        "",
        *gex,
        "",
        "폴백 GEX 행 vs KIS 감마(진단 — GEX 에는 자체 감마만 쓴다): 비는 KIS 감마가 있는 같은 "
        "행끼리. KIS 가 gama 0(값 없음)으로 준 행은 비 밖에 따로 적는다.",
        "",
        *fbk,
    ]


def _scope_lines(s: SnapshotSummary) -> list[str]:
    out = [
        f"- 범위 all 순GEX {_exposure_cell(s.gex_all)} · Flip {_flip_cell(s.flip_all)}, "
        f"기준 F {_f(s.flip_all.f_ref, '.2f')}"
    ]
    if s.gex_0dte is not None:
        exp = ", ".join(s.gex_0dte.expiries) or "없음"
        out.append(f"- 범위 0dte({s.trade_date}: {exp}) 순GEX {_exposure_cell(s.gex_0dte)}")
    return out


def _summary_repro(summaries: Sequence[SeriesSummary | Skipped]) -> list[str]:
    """§6.2 재현 검사 통과 수(판정 — 평이 상대오차 ≤ 1%)."""
    out = [
        "| 시리즈 | Δ ATM±5 | Γ ATM±5 | Δ 전체 | Γ 전체 |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        if not isinstance(s, SeriesSummary):
            continue
        rp = s.repro
        atm = None if rp is None else rp.counts(True)
        full = None if rp is None else rp.counts(False)

        def cells(c: PassCounts | None) -> str:
            return "— | —" if c is None else f"{c.delta}/{c.delta_n} | {c.gamma}/{c.gamma_n}"

        out.append(f"| {s.series.label} | {cells(atm)} | {cells(full)} |")
    return out


def _summary_moves(summaries: Sequence[SeriesSummary | Skipped]) -> list[str]:
    """§3.8 ±1σ 달력·거래시간 기준 — F·ATM IV 는 이 요약의 파이프라인 그대로."""
    out = [
        "| 시리즈 | F | ATM IV (품질) | 남은 세션 분 | ±1σ 달력(pt) | ±1σ 거래(pt) | 거래/달력 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        if not isinstance(s, SeriesSummary):
            continue
        cal_m, trade_m = s.moves
        ratio = (
            None
            if cal_m.sigma is None or trade_m.sigma is None or cal_m.sigma == 0
            else trade_m.sigma / cal_m.sigma
        )
        atm = "—" if s.atm.value is None else f"{s.atm.value * 100:.2f}%"
        out.append(
            f"| {s.series.label} | {_f(s.ev.F, '.2f')} | {atm} ({s.atm.quality}) | "
            f"{cal_m.minutes:.0f} | {_f(cal_m.sigma, '.2f')} | {_f(trade_m.sigma, '.2f')} | "
            f"{_f(ratio, '.2f')} |"
        )
    return out


def summary_report(summaries: Sequence[SnapshotSummary]) -> str:
    """`--summary` 마크다운 — 전후 비교 표에 옮길 수치(docs/validation_greeks.md §9)."""
    out: list[str] = []
    for s in summaries:
        snap = s.snap
        out += [
            f"## {s.name} — {snap.started_kst.isoformat()} "
            f"(S_ref {snap.atm_ref.code} {snap.atm_ref.price})",
            "",
            f"### {SUMMARY_HEADER}",
            "",
            "F·기준가·F − 기준가 pt(기준가 = 근월물 선물가 + 앞 스냅샷의 확정 베이시스, 없으면 "
            "교차 확인 건너뜀). IV %p·감마 % 는 ATM±5 자체 vs KIS(옛 §6.2 기록). 폴백은 시리즈 전 "
            "종목(§1.5 — 쓴 행·그중 GEX 에 든 행·T 환산으로 σ 가 바뀐 행).",
            "",
            *_summary_series_tables(s.series),
            "",
            *_scope_lines(s),
            "",
            f"#### §6.2 재현 검사 통과 수 (|자체/KIS − 1| ≤ {REPRO_REL_TOL:.0%})",
            "",
            *_summary_repro(s.series),
            "",
            "#### §3.8 기대변동폭 — Δt 달력(÷ 365×24×60)·거래시간(÷ 252×420) 기준",
            "",
            *_summary_moves(s.series),
            "",
        ]
    return "\n".join(out)


def parse_expiry_arg(s: str) -> tuple[str, date]:
    """`--expiry LABEL=YYYYMMDD` 하나 → (라벨, 만기일). 형식이 틀리면 ValueError."""
    label, sep, ymd = s.partition("=")
    _code(label)  # 라벨 형식
    if not sep or len(ymd) != 8 or not ymd.isdigit():
        raise ValueError(f"--expiry 는 LABEL=YYYYMMDD: {s!r}")
    return label, _ymd(ymd)


def _main_summary(paths: Sequence[Path], cal: TradingCalendar, given: Mapping[str, date]) -> int:
    """`--summary`: 읽은 스냅샷을 한꺼번에(시각 순) 요약한다. 읽지 못한 스냅샷은 사유 줄을 쓰고
    나머지로 계속한다(종료 코드 1)."""
    rc = 0
    snaps: list[tuple[str, Snapshot]] = []
    for p in paths:
        try:
            snaps.append((p.parent.name, load_snapshot(p)))
        except Exception as e:  # 스냅샷 단위 격리
            sys.stdout.write(f"## {p} — 읽지 못함: {_reason(e)}\n\n")
            rc = 1
    sys.stdout.write(summary_report(summarize(snaps, cal, given)) + "\n")
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    """스냅샷마다 보고서를 쓴다(`--summary` 면 파이프라인 요약). 읽지 못한 스냅샷이 있으면
    (나머지는 계속) 1."""
    ap = argparse.ArgumentParser(description="PLAN §6.2 KIS 관례 재현 검사 + 자체 vs KIS 기록")
    ap.add_argument("snapshots", nargs="+", type=Path, help="chain_snapshot.json 경로")
    ap.add_argument("--top", type=int, default=3, help="관례 후보 표에 보일 상위 개수")
    ap.add_argument("--detail", action="store_true", help="ATM±5 종목별 표도 쓴다")
    ap.add_argument(
        "--expiry",
        action="append",
        type=parse_expiry_arg,
        default=[],
        metavar="LABEL=YYYYMMDD",
        help="시리즈 만기일(KIS futs_last_tr_date). 스냅샷·내장 표에 없는 위클리에 준다(반복 가능)",
    )
    ap.add_argument(
        "--summary",
        action="store_true",
        help="파이프라인 요약만 쓴다(전후 비교용 — F·기준가·ATM IV·폴백·순GEX·Flip·재현·§3.8). "
        "스냅샷을 시각 순으로 돌며 만기별 확정 베이시스를 다음 스냅샷에 넘긴다",
    )
    args = ap.parse_args(argv)
    given: dict[str, date] = dict(args.expiry)
    cal = TradingCalendar.default()
    if args.summary:
        return _main_summary(args.snapshots, cal, given)
    rc = 0
    for p in args.snapshots:
        try:
            text = report(p, cal, args.top, args.detail, given)
        except Exception as e:  # 스냅샷 단위 격리 — 다른 스냅샷은 계속 본다
            text = f"## {p} — 읽지 못함: {_reason(e)}"
            rc = 1
        sys.stdout.write(text + "\n\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

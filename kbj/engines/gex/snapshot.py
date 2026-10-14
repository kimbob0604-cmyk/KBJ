"""체인 스냅샷 읽기 — GX `scripts/validate_greeks.py` 의 입력 부분(P7 FND-C, p7_design §1.2).

녹화 스냅샷(`chain_snapshot.json` — 전광판 `boards`·단건 보강 `fills`·`atm_ref`)을 pydantic 으로
검증하고(`Snapshot`), 만기 하나를 `OptionQuote`·KIS 값으로 바꾼다(`series_rows`). 골든(`kbj.engines.
gex.golden`)·체인 부분집합·GX 검증 스크립트(legacy `scripts/validate_greeks.py` — 이 모듈을 다시
내보낸다)가 같은 정의를 쓴다. 파일을 읽지 않는다 — 바이트·문자열은 부르는 쪽이 읽어 넘긴다
(`Snapshot.model_validate_json`).

`kbj/core/gex` 가 아니라 여기 둔다: KIS 그릭스 필드(`gama`·`delta_val`·`hist_vltl`·`hts_thpr`)를
검증 기록용으로 읽는데, GEX 순수 계산(core)에는 그 이름이 없어야 한다(KIS 그릭스는 GEX 입력이
아니다 — metrics §1.7, `test_gex_never_reads_kis_greeks`).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from kbj.core.calendar import TradingCalendar, monthly_expiry
from kbj.core.gex.gex import CallPut, OptionQuote

# 만기일 — KIS 단건 `futs_last_tr_date` 실측(probe_results #19 14:42 런). 라벨(YYMMWW·YYYYMM)은 한
# 시리즈를 가리키므로 다른 날 스냅샷에도 맞다. 순서: 스냅샷 fills → `--expiry` → 이 표 → 월물 계산.
# 위클리는 계산하지 않는다(코드 WW 의 뜻이 실측되지 않았다) — 모르면 그 시리즈만 건너뛴다
KIS_EXPIRY: Mapping[str, date] = {
    "MONTH:202610": date(2026, 10, 8),
    "WKM:260904": date(2026, 9, 28),
    "WKI:261001": date(2026, 10, 1),
    "WKM:261001": date(2026, 10, 6),
}

ExpirySource = Literal["kis", "arg", "computed"]  # arg: --expiry


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


class OptionRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    acpr: str
    hts_otst_stpl_qty: int = Field(ge=0)
    acml_vol: KisInt = None
    hts_ints_vltl: KisNum = None
    gama: KisNum = None
    delta_val: KisNum = None
    hts_thpr: KisNum = None
    hist_vltl: KisNum = None


class BoardRow(OptionRow):
    """전광판 콜(`output1`)·풋(`output2`) 한 행. invl_val: KIS 내재가치(pt) — 현물 S 가 드러난다."""

    optn_bidp: str = ""
    optn_askp: str = ""
    optn_prpr: str = ""
    invl_val: KisNum = None


class FillRow(OptionRow):
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


# ── 시리즈 → OptionQuote ─────────────────────────────────────────────────────


def positive(v: float | None) -> float | None:
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
    def of(cls, r: OptionRow, rmnn: int | None = None) -> KisRow:
        return cls(
            iv_pct=positive(r.hts_ints_vltl),
            gamma=positive(r.gama),
            delta=r.delta_val,
            hist_pct=positive(r.hist_vltl),
            thpr=positive(r.hts_thpr),
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


def label_code(label: str) -> tuple[str, str]:
    kind, _, code = label.partition(":")
    if not kind or len(code) != 6 or not code.isdigit():
        raise ValueError(f"전광판 라벨은 '<구분>:<6자리 만기 코드>': {label!r}")
    return kind, code


def ymd(s: str) -> date:
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
    _, code = label_code(label)
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
    dates = {ymd(d) for d in fill_dates if d}
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
    kind, code = label_code(label)
    if kind == "MONTH":
        return monthly_expiry(int(code[:4]), int(code[4:]), cal), "computed"
    raise ValueError(f"{label}: 만기일을 모른다 — --expiry {label}=YYYYMMDD")


def series_rows(
    snap: Snapshot, label: str, cal: TradingCalendar, given: Mapping[str, date] | None = None
) -> Series:
    """전광판 한 시리즈(+ 같은 만기의 단건 보강 행)를 OptionQuote·KisRow 로. 겹치면 전광판 행.

    given: `--expiry` 로 받은 만기일(라벨 → 날짜).
    """
    _, code = label_code(label)
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

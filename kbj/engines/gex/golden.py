"""GEX 골든 — 비교·허용오차·체인 부분집합·코어 파이프라인 골든 내용(P7 FND-C, p7_design §1.2).

GX `scripts/make_golden.py` 의 core·chain-subset 부분에서 파일을 읽고 쓰지 않는 것만 옮겼다
(수식·허용·표기 그대로). 파일 읽기·쓰기·CLI 는 `kbj.services.engine.gex_golden`(경로 상수·
`core_golden()`·`main`). raw 녹화 골든은 ING(`kbj.services.recorder.golden`).

- 비교: `compare_golden`(JSON 경로별 차이 — float 는 키 단위 접미사의 허용 `TOLERANCES`,
  단위를 모르는 float 는 `GoldenSchemaError`), `check_schema`, `failure_report`, `dump_golden`
- 체인 부분집합: `chain_subset`(전체 체인 스냅샷 → 작은 fixture — 고른 행·필드만), `dump_chain`
- 코어 골든: `core_golden_of`(읽어 둔 스냅샷들 → 골든 dict). 파이프라인은 `kbj.engines.gex.snapshot.
  series_rows` → `kbj.core.gex.gex.evaluate_expiry` → 범위 all·nearest·0dte 합산·레벨, 만기별
  ATM IV·±1σ, 상위 레벨·만기별 감마(명세 `docs/metrics.md` §G)
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kbj.core.calendar import TradingCalendar, expiry_at, state_at
from kbj.core.gex.chain import atm_strike, atm_window
from kbj.core.gex.forward import ForwardResult, confirm_basis
from kbj.core.gex.gex import (
    ExpiryEval,
    Exposure,
    OptionEval,
    dex,
    evaluate_expiry,
    net_gex,
    option_gex,
    select_scope,
    strike_gex,
)
from kbj.core.gex.iv import IvResult
from kbj.core.gex.levels import (
    AtmIv,
    DeltaBasis,
    ExpectedMove,
    GammaFlip,
    Wall,
    abs_gamma_strike,
    atm_iv,
    call_wall,
    expected_move,
    gamma_by_expiry,
    gamma_flip,
    put_wall,
    top_levels_in_range,
)
from kbj.engines.gex.snapshot import Series, Snapshot, series_rows


class InputError(ValueError):
    """입력 파일이 틀렸다 — 종료 코드 2. 문구에 입력 값을 싣지 않는다."""


# ── chain-subset: 전체 체인 스냅샷 → 작은 fixture ─────────────────────────────

# 전광판 행(output1 콜·output2 풋)·단건 보강 행에서 옮길 필드 — core 파이프라인·§6.2 재현 검사가
# 읽는 것
BOARD_FIELDS: tuple[str, ...] = (
    "acpr",
    "optn_bidp",
    "optn_askp",
    "optn_prpr",
    "hts_otst_stpl_qty",
    "acml_vol",
    "hts_ints_vltl",
    "gama",
    "delta_val",
    "hts_thpr",
    "hist_vltl",
    "invl_val",
)
FILL_FIELDS: tuple[str, ...] = (
    "acpr",
    "futs_prpr",
    "hts_otst_stpl_qty",
    "acml_vol",
    "hts_ints_vltl",
    "gama",
    "delta_val",
    "hts_thpr",
    "hist_vltl",
    "futs_last_tr_date",
    "hts_rmnn_dynu",
)


@dataclass(frozen=True)
class Fixed:
    """이 행사가(KIS `acpr` 문자열 그대로)만 — 콜·풋 둘 다."""

    strikes: tuple[str, ...]


@dataclass(frozen=True)
class AroundAtm:
    """S_ref 기준 ATM(`core.chain.atm_strike`, 그 전광판 행사가 중) 둘레 행사가 칸 (아래, 위) —
    (-2, 2) 는 ATM±2. 콜·풋 따로."""

    calls: tuple[int, int]
    puts: tuple[int, int]


BoardPick = Fixed | AroundAtm

# 2026-09-28 14:27 fixture 를 만든 규칙 — 월물 202610 은 전광판이 최고 행사가부터 잘려(#11) 첫 행과
# 먼 OTM 한 행만, ATM 은 보강 행. 202611 은 잘린 전광판의 ATM 쪽 끝 3행(보강 행 없음 — F 없음).
# 0DTE 는 콜 ATM±2·풋 ATM±3, WKM 261001 은 ATM 과 한 칸 아래(전 세션 가격 — F 없음)
SUBSET: Mapping[str, BoardPick] = {
    "MONTH:202610": Fixed(("1595.00", "1470.00")),
    "MONTH:202611": Fixed(("1352.50", "1350.00", "1347.50")),
    "WKM:260904": AroundAtm((-2, 2), (-3, 3)),
    "WKM:261001": AroundAtm((-1, 0), (-1, 0)),
}
FILL_SERIES: tuple[str, ...] = ("C 202610", "P 202610")
FILL_WINDOW = 5  # 보강 행 ATM±5 (S_ref 기준, 보강 행 행사가 중)


def _aware_text(v: str) -> str:
    try:
        ts = datetime.fromisoformat(v)
    except ValueError as e:
        raise ValueError("ISO 시각이 아니다") from e
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive 시각은 받지 않는다")
    return v  # 원문 표기 그대로 옮긴다


class _AtmRefIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    price: float = Field(gt=0)


class _BoardIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts_kst: str
    output1: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    output2: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])

    _aware = field_validator("ts_kst")(_aware_text)


class _FillIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts_kst: str
    series: str
    rt_cd: str = ""
    row: dict[str, Any] | None = None

    _aware = field_validator("ts_kst")(_aware_text)


class _ChainIn(BaseModel):
    """probe `chain_snapshot.json` 에서 자르는 데 쓰는 부분(나머지는 읽지도 옮기지도 않는다)."""

    model_config = ConfigDict(extra="ignore")

    started_kst: str
    atm_ref: _AtmRefIn
    boards: dict[str, _BoardIn]
    fills: list[_FillIn] = Field(default_factory=list[_FillIn])

    _aware = field_validator("started_kst")(_aware_text)


def _strike(row: Mapping[str, Any]) -> Decimal:
    v = row.get("acpr")
    try:
        k = Decimal(str(v).replace(",", ""))
    except InvalidOperation as e:
        raise InputError("행사가(acpr)가 숫자가 아니다") from e
    if not k.is_finite() or k <= 0:
        raise InputError("행사가(acpr)가 양수가 아니다")
    return k


def _pick(row: Mapping[str, Any], fields: Sequence[str], where: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fields:
        v = row.get(f)
        if not isinstance(v, str):
            raise InputError(f"{where}: 필드 {f} 가 없거나 문자열이 아니다")
        out[f] = v
    return out


def _around(strikes: Sequence[Decimal], atm: Decimal, span: tuple[int, int]) -> set[Decimal]:
    ks = sorted(set(strikes))
    i = ks.index(atm)
    return set(ks[max(0, i + span[0]) : i + span[1] + 1])


def _board_subset(label: str, board: _BoardIn, pick: BoardPick, s_ref: float) -> dict[str, Any]:
    sides = (("output1", board.output1), ("output2", board.output2))
    if isinstance(pick, Fixed):
        want = {Decimal(k) for k in pick.strikes}
        keep = {"output1": want, "output2": want}
        for name, rows in sides:  # 없는 행사가를 조용히 빼면 짧은 fixture 가 exit 0 으로 나온다
            present = {_strike(r) for r in rows}
            missing = [k for k in pick.strikes if Decimal(k) not in present]
            if missing:
                raise InputError(f"{label} {name}: 행사가 {missing} 가 전광판에 없다")
    else:
        strikes = [_strike(r) for _, rows in sides for r in rows]
        if not strikes:
            raise InputError(f"{label}: 전광판이 비었다")
        atm = atm_strike(strikes, s_ref)
        keep = {
            "output1": _around(strikes, atm, pick.calls),
            "output2": _around(strikes, atm, pick.puts),
        }
    out: dict[str, Any] = {"ts_kst": board.ts_kst}
    for name, rows in sides:
        out[name] = [
            _pick(r, BOARD_FIELDS, f"{label} {name}") for r in rows if _strike(r) in keep[name]
        ]
    return out


def _fills_subset(fills: Sequence[_FillIn], s_ref: float) -> list[dict[str, Any]]:
    ok = [f for f in fills if f.series in FILL_SERIES and f.rt_cd == "0" and f.row is not None]
    if not ok:
        return []
    strikes = [_strike(f.row) for f in ok if f.row is not None]
    keep = set(atm_window(strikes, s_ref, FILL_WINDOW))
    out: list[dict[str, Any]] = []
    for f in ok:
        if f.row is None or _strike(f.row) not in keep:
            continue
        row = _pick(f.row, FILL_FIELDS, f"보강 {f.series}")
        out.append({"ts_kst": f.ts_kst, "series": f.series, "rt_cd": f.rt_cd, "row": row})
    return out


def chain_subset(
    full: Mapping[str, Any], subset: Mapping[str, BoardPick] = SUBSET
) -> dict[str, Any]:
    """전체 체인 스냅샷(dict) → 작은 fixture(dict). 시리즈 순서는 `subset` 순서, 행은 원문 순서.

    `subset` 의 시리즈가 스냅샷에 없거나, 고정 행사가(`Fixed`)가 그 전광판 콜·풋 어느 쪽에라도
    없거나, 행 필드가 빠지면 InputError.
    """
    try:
        snap = _ChainIn.model_validate(full)
    except ValidationError as e:
        errs = e.errors(include_input=False, include_url=False)
        raise InputError(f"체인 스냅샷 형식이 아니다: {errs}") from None
    s_ref = snap.atm_ref.price
    boards: dict[str, Any] = {}
    for label, pick in subset.items():
        board = snap.boards.get(label)
        if board is None:
            raise InputError(f"스냅샷에 전광판 {label} 이 없다")
        boards[label] = _board_subset(label, board, pick, s_ref)
    return {
        "started_kst": snap.started_kst,
        "atm_ref": {"code": snap.atm_ref.code, "price": snap.atm_ref.price},
        "boards": boards,
        "fills": _fills_subset(snap.fills, s_ref),
    }


def _line(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)


def dump_chain(small: Mapping[str, Any]) -> str:
    """작은 fixture 표기 — 한 칸 들여쓰기, 행 하나가 한 줄(diff 가 행 단위로 읽힌다)."""
    out = ["{", f' "started_kst": {_line(small["started_kst"])},']
    out.append(f' "atm_ref": {_line(small["atm_ref"])},')
    out.append(' "boards": {')
    labels = list(small["boards"])
    for i, label in enumerate(labels):
        board = small["boards"][label]
        out.append(f"  {_line(label)}: {{")
        out.append(f'   "ts_kst": {_line(board["ts_kst"])},')
        for name, tail in (("output1", ","), ("output2", "")):
            rows = board[name]
            if not rows:
                out.append(f'   "{name}": []{tail}')
                continue
            out.append(f'   "{name}": [')
            out += [f"    {_line(r)}{',' if j < len(rows) - 1 else ''}" for j, r in enumerate(rows)]
            out.append(f"   ]{tail}")
        out.append("  }" + ("," if i < len(labels) - 1 else ""))
    out.append(" },")
    fills = small["fills"]
    if fills:
        out.append(' "fills": [')
        out += [f"  {_line(f)}{',' if j < len(fills) - 1 else ''}" for j, f in enumerate(fills)]
        out.append(" ]")
    else:
        out.append(' "fills": []')
    out.append("}")
    return "\n".join(out) + "\n"


# ── 골든 비교 (허용오차) ─────────────────────────────────────────────────────

# float 키의 단위(키 이름 끝 `_<단위>` 또는 키 자체) → (상대, 절대) 허용. 단위를 모르는 float 는
# 비교 오류(`GoldenSchemaError`) — 새 필드를 넣을 때 단위(허용)를 정하게 한다. float 가 아닌 값
# (문자열 — 행사가·가격 Decimal 포함, 정수, bool, None)은 같아야 한다. 상대 1e-9 는 같은 입력의
# 같은 계산이 플랫폼(libm·vollib)마다 낼 수 있는 끝자리 차이는 넘기고 뜻 있는 변화는 잡는 폭이다
TOLERANCES: Mapping[str, tuple[float, float]] = {
    "pt": (1e-9, 1e-9),  # 가격·행사가·F·Flip — pt
    "won": (1e-9, 1e-3),  # GEX(원/1%)·DEX(원) — 1e11 규모, 0 근처는 0.001원
    "years": (1e-9, 1e-15),  # T·T_KIS·Δt
    "minutes": (1e-9, 1e-9),
    "ratio": (1e-9, 1e-12),  # 0~1 비율(제외 OI)
    "pct": (1e-9, 1e-9),  # % (전환점 거리)
    "sigma": (1e-9, 1e-12),  # 연율 소수 IV
    "delta": (1e-9, 1e-12),
    "gamma": (1e-9, 1e-15),  # 1/pt
}


class GoldenSchemaError(InputError):
    """골든 JSON 에 허용을 모르는 float 키가 있다 — 종료 코드 2(비교도 쓰기도 하지 않는다)."""


def tolerance(key: str | None) -> tuple[float, float]:
    """키의 (상대, 절대) 허용. 키가 단위 이름이거나 `_<단위>` 로 끝나야 한다."""
    if key is not None:
        unit = key if key in TOLERANCES else key.rsplit("_", 1)[-1]
        if unit in TOLERANCES:
            return TOLERANCES[unit]
    raise GoldenSchemaError(f"float 키 {key!r} 의 단위(허용)를 모른다 — TOLERANCES 에 정한다")


def check_schema(golden: object) -> None:
    """골든의 float 키마다 허용(단위)이 있는지 — 없으면 GoldenSchemaError. 쓰기 전에 부른다."""
    compare_golden(golden, golden)


def compare_golden(expected: object, actual: object) -> list[str]:
    """골든(expected)과 새 값(actual)의 차이 — JSON 경로와 값. 같으면 빈 목록."""
    out: list[str] = []
    _walk(expected, actual, "$", None, out)
    return out


def _walk(e: object, a: object, path: str, key: str | None, out: list[str]) -> None:
    if isinstance(e, dict) and isinstance(a, dict):
        de = cast(dict[str, object], e)
        da = cast(dict[str, object], a)
        for k in sorted(de.keys() | da.keys()):
            if k not in da:
                out.append(f"{path}.{k}: 골든에만 있다")
            elif k not in de:
                out.append(f"{path}.{k}: 새 값에만 있다")
            else:
                _walk(de[k], da[k], f"{path}.{k}", k, out)
        return
    if isinstance(e, list) and isinstance(a, list):
        le = cast(list[object], e)
        la = cast(list[object], a)
        if len(le) != len(la):
            out.append(f"{path}: 길이 {len(le)} → {len(la)}")
        for i, (x, y) in enumerate(zip(le, la, strict=False)):
            _walk(x, y, f"{path}[{i}]", key, out)
        return
    if type(e) is float and type(a) is float:
        rel, absolute = tolerance(key)
        if not math.isclose(e, a, rel_tol=rel, abs_tol=absolute):
            out.append(f"{path}: {e!r} → {a!r} (허용 상대 {rel:g}·절대 {absolute:g})")
        return
    if type(e) is float or type(a) is float:
        tolerance(key)  # float 가 끼면 단위부터 — 모르면 GoldenSchemaError
    if type(e) is not type(a) or e != a:
        out.append(f"{path}: {e!r} → {a!r}")


def failure_report(diffs: Sequence[str], regenerate: str, limit: int = 30) -> str:
    """시험 실패 문구 — 차이(앞 limit 개)와 다시 만드는 명령."""
    lines = [f"골든과 다르다 — {len(diffs)}곳:", *(f"  {d}" for d in diffs[:limit])]
    if len(diffs) > limit:
        lines.append(f"  … {len(diffs) - limit}곳 더")
    lines += [
        "의도한 변경(수식·기본값·파서·fixture)이면 다시 만들고 diff 를 검토해 함께 커밋한다:",
        f"    {regenerate}",
    ]
    return "\n".join(lines)


def dump_golden(obj: object) -> str:
    """골든 JSON 표기 — 한 칸 들여쓰기, 끝 줄바꿈."""
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


# ── core: 코어 파이프라인 골든 ────────────────────────────────────────────────


@dataclass(frozen=True)
class Derived:
    """작은 fixture(KBJ P1 부터 합성)의 시각(started_kst·전광판·보강 `ts_kst`)만 `shift`(분
    단위)만큼 옮긴 파생 입력. 가격·OI·KIS 값은 원본 그대로라 옮긴 시각의 시장이 아니다 — 값의 뜻은
    없고, 실측 체인 스냅샷이 없는 시각의 캘린더·범위 갈래를 파이프라인 전체로 고정하려고 쓴다."""

    source: Path
    shift: timedelta
    note: str

    def __post_init__(self) -> None:
        if self.shift % timedelta(minutes=1):
            raise ValueError(f"shift 는 분 단위여야 한다: {self.shift}")

    @property
    def shift_minutes(self) -> int:
        return self.shift // timedelta(minutes=1)


CoreInput = Path | Derived
PROFILE_STRIDE = 20  # Flip 격자(0.25pt)에서 골든에 남길 표본 간격 — 20칸 = 5pt, 끝점 포함
MOVE_BASES: tuple[DeltaBasis, ...] = ("calendar", "trading")


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _iv_golden(iv: IvResult | None) -> dict[str, Any] | None:
    if iv is None:
        return None
    return {
        "sigma": iv.sigma,
        "quality": iv.quality,
        "source": iv.source,
        "reason": iv.reason,
        "below_min_premium": iv.below_min_premium,
        "rescaled": iv.rescaled,
        "t_kis_years": iv.t_kis,
    }


def _option_golden(o: OptionEval, F: float | None) -> dict[str, Any]:
    q, g = o.quote, o.greeks
    return {
        "strike": str(q.strike),
        "cp": q.cp,
        "source": q.source,
        "oi": q.oi,
        "volume": q.volume,
        "price": _dec(o.choice.price),
        "price_kind": o.choice.kind,
        "prev_session": o.choice.prev_session,
        "iv": _iv_golden(o.iv),
        "excluded": o.reason,
        "quality": o.quality,
        "quality_reasons": list(o.quality_reasons),
        "delta": None if g is None else g.delta,
        "gamma": None if g is None else g.gamma,
        "gex_won": None if g is None or F is None else option_gex(q.cp, g.gamma, q.oi, F),
    }


def _forward_golden(fwd: ForwardResult) -> dict[str, Any]:
    ref = fwd.reference
    return {
        "F_pt": fwd.F,
        "quality": fwd.quality,
        "reasons": list(fwd.reasons),
        "notes": list(fwd.notes),
        "atm": _dec(fwd.atm),
        "strikes": [str(k) for k in fwd.strikes],
        "prev_session_skipped": [str(k) for k in fwd.prev_session_skipped],
        "reference": None
        if ref is None
        else {
            "kind": ref.kind,
            "price_pt": float(ref.price),
            "basis_pt": None if ref.basis is None else float(ref.basis),
        },
        "futures_gap_pt": fwd.futures_gap,
    }


def _exposure_golden(x: Exposure) -> dict[str, Any]:
    return {
        "value_won": x.value,
        "quality": x.quality,
        "excluded_oi_ratio": x.excluded_oi_ratio,
        "expiries": list(x.expiries),
    }


def _wall_golden(w: Wall) -> dict[str, Any]:
    return {"strike": _dec(w.strike), "value_won": w.value, "quality": w.quality}


def _flip_golden(f: GammaFlip) -> dict[str, Any]:
    idx = list(range(0, len(f.profile), PROFILE_STRIDE))
    if f.profile and idx[-1] != len(f.profile) - 1:
        idx.append(len(f.profile) - 1)
    return {
        "level_pt": f.level,
        "multi_cross": f.multi_cross,
        "crossings_pt": list(f.crossings),
        "f_ref_pt": f.f_ref,
        "distance_pct": f.distance_pct,
        "quality": f.quality,
        "grid_points": len(f.profile),
        "profile_sample": [{"x_pt": f.profile[i][0], "gex_won": f.profile[i][1]} for i in idx],
    }


def _move_golden(m: ExpectedMove) -> dict[str, Any]:
    return {
        "minutes": m.minutes,
        "dt_years": m.dt,
        "sigma_pt": m.sigma,
        "lower_pt": m.lower,
        "upper_pt": m.upper,
        "quality": m.quality,
    }


def _atm_golden(a: AtmIv) -> dict[str, Any]:
    return {
        "sigma": a.value,
        "quality": a.quality,
        "strikes": [str(k) for k in a.strikes],
        "reasons": list(a.reasons),
    }


def expiry_golden(
    series: Series, ev: ExpiryEval, basis: Decimal | None, cal: TradingCalendar
) -> dict[str, Any]:
    atm = atm_iv(ev)
    table = strike_gex(ev)
    moves = {b: expected_move(ev.F, atm, series.now, cal=cal, basis=b) for b in MOVE_BASES}
    return {
        "label": series.label,
        "expiry": series.expiry,
        "expiry_date": series.expiry_date.isoformat(),
        "expiry_source": series.expiry_source,
        "now_kst": series.now.isoformat(),
        "T_years": ev.T,
        "T_kis_years": ev.T_kis,
        "basis_in_pt": None if basis is None else float(basis),
        "forward": _forward_golden(ev.forward),
        "quality": ev.quality,
        "options": [_option_golden(o, ev.F) for o in ev.options],
        "strike_gex": {
            "quality": table.quality,
            "excluded_oi_ratio": table.excluded_oi_ratio,
            "rows": [
                {"strike": str(r.strike), "call_won": r.gex_call, "put_won": r.gex_put}
                for r in table.rows
            ],
        },
        "net_gex": _exposure_golden(net_gex([ev])),
        "atm_iv": _atm_golden(atm),
        "expected_move": {b: _move_golden(m) for b, m in moves.items()},
    }


def _scope_golden(chosen: Sequence[ExpiryEval]) -> dict[str, Any]:
    """범위(호출 쪽이 `select_scope` 로 고른 만기들) 하나의 합산·레벨."""
    return {
        "expiries": [e.expiry for e in chosen],
        "net_gex": _exposure_golden(net_gex(chosen)),
        "dex": _exposure_golden(dex(chosen)),
        "call_wall": _wall_golden(call_wall(chosen)),
        "put_wall": _wall_golden(put_wall(chosen)),
        "abs_gamma": _wall_golden(abs_gamma_strike(chosen)),
        "flip": _flip_golden(gamma_flip(chosen)),
    }


def _top_levels_golden(
    evs: Sequence[ExpiryEval], nearest: Sequence[ExpiryEval], now: datetime, cal: TradingCalendar
) -> dict[str, Any] | None:
    """§3.9 — 최근접 만기의 ATM IV·달력 기준 ±1σ 로 범위 all 의 상위 레벨."""
    if not nearest:
        return None
    ev = nearest[0]
    move = expected_move(ev.F, atm_iv(ev), now, cal=cal)
    top = top_levels_in_range(evs, move)
    return {
        "from": ev.expiry,
        "basis": move.basis,
        "lower_pt": top.lower,
        "upper_pt": top.upper,
        "quality": top.quality,
        "levels": [{"strike": str(r.strike), "gex_won": r.gex} for r in top.levels],
    }


def shift_snapshot(snap: Snapshot, shift: timedelta) -> Snapshot:
    """스냅샷의 시각(started_kst·전광판·보강 `ts_kst`)만 옮긴다 — 나머지는 그대로(`Derived`)."""
    return snap.model_copy(
        update={
            "started_kst": snap.started_kst + shift,
            "boards": {
                k: b.model_copy(update={"ts_kst": b.ts_kst + shift}) for k, b in snap.boards.items()
            },
            "fills": [f.model_copy(update={"ts_kst": f.ts_kst + shift}) for f in snap.fills],
        }
    )


def _derived_golden(d: Derived | None) -> dict[str, Any] | None:
    return None if d is None else {"shift_minutes": d.shift_minutes, "note": d.note}


ExpiryGolden = Callable[[Series, ExpiryEval, Decimal | None, TradingCalendar], dict[str, Any]]


def snapshot_golden(
    rel: str,
    snap: Snapshot,
    cal: TradingCalendar,
    basis: dict[str, Decimal],
    derived: Derived | None = None,
    *,
    render_expiry: ExpiryGolden = expiry_golden,
) -> dict[str, Any]:
    """스냅샷 하나. basis(라벨 → 확정 베이시스)는 이 스냅샷의 ok F 로 갱신한다(다음 스냅샷용).

    rel: 골든에 적을 입력 경로(저장소 기준 — 부르는 쪽이 정한다). render_expiry: 만기 하나의
    골든 표기(기본 `expiry_golden` — 시험이 실패 격리를 보려고 바꿔 끼운다).

    만기 시각(만기일 15:20 KST)이 지난 만기는 평가하지 않고 `expired` 에 남긴다 — §1.4·§2.2 "만기
    지난 만기는 상위에서 뺀다"(운영 engine 몫을 흉내, 판정 시각은 그 전광판 조회 시각).
    """
    s_ref = snap.atm_ref.price
    info = state_at(snap.started_kst, cal)
    evs: list[ExpiryEval] = []
    expiries: list[dict[str, Any]] = []
    expired: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for label in snap.boards:
        b = basis.get(label)
        try:
            series = series_rows(snap, label, cal)
            if series.now >= expiry_at(series.expiry_date):
                expired.append({"label": label, "expiry_date": series.expiry_date.isoformat()})
                continue
            ev = evaluate_expiry(series.quotes, series.now, s_ref, forward_basis=b)
            expiry = render_expiry(series, ev, b, cal)
        except Exception as e:  # 시리즈 단위 격리 — 사유를 골든에 남긴다
            skipped.append({"label": label, "reason": f"{type(e).__name__}: {e}"[:200]})
            continue
        # 범위 합산은 만기 코드 대신 라벨로 — WKI·WKM 261001 처럼 코드가 겹친다(validation §9.6)
        evs.append(replace(ev, expiry=label))
        expiries.append(expiry)
        confirmed = confirm_basis(ev.forward, s_ref, b)
        if confirmed is not None:
            basis[label] = confirmed
    td = info.trade_date
    scopes: dict[str, Any] = {
        "all": _scope_golden(select_scope(evs, "all")),
        "nearest": _scope_golden(select_scope(evs, "nearest")),
        "0dte": None if td is None else _scope_golden(select_scope(evs, "0dte", td)),
    }
    nearest = select_scope(evs, "nearest")
    return {
        "input": rel,
        "derived": _derived_golden(derived),
        "started_kst": snap.started_kst.isoformat(),
        "s_ref": {"code": snap.atm_ref.code, "price_pt": s_ref},
        "state": info.state.value,
        "trade_date": None if td is None else td.isoformat(),
        "session": info.session,
        "expiries": expiries,
        "expired": expired,
        "skipped": skipped,
        "scopes": scopes,
        "top_levels": _top_levels_golden(evs, nearest, snap.started_kst, cal),
        "gamma_by_expiry": [
            {
                "expiry": g.expiry,
                "expiry_date": g.expiry_date.isoformat(),
                "value_won": g.value,
                "quality": g.quality,
                "excluded_oi_ratio": g.excluded_oi_ratio,
            }
            for g in gamma_by_expiry(evs)
        ],
    }


ABOUT_CORE = (
    "코어 파이프라인 골든(PLAN §6.3) — 비교 tests/golden/gex/test_core_golden.py, "
    "허용오차 kbj/engines/gex/golden.py TOLERANCES. 손으로 고치지 않는다"
)


@dataclass(frozen=True)
class LoadedInput:
    """읽어 둔 입력 하나 — rel·sha256 은 골든 `inputs` 에 적는 값, snap 은 `derived` 를 이미 적용한
    스냅샷(파일 읽기는 `kbj.services.engine.gex_golden.core_golden`)."""

    rel: str
    sha256: str
    snap: Snapshot
    derived: Derived | None = None


def core_golden_of(
    inputs: Sequence[LoadedInput],
    cal: TradingCalendar,
    regenerate: str,
    *,
    render_expiry: ExpiryGolden = expiry_golden,
) -> dict[str, Any]:
    """체인 스냅샷들을 시각 순으로 코어 파이프라인에 넣은 결과 — 골든 JSON 의 내용.

    파이프라인: 스냅샷 → `kbj.engines.gex.snapshot.series_rows`(전광판 + 단건 보강 → OptionQuote)
    → `kbj.core.gex.gex.evaluate_expiry`(§1.1 가격·§1.3 합성 F·§1.4 T·§1.5 IV·§1.6 그릭스) → 범위
    all·nearest·0dte 순GEX·DEX·콜월·풋월·절대감마·Flip(§2·§3), 만기별 ATM IV·±1σ 두 기준(§3.7·
    §3.8), 상위 레벨·만기별 감마(§3.9·§3.10). 만기별 확정 베이시스(`confirm_basis`)는 앞 스냅샷의
    ok F 로 만들어 다음 스냅샷에 넘긴다(§1.3 선물 교차 확인 — 운영 engine 몫을 흉내). 근월물
    코드가 바뀌면 버린다.
    """
    ordered = sorted(inputs, key=lambda x: x.snap.started_kst)
    out: list[dict[str, Any]] = []
    basis: dict[str, Decimal] = {}
    near: str | None = None
    for inp in ordered:
        if inp.snap.atm_ref.code != near:
            basis, near = {}, inp.snap.atm_ref.code
        out.append(
            snapshot_golden(inp.rel, inp.snap, cal, basis, inp.derived, render_expiry=render_expiry)
        )
    inputs_out: list[dict[str, Any]] = []
    for inp in ordered:
        entry: dict[str, Any] = {"path": inp.rel, "sha256": inp.sha256}
        if inp.derived is not None:
            entry["derived"] = _derived_golden(inp.derived)
        inputs_out.append(entry)
    return {
        "_about": ABOUT_CORE,
        "_regenerate": regenerate,
        "inputs": inputs_out,
        "snapshots": out,
    }

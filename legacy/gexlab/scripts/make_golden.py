"""골든 테스트 입력·기대값 만들기 (PLAN §6.3 골든, docs/phase1_design.md §10).

KIS·KRX 를 부르지 않는다(`raw --from-db` 만 DATABASE_URL — .env·환경변수, 출력·오류에 싣지
않는다). 쓰는 곳은 늘 `--write` 를 준 때뿐이다 — 없으면 이미 있는 파일과 비교만 하고, 다르거나
없으면 까닭을 찍고 종료 코드 1(같으면 0). 입력·DB 오류·비밀 같은 문자열·단위(허용)를 모르는 골든
float(`GoldenSchemaError`)는 2(아무것도 쓰지 않음).

    uv run python -m scripts.make_golden core [--write]
    uv run python -m scripts.make_golden raw (--from-db | --from-jsonl <내보내기.jsonl>) \
        --start <ISO+시간대> --end <ISO+시간대> --name <이름> \
        [--source kis_ws|kis_rest|krx ...] [--tr-id <TR> ...] [--max-rows 2000] [--write]
    uv run python -m scripts.make_golden raw-parse [--name <이름> ...] [--write]
    uv run python -m scripts.make_golden chain-subset <체인.json> --out <작은.json> [--write]

- core: 작은 체인 fixture 두 개(2026-09-28 14:27·14:52)와 야간 파생 입력 하나(14:52 의 시각만
  2026-10-07 수 21:52 로 옮긴 것 — `NIGHT_DERIVED`, 실측 야간 체인이 아니다)를 시각 순으로 코어
  파이프라인(`core_golden`, 입력 `CORE_INPUTS`)에 넣은 결과를 tests/golden/core/
  chain_synthetic_20260928_small.json 에 고정한다 — 세션·귀속 거래일, 만기 지난 만기(빠짐),
  만기별 합성 F(품질·사유·기준가), 종목별 가격·IV(출처·사유·T 환산)·그릭스·GEX, 범위
  all·nearest·0dte
  순GEX·DEX·콜월·풋월·절대감마·Flip(다중 교차·격자 표본), ATM IV·±1σ 두 기준(달력·거래시간),
  상위 레벨·만기별 감마. 비교는 허용오차(`TOLERANCES` — float 키의 단위 접미사)로 한다
  (tests/golden/test_core_golden.py). 수식·기본값·fixture 를 일부러 바꿨을 때만 `--write` 로
  다시 쓰고 diff 를 검토해 같은 커밋에 넣는다

- raw: raw_messages 의 시간 창 [start, end)(거르기 `--source`·`--tr-id`)을 잘라
  tests/golden/raw/<이름>.jsonl(녹화 — 한 줄에 한 행, 정규화)과 <이름>.parsed.json(파서 출력
  스냅샷 — `data.kis.ws` 프레임·체결 틱·체결 시각 변환, `data.kis.models` REST 모델)을 만든다.
  입력은 DB(`--from-db`) 또는 DB 내보내기 JSONL(`--from-jsonl` — 예: `psql "$DATABASE_URL" -At
  -c "SELECT row_to_json(r) FROM raw_messages r WHERE ts >= '…' AND ts < '…'" > x.jsonl` — `\\copy`
  는 텍스트 형식이 역슬래시를 겹쳐 JSON 이 깨진다).
  옮기기 전 가린다(`sanitize`): 웹소켓 체결통보 암호문(`1|TR|건수|` 뒤)과 제어 프레임 body.output
  의 복호화 키·IV, 체결통보 TR(`NOTICE_TR`)의 구독 키(HTS ID — 행 key·header.tr_key, 평문 `0|…`
  프레임 본문) → `REDACTED`. 가릴 모양이 아니면(JSON 이 아닌 제어 프레임, 객체가 아닌 header·body·
  body.output) 쓰지 않는다(fail closed). 가린 뒤에도 비밀 같은 문자열(`SECRET_PATTERNS`)이나
  REDACTED 아닌 `"key"`·`"iv"` 값이 본문에 남으면 쓰지 않는다. 골든 한 벌은 `--max-rows`(기본
  2000) 이하. 비교는 tests/golden/test_raw_golden.py
- raw-parse: 이미 있는 녹화(tests/golden/raw/*.jsonl)의 파서 출력 스냅샷만 다시 만든다 — 파서를
  일부러 바꿨을 때
- 2026-09-29 기준 라이브 녹화가 없어(Phase 1 소크 전) 녹화 골든은 합성 한 벌(synthetic_20260928 —
  tests/golden/synthetic.py)뿐이다. 소크에서 주간·야간 각 1일을 `raw --from-db` 로 잘라 넣는다

- chain-subset: 전체 체인 스냅샷(probe `chain_snapshot.json` — probe_out, git 제외)에서 작은
  fixture(tests/fixtures/validation)를 자른다. 고른 행(`SUBSET` — 시리즈마다 고정 행사가 또는
  S_ref 기준 ATM 둘레, 보강 행 ATM±5)의 고른 필드(`BOARD_FIELDS`·`FILL_FIELDS`)만 옮긴다 —
  나머지 필드·응답 머리·선물 전광판·월물리스트는 옮기지 않아 비밀정보가 끼어들 자리가 없다.
  2026-09-28 14:27 fixture 가 이 규칙의 결과와 바이트까지 같다(tests/golden/test_chain_subset.py)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

import psycopg
from psycopg import sql
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from core.calendar import TradingCalendar, expiry_at, state_at
from core.chain import atm_strike, atm_window
from core.forward import ForwardResult, confirm_basis
from core.gex import (
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
from core.iv import IvResult
from core.levels import (
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
from data.kis.models import (
    CallPutRow,
    FuturesBoardRow,
    InvestorRow,
    MinuteBar,
    OptionListRow,
    PriceOutput,
    output_rows,
    parse_rows,
)
from data.kis.rest import MINUTE_TR
from data.kis.ws import (
    FuturesTick,
    OptionTick,
    WsControl,
    WsEncrypted,
    WsParseError,
    load_fields,
    parse_frame,
    ticks_from,
)
from scripts.probe_common import (
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_INVESTOR,
    TR_OPTION_LIST,
    TR_PRICE,
    TR_TOP,
)
from scripts.validate_greeks import Series, Snapshot, load_snapshot, series_rows
from services.ws_gateway.service import tick_time

ROOT = Path(__file__).resolve().parents[1]


class InputError(ValueError):
    """입력 파일이 틀렸다 — 종료 코드 2. 문구에 입력 값을 싣지 않는다."""


def _rel(path: Path) -> str:
    """저장소 안이면 저장소 기준 상대 경로(골든·출력에 로컬 경로를 싣지 않는다)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _check_or_write(path: Path, text: str, write: bool) -> int:
    """write 면 쓴다(0). 아니면 있는 파일과 비교 — 같으면 0, 다르거나 없으면 1."""
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"썼다: {_rel(path)}")
        return 0
    if not path.exists():
        print(f"없다: {_rel(path)} — 만들려면 --write")
        return 1
    if path.read_text(encoding="utf-8") != text:
        print(f"다르다: {_rel(path)} — 의도한 변경이면 --write 로 다시 쓴다")
        return 1
    print(f"같다: {_rel(path)}")
    return 0


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


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as e:
        raise InputError(f"{path.name}: JSON 을 읽지 못했다 ({type(e).__name__})") from None


def _main_chain_subset(args: argparse.Namespace) -> int:
    text = dump_chain(chain_subset(_load_json(args.source)))
    return _check_or_write(args.out, text, args.write)


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


def _dump_golden(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_golden(path: Path, golden: Mapping[str, Any], regenerate: str, write: bool) -> int:
    """write 면 쓴다. 아니면 있는 골든과 허용오차로 비교해 차이를 찍는다(다르거나 없으면 1).
    단위를 모르는 float 가 있으면 어느 쪽이든 GoldenSchemaError(쓰지 않는다)."""
    check_schema(golden)
    text = _dump_golden(golden)
    if write or not path.exists():
        return _check_or_write(path, text, write)
    diffs = compare_golden(json.loads(path.read_text(encoding="utf-8")), json.loads(text))
    if diffs:
        print(f"{_rel(path)}:\n{failure_report(diffs, regenerate)}")
        return 1
    print(f"같다(허용오차 안): {_rel(path)}")
    return 0


# ── core: 코어 파이프라인 골든 ────────────────────────────────────────────────

VALIDATION = ROOT / "tests" / "fixtures" / "validation"
CORE_FIXTURES: tuple[Path, ...] = (
    VALIDATION / "chain_snapshot_synthetic_20260928_1427_small.json",
    VALIDATION / "chain_snapshot_synthetic_20260928_1452_small.json",
)


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
# 야간 — 실측 야간 체인 스냅샷이 없다(#19 야간 probe 는 전광판이 멈춰 단건 몇 행뿐, 분기 B).
# 14:52 를 2026-10-07(수) 21:52 로 옮긴다: 귀속 T+1 10-08 = 월물 202610 만기일(달력일 10-07 엔
# 만기 없음), 위클리 WKM 260904(09-28)·261001(10-06)은 15:20 이 지나 빠진다, ±1σ Δt 는 10-08
# 06:00 까지. 야간 시세 경로(분기 B 단건 EU·CM 행)는 덮지 않는다 — 전광판 행 그대로다
NIGHT_DERIVED = Derived(
    CORE_FIXTURES[1],
    timedelta(days=9, hours=7),
    "야간 파생 — 14:52 의 시각만 2026-10-07(수) 21:52 로 옮김(가격·OI 그대로, 실측 아님): "
    "귀속 T+1·0dte·만기 지난 만기 빼기·±1σ 06:00 끝 갈래용",
)
CORE_INPUTS: tuple[CoreInput, ...] = (*CORE_FIXTURES, NIGHT_DERIVED)
CORE_GOLDEN = ROOT / "tests" / "golden" / "core" / "chain_synthetic_20260928_small.json"
REGEN_CORE = "uv run python -m scripts.make_golden core --write"
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


def _expiry_golden(
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


def _snapshot_golden(
    path: Path,
    snap: Snapshot,
    cal: TradingCalendar,
    basis: dict[str, Decimal],
    derived: Derived | None = None,
) -> dict[str, Any]:
    """스냅샷 하나. basis(라벨 → 확정 베이시스)는 이 스냅샷의 ok F 로 갱신한다(다음 스냅샷용).

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
            expiry = _expiry_golden(series, ev, b, cal)
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
        "input": _rel(path),
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


def core_golden(
    inputs: Sequence[CoreInput] = CORE_INPUTS, cal: TradingCalendar | None = None
) -> dict[str, Any]:
    """체인 스냅샷들(시각 순)을 코어 파이프라인에 넣은 결과 — 골든 JSON 의 내용.

    입력은 작은 fixture 경로 또는 그 시각만 옮긴 파생 입력(`Derived` — 골든에 `derived` 로 표시).

    파이프라인: 스냅샷 → `scripts.validate_greeks.series_rows`(전광판 + 단건 보강 → OptionQuote)
    → `core.gex.evaluate_expiry`(§1.1 가격·§1.3 합성 F·§1.4 T·§1.5 IV·§1.6 그릭스) → 범위
    all·nearest·0dte 순GEX·DEX·콜월·풋월·절대감마·Flip(§2·§3), 만기별 ATM IV·±1σ 두 기준(§3.7·
    §3.8), 상위 레벨·만기별 감마(§3.9·§3.10). 만기별 확정 베이시스(`core.forward.confirm_basis`)는
    앞 스냅샷의 ok F 로 만들어 다음 스냅샷에 넘긴다(§1.3 선물 교차 확인 — 운영 engine 몫을 흉내).
    근월물 코드가 바뀌면 버린다.
    """
    cal = TradingCalendar.default() if cal is None else cal
    snaps: list[tuple[Path, Snapshot, Derived | None]] = []
    for inp in inputs:
        derived = inp if isinstance(inp, Derived) else None
        p = inp.source if isinstance(inp, Derived) else inp
        try:
            snap = load_snapshot(p)
        except (OSError, ValidationError) as e:
            raise InputError(f"{p.name}: 체인 스냅샷을 읽지 못했다 ({type(e).__name__})") from None
        snaps.append((p, snap if derived is None else shift_snapshot(snap, derived.shift), derived))
    snaps.sort(key=lambda x: x[1].started_kst)
    out: list[dict[str, Any]] = []
    basis: dict[str, Decimal] = {}
    near: str | None = None
    for p, snap, derived in snaps:
        if snap.atm_ref.code != near:
            basis, near = {}, snap.atm_ref.code
        out.append(_snapshot_golden(p, snap, cal, basis, derived))
    inputs_out: list[dict[str, Any]] = []
    for p, _, derived in snaps:
        entry: dict[str, Any] = {"path": _rel(p), "sha256": _sha256(p)}
        if derived is not None:
            entry["derived"] = _derived_golden(derived)
        inputs_out.append(entry)
    return {
        "_about": "코어 파이프라인 골든(PLAN §6.3) — 비교 tests/golden/test_core_golden.py, "
        "허용오차 scripts/make_golden.py TOLERANCES. 손으로 고치지 않는다",
        "_regenerate": REGEN_CORE,
        "inputs": inputs_out,
        "snapshots": out,
    }


def _main_core(args: argparse.Namespace) -> int:
    return _check_golden(args.out, core_golden(), REGEN_CORE, args.write)


# ── raw: raw_messages 창 → 녹화 골든 + 파서 출력 스냅샷 ───────────────────────

RAW_DIR = ROOT / "tests" / "golden" / "raw"
RAW_MAX_ROWS = 2000  # 골든 한 벌의 상한 — 넘으면 창을 좁히거나 거른다
REDACTED = "REDACTED"
# 체결통보 구독 응답(제어 프레임 body.output)의 AES 복호화 키·IV — 녹화 골든에 옮기지 않는다
WS_SECRET_OUTPUT: tuple[str, ...] = ("key", "iv")
# 체결통보 TR(주간 H0IFCNI0·야간 H0MFCNI0·H0EUCNI0, 모의투자 …CNI9 가정) — 구독 키(tr_key)가
# HTS ID 다. 행 key·제어 프레임 header.tr_key 를 가리고, 평문 데이터 프레임(`0|…`)이면 본문(고객
# ID·계좌)도 가린다. Phase 8 전엔 구독하지 않는다(config/ws_budget.yaml — 자리만)
NOTICE_TR = re.compile(r"CNI[0-9]$")
# 비밀 같은 문자열 — 하나라도 있으면 쓰지 않는다. tests/unit/test_fixtures_clean.py 의 PATTERNS 를
# 모두 담는다(시험이 확인한다)
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"Bearer\s", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"app_?key", re.IGNORECASE),
    re.compile(r"app_?secret", re.IGNORECASE),
    re.compile(r"access_token", re.IGNORECASE),
    re.compile(r"approval_key", re.IGNORECASE),
)
# REST TR → (응답 output 키, 검증 모델) — 수집기(poller·scheduler 분봉)가 쓰는 그 모델
REST_PARSERS: Mapping[str, tuple[tuple[str, type[BaseModel]], ...]] = {
    TR_CALLPUT: (("output1", CallPutRow), ("output2", CallPutRow)),
    TR_PRICE: (("output1", PriceOutput),),
    TR_FUT_BOARD: (("output", FuturesBoardRow),),
    TR_INVESTOR: (("output", InvestorRow),),
    TR_OPTION_LIST: (("output", OptionListRow),),
    MINUTE_TR: (("output2", MinuteBar),),
}
# 기초자산 시세 — 응답 필드가 미실측이라 수집기도 원문만 남긴다(poller `_on_underlying`)
RAW_ONLY_TRS: frozenset[str] = frozenset({TR_TOP})


class SecretFound(InputError):
    """자른 녹화에 비밀 같은 문자열이 있다 — 아무것도 쓰지 않는다."""


class RawRow(BaseModel):
    """raw_messages 한 행 = 녹화 JSONL 한 줄. DB 열 그대로(digest 는 다시 계산할 수 있어 뺀다).

    DB 내보내기(`SELECT row_to_json(r) FROM raw_messages r …`)의 줄도 그대로 읽는다 — 모르는 열은
    버린다. 시각은 aware 만.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ts: AwareDatetime
    trade_date: date | None = None
    session: Literal["day", "night"] | None = None
    source: Literal["kis_ws", "kis_rest", "krx"]
    tr_id: str
    key: str = ""
    payload: dict[str, Any] | None = None
    payload_text: str | None = None
    lossy: bool = False

    @model_validator(mode="after")
    def _shape(self) -> RawRow:
        if (self.payload is None) == (self.payload_text is None):
            raise ValueError("payload·payload_text 중 하나만 있어야 한다")
        if (self.trade_date is None) != (self.session is None):
            raise ValueError("trade_date·session 은 둘 다 있거나 둘 다 없다")
        return self

    def body_text(self) -> str:
        if self.payload_text is not None:
            return self.payload_text
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_line(self) -> str:
        """정규화한 JSONL 한 줄 — 키 정렬·UTC 시각·공백 없음(다시 잘라도 같다)."""
        d: dict[str, Any] = {
            "ts": self.ts.astimezone(UTC).isoformat(),
            "trade_date": None if self.trade_date is None else self.trade_date.isoformat(),
            "session": self.session,
            "source": self.source,
            "tr_id": self.tr_id,
            "key": self.key,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        else:
            d["payload_text"] = self.payload_text
        if self.lossy:
            d["lossy"] = True
        return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class RawWindow:
    """자를 창 [start, end) 과 거르기(비면 전부)."""

    start: datetime
    end: datetime
    sources: tuple[str, ...] = ()
    tr_ids: tuple[str, ...] = ()
    max_rows: int = RAW_MAX_ROWS

    def __post_init__(self) -> None:
        for name, t in (("start", self.start), ("end", self.end)):
            if t.tzinfo is None or t.utcoffset() is None:
                raise InputError(f"{name} 는 시간대가 있는 시각이어야 한다(naive 금지)")
        if not self.start < self.end:
            raise InputError("start < end 여야 한다")
        if self.max_rows < 1:
            raise InputError("max_rows 는 1 이상")

    def admits(self, row: RawRow) -> bool:
        return (
            self.start <= row.ts < self.end
            and (not self.sources or row.source in self.sources)
            and (not self.tr_ids or row.tr_id in self.tr_ids)
        )

    def meta(self, origin: str) -> dict[str, Any]:
        return {
            "from": origin,
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "sources": list(self.sources),
            "tr_ids": list(self.tr_ids),
        }


def read_jsonl(path: Path) -> list[RawRow]:
    """JSONL(한 줄에 raw_messages 한 행)을 모두 읽는다. 틀린 줄은 줄 번호로 InputError(값은 싣지
    않는다), 빈 줄은 건너뛴다."""
    try:
        # "\n" 으로만 가른다 — splitlines 는 JSON 이 이스케이프하지 않는 U+2028 등에서도 가른다
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"{path.name}: 읽지 못했다 ({type(e).__name__})") from None
    rows: list[RawRow] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rows.append(RawRow.model_validate_json(line))
        except ValidationError as e:
            where = sorted({".".join(map(str, x["loc"])) or "(행)" for x in e.errors()})
            raise InputError(f"{path.name}:{n}: raw_messages 행이 아니다 — {where}") from None
    return rows


class DbRawSource:
    """raw_messages 를 DB 에서 읽는다(DATABASE_URL — 출력·오류 문구에 싣지 않는다).

    connect 는 시험이 넣는다. 오류는 종류 이름만 남긴다 — psycopg 문구에 접속 정보가 섞일 수 있다.
    """

    def __init__(self, connect: Callable[[], psycopg.Connection[Any]]) -> None:
        self._connect = connect

    @classmethod
    def from_url(cls, dsn: str) -> DbRawSource:
        def connect() -> psycopg.Connection[Any]:
            return psycopg.connect(dsn, connect_timeout=5, application_name="gexlab-make_golden")

        return cls(connect)

    @classmethod
    def from_settings(cls) -> DbRawSource:
        from config.settings import Settings

        url = Settings().database_url
        if url is None:
            raise InputError("DATABASE_URL 이 없다(.env·환경변수)")
        return cls.from_url(url.get_secret_value())

    def read(self, window: RawWindow) -> list[RawRow]:
        conds = [sql.SQL("ts >= %s"), sql.SQL("ts < %s")]
        params: list[Any] = [window.start, window.end]
        if window.sources:
            conds.append(sql.SQL("source = ANY(%s)"))
            params.append(list(window.sources))
        if window.tr_ids:
            conds.append(sql.SQL("tr_id = ANY(%s)"))
            params.append(list(window.tr_ids))
        query = sql.SQL(
            "SELECT ts, trade_date, session, source, tr_id, key, payload, payload_text, lossy "
            "FROM raw_messages WHERE {} ORDER BY ts, digest LIMIT %s"
        ).format(sql.SQL(" AND ").join(conds))
        params.append(window.max_rows + 1)  # 넘쳤는지 알 만큼만
        cols = ("ts", "trade_date", "session", "source", "tr_id", "key")
        cols += ("payload", "payload_text", "lossy")
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'UTC'")
                cur.execute(query, params)
                fetched = cur.fetchall()
        except psycopg.Error as e:
            raise InputError(f"raw_messages 를 읽지 못했다: {type(e).__name__}") from None
        try:
            return [RawRow.model_validate(dict(zip(cols, r, strict=True))) for r in fetched]
        except ValidationError:
            raise InputError("raw_messages 행이 녹화 형식이 아니다") from None


def is_notice_tr(tr_id: str) -> bool:
    return NOTICE_TR.search(tr_id) is not None


def _sanitize_control(text: str, notice: bool, tr_id: str) -> tuple[str, bool]:
    """JSON 제어 프레임 가리기 → (본문, 체결통보인가). 가릴 모양이 아니면 SecretFound."""
    where = f"웹소켓 제어 프레임({tr_id or '?'})"
    try:
        obj = json.loads(text)
    except ValueError:
        raise SecretFound(
            f"{where}이 JSON 이 아니다 — 복호화 키를 가릴 수 없어 옮기지 않는다"
        ) from None
    doc = cast(dict[str, Any], obj)  # '{' 로 시작한 JSON 은 늘 객체
    header, body = doc.get("header"), doc.get("body")
    if (header is not None and not isinstance(header, dict)) or (
        body is not None and not isinstance(body, dict)
    ):
        raise SecretFound(f"{where}의 header·body 가 객체가 아니다 — 가릴 수 없어 옮기지 않는다")
    out = cast(dict[str, Any], body).get("output") if body is not None else None
    if out is not None and not isinstance(out, dict):
        raise SecretFound(f"{where}의 body.output 이 객체가 아니다 — 가릴 수 없어 옮기지 않는다")
    changed = False
    if out is not None:
        secret = cast(dict[str, Any], out)
        for k in WS_SECRET_OUTPUT:
            if k in secret and secret[k] != REDACTED:
                secret[k] = REDACTED
                changed = True
    if header is not None:
        head = cast(dict[str, Any], header)
        frame_tr = head.get("tr_id")
        notice = notice or (isinstance(frame_tr, str) and is_notice_tr(frame_tr))
        if notice and head.get("tr_key") not in (None, "", REDACTED):
            head["tr_key"] = REDACTED
            changed = True
    if not changed:
        return text, notice
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")), notice


def sanitize(row: RawRow) -> RawRow:
    """녹화 골든에 옮기기 전 가리기 — 웹소켓 행만(나머지는 원문 그대로). 가릴 모양이 아니면
    SecretFound(fail closed — 아무것도 쓰지 않는다).

    - 암호화 프레임(`1|TR|건수|…`) 본문 → REDACTED
    - 제어 프레임(JSON) body.output 의 복호화 키·IV → REDACTED. JSON 이 아니거나 header·body·
      body.output 이 객체가 아니면 SecretFound
    - 체결통보 TR(`NOTICE_TR` — 행 tr_id 나 프레임의 TR): 행 key·header.tr_key(HTS ID) → REDACTED,
      평문 데이터 프레임(`0|…`) 본문도 → REDACTED
    - 본문이 JSON 열(payload)인 웹소켓 행은 SecretFound — 수집기는 웹소켓 원문을 텍스트로만 남긴다
    """
    if row.source != "kis_ws":
        return row
    text = row.payload_text
    if text is None:
        raise SecretFound(f"웹소켓 행({row.tr_id or '?'})의 본문이 JSON 열이다 — 가리는 규칙 밖")
    notice = is_notice_tr(row.tr_id)
    if text.startswith(("0|", "1|")):
        parts = text.split("|", 3)
        notice = notice or is_notice_tr(parts[1])
        if len(parts) == 4 and (parts[0] == "1" or notice) and parts[3] != REDACTED:
            text = "|".join([*parts[:3], REDACTED])
    elif text.lstrip().startswith("{"):
        text, notice = _sanitize_control(text, notice, row.tr_id)
    update: dict[str, Any] = {}
    if text != row.payload_text:
        update["payload_text"] = text
    if notice and row.key not in ("", REDACTED):
        update["key"] = REDACTED
    return row.model_copy(update=update) if update else row


# 가린 뒤 본문에 남으면 안 되는 복호화 키·IV 모양 — 값이 REDACTED(또는 빈 문자열)가 아니면 거부
_KEY_IV = re.compile(r'"(?:iv|key)"\s*:\s*"(?!REDACTED"|")')


def unredacted_key_iv(text: str) -> bool:
    """본문에 `"key":"…"`·`"iv":"…"` 가 REDACTED 아닌 값으로 남았나(JSON 공백 표기 포함)."""
    return _KEY_IV.search(text) is not None


def secret_hits(text: str) -> list[str]:
    """비밀 같은 문자열 패턴(`SECRET_PATTERNS`) 중 걸린 것."""
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def _order(row: RawRow) -> tuple[datetime, str, str, str, str]:
    return (row.ts, row.source, row.tr_id, row.key, row.body_text())


def cut_raw(rows: Iterable[RawRow], window: RawWindow) -> list[RawRow]:
    """창 안 행을 가려(`sanitize`) 시각 순으로. 비었거나 상한을 넘거나, 가릴 수 없거나, 비밀 같은
    문자열(`SECRET_PATTERNS`)·REDACTED 아닌 `"key"`·`"iv"` 값(`unredacted_key_iv`, 본문)이 남으면
    InputError(SecretFound) — 그때는 아무것도 쓰지 않는다."""
    picked = sorted((sanitize(r) for r in rows if window.admits(r)), key=_order)
    if not picked:
        raise InputError("창 안에 행이 없다")
    if len(picked) > window.max_rows:
        raise InputError(
            f"창 안 행이 {window.max_rows}개를 넘는다 — 창을 좁히거나 --source·--tr-id 로 거른다"
        )
    for i, r in enumerate(picked):
        hits = secret_hits(r.to_line())
        if unredacted_key_iv(r.body_text()):  # 가리기 규칙이 못 본 자리의 복호화 키·IV
            hits.append('"key"·"iv" 값')
        if hits:
            raise SecretFound(f"{i}번째 행({r.source} {r.tr_id}): 비밀 같은 문자열 {hits}")
    return picked


def _short(e: BaseException) -> str:
    if isinstance(e, ValidationError):
        return str(e.errors(include_input=False, include_url=False))[:300]
    return f"{type(e).__name__}: {e}"[:200]


def _tick_golden(tick: FuturesTick | OptionTick, received: datetime) -> dict[str, Any]:
    d = tick.model_dump(mode="json")
    try:
        d["ts"] = tick_time(tick.hhmmss, received).isoformat()
    except ValueError as e:
        d["ts"] = None
        d["ts_error"] = str(e)[:100]
    return d


def _parse_ws(row: RawRow) -> dict[str, Any]:
    if row.payload_text is None:
        return {"kind": "unexpected_payload"}
    try:
        frame = parse_frame(row.payload_text)
    except WsParseError as e:
        return {"kind": "error", "error": str(e)[:200]}
    if isinstance(frame, WsControl):
        return {
            "kind": "control",
            "frame_tr_id": frame.tr_id,
            "tr_key": frame.tr_key,
            "rt_cd": frame.rt_cd,
            "msg_cd": frame.msg_cd,
            "msg1": frame.msg1,
            "pingpong": frame.is_pingpong,
            "ok": frame.ok,
            "output_keys": sorted(frame.output),  # 값은 싣지 않는다(복호화 키)
        }
    if isinstance(frame, WsEncrypted):
        return {"kind": "encrypted", "frame_tr_id": frame.tr_id, "count": frame.count}
    head: dict[str, Any] = {
        "kind": "data",
        "frame_tr_id": frame.tr_id,
        "count": frame.count,
        "width": frame.width,
        "width_mismatch": frame.width_mismatch,
    }
    if frame.tr_id not in load_fields():
        return head  # 컬럼 설정이 없는 TR — 수집기도 원문만
    try:
        ticks = ticks_from(frame)
    except (WsParseError, ValidationError) as e:
        return head | {"kind": "data_error", "error": _short(e)}
    return head | {"kind": "ticks", "ticks": [_tick_golden(t, row.ts) for t in ticks]}


def _parse_rest(row: RawRow) -> dict[str, Any]:
    body = row.payload
    if body is None:
        return {"kind": "unexpected_payload"}
    head = {k: body.get(k) for k in ("rt_cd", "msg_cd", "msg1")}
    if row.tr_id in RAW_ONLY_TRS:
        return {"kind": "raw_only", **head, "keys": sorted(body)}
    spec = REST_PARSERS.get(row.tr_id)
    if spec is None:
        return {"kind": "unparsed", **head}
    outputs: dict[str, Any] = {}
    for key, model in spec:
        rows, bad = parse_rows(model, output_rows(body, key))
        outputs[key] = {
            "rows": [r.model_dump(mode="json") for r in rows],
            "bad": [{"index": b.index, "error": b.error[:300]} for b in bad],
        }
    return {"kind": "rest", **head, "outputs": outputs}


def parse_message(row: RawRow) -> dict[str, Any]:
    """녹화 한 행의 파서 출력 — 웹소켓은 `data.kis.ws`(프레임 분류·체결 틱·체결 시각 변환),
    REST 는 `data.kis.models`(수집기와 같은 모델·행 단위 검증). 메시지 하나의 예외는 그 메시지만
    `parser_exception` 으로 남긴다(격리)."""
    head: dict[str, Any] = {
        "ts": row.ts.astimezone(UTC).isoformat(),
        "source": row.source,
        "tr_id": row.tr_id,
        "key": row.key,
    }
    try:
        if row.source == "kis_ws":
            return head | _parse_ws(row)
        if row.source == "kis_rest":
            return head | _parse_rest(row)
        return head | {
            "kind": "unparsed",
            "why": "KRX 원문 파서 없음(krx 원문은 아직 녹화하지 않는다)",
        }
    except Exception as e:  # 메시지 단위 격리
        return head | {"kind": "parser_exception", "error": _short(e)}


def recording_text(rows: Sequence[RawRow]) -> str:
    return "".join(r.to_line() + "\n" for r in rows)


def raw_regenerate(name: str) -> str:
    return f"uv run python -m scripts.make_golden raw-parse --name {name} --write"


def raw_snapshot(name: str, rows: Sequence[RawRow], cut: Mapping[str, Any]) -> dict[str, Any]:
    """녹화의 파서 출력 스냅샷(`<name>.parsed.json` 내용)."""
    messages: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for i, r in enumerate(rows):
        m = parse_message(r)
        kind = str(m["kind"])
        counts[kind] = counts.get(kind, 0) + 1
        messages.append({"i": i} | m)
    return {
        "_about": "raw 녹화 골든의 파서 출력 스냅샷(PLAN §6.3) — 비교 "
        "tests/golden/test_raw_golden.py. 손으로 고치지 않는다",
        "_regenerate": raw_regenerate(name),
        "recording": f"{name}.jsonl",
        "sha256": hashlib.sha256(recording_text(rows).encode("utf-8")).hexdigest(),
        "cut": dict(cut),
        "counts": dict(sorted(counts.items())),
        "messages": messages,
    }


_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


def _raw_paths(out_dir: Path, name: str) -> tuple[Path, Path]:
    if not _NAME.fullmatch(name):
        raise InputError(f"--name 은 소문자·숫자·밑줄(64자 이하): {name!r}")
    return out_dir / f"{name}.jsonl", out_dir / f"{name}.parsed.json"


def _aware_arg(s: str) -> datetime:
    try:
        t = datetime.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"ISO 시각이 아니다: {s!r}") from e
    if t.tzinfo is None or t.utcoffset() is None:
        raise argparse.ArgumentTypeError(f"시간대가 없다(+09:00·Z 를 붙인다): {s!r}")
    return t


def _main_raw(args: argparse.Namespace) -> int:
    rec_path, parsed_path = _raw_paths(args.out_dir, args.name)
    window = RawWindow(args.start, args.end, tuple(args.source), tuple(args.tr_id), args.max_rows)
    if args.from_jsonl is not None:
        rows, origin = read_jsonl(args.from_jsonl), "jsonl"
    else:
        rows, origin = DbRawSource.from_settings().read(window), "db"
    picked = cut_raw(rows, window)
    snapshot = raw_snapshot(args.name, picked, window.meta(origin))
    check_schema(snapshot)  # 녹화를 쓰기 전에 — 스냅샷을 못 쓰면 둘 다 쓰지 않는다
    rc = _check_or_write(rec_path, recording_text(picked), args.write)
    return max(rc, _check_golden(parsed_path, snapshot, raw_regenerate(args.name), args.write))


def _main_raw_parse(args: argparse.Namespace) -> int:
    names = args.name or sorted(p.stem for p in args.out_dir.glob("*.jsonl"))
    if not names:
        print(f"녹화가 없다: {_rel(args.out_dir)}")
        return 1
    rc = 0
    for name in names:
        rec_path, parsed_path = _raw_paths(args.out_dir, name)
        rows = read_jsonl(rec_path)
        old = _load_json(parsed_path) if parsed_path.exists() else {}
        cut = old.get("cut", {}) if isinstance(old, dict) else {}
        snapshot = raw_snapshot(name, rows, cast(dict[str, Any], cut))
        rc = max(rc, _check_golden(parsed_path, snapshot, raw_regenerate(name), args.write))
    return rc


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.make_golden",
        description="골든 입력·기대값 — --write 가 없으면 비교만(다르면 1)",
    )
    sub = ap.add_subparsers(dest="mode", required=True)
    cs = sub.add_parser("chain-subset", help="전체 체인 스냅샷 → 작은 fixture")
    cs.add_argument("source", type=Path, help="probe chain_snapshot.json")
    cs.add_argument("--out", type=Path, required=True, help="작은 fixture 경로")
    cs.add_argument("--write", action="store_true", help="파일을 쓴다(없으면 비교만)")
    co = sub.add_parser("core", help="코어 파이프라인 골든(작은 체인 fixture 두 개)")
    co.add_argument("--out", type=Path, default=CORE_GOLDEN, help="골든 JSON 경로")
    co.add_argument("--write", action="store_true", help="골든을 다시 쓴다(없으면 비교만)")
    rw = sub.add_parser("raw", help="raw_messages 창 → 녹화 골든 + 파서 출력 스냅샷")
    src = rw.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-jsonl", type=Path, help="raw_messages 내보내기(JSONL — 한 줄에 한 행)")
    src.add_argument("--from-db", action="store_true", help="DATABASE_URL 의 raw_messages")
    rw.add_argument("--start", type=_aware_arg, required=True, help="창 시작(포함, ISO+시간대)")
    rw.add_argument("--end", type=_aware_arg, required=True, help="창 끝(제외, ISO+시간대)")
    rw.add_argument("--name", required=True, help="골든 이름(tests/golden/raw/<name>.*)")
    rw.add_argument("--source", action="append", default=[], choices=["kis_ws", "kis_rest", "krx"])
    rw.add_argument("--tr-id", action="append", default=[], help="이 TR 만(반복 가능)")
    rw.add_argument("--max-rows", type=int, default=RAW_MAX_ROWS, help="창 안 행 상한")
    rw.add_argument("--out-dir", type=Path, default=RAW_DIR, help=argparse.SUPPRESS)
    rw.add_argument("--write", action="store_true", help="파일을 쓴다(없으면 비교만)")
    rp = sub.add_parser("raw-parse", help="있는 녹화 골든의 파서 출력 스냅샷만 다시")
    rp.add_argument("--name", action="append", default=[], help="이 녹화만(없으면 전부)")
    rp.add_argument("--out-dir", type=Path, default=RAW_DIR, help=argparse.SUPPRESS)
    rp.add_argument("--write", action="store_true", help="스냅샷을 다시 쓴다(없으면 비교만)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "chain-subset":
            return _main_chain_subset(args)
        if args.mode == "core":
            return _main_core(args)
        if args.mode == "raw":
            return _main_raw(args)
        if args.mode == "raw-parse":
            return _main_raw_parse(args)
    except GoldenSchemaError as e:
        print(f"골든 스키마 오류: {e}", file=sys.stderr)
        return 2
    except InputError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2
    raise AssertionError(f"모르는 모드: {args.mode}")  # argparse 가 막는다


if __name__ == "__main__":
    raise SystemExit(main())

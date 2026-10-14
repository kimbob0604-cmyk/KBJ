"""GEX 골든 CLI — 코어 파이프라인 골든·체인 부분집합(P7 FND-C, docs/p7_design.md §1.2).

GX `scripts/make_golden.py` 의 core·chain-subset 갈래. 순수 부분(비교·허용오차·골든 내용)은
`kbj.engines.gex.golden`, 여기는 파일 읽기·쓰기와 경로 상수만 둔다. 네트워크·DB·.env 를 쓰지 않는다.
쓰는 곳은 늘 `--write` 를 준 때뿐이다 — 없으면 있는 파일과 비교만 하고 다르거나 없으면 까닭을 찍고
종료 코드 1(같으면 0). 입력 오류·단위(허용)를 모르는 골든 float(`GoldenSchemaError`)는 2.

    uv run python -m kbj.services.engine.gex_golden core [--write]
    uv run python -m kbj.services.engine.gex_golden chain-subset <체인.json> --out <작은.json> \
        [--write]

- core: 작은 합성 체인 fixture 두 개(`tests/fixtures/synthetic/gex/validation/` — 2026-09-28
  14:27·14:52)와 야간 파생 입력 하나(14:52 의 시각만 2026-10-07 수 21:52 로 옮긴 것 —
  `NIGHT_DERIVED`, 실측 야간 체인이 아니다)를 코어 파이프라인에 넣은 결과를
  `tests/golden/gex/core/chain_synthetic_20260928_small.json` 에 고정한다. 비교는 허용오차
  (`kbj.engines.gex.golden.TOLERANCES`, 시험 `tests/golden/gex/test_core_golden.py`). 수식·기본값·
  fixture 를 일부러 바꿨을 때만 `--write` 로 다시 쓰고 diff 를 검토해 같은 커밋에 넣는다
- chain-subset: 전체 체인 스냅샷에서 작은 fixture 를 자른다(고른 행·필드만 — 비밀이 끼어들 자리가
  없다). 커밋된 두 fixture 가 이 규칙의 고정점이다(`tests/golden/gex/test_chain_subset.py`)

fixture 생성기는 아직 legacy GX `scripts/make_synthetic_fixtures.py`(시드 고정 — 합성)다. legacy GX
의 남은 도구·시험이 같은 바이트의 사본(`legacy/gexlab/tests/fixtures/validation/`)을 읽는다 — 두
사본이 같은지는 legacy 다리 시험이 본다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from kbj.core.calendar import TradingCalendar
from kbj.engines.gex import golden
from kbj.engines.gex.golden import (
    BOARD_FIELDS,
    FILL_FIELDS,
    SUBSET,
    TOLERANCES,
    AroundAtm,
    CoreInput,
    Derived,
    Fixed,
    GoldenSchemaError,
    InputError,
    LoadedInput,
    chain_subset,
    check_schema,
    compare_golden,
    dump_chain,
    dump_golden,
    failure_report,
    shift_snapshot,
    tolerance,
)
from kbj.engines.gex.golden import expiry_golden as _expiry_golden
from kbj.engines.gex.snapshot import Snapshot

__all__ = [
    "BOARD_FIELDS",
    "CORE_FIXTURES",
    "CORE_GOLDEN",
    "CORE_INPUTS",
    "FILL_FIELDS",
    "NIGHT_DERIVED",
    "REGEN_CORE",
    "ROOT",
    "SUBSET",
    "TOLERANCES",
    "VALIDATION",
    "AroundAtm",
    "CoreInput",
    "Derived",
    "Fixed",
    "GoldenSchemaError",
    "InputError",
    "chain_subset",
    "check_or_write",
    "check_schema",
    "compare_golden",
    "core_golden",
    "dump_chain",
    "failure_report",
    "load_json",
    "load_snapshot",
    "main",
    "rel",
    "shift_snapshot",
    "tolerance",
    "write_golden",
]

ROOT = Path(__file__).resolve().parents[3]


def rel(path: Path, root: Path = ROOT) -> str:
    """저장소 안이면 저장소 기준 상대 경로(골든·출력에 로컬 경로를 싣지 않는다)."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def check_or_write(path: Path, text: str, write: bool) -> int:
    """write 면 쓴다(0). 아니면 있는 파일과 비교 — 같으면 0, 다르거나 없으면 1."""
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"썼다: {rel(path)}")
        return 0
    if not path.exists():
        print(f"없다: {rel(path)} — 만들려면 --write")
        return 1
    if path.read_text(encoding="utf-8") != text:
        print(f"다르다: {rel(path)} — 의도한 변경이면 --write 로 다시 쓴다")
        return 1
    print(f"같다: {rel(path)}")
    return 0


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as e:
        raise InputError(f"{path.name}: JSON 을 읽지 못했다 ({type(e).__name__})") from None


def write_golden(path: Path, data: Mapping[str, Any], regenerate: str, write: bool) -> int:
    """write 면 쓴다. 아니면 있는 골든과 허용오차로 비교해 차이를 찍는다(다르거나 없으면 1).
    단위를 모르는 float 가 있으면 어느 쪽이든 GoldenSchemaError(쓰지 않는다)."""
    check_schema(data)
    text = dump_golden(data)
    if write or not path.exists():
        return check_or_write(path, text, write)
    diffs = compare_golden(json.loads(path.read_text(encoding="utf-8")), json.loads(text))
    if diffs:
        print(f"{rel(path)}:\n{failure_report(diffs, regenerate)}")
        return 1
    print(f"같다(허용오차 안): {rel(path)}")
    return 0


# ── core: 코어 파이프라인 골든 ────────────────────────────────────────────────

VALIDATION = ROOT / "tests" / "fixtures" / "synthetic" / "gex" / "validation"
CORE_FIXTURES: tuple[Path, ...] = (
    VALIDATION / "chain_snapshot_synthetic_20260928_1427_small.json",
    VALIDATION / "chain_snapshot_synthetic_20260928_1452_small.json",
)
# 야간 — 실측 야간 체인 스냅샷이 없다(GX #19 야간 probe 는 전광판이 멈춰 단건 몇 행뿐, 분기 B).
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
CORE_GOLDEN = ROOT / "tests" / "golden" / "gex" / "core" / "chain_synthetic_20260928_small.json"
REGEN_CORE = "uv run python -m kbj.services.engine.gex_golden core --write"


def load_snapshot(path: Path) -> Snapshot:
    return Snapshot.model_validate_json(path.read_bytes())


def core_golden(
    inputs: Sequence[CoreInput] = CORE_INPUTS, cal: TradingCalendar | None = None
) -> dict[str, Any]:
    """입력(작은 fixture 경로 또는 그 시각만 옮긴 `Derived`)을 읽어 코어 골든 내용을 만든다
    (`kbj.engines.gex.golden.core_golden_of` — 시각 순·만기별 확정 베이시스 넘김)."""
    cal = TradingCalendar.default() if cal is None else cal
    loaded: list[LoadedInput] = []
    for inp in inputs:
        derived = inp if isinstance(inp, Derived) else None
        p = inp.source if isinstance(inp, Derived) else inp
        try:
            snap = load_snapshot(p)
        except (OSError, ValidationError) as e:
            raise InputError(f"{p.name}: 체인 스냅샷을 읽지 못했다 ({type(e).__name__})") from None
        if derived is not None:
            snap = shift_snapshot(snap, derived.shift)
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        loaded.append(LoadedInput(rel(p), sha, snap, derived))
    # 만기 표기는 이 모듈의 이름으로 찾는다 — 시험이 `_expiry_golden` 을 바꿔 끼워 격리를 본다
    return golden.core_golden_of(loaded, cal, REGEN_CORE, render_expiry=_expiry_golden)


def _main_core(args: argparse.Namespace) -> int:
    return write_golden(args.out, core_golden(), REGEN_CORE, args.write)


def _main_chain_subset(args: argparse.Namespace) -> int:
    text = dump_chain(chain_subset(load_json(args.source)))
    return check_or_write(args.out, text, args.write)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m kbj.services.engine.gex_golden",
        description="GEX 골든 — --write 가 없으면 비교만(다르면 1)",
    )
    sub = ap.add_subparsers(dest="mode", required=True)
    cs = sub.add_parser("chain-subset", help="전체 체인 스냅샷 → 작은 fixture")
    cs.add_argument("source", type=Path, help="probe chain_snapshot.json")
    cs.add_argument("--out", type=Path, required=True, help="작은 fixture 경로")
    cs.add_argument("--write", action="store_true", help="파일을 쓴다(없으면 비교만)")
    co = sub.add_parser("core", help="코어 파이프라인 골든(작은 체인 fixture 두 개)")
    co.add_argument("--out", type=Path, default=CORE_GOLDEN, help="골든 JSON 경로")
    co.add_argument("--write", action="store_true", help="골든을 다시 쓴다(없으면 비교만)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "chain-subset":
            return _main_chain_subset(args)
        if args.mode == "core":
            return _main_core(args)
    except GoldenSchemaError as e:
        print(f"골든 스키마 오류: {e}", file=sys.stderr)
        return 2
    except InputError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2
    raise AssertionError(f"모르는 모드: {args.mode}")  # argparse 가 막는다


if __name__ == "__main__":
    raise SystemExit(main())

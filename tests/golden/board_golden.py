"""신고가 보드 골든 공용 — 저장된 합성 입력을 `BoardInputs` 로 읽고, 산출을 허용오차로 비교한다.

docs/p3_design.md §4.1 '골든 비교 방법' 4단계: 키 집합·목록 순서·문자열은 정확히, 실수는
`math.isclose(rel_tol=1e-9, abs_tol=1e-6)`(CLAUDE.md 4장 — 바이트 비교 금지). 무시: `generated_at`.
정규화: 네이버 잠정 문구 1개(ET '네이버 16:07 값' → kbj 'KIS 마감값(잠정)'), `source` 대소문자.
"""

from __future__ import annotations

import gzip
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kbj.engines.board.build import (
    PROVISIONAL_CLOSE_PHRASE,
    BoardInputs,
    prev_near_from,
)
from kbj.engines.board.config import BoardConfig

GOLDEN = Path(__file__).resolve().parent / "board"
FILES = ("universe", "newhigh", "sectors", "events", "rankings")
REL_TOL = 1e-9
ABS_TOL = 1e-6
IGNORE_KEYS = frozenset({"generated_at"})
CASE_FREE_KEYS = frozenset({"source", "close_source"})
# legacy 문구 → kbj 문구(이 한 문장만 다르다 — §4.1)
NORMALIZE = {"네이버 16:07 값": PROVISIONAL_CLOSE_PHRASE}


def read_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class GoldenDay:
    index: int
    asof: str
    inputs: BoardInputs
    expected: dict[str, Any]


def load_meta() -> dict[str, Any]:
    return json.loads((GOLDEN / "META.json").read_text(encoding="utf-8"))


def load_common() -> dict[str, Any]:
    return read_gz(GOLDEN / "inputs" / "common.json.gz")


def golden_config(common: dict[str, Any]) -> BoardConfig:
    """캡처할 때 legacy 가 쓴 설정 — 나중에 config/board.yaml 을 조정해도 골든은 그대로 돈다."""
    return BoardConfig.from_mapping(common["config"])


def _series(common: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dates: list[str] = common["dates"]
    return {
        code: [
            dict(asof=dates[r[0]], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows
        ]
        for code, rows in common["series"].items()
    }


def iter_days(common: dict[str, Any] | None = None) -> Iterator[GoldenDay]:
    """30일을 차례로. 전일 newhigh·rankings 는 전날 expected(= legacy 가 실제로 읽은 state 파일)."""
    common = common if common is not None else load_common()
    series = _series(common)
    meta = load_meta()
    prev: dict[str, Any] | None = None
    for i, asof in enumerate(meta["days"]):
        raw = read_gz(GOLDEN / "inputs" / f"day_{i:02d}.json.gz")
        expected = read_gz(GOLDEN / "expected" / f"day_{i:02d}.json.gz")
        inp = BoardInputs(
            asof=raw["asof"],
            prev_asof=raw["prev_asof"],
            series={c: [r for r in rows if r["asof"] <= asof] for c, rows in series.items()},
            snapshot=raw["snapshot"],
            snap_asof=raw["snap_asof"],
            generated_at="2026-10-07T00:00:00+09:00",
            alltime=raw["alltime"],
            sectors=raw["sectors"],
            taxonomy_counts=[(t, n) for t, n in raw["taxonomy_counts"]],
            prev_ranks=raw["prev_ranks"],
            split_cleared=frozenset(raw["split_cleared"]),
            split_unknown=[(c, n) for c, n in raw["split_unknown"]],
            prev_near=prev_near_from(prev["newhigh"] if prev else None),
            collect_notes=[(s, n) for s, n in raw["collect_notes"]],
            close_note=raw["close_note"],
            funds_excluded=raw["funds_excluded"],
            themes_yaml=common["themes_yaml"],
            taxonomy=common["taxonomy"],
            prev_rankings=prev["rankings"] if prev else None,
        )
        yield GoldenDay(i, asof, inp, expected)
        prev = expected


def _norm_str(s: str) -> str:
    for a, b in NORMALIZE.items():
        s = s.replace(a, b)
    return s


def diff(path: str, legacy: Any, kbj: Any, out: list[str], *, key: str = "") -> None:
    """legacy 대 kbj — 다른 곳을 (JSON 경로, legacy 값, kbj 값) 으로 모은다."""
    if isinstance(legacy, dict) and isinstance(kbj, dict):
        lk = {k for k in legacy if k not in IGNORE_KEYS}
        kk = {k for k in kbj if k not in IGNORE_KEYS}
        if lk != kk:
            out.append(
                f"{path}: 키 집합 다름 — legacy 만 {sorted(lk - kk)} / kbj 만 {sorted(kk - lk)}"
            )
        for k in sorted(lk & kk):
            diff(f"{path}.{k}", legacy[k], kbj[k], out, key=k)
        return
    if isinstance(legacy, list) and isinstance(kbj, list):
        if len(legacy) != len(kbj):
            out.append(f"{path}: 길이 다름 — legacy {len(legacy)} / kbj {len(kbj)}")
        for i, (a, b) in enumerate(zip(legacy, kbj, strict=False)):
            diff(f"{path}[{i}]", a, b, out, key=key)
        return
    if isinstance(legacy, bool) or isinstance(kbj, bool):
        if legacy is not kbj:
            out.append(f"{path}: legacy {legacy!r} / kbj {kbj!r}")
        return
    if isinstance(legacy, int | float) and isinstance(kbj, int | float):
        if not math.isclose(legacy, kbj, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            out.append(f"{path}: legacy {legacy!r} / kbj {kbj!r}")
        return
    if isinstance(legacy, str) and isinstance(kbj, str):
        a, b = _norm_str(legacy), _norm_str(kbj)
        if key in CASE_FREE_KEYS:
            a, b = a.lower(), b.lower()
        if a != b:
            out.append(f"{path}: legacy {legacy[:120]!r} / kbj {kbj[:120]!r}")
        return
    if legacy != kbj:
        out.append(f"{path}: legacy {legacy!r} / kbj {kbj!r}")


def as_json(obj: Any) -> Any:
    """kbj 산출을 JSON 으로 한 번 왕복(artifact 는 jsonb 로 저장된다 — 튜플은 목록이 된다)."""
    return json.loads(json.dumps(obj, ensure_ascii=False))

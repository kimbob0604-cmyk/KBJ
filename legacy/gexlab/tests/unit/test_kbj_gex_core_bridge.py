"""KBJ P7 FND-C 다리 시험 — GX core 는 KBJ `kbj.core.gex` 를 다시 내보낸다(docs/p7_design.md §1.2).

- `core.<모듈>`·`core.metrics.<모듈>` shim 은 KBJ 정본의 공개 이름을 **같은 객체로** 모두 내보낸다
  (두 벌 금지 — 남은 legacy 엔진·검증 도구가 그대로 돈다)
- `scripts.make_golden` 의 core·chain-subset 이름과 `scripts.validate_greeks` 의 스냅샷 입력 이름은
  KBJ(`kbj.services.engine.gex_golden`·`kbj.engines.gex.snapshot`)의 것이다
- 작은 체인 fixture·분봉 fixture 의 legacy 사본은 KBJ 사본과 바이트까지 같다(합성 — 생성기는 아직
  `scripts/make_synthetic_fixtures.py`)
"""

from __future__ import annotations

import importlib
from pathlib import Path

import kbj.core.gex.metrics.vol
import kbj.engines.gex.snapshot
import kbj.services.engine.gex_golden

import scripts.make_golden as mg
import scripts.validate_greeks as vg

GX = Path(__file__).resolve().parents[2]
KBJ = GX.parents[1]
CORE = ("black76", "chain", "forward", "gex", "greeks", "iv", "levels", "preprocess", "specs")
METRICS = ("exposure", "flow", "futures", "vol")


def _public(mod: object) -> dict[str, object]:
    return {k: v for k, v in vars(mod).items() if not k.startswith("_")}


def test_gexlab_core_reexports_kbj_core_gex() -> None:
    pairs = [(f"core.{m}", f"kbj.core.gex.{m}") for m in CORE]
    pairs += [(f"core.metrics.{m}", f"kbj.core.gex.metrics.{m}") for m in METRICS]
    for old, new in pairs:
        shim, canon = importlib.import_module(old), importlib.import_module(new)
        public = _public(canon)
        assert public, new
        for name, obj in public.items():
            assert getattr(shim, name) is obj, (old, name)  # 같은 객체 — 사본이 아니다
        shim_src = (GX / (old.replace(".", "/") + ".py")).read_text(encoding="utf-8")
        assert "def " not in shim_src and "class " not in shim_src, old  # shim 에는 정의가 없다

    # 골든·부분집합: make_golden 의 core 이름은 KBJ CLI 모듈의 것, 모드는 KBJ 로 넘긴다
    for name in ("CORE_FIXTURES", "CORE_GOLDEN", "NIGHT_DERIVED", "chain_subset", "dump_chain"):
        assert getattr(mg, name) is getattr(kbj.services.engine.gex_golden, name), name
    for name in ("compare_golden", "failure_report", "shift_snapshot", "InputError"):
        assert getattr(mg, name) is getattr(kbj.services.engine.gex_golden, name), name
    assert mg.CORE_GOLDEN.is_relative_to(KBJ / "tests" / "golden" / "gex")
    assert mg.main(["core"]) == 0  # KBJ 골든과 허용오차 안(쓰지 않는다)

    # 스냅샷 입력 모델은 KBJ 한 벌(KIS 그릭스 필드가 있어 core 가 아닌 engines 에 둔다)
    for name in ("Snapshot", "Series", "KisRow", "series_rows", "resolve_expiry", "board_spot"):
        assert getattr(vg, name) is getattr(kbj.engines.gex.snapshot, name), name
    assert vg.KIS_EXPIRY is kbj.engines.gex.snapshot.KIS_EXPIRY

    # 합성 fixture 사본 — 바이트까지 같다
    copies = {
        GX / "tests/fixtures/validation" / p.name: p
        for p in sorted((KBJ / "tests/fixtures/synthetic/gex/validation").glob("*.json"))
    }
    copies[GX / "tests/fixtures/kis/minute_day.json"] = (
        KBJ / "tests/fixtures/synthetic/kis/minute_day.json"
    )
    assert len(copies) == 3
    for legacy_copy, kbj_copy in copies.items():
        assert legacy_copy.read_bytes() == kbj_copy.read_bytes(), legacy_copy.name
    assert kbj.core.gex.metrics.vol.TERM_SLOTS  # 패키지 경로로도 읽힌다

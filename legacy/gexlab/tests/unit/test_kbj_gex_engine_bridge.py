"""KBJ P7 ENG 다리 시험 — GX engine 은 KBJ `kbj.engines.gex`·`kbj.services.engine.gex`·
`kbj.config.gex` 를 다시 내보낸다(docs/p7_design.md §1.3).

- `services.engine.<모듈>` shim 은 KBJ 정본의 공개 이름을 **같은 객체로** 내보낸다(두 벌 금지 — GX
  compose 의 `python -m services.engine`·통합 시험 `test_full_day`·도구가 KBJ 엔진으로 돈다)
- `core.features` 는 `kbj.config.gex` 의 플래그 카탈로그·로더다. 이 폴더의 `config/features.yaml`
  (GX compose 마운트)은 KBJ `config/gex.yaml` 의 `features:` 와 플래그가 같다
- 스냅샷 → engine 입력 변환(`scripts.metric_report.snapshot_cycle`)은 KBJ 것이다
"""

from __future__ import annotations

import importlib
from pathlib import Path

import kbj.config.gex
import kbj.engines.gex.snapshot_cycle
import kbj.services.engine.gex

import core.features
import scripts.metric_report as mr
import tests.fakes.engine_inputs as ei

GX = Path(__file__).resolve().parents[2]
ENGINE = ("registry", "evaluate", "extended", "flow", "futures", "daily")


def _public(mod: object) -> dict[str, object]:
    return {k: v for k, v in vars(mod).items() if not k.startswith("_")}


def test_gexlab_engine_reexports_kbj_engine_service_and_flags() -> None:
    for m in ENGINE:
        shim, canon = (
            importlib.import_module(f"services.engine.{m}"),
            importlib.import_module(f"kbj.engines.gex.{m}"),
        )
        public = _public(canon)
        assert public, m
        for name, obj in public.items():
            assert getattr(shim, name) is obj, (m, name)
    svc = kbj.services.engine.gex
    for m in ("service", "publish", "context"):
        shim = importlib.import_module(f"services.engine.{m}")
        for name, obj in _public(shim).items():
            if not isinstance(obj, type(importlib)):  # 모듈 이름(다시 내보낸 import)은 빼고
                assert getattr(svc, name) is obj, (m, name)
    shim_svc = importlib.import_module("services.engine.service")
    assert shim_svc.EngineService is svc.EngineService and shim_svc.main is svc.main
    assert importlib.import_module("services.engine.futures").quote_of is svc.quote_of
    assert importlib.import_module("services.engine.publish").EnginePublisher is svc.EnginePublisher
    assert importlib.import_module("services.engine.context").EngineContext is svc.EngineContext
    # 플래그: 카탈로그·로더는 KBJ 하나, GX compose 가 읽는 파일도 KBJ 와 같은 값
    for name in ("CATALOG", "Features", "FeatureWatcher", "load_features", "parse_text"):
        assert getattr(core.features, name) is getattr(kbj.config.gex, name), name
    gx_flags = kbj.config.gex.load_features(GX / "config" / "features.yaml")
    kbj_flags = kbj.config.gex.load_features(kbj.config.gex.FEATURES_PATH)
    assert gx_flags.resolved() == kbj_flags.resolved()
    assert gx_flags.metrics == kbj_flags.metrics
    # 스냅샷 → engine 입력은 KBJ 변환 하나
    assert mr.snapshot_cycle is kbj.engines.gex.snapshot_cycle.snapshot_cycle
    assert mr.SnapshotCycle is kbj.engines.gex.snapshot_cycle.SnapshotCycle
    assert ei.SnapshotCycle is kbj.engines.gex.snapshot_cycle.SnapshotCycle

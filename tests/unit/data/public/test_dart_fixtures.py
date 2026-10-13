"""DART 합성 원문 fixture(tests/fixtures/synthetic/dart/p4_docs.json) — 생성기와 같고 합성 표시가
있다.

공개 등급이지만 실측 전이라 합성이다(묶음 F, README 같은 폴더). 세계(p4_world — 묶음 M)가 바뀌면
`make_dart.py --write` 로 다시 만든다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "dart"


def test_fixture_is_synthetic_and_matches_the_generator() -> None:
    data = json.loads((FIX / "p4_docs.json").read_text(encoding="utf-8"))
    assert data["_source"].startswith("SYNTHETIC")
    spec = importlib.util.spec_from_file_location("_make_dart", FIX / "make_dart.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (FIX / "p4_docs.json").read_text(encoding="utf-8") == mod.render()


def test_every_document_carries_the_synthetic_mark() -> None:
    docs = json.loads((FIX / "p4_docs.json").read_text(encoding="utf-8"))["docs"]
    assert len(docs) == 20
    for name, d in docs.items():
        assert "<!-- SYNTHETIC" in d["html"], name
        if d["ident"] is not None:
            assert len(d["rcept_no"]) == 14 and d["corp_code"].startswith("994"), name

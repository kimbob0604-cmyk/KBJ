"""공개 수출 속보 `trade/flash.json`·띠 칩 `trade/ribbon.json`(묶음 FLASH —
docs/p6_design.md §7, metrics §25).

이 패키지는 엔진을 import 하지 않아(계약 ⑧) 정의를 따로 적는다 — 여기서
엔진(`kbj.engines.trade.flash`)과 값을 대조한다. 합성 세계(p6_world)만 쓴다(실데이터 없음).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from kbj.engines.trade.flash import flash_chip, segments
from kbj.services.public_export.manifest import FILE_CAPS, check_payload, file_cap, trade_problems
from kbj.services.public_export.trade_flash_json import (
    FLASH_NAME,
    KINDS,
    PUBLIC_MONTHS,
    RIBBON_NAME,
    build_trade_flash,
)
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import CLOCK, P6World, build_world
from tests.unit.api.web_fixtures_p6.flash import memory_flash_reader


@pytest.fixture(scope="module")
def world() -> P6World:
    return build_world()


@pytest.fixture(scope="module")
def files(world: P6World) -> dict[str, Any]:
    repo = MemoryTradeRepo()
    world.fill(repo)
    out = build_trade_flash(memory_flash_reader(repo), now=CLOCK)
    assert out.skipped == ()
    return {f.name: f for f in out.files}


def test_빈_표는_파일_0_이유_0() -> None:
    out = build_trade_flash(memory_flash_reader(MemoryTradeRepo()), now=CLOCK)
    assert out.files == () and out.skipped == ()


def test_파일_이름·상한·공개_검사(files: dict[str, Any]) -> None:
    assert set(files) == {FLASH_NAME, RIBBON_NAME}
    for name, f in files.items():
        text = f.render(CLOCK)
        cap = file_cap(name)
        if cap is not None:
            assert len(text.encode()) <= cap, (name, len(text.encode()), cap)
        assert check_payload(name, text) == []
        assert trade_problems(name, json.loads(text)) == []
    assert FILE_CAPS[FLASH_NAME] >= len(files[FLASH_NAME].render(CLOCK).encode())


def test_속보_4종_기간_엔진과_같은_값(world: P6World, files: dict[str, Any]) -> None:
    f = files[FLASH_NAME]
    assert f.source == "관세청(공공데이터포털)" and f.quality == "estimated"
    kinds = {k["kind"]: k for k in f.data["kinds"]}
    assert tuple(kinds) == KINDS
    for kind, d in kinds.items():
        assert d["months"] == PUBLIC_MONTHS and len(d["columns"]) == 11
        want = segments(list(world.ten_day), kind=kind, months=PUBLIC_MONTHS)
        got = d["segments"]
        assert [(s["ym"], s["seg"]) for s in got] == [(s.ym, s.seg) for s in want]
        for g, w in zip(got, want, strict=True):
            assert g["quality"] == w.quality.value and g["span_text"] == w.span_text
            for gc, wc in zip(g["cells"], w.cells, strict=True):
                assert (gc["col_no"], gc["name"], gc["usd"], gc["first_usd"]) == (
                    wc.col_no, wc.name, wc.usd, wc.first_usd,
                )  # fmt: skip
                assert gc["yoy"] == pytest.approx(wc.yoy) and gc["yoy_reason"] == wc.yoy_reason
                assert gc["contribution"] == pytest.approx(wc.contribution)
    assert kinds["exp_country"]["columns"][10] == "가상국-10"


def test_띠_칩은_엔진_flash_chip_과_같다(world: P6World, files: dict[str, Any]) -> None:
    chip = files[RIBBON_NAME].data["chips"][0]
    want = flash_chip(list(world.ten_day))
    assert want is not None and want.yoy is not None
    assert (
        chip["key"] == "export_flash" and chip["tier"] == "public" and chip["phase_pending"] is None
    )
    assert chip["value"]["value"] == pytest.approx(want.yoy)
    assert chip["value"]["quality"] == "estimated" and chip["value"]["source"] == want.source
    assert chip["detail"]["span_label"] == want.label and chip["detail"]["usd"] == want.usd
    # 로그인 API 칩과 같은 칸
    assert set(chip) == {"key", "label", "value", "tier", "phase_pending", "tags", "note", "detail"}


def test_같은_입력이면_같은_글자(world: P6World) -> None:
    def once() -> list[str]:
        repo = MemoryTradeRepo()
        world.fill(repo)
        return [
            f.render(CLOCK) for f in build_trade_flash(memory_flash_reader(repo), now=CLOCK).files
        ]

    assert once() == once()

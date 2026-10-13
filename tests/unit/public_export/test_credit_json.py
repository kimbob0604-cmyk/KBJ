"""공개 띠 칩 파일 — 신용 칩(`credit.json`, P5 MAC)과 수출 속보 칩(`trade/ribbon.json`, P6
FLASH)의 경계.

설계서 §1.3 은 `credit.json` 칩 목록에 `export_flash` 를 더하는 안을 [제안]으로 적었다.
FLASH 는 수출 속보 칩을 관세청 표만 읽는 `trade/ribbon.json`(trade_flash_json)에 두었다 —
credit.json 은 금투협·ECOS 신용 칩만 그대로(입력 표가 다른 두 원천을 한 파일에 섞지 않는다).
두 파일의 칩 모양은 같다(화면 띠가 같은 코드로 그린다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from kbj.core.time import KST
from kbj.services.public_export import credit_json
from kbj.services.public_export.credit_json import build_credit
from kbj.services.public_export.pub_read import Query
from kbj.services.public_export.trade_flash_json import RIBBON_NAME, build_trade_flash
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import CLOCK, build_world
from tests.unit.api.web_fixtures_p6.flash import memory_flash_reader

NOW = datetime(2026, 11, 17, 16, 30, tzinfo=KST)


def _empty(q: Query, p: Mapping[str, object]) -> list[dict[str, Any]]:
    del q, p
    return []


def test_credit_json_은_수출_속보를_읽지_않는다() -> None:
    names: list[str] = []

    def read(q: Query, p: Mapping[str, object]) -> list[dict[str, Any]]:
        names.append(q.name)
        return _empty(q, p)

    assert build_credit(read, now=NOW).files == ()
    assert not any(n.startswith("trade") for n in names)
    assert all("pub_trade" not in q.sql for q in vars(credit_json).values() if isinstance(q, Query))


def test_두_칩_파일의_칩_모양이_같다() -> None:
    credit_chip = credit_json._chip(  # pyright: ignore[reportPrivateUsage]
        "credit_spread", "신용스프레드 AA-", 0.5, "ECOS:817Y002", NOW, "ok", ("%p",), None, {}
    )
    repo = MemoryTradeRepo()
    build_world().fill(repo)
    out = {f.name: f for f in build_trade_flash(memory_flash_reader(repo), now=CLOCK).files}
    flash_chip = out[RIBBON_NAME].data["chips"][0]
    assert set(flash_chip) == set(credit_chip)
    assert set(flash_chip["value"]) == set(credit_chip["value"])

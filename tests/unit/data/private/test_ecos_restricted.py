"""ECOS 타기관 작성 표(로그인) — 공개 쪽은 거절하고 로그인 쪽은 받는다(새로 쓴 시험).

응답은 ECOS StatisticSearch 봉투 모양의 합성 JSON(값은 가짜). 키는 가짜 값이다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.ecos_restricted import (
    DATASETS,
    EcosRestrictedClient,
    require_restricted,
)
from kbj.data.public.ecos.client import (
    PUBLIC_TABLES,
    RESTRICTED_TABLES,
    EcosClient,
    TierError,
)
from kbj.data.ratelimit import Priority, scoped_key
from kbj.data.spec import Tier

KEY = "FAKEECOSKEY0123456789"
T0 = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)


class RecordingLimiter:
    def __init__(self) -> None:
        self.acquired: list[str] = []

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.acquired.append(tr_id)

    def on_rate_limited(self) -> float:
        return 1.0


def search_body(stat_code: str) -> dict[str, Any]:
    row = {
        "STAT_CODE": stat_code,
        "STAT_NAME": "합성 표",
        "ITEM_CODE1": "0000001",
        "ITEM_NAME1": "합성 항목",
        "UNIT_NAME": "원",
        "WGT": "",
        "TIME": "20261006",
        "DATA_VALUE": "1234.5",
    }
    return {"StatisticSearch": {"list_total_count": 1, "row": [row]}}


def handler_for(seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        stat = req.url.path.split("/")[8]
        return httpx.Response(200, json=search_body(stat))

    return handle


@pytest.mark.parametrize("stat_code", sorted(RESTRICTED_TABLES))
def test_public_client_refuses_restricted_tables_without_calling(stat_code: str) -> None:
    seen: list[httpx.Request] = []
    c = EcosClient(
        SecretStr(KEY), limiter=RecordingLimiter(), transport=httpx.MockTransport(handler_for(seen))
    )
    with pytest.raises(TierError, match="ecos_restricted"):
        c.search(stat_code, "D", "20261001", "20261006")
    assert seen == []


@pytest.mark.parametrize("stat_code", sorted(RESTRICTED_TABLES))
def test_restricted_client_fetches_restricted_tables(stat_code: str) -> None:
    seen: list[httpx.Request] = []
    lim = RecordingLimiter()
    c = EcosRestrictedClient(
        SecretStr(KEY),
        limiter=lim,
        transport=httpx.MockTransport(handler_for(seen)),
        now=lambda: T0,
    )
    (row,) = c.search(stat_code, "D", "20261001", "20261006", ("0000001",))
    assert row.source == f"ECOS:{stat_code}" and str(row.value) == "1234.5"
    (req,) = seen
    assert f"/StatisticSearch/{KEY}/json/kr/1/" in req.url.path
    assert req.url.path.endswith(f"/{stat_code}/D/20261001/20261006/0000001")
    assert lim.acquired == ["ecos:StatisticSearch"]
    assert KEY not in repr(c)


@pytest.mark.parametrize("stat_code", ["817Y002", "722Y001", "999Z999"])
def test_restricted_client_refuses_public_and_unjudged_tables(stat_code: str) -> None:
    seen: list[httpx.Request] = []
    c = EcosRestrictedClient(
        SecretStr(KEY), limiter=RecordingLimiter(), transport=httpx.MockTransport(handler_for(seen))
    )
    with pytest.raises(TierError):
        c.search(stat_code, "D", "20261001", "20261006")
    with pytest.raises(TierError):
        c.item_list(stat_code)
    assert seen == []


def test_require_restricted_messages_point_the_right_way() -> None:
    with pytest.raises(TierError, match=r"kbj\.data\.public\.ecos"):
        require_restricted(sorted(PUBLIC_TABLES)[0])
    with pytest.raises(TierError, match="등급 판정 전"):
        require_restricted("999Z999")
    require_restricted("802Y001")


def test_datasets_are_private_and_cover_every_restricted_table() -> None:
    assert {d.dataset for d in DATASETS} == set(RESTRICTED_TABLES)
    for d in DATASETS:
        assert d.id == f"ECOS:{d.dataset}" and d.tier is Tier.PRIVATE
        assert d.store == "prv_macro.series" and d.limiter == "ecos" and d.budget is None
        assert RESTRICTED_TABLES[d.dataset] in d.notes
    assert not {d.dataset for d in DATASETS} & PUBLIC_TABLES


def test_from_settings_shares_the_public_ecos_bucket() -> None:
    r = fakeredis.FakeRedis()
    s = Settings(_env_file=None, ecos_key=KEY)  # pyright: ignore[reportCallIssue]
    seen: list[httpx.Request] = []
    c = EcosRestrictedClient.from_settings(
        s, r, transport=httpx.MockTransport(handler_for(seen)), now=lambda: T0
    )
    c.search("731Y001", "D", "20261006", "20261006")
    raw: list[bytes] = r.keys("*")  # pyright: ignore[reportAssignmentType]
    keys = {k.decode() for k in raw}
    assert scoped_key("ecos", KEY) in keys  # 공개 EcosClient.from_settings 와 같은 키
    assert all(KEY not in k for k in keys)

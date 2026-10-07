"""금융위 지수시세 — 경로·파라미터·이름·구간 거르기(새로 쓴 시험, 합성). 데이터셋 ID [확인 필요]."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kbj.core.quality import Quality
from kbj.data.budget import DailyBudget
from kbj.data.datago import DatagoTransport
from kbj.data.private.fsc_index_price.client import DATASET_ID, PATH, FscIndexPriceClient
from kbj.data.private.fsc_stock_price.client import FscFormatError
from kbj.data.ratelimit import Priority

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"
KEY = "FakeDatagoKey-index-0123456789"
T0 = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
D29, D30 = date(2026, 9, 29), date(2026, 9, 30)


def items() -> list[dict[str, Any]]:
    return json.loads((FIX / "fsc_index_price.json").read_text(encoding="utf-8"))["items"]


class NullLimiter:
    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 1.0


def make(rows: list[dict[str, Any]]) -> tuple[FscIndexPriceClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = {"items": {"item": rows}, "totalCount": len(rows)}
        return httpx.Response(
            200, json={"response": {"header": {"resultCode": "00"}, "body": body}}
        )

    t = DatagoTransport(
        SecretStr(KEY),
        limiters={DATASET_ID: NullLimiter()},
        budgets={DATASET_ID: DailyBudget(None, "datago", 10, scope=DATASET_ID)},
        transport=httpx.MockTransport(handler),
        retry_waits_s=(),
        now=lambda: T0,
    )
    return FscIndexPriceClient(t), seen


def test_index_calls_the_index_service_and_filters_by_name() -> None:
    c, seen = make(items())
    rows, bad = c.index("코스피", D29, D30)
    assert bad == []
    assert [(r.idx_nm, r.bas_dt) for r in rows] == [("코스피", D29), ("코스피", D30)]
    for r in rows:
        assert r.source == f"DATAGO:{DATASET_ID}" and r.quality is Quality.OK
        assert isinstance(r.clpr, Decimal) and isinstance(r.tr_prc, int)
    (req,) = seen
    assert req.url.path == PATH
    p = req.url.params
    assert (p["idxNm"], p["beginBasDt"], p["endBasDt"], p["resultType"]) == (
        "코스피",
        "20260929",
        "20261001",
        "json",
    )


def test_missing_volume_is_none_not_zero() -> None:
    raw = dict(items()[0]) | {"trqu": ""}
    c, _ = make([raw])
    (r,), _ = c.index(raw["idxNm"], D29, D30)
    assert r.trqu is None  # ET 는 0.0 으로 채웠다(D-019 위반)


def test_empty_result_is_empty_and_garbage_is_an_error() -> None:
    c, _ = make([])
    assert c.index("코스닥", D29, D30) == ([], [])
    c, _ = make([{"nope": "1"}])
    with pytest.raises(FscFormatError):
        c.index("코스닥", D29, D30)


def test_bad_arguments() -> None:
    c, seen = make(items())
    with pytest.raises(ValueError):
        c.index(" ", D29, D30)
    with pytest.raises(ValueError):
        c.index("코스피", D30, D29)
    assert seen == []

"""데이터셋 명세·데이터 키(kbj.data.spec) — id 형식, 등급 ⇔ 저장 스키마, 거래소 구분(venue)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from kbj.data.spec import AS_OF_KINDS, DataKey, DatasetSpec, Tier, Venue


def spec(**kw: Any) -> DatasetSpec:
    base: dict[str, Any] = {
        "id": "KRX:sto/stk_bydd_trd",
        "source": "KRX",
        "dataset": "sto/stk_bydd_trd",
        "tier": Tier.PRIVATE,
        "limiter": "krx",
        "budget": "krx",
        "published": "D+1 08:00 KST",
        "as_of_kind": "prev_trading_day",
        "store": "prv_market.daily_bar",
    }
    return DatasetSpec(**{**base, **kw})


def test_valid_spec_and_its_keys() -> None:
    s = spec(notes="ACC_TRDVAL [실측 필요]")
    k = s.key("2026-10-02")
    assert (
        k
        == DataKey("KRX", "sto/stk_bydd_trd", "2026-10-02")
        == ("KRX", "sto/stk_bydd_trd", "2026-10-02", "")
    )
    assert k.dataset_id == s.id and k.label() == "KRX:sto/stk_bydd_trd@2026-10-02"
    assert hash(k) == hash(DataKey(*k))  # 집합·사전 키로 쓴다(선점 대조)


def test_public_corp_code_spec() -> None:
    s = DatasetSpec(
        id="DART:corpCode",
        source="DART",
        dataset="corpCode",
        tier=Tier.PUBLIC,
        limiter="dart",
        budget="dart",
        published="수시(매일 03:05 갈아 넣기)",
        as_of_kind="run_date",
        store="pub_filings.corp_code",
    )
    assert s.key("2026-10-06").source == "DART"


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "KRX-sto/stk_bydd_trd"},  # id 형식
        {"id": "KIS:sto/stk_bydd_trd"},  # id 와 source 가 다르다
        {"source": "krx", "id": "krx:sto/stk_bydd_trd"},  # 출처는 대문자
        {"dataset": " sto", "id": "KRX: sto"},
        {"limiter": "KRX"},  # limits.yaml 출처 이름은 소문자
        {"budget": "krx budget"},
        {"store": "market.daily_bar"},  # 등급 접두사 없음
        {"store": "public.daily_bar"},  # Postgres 기본 스키마(ADR 0002 금지)
        {"store": "pub_market.daily_bar"},  # 로그인 등급을 pub_* 에
        {"tier": Tier.PUBLIC},  # 공개 등급을 prv_* 에
        {"as_of_kind": "daily"},
        {"published": "  "},
        {"extra": 1},  # 모르는 필드
        {"venues": (Venue.KRX, Venue.KRX)},
    ],
)
def test_bad_specs_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        spec(**bad)


def test_unstored_on_demand_dataset() -> None:
    """명령 응답처럼 쌓지 않는 즉석 조회는 store="" (선점 대상 아님 — §6.5)."""
    s = spec(
        id="KIS:quote_on_demand",
        source="KIS",
        dataset="quote_on_demand",
        limiter="kis",
        budget=None,
        published="요청 시",
        as_of_kind="event",
        store="",
    )
    assert s.store == "" and s.tier is Tier.PRIVATE
    public = spec(store="", tier=Tier.PUBLIC, id="DART:x", source="DART", dataset="x")
    assert public.store == ""  # 저장하지 않으면 등급과 스키마를 맞출 것이 없다


def test_venue_is_part_of_the_key_for_split_datasets() -> None:
    """KRX·NXT 를 나눠 받는 데이터셋(docs/metrics.md §1)은 키에 거래소를 넣는다 — 같은 날 두 키."""
    s = spec(
        id="KIS:volume_rank",
        source="KIS",
        dataset="volume_rank",
        limiter="kis",
        budget=None,
        as_of_kind="trade_date",
        store="prv_market.turnover_rank",
        venues=(Venue.KRX, Venue.NXT),
        notes="FHPST01710000 [실측 필요]",
    )
    krx = s.key("2026-10-06", Venue.KRX)
    nxt = s.key("2026-10-06", Venue.NXT)
    assert krx != nxt and krx.venue == "KRX" and nxt.label().endswith("[NXT]")
    assert {krx, nxt} == {DataKey("KIS", "volume_rank", "2026-10-06", v) for v in ("KRX", "NXT")}
    with pytest.raises(ValueError, match="venue"):
        s.key("2026-10-06")  # 나누는 데이터셋은 거래소가 필수
    with pytest.raises(ValueError, match="venue"):
        s.key("2026-10-06", Venue.TOTAL)  # 명세에 없는 구분
    with pytest.raises(ValueError, match="나누지 않는"):
        spec().key("2026-10-02", Venue.KRX)
    with pytest.raises(ValueError):
        spec().key("2026 10 02")


def test_as_of_kinds_match_the_design() -> None:
    assert AS_OF_KINDS == (
        "trade_date",
        "prev_trading_day",
        "us_trade_date",
        "run_date",
        "minute",
        "slot10m",
        "ten_day",
        "month",
        "quarter",
        "event",
    )
    assert [v.value for v in Venue] == ["KRX", "NXT", "TOTAL"]
    assert [t.value for t in Tier] == ["public", "private"]

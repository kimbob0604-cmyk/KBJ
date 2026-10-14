"""가짜 KIS 의 P8 슬롯 시세(묶음 EVL) — 세계의 슬롯이 파서를 지나 같은 값으로 돌아온다."""

from __future__ import annotations

from datetime import timedelta

from kbj.core.quality import Quality
from kbj.data.private.kis.quotes import bar_from_quote, parse_quote
from kbj.engines.rules.live import slot_from_quote
from tests.fakes.kis_server import SYMBOLS, slot_quote_body, world_slot
from tests.fixtures.synthetic.p8_world import (  # pyright: ignore[reportPrivateUsage]
    _slot_ts,
    build_world,
)
from tests.unit.collectors.p3_fakes import Clock, DirectKis

W = build_world()


def test_slot_body_round_trips_through_the_kbj_parser() -> None:
    d = W.intraday_days[-1]
    for s in W.slots_for("Q80015", d)[::7]:
        body = {"rt_cd": "0", "output": slot_quote_body(s)}
        snap = parse_quote(body, "Q80015", day=d, quality=Quality.ESTIMATED)
        back = slot_from_quote(snap, bar_from_quote(body, snap), slot_ts=s.slot_ts)
        assert (back.open, back.high, back.low, back.price, back.volume, back.change_pct) == (
            s.open,
            s.high,
            s.low,
            s.price,
            s.volume,
            s.change_pct,
        )
        assert back.flags == () and back.quality is Quality.ESTIMATED


def test_world_slot_picks_last_slot_at_or_before_now() -> None:
    d = W.intraday_days[0]
    t = _slot_ts(d, 3)
    assert world_slot(W, "Q80001", t + timedelta(minutes=9)) == W.slots_for("Q80001", d)[3]
    assert world_slot(W, "Q80001", t.replace(hour=8)) is None  # 장 전
    assert world_slot(W, "Q80079", t) is None  # 관심종목 밖(슬롯 없음)


def test_server_serves_world_codes_and_synthetic_for_others() -> None:
    from tests.fakes.kis_server import FakeKisServer

    d = W.intraday_days[1]
    clock = Clock(_slot_ts(d, 12) + timedelta(seconds=30))
    kis = DirectKis(FakeKisServer(clock, quotes=W))
    path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    got = kis.get(
        path, "FHKST01010100", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "Q80003"}
    )
    want = W.slots_for("Q80003", d)[12]
    assert float(got.body["output"]["stck_prpr"]) == want.price
    other = kis.get(
        path, "FHKST01010100", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": SYMBOLS[0]}
    )
    assert other.body["output"]["stck_shrn_iscd"] == SYMBOLS[0]

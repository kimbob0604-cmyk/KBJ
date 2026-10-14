"""`kbj.core.gex.records`(GX 레코드 모델 이동 — P7 FND-B)·`kbj.services.runtime.{bus,chain_feed}`.

모델만 옮겼으니 GX 와 같은 검증을 하는지, 코어가 어댑터 대신 둔 표(시장분류·투자자 12종)가 정본과
같은지 본다. GX 원래 시험은 legacy 에서 shim 으로 그대로 돈다(`scripts/test_legacy.sh gexlab`).
"""

from __future__ import annotations

import typing
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from kbj.core.gex import records as R
from kbj.data.private.kis.master import MRKT_CLS_FAMILY
from kbj.engines.flows.minute import LINE_ORDER
from kbj.services.runtime import bus, chain_feed

KST = timezone(timedelta(hours=9))
T = datetime(2026, 10, 13, 10, 0, tzinfo=KST)


def test_family_table_is_inverse_of_master_mapping() -> None:
    assert {v: k for k, v in MRKT_CLS_FAMILY.items()} == R._FAMILY_MRKT_CLS  # pyright: ignore[reportPrivateUsage]


def test_investor_literal_matches_kbj_list() -> None:
    assert set(typing.get_args(R.Investor)) == set(LINE_ORDER)


def test_records_keep_gx_validation() -> None:
    lv = R.LevelRecord(
        ts=T, trade_date=date(2026, 10, 13), session="day", scope="all", name="flip",
        value=350.0, quality="ok",
    )  # fmt: skip
    assert lv.ts.tzinfo is UTC and lv.flag == "visible"
    with pytest.raises(ValidationError):
        R.LevelRecord(
            ts=T, trade_date=date(2026, 10, 13), session="day", scope="all", name="flip",
            value=float("inf"), quality="ok",
        )  # fmt: skip
    with pytest.raises(ValidationError):
        R.OptionIvRecord.model_validate(
            dict(
                ts=T,
                trade_date=date(2026, 10, 13),
                session="day",
                mrkt_cls="",
                expiry="202611",
                strike=Decimal(350),
                cp="C",
                quote_source="board",
                price=None,
                price_kind=None,
                prev_session=False,
                oi=1,
                iv=0.2,
                source=None,
                rescaled=False,
                t_kis=None,
                reason=None,
                excluded=None,
                delta=None,
                gamma=None,
                forward=None,
                t_years=0.1,
                quality="ok",
            )
        )  # fmt: skip — iv 와 source 는 함께


class _Bar:
    stck_bsop_date = "20261013"
    stck_cntg_hour = "090100"
    futs_oprc = futs_hgpr = futs_lwpr = futs_prpr = Decimal("350.00")
    cntg_vol = 3
    acml_tr_pbmn = None


def test_minute_bar_from_kis_uses_core_calendar() -> None:
    from kbj.core.calendar import TradingCalendar

    rec = R.MinuteBarRecord.from_kis(
        _Bar(), code="A01612", market="F", received_at=T, calendar=TradingCalendar.default()
    )
    assert rec.session == "day" and rec.trade_date == date(2026, 10, 13)
    assert rec.ts == datetime(2026, 10, 13, 0, 1, tzinfo=UTC)


def test_bus_and_chain_feed_keep_gx_names() -> None:
    assert (bus.WS_RAW, bus.REST_RAW, bus.CHAIN_READY) == ("ws.raw", "rest.raw", "chain.ready")
    assert bus.heartbeat_key("gex.engine") == "health:heartbeat:gex.engine"
    env = R.RawEnvelope(
        received_at=T, source="kis_ws", tr_id="H0IFCNT0", payload="x", trade_date=None,
        session=None,
    )  # fmt: skip
    assert bus.decode_envelope(bus.encode_envelope(env)) == env
    s = chain_feed.Series("WKM", "261001")
    assert s.weekly and s.label == "WKM:261001" and s.family == "kospi200_weekly_mon"
    assert chain_feed.ChainContext().version == 0

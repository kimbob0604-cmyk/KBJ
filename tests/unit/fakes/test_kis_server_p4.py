"""가짜 KIS 서버의 P4 컨센서스 TR(묶음 K) — 세계 의미값 왕복·빈 출력·고정 시드·HTTP 경로.

합성 20종목 세계(p4_world)의 `consensus_rows`·`opinion_rows` 를 TR 출력으로 바꿔 싣고
(`estimate_body`·`opinion_body`), kbj 파서(`kbj.data.private.kis.consensus`)가 같은 의미값으로
되읽는지 본다 — 출력 모양은 파서 상수 표 하나([확인 필요 #29])를 둘이 같이 따른다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from kbj.core.fin_rows import ConsensusRow
from kbj.data.private.kis.consensus import (
    TR_ESTIMATE,
    TR_OPINION,
    params_estimate,
    params_opinion,
    parse_estimate,
    parse_opinion,
)
from kbj.data.private.kis.datasets import CONSENSUS_TRS
from kbj.data.private.kis.parse import KisEmpty
from kbj.services.auth.issuer import TOKEN_PATH
from tests.fakes.kis_server import (
    APP_KEY,
    APP_SECRET,
    CONSENSUS_TR_IDS,
    SYMBOLS,
    SYN_BROKERS,
    TRS,
    FakeKisServer,
    consensus_output,
    estimate_body,
    opinion_body,
    synthetic_output,
)
from tests.fixtures.synthetic.p4_world import CONSENSUS_FROM, TODAY, build_world

KST = ZoneInfo("Asia/Seoul")


def _fields(rows: Sequence[ConsensusRow]) -> set[tuple[object, ...]]:
    return {(r.code, r.snap_date, r.fiscal_ym, r.is_estimate, r.revenue, r.op, r.ni, r.eps,
             r.ebitda) for r in rows}  # fmt: skip


def test_trs_follow_the_constant() -> None:
    assert {TRS[tr] for tr in CONSENSUS_TR_IDS} == {
        CONSENSUS_TRS["consensus_estimate"][1],
        CONSENSUS_TRS["invest_opinion"][1],
    }
    assert CONSENSUS_TR_IDS == {TR_ESTIMATE.tr_id, TR_OPINION.tr_id}


@pytest.mark.parametrize("day", [CONSENSUS_FROM, date(2026, 10, 15), TODAY])
def test_world_estimates_round_trip(day: date) -> None:
    w = build_world()
    covered = {r.code for r in w.consensus}
    for code in sorted(covered):
        rows = w.consensus_rows(code, day)
        if not rows:  # 그날 상장 전(신규상장 #3)
            continue
        body = estimate_body(code, rows, day)
        got = parse_estimate(body, code, snap_date=day)
        assert _fields(got) == _fields(rows), code


def test_world_opinions_round_trip_and_window() -> None:
    w = build_world()
    start = TODAY - timedelta(days=90)
    for code in sorted({o.code for o in w.opinions}):
        body = consensus_output(TR_OPINION.tr_id, params_opinion(code, start, TODAY),
                                TODAY.strftime("%Y%m%d"), w)  # fmt: skip
        want = [o for o in w.opinion_rows(code, upto=TODAY) if o.opinion_date >= start]
        got = parse_opinion(body, code)
        assert {(o.broker, o.opinion_date, o.opinion, o.opinion_prev, o.target_price)
                for o in got} == {
            (o.broker, o.opinion_date, o.opinion, o.opinion_prev, o.target_price) for o in want
        }  # fmt: skip


def test_world_uncovered_codes_are_empty_and_latest_snapshot_is_served() -> None:
    w = build_world()
    ymd = TODAY.strftime("%Y%m%d")
    for code in ("994080", "994120", "994180", "994190"):  # 커버 없음(empty)
        body = consensus_output(TR_ESTIMATE.tr_id, params_estimate(code), ymd, w)
        with pytest.raises(KisEmpty):
            parse_estimate(body, code, snap_date=TODAY)
        op = consensus_output(TR_OPINION.tr_id, params_opinion(code, TODAY, TODAY), ymd, w)
        with pytest.raises(KisEmpty):
            parse_opinion(op, code)
    # 휴일(일요일 11-15)에 부르면 그 이전 마지막 스냅(11-13)을 준다
    sun = consensus_output(TR_ESTIMATE.tr_id, params_estimate("994010"), "20261115", w)
    assert sun["output1"]["estdate"] == "20261113"


def test_seeded_outputs_outside_the_world_are_deterministic() -> None:
    code = SYMBOLS[0]
    a = synthetic_output(TR_ESTIMATE.tr_id, params_estimate(code), "20261007")
    b = synthetic_output(TR_ESTIMATE.tr_id, params_estimate(code), "20261007")
    assert a == b
    rows = parse_estimate(a, code, snap_date=date(2026, 10, 7))
    assert len(rows) == 5 and [r.is_estimate for r in rows] == [False, False, True, True, True]
    ops = parse_opinion(
        synthetic_output(
            TR_OPINION.tr_id, params_opinion(code, date(2026, 7, 9), date(2026, 10, 7)), "20261007"
        ),
        code,
    )
    assert {o.broker for o in ops} == set(SYN_BROKERS)
    assert all(date(2026, 7, 9) <= o.opinion_date <= date(2026, 10, 7) for o in ops)


def test_encoder_refuses_amounts_not_in_output_units() -> None:
    w = build_world()
    (row,) = [r for r in w.consensus_rows("994010", TODAY) if r.fiscal_ym == "202612"]
    assert row.revenue is not None
    bad = replace(row, revenue=row.revenue + 1)
    with pytest.raises(ValueError, match="출력 단위"):
        estimate_body("994010", [bad], TODAY)
    assert opinion_body([])["output"] == []


def _token(server: FakeKisServer) -> str:
    r = server.handle(
        httpx.Request(
            "POST",
            "https://x" + TOKEN_PATH,
            json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        ),
        "auth",
    )
    return r.json()["access_token"]


def _get(server: FakeKisServer, tok: str, tr: str, params: dict[str, str]) -> httpx.Response:
    req = httpx.Request(
        "GET",
        "https://x" + TRS[tr],
        params=params,
        headers={"authorization": f"Bearer {tok}", "appkey": APP_KEY, "appsecret": APP_SECRET,
                 "tr_id": tr},
    )  # fmt: skip
    return server.handle(req, "scheduler")


def test_server_serves_world_and_seeded_codes_over_http() -> None:
    now = datetime(2026, 11, 16, 16, 0, tzinfo=KST)
    w = build_world()
    server = FakeKisServer(lambda: now, consensus=w)
    tok = _token(server)
    r = _get(server, tok, TR_ESTIMATE.tr_id, params_estimate("994010"))
    assert r.status_code == 200
    assert _fields(parse_estimate(r.json(), "994010", snap_date=TODAY)) == _fields(
        w.consensus_rows("994010", TODAY)
    )
    seeded = _get(server, tok, TR_ESTIMATE.tr_id, params_estimate(SYMBOLS[3]))
    assert seeded.json() == synthetic_output(
        TR_ESTIMATE.tr_id, params_estimate(SYMBOLS[3]), "20261116"
    )
    # 경로가 다르면 거절(TR 표와 맞아야 한다)
    bad = httpx.Request(
        "GET",
        "https://x" + TRS[TR_OPINION.tr_id],
        params=params_estimate("994010"),
        headers={"authorization": f"Bearer {tok}", "appkey": APP_KEY, "appsecret": APP_SECRET,
                 "tr_id": TR_ESTIMATE.tr_id},
    )  # fmt: skip
    assert server.handle(bad, "scheduler").json()["msg_cd"] == "OPSQ0002"
    # 세계 없이 만든 서버는 세계 종목도 고정 시드 합성으로 준다(시뮬레이션)
    plain = FakeKisServer(lambda: now)
    body = _get(plain, _token(plain), TR_ESTIMATE.tr_id, params_estimate("994010")).json()
    assert len(parse_estimate(body, "994010", snap_date=TODAY)) == 5

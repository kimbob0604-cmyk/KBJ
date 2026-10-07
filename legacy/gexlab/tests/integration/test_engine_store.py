"""engine 저장·입력 읽기 통합 시험 — 실제 TimescaleDB (docs/phase3_design.md §1·§2, 003_engine).

- 003 마이그레이션: 다섯 표가 hypertable(ts 1일), DB 제약(옵션 IV 출처·T 환산 조합)
- 산출 쓰기: 읽어 보면 같은 값, 같은 사이클을 다시 쓰면 행이 늘지 않고 최신 값(DO UPDATE) —
  oi_changes 의 첫 스냅샷 칸(증감 없음)은 저장된 칸을 덮지 않는다
- 입력 읽기: 세션 태그로만(야간에 주간 전광판 행이 섞이지 않는다), upto 까지의 최신 행, 검증 실패
  행은 건너뛰어 그 앞의 성한 행, 선물 최신 행, 최종거래일(KIS 가 캘린더보다 먼저)
- 일별 지표 입력(metrics §5.4·§5.5): 그 세션 마지막 사이클의 월물 `atm_iv`(key 앞머리), 일별 이력,
  KRX 코스피200 월물 옵션 정규 행(야간·미니·위클리 제외)·선물 정규 정산가
- 플로우·선물 입력(metrics §6·§7): 그 세션 투자자별 최신 성한 행, 옵션 틱 거래일(가장 최근 n 개),
  틱 표본과 그 틱 앞 lookback 안의 같은 종목 engine F(없으면 뺀다), KIS REST 원문의 output1
- 섀도 운영 점검 읽기(설계 §4): 귀속 거래일 범위 산출의 (표, 이름, 행 플래그)별 수·null 묶음,
  engine 예외 health(태그가 기간 안 또는 태그 없이 창 안)

컨테이너는 tests/integration/conftest.py 가 세션마다 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest

from data.krx.models import KrxFuturesDaily, parse_option_rows
from data.store import OptionTickRecord, PostgresSink
from db.migrate import migrate
from services.auth.health import HealthEvent as AuthHealthEvent
from services.engine.records import (
    LevelRecord,
    MetricRecord,
    OiChangeRecord,
    OptionIvRecord,
    StrikeGexRecord,
)
from services.poller.records import ChainRecord, ExpiryRecord, FuturesRecord, InvestorRecord
from services.recorder.envelope import RawEnvelope
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

KST = ZoneInfo("Asia/Seoul")
D = date(2026, 9, 28)
N = date(2026, 9, 29)
T = datetime(2026, 9, 28, 10, 0, 30, tzinfo=KST)
ENGINE = ("levels", "metrics", "strike_gex", "option_iv", "oi_changes")


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("engine"))
    assert {"003_engine.sql", "004_flags.sql"} <= set(migrate(url))
    return url


def _rows(dsn: str, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as c:
        return c.execute(query.encode(), params or None).fetchall()


def test_engine_tables_are_daily_hypertables(dsn: str) -> None:
    got = _rows(
        dsn,
        "SELECT h.hypertable_name, d.column_name, d.time_interval "
        "FROM timescaledb_information.hypertables h "
        "JOIN timescaledb_information.dimensions d USING (hypertable_schema, hypertable_name) "
        "WHERE h.hypertable_name = ANY(%s)",
        list(ENGINE),
    )
    assert {n: (c, iv) for n, c, iv in got} == dict.fromkeys(ENGINE, ("ts", timedelta(days=1)))


def test_option_iv_source_and_rescaling_are_checked_by_the_db(dsn: str) -> None:
    ins = (
        "INSERT INTO option_iv (ts, trade_date, session, mrkt_cls, expiry, strike, cp, "
        "quote_source, oi, iv, source, rescaled, t_kis, t_years, quality) "
        "VALUES (now(), '2026-09-28', 'day', 'WKM', '260904', 1095, 'C', 'board', 1, "
        "%s, %s, %s, %s, 0.001, 'ok')"
    )
    for params in ((0.3, None, False, None), (None, "kis", False, None), (0.3, "kis", True, None)):
        with psycopg.connect(dsn) as c, pytest.raises(psycopg.errors.CheckViolation):
            c.execute(ins.encode(), params)


# ── 산출 쓰기 ──


def _outputs(value: float) -> tuple[list[Any], ...]:
    stamp: dict[str, Any] = {"ts": T, "trade_date": D, "session": "day"}
    levels = [
        LevelRecord(
            **stamp,
            scope=scope,
            name="flip",
            value=value,
            detail={"multi_cross": False, "crossings": [value]},
            quality="ok",
        )
        for scope in ("all", "nearest", "0dte")
    ]
    metrics = [
        MetricRecord(
            **stamp,
            metric="net_gex",
            scope="all",
            value=-value * 1e7,
            payload={"excluded_oi_ratio": 0.01},
            quality="estimated",
            flag="visible",
        ),
        MetricRecord(
            **stamp,
            metric="atm_iv",
            scope="series",
            key="WKM:260904",
            value=0.3,
            quality="ok",
            flag="visible",
        ),
    ]
    strikes = [
        StrikeGexRecord(
            **stamp,
            mrkt_cls=cls,
            expiry="261001",
            strike=Decimal("1095.00"),
            gex_call=1.0e9,
            gex_put=-value,
            gex=1.0e9 - value,
            forward=1095.1,
            excluded_oi_ratio=0.0,
            quality="ok",
        )
        for cls in ("WKM", "WKI")  # 같은 6자리 만기, 다른 시리즈
    ]
    ivs = [
        OptionIvRecord(
            **stamp,
            mrkt_cls="WKM",
            expiry="260904",
            strike=Decimal("1100.00"),
            cp="P",
            quote_source="board",
            price=Decimal("6.25"),
            price_kind="mid",
            prev_session=False,
            oi=120,
            iv=0.31,
            source="kis",
            rescaled=True,
            t_kis=0.5 / 365,
            reason="below_intrinsic",
            excluded=None,
            delta=-0.45,
            gamma=0.012,
            forward=1095.1,
            t_years=53 / 525600,
            quality="estimated",
        )
    ]
    oi = [
        OiChangeRecord(
            **stamp,
            mrkt_cls="",
            expiry="202610",
            strike=Decimal("1100.00"),
            cp="C",
            oi=150,
            prev_ts=T - timedelta(seconds=30),
            prev_oi=140,
            change=10,
            quality="ok",
        )
    ]
    return levels, metrics, strikes, ivs, oi


def _write(sink: PostgresSink, value: float) -> None:
    levels, metrics, strikes, ivs, oi = _outputs(value)
    sink.write_levels(levels)
    sink.write_metrics(metrics)
    sink.write_strike_gex(strikes)
    sink.write_option_iv(ivs)
    sink.write_oi_changes(oi)


def test_outputs_round_trip_and_a_recomputed_cycle_replaces_them(dsn: str) -> None:
    with PostgresSink(dsn, service="engine") as sink:
        _write(sink, 1090.25)
        counts = {t: _rows(dsn, f"SELECT count(*) FROM {t}")[0][0] for t in ENGINE}  # noqa: S608
        assert counts == {
            "levels": 3,
            "metrics": 2,
            "strike_gex": 2,
            "option_iv": 1,
            "oi_changes": 1,
        }
        _write(sink, 1091.5)  # 같은 사이클(ts)을 다시 계산 — 행은 그대로, 값은 최신
        again = {t: _rows(dsn, f"SELECT count(*) FROM {t}")[0][0] for t in ENGINE}  # noqa: S608
        assert again == counts
    lv = _rows(
        dsn,
        "SELECT ts, trade_date, session, value, detail, reasons FROM levels WHERE scope = 'all'",
    )
    assert lv == [
        (
            T.astimezone(UTC),
            D,
            "day",
            Decimal("1091.5"),
            {"crossings": [1091.5], "multi_cross": False},
            [],
        )
    ]
    iv = _rows(
        dsn,
        "SELECT iv, source, rescaled, t_kis, reason, delta, gamma, t_years, quality FROM option_iv",
    )
    ((sigma, source, rescaled, t_kis, reason, delta, gamma, t_years, quality),) = iv
    assert (float(sigma), source, rescaled, reason, quality) == (
        0.31,
        "kis",
        True,
        "below_intrinsic",
        "estimated",
    )
    assert float(t_kis) == pytest.approx(0.5 / 365) and float(t_years) == pytest.approx(53 / 525600)
    assert (float(delta), float(gamma)) == (-0.45, 0.012)
    sg = _rows(dsn, "SELECT mrkt_cls, gex_put FROM strike_gex ORDER BY mrkt_cls")
    assert sg == [("WKI", Decimal("-1091.5")), ("WKM", Decimal("-1091.5"))]
    m = _rows(dsn, "SELECT metric, scope, key, payload, flag FROM metrics ORDER BY metric")
    assert m[0][:3] == ("atm_iv", "series", "WKM:260904")
    assert m[1][3] == {"excluded_oi_ratio": 0.01} and m[1][4] == "visible"
    assert json.dumps(m[1][3])  # jsonb


def test_a_first_snapshot_oi_cell_keeps_the_stored_change_and_outlier(dsn: str) -> None:
    """재기동한 engine 이 같은 스냅샷을 첫 스냅샷(증감 없음)으로 다시 써도 저장된 칸(증감·이상치
    격리)이 남는다. 증감이 있는 행은 그대로 고친다(같은 사이클 재계산·이상치로 고친 앞 칸)."""
    at = T + timedelta(minutes=5)
    cell: dict[str, Any] = {
        "ts": at,
        "trade_date": D,
        "session": "day",
        "mrkt_cls": "WKI",
        "expiry": "261001",
        "strike": Decimal("1097.50"),
        "cp": "P",
        "oi": 480,
    }
    stored = OiChangeRecord(
        **cell,
        prev_ts=at - timedelta(seconds=30),
        prev_oi=300,
        change=180,
        outlier=True,
        quality="ok",
    )
    first = OiChangeRecord(**cell, prev_ts=None, prev_oi=None, change=None, quality="stale")
    query = (
        "SELECT prev_oi, change, outlier, quality FROM oi_changes "
        "WHERE mrkt_cls = 'WKI' AND expiry = '261001' ORDER BY ts"
    )
    with PostgresSink(dsn, service="engine") as sink:
        sink.write_oi_changes([stored])
        sink.write_oi_changes([first])  # 재기동 — 같은 최신 행을 첫 스냅샷으로
        assert _rows(dsn, query) == [(300, 180, True, "ok")]
        sink.write_oi_changes([stored.model_copy(update={"prev_oi": 310, "change": 170})])
        assert _rows(dsn, query) == [(310, 170, True, "ok")]
        later = first.model_copy(update={"ts": at + timedelta(seconds=30)})
        sink.write_oi_changes([later])  # 새 키의 첫 스냅샷은 그대로 들어간다
        assert _rows(dsn, query) == [(310, 170, True, "ok"), (None, None, False, "stale")]


def test_levels_and_oi_changes_store_the_flag_and_older_rows_get_the_old_default(
    dsn: str,
) -> None:
    """004_flags: 계산 당시 플래그가 남는다(같은 사이클 재계산이면 최신 플래그). 플래그 없이 넣은
    행(004 전의 행)은 그때의 값 — 레벨 visible, OI 증감 shadow. DB 가 값도 막는다."""
    at = T + timedelta(hours=2)
    stamp: dict[str, Any] = {"ts": at, "trade_date": D, "session": "day"}
    level = LevelRecord(**stamp, scope="all", name="call_wall", value=1100.0, quality="ok")
    cell = OiChangeRecord(
        **stamp,
        mrkt_cls="WKM",
        expiry="261001",
        strike=Decimal("1090.00"),
        cp="P",
        oi=90,
        prev_ts=at - timedelta(seconds=30),
        prev_oi=80,
        change=10,
        quality="ok",
        flag="visible",
    )
    with PostgresSink(dsn, service="engine") as sink:
        sink.write_levels([level.model_copy(update={"flag": "shadow"})])
        sink.write_oi_changes([cell])
        lv = "SELECT flag FROM levels WHERE ts = %s AND name = 'call_wall'"
        oi = "SELECT flag FROM oi_changes WHERE ts = %s AND mrkt_cls = 'WKM'"
        assert _rows(dsn, lv, at) == [("shadow",)] and _rows(dsn, oi, at) == [("visible",)]
        sink.write_levels([level])  # 같은 사이클 재계산 — 플래그도 최신
        assert _rows(dsn, lv, at) == [("visible",)]
    old = at + timedelta(minutes=1)
    with psycopg.connect(dsn) as c:
        c.execute(
            b"INSERT INTO levels (ts, trade_date, session, scope, name, value, quality) "
            b"VALUES (%s, %s, 'day', 'all', 'flip', 1095, 'ok')",
            (old, D),
        )
        c.execute(
            b"INSERT INTO oi_changes (ts, trade_date, session, mrkt_cls, expiry, strike, cp, oi, "
            b"quality) VALUES (%s, %s, 'day', '', '202610', 1100, 'C', 5, 'ok')",
            (old, D),
        )
    assert _rows(dsn, "SELECT flag FROM levels WHERE ts = %s", old) == [("visible",)]
    assert _rows(dsn, "SELECT flag FROM oi_changes WHERE ts = %s", old) == [("shadow",)]
    with psycopg.connect(dsn) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute(b"UPDATE levels SET flag = 'hidden' WHERE ts = %s", (old,))


# ── 입력 읽기 ──


def _chain(at: datetime, **kw: Any) -> ChainRecord:
    base: dict[str, Any] = {
        "ts": at,
        "trade_date": D,
        "session": "day",
        "mrkt_cls": "WKM",
        "expiry": "260904",
        "strike": Decimal("1095.0"),
        "cp": "C",
        "source": "board",
        "last": Decimal("5.2"),
        "bid": Decimal("5.15"),
        "ask": Decimal("5.25"),
        "oi": 1000,
        "volume": 10,
    }
    return ChainRecord.model_validate(base | kw)


def _fut(at: datetime, **kw: Any) -> FuturesRecord:
    base: dict[str, Any] = {
        "ts": at,
        "trade_date": D,
        "session": "day",
        "code": "A01612",
        "market": "F",
        "source": "board",
        "price": Decimal("1095.10"),
        "remaining_days": 74,
    }
    return FuturesRecord.model_validate(base | kw)


def test_engine_inputs_are_the_session_latest_valid_rows(timescale: PgContainer) -> None:
    url = timescale.dsn(timescale.fresh_database("engine_in"))
    migrate(url)
    t0 = T
    night = datetime(2026, 9, 28, 21, 0, tzinfo=KST)
    with PostgresSink(url, service="engine") as s:
        s.write_chain(
            [
                _chain(t0, oi=1000),
                _chain(t0 + timedelta(seconds=30), oi=1010),
                _chain(t0 + timedelta(seconds=60), oi=1020, quality="invalid"),  # 건너뛴다
                _chain(t0 + timedelta(seconds=90), oi=1030),  # upto 뒤
                _chain(t0 + timedelta(seconds=30), source="fill", bid=None, ask=None, oi=1011),
                _chain(t0 + timedelta(seconds=30), mrkt_cls="WKI", expiry="260904", oi=7),
            ]
        )
        s.write_chain(
            [_chain(night, trade_date=N, session="night", source="fill", bid=None, ask=None)]
        )
        s.write_futures(
            [
                _fut(t0),
                _fut(t0 + timedelta(seconds=30), price=Decimal("1095.60")),
                _fut(t0 + timedelta(seconds=40), price=None, quality="invalid"),
                _fut(t0 + timedelta(seconds=30), code="A01703", price=Decimal("1085.00")),
                _fut(night, trade_date=N, session="night", market="CM", source="single"),
            ]
        )
        s.write_expiries(
            [
                ExpiryRecord(
                    ts=t0,
                    trade_date=D,
                    session="day",
                    mrkt_cls="WKM",
                    expiry="260904",
                    last_trade_date=D,
                    source="kis",
                ),
                ExpiryRecord(
                    ts=t0 + timedelta(seconds=5),
                    trade_date=D,
                    session="day",
                    mrkt_cls="WKM",
                    expiry="260904",
                    last_trade_date=date(2026, 9, 29),
                    source="calendar",
                ),  # 늦지만 캘린더 — KIS 가 먼저
                ExpiryRecord(
                    ts=t0,
                    trade_date=D,
                    session="day",
                    mrkt_cls="",
                    expiry="202610",
                    last_trade_date=date(2026, 10, 8),
                    source="calendar",
                ),
                ExpiryRecord(
                    ts=t0 - timedelta(days=30),
                    trade_date=date(2026, 8, 28),
                    session="day",
                    mrkt_cls="",
                    expiry="202609",
                    last_trade_date=date(2026, 9, 10),
                    source="kis",
                ),  # since 앞
            ]
        )
        upto = t0 + timedelta(seconds=60)
        assert s.chain_max_ts(D, "day") == t0 + timedelta(seconds=90)
        # 따라잡기의 여유 — upto 까지(그 뒤 행은 알림이 오는 중)
        assert s.chain_max_ts(D, "day", upto=t0 + timedelta(seconds=45)) == t0 + timedelta(
            seconds=30
        )
        assert s.chain_max_ts(D, "day", upto=t0 - timedelta(seconds=1)) is None
        assert s.chain_max_ts(N, "night") == night
        rows = s.chain_latest(D, "day", upto)
        got = sorted((r.mrkt_cls, r.source, r.oi, r.ts) for r in rows)
        assert got == [
            ("WKI", "board", 7, t0 + timedelta(seconds=30)),
            ("WKM", "board", 1010, t0 + timedelta(seconds=30)),
            ("WKM", "fill", 1011, t0 + timedelta(seconds=30)),
        ]
        night_rows = s.chain_latest(N, "night", night)
        assert [(r.session, r.source) for r in night_rows] == [("night", "fill")]  # 주간 행 없음
        fut = s.futures_latest(D, "day", upto)
        assert sorted((q.code, q.price) for q in fut) == [
            ("A01612", Decimal("1095.60")),
            ("A01703", Decimal("1085.00")),
        ]
        assert [(q.market, q.source) for q in s.futures_latest(N, "night", night)] == [
            ("CM", "single")
        ]
        assert sorted(s.expiry_dates(D - timedelta(days=14))) == [
            ("", "202610", date(2026, 10, 8), "calendar"),
            ("WKM", "260904", D, "kis"),
        ]


# ── 일별 지표 입력 ──


def _kospi_futures(bas_dd: str, expiry: str, setl: str, session: str = "정규") -> KrxFuturesDaily:
    tag = "주간" if session == "정규" else "야간"
    return KrxFuturesDaily.model_validate(
        {
            "BAS_DD": bas_dd,
            "PROD_NM": "코스피200 선물",
            "MKT_NM": session,
            "ISU_CD": f"A016{expiry[-2:]}{bas_dd[-2:]}",
            "ISU_NM": f"코스피200 F {expiry} ({tag})",
            "TDD_CLSPRC": setl,
            "SETL_PRC": setl if session == "정규" else "",
            "ACC_TRDVOL": "1000",
        }
    )


def test_daily_metric_inputs_read_what_the_engine_and_the_krx_loader_wrote(
    timescale: PgContainer,
) -> None:
    url = timescale.dsn(timescale.fresh_database("engine_daily"))
    migrate(url)
    close = datetime(2026, 9, 28, 15, 44, 30, tzinfo=KST)

    def atm(at: datetime, key: str, value: float, **kw: Any) -> MetricRecord:
        base: dict[str, Any] = {"ts": at, "trade_date": D, "session": "day"}
        return MetricRecord(
            **(base | kw),
            metric="atm_iv",
            scope="series",
            key=key,
            value=value,
            quality="ok",
            flag="visible",
        )

    daily = [
        MetricRecord(
            ts=datetime(2026, 9, d, 15, 45, tzinfo=KST),
            trade_date=date(2026, 9, d),
            session="day",
            metric="atm_iv_daily",
            scope="all",
            value=0.2 + d / 1000,
            payload={"source": "krx" if d < 23 else "self"},
            quality="ok",
            flag="shadow",
        )
        for d in (21, 22, 23)
    ]
    raw = json.loads((FIXTURES / "krx" / "opt_daily.json").read_text(encoding="utf-8"))
    opt = parse_option_rows(raw["20260923"])[0]
    fut = [
        _kospi_futures("20260922", "202612", "1120.10"),
        _kospi_futures("20260923", "202612", "1122.50"),
        _kospi_futures("20260923", "202703", "1110.00"),
        _kospi_futures("20260923", "202612", "1130.00", session="야간"),  # 정산가 없음
    ]
    with PostgresSink(url, service="engine") as s:
        s.write_metrics(
            [
                atm(close - timedelta(seconds=30), "M:202610", 0.30),
                atm(close, "M:202610", 0.31),
                atm(close, "M:202611", 0.29),
                atm(close, "WKI:261001", 0.40),
                atm(close + timedelta(hours=3), "M:202610", 0.5, trade_date=N, session="night"),
                *daily,
            ]
        )
        s.write_krx_options(opt, ts=close)
        s.write_krx_futures(fut, ts=close)
        last = s.metric_last(D, "day", "atm_iv", "series", "M:")
        assert [(r.key, r.value, r.ts) for r in last] == [
            ("M:202610", 0.31, close.astimezone(UTC)),
            ("M:202611", 0.29, close.astimezone(UTC)),
        ]
        assert s.metric_last(D, "day", "atm_iv", "series", "X") == []
        hist = s.metric_history("atm_iv_daily", "all", "", date(2026, 9, 22), date(2026, 9, 23))
        assert [(r.trade_date, r.payload["source"]) for r in hist] == [
            (date(2026, 9, 22), "krx"),
            (date(2026, 9, 23), "self"),
        ]
        assert s.krx_option_days(date(2026, 9, 1), date(2026, 9, 30)) == [date(2026, 9, 23)]
        assert s.krx_option_days(date(2026, 9, 24), date(2026, 9, 30)) == []
        rows = s.krx_iv_rows(date(2026, 9, 23), "202610")  # 코스피200 월물 정규 행만
        assert rows == [
            (Decimal("745.00"), "C", None, Decimal("31.50"), 0),
            (Decimal("1100.00"), "C", Decimal("44.70"), Decimal("35.20"), 72),
            (Decimal("1100.00"), "P", Decimal("25.30"), Decimal("39.80"), 887),
        ]
        assert s.krx_futures_settles(date(2026, 9, 1), date(2026, 9, 30)) == [
            (date(2026, 9, 22), "202612", Decimal("1120.10")),
            (date(2026, 9, 23), "202612", Decimal("1122.50")),
            (date(2026, 9, 23), "202703", Decimal("1110.00")),
        ]


def test_flow_inputs_read_what_the_poller_ws_gateway_and_engine_wrote(
    timescale: PgContainer,
) -> None:
    url = timescale.dsn(timescale.fresh_database("engine_flow"))
    migrate(url)

    def inv(at: datetime, net: int, **kw: Any) -> InvestorRecord:
        base: dict[str, Any] = {
            "ts": at,
            "trade_date": D,
            "session": "day",
            "market_code": "K2I",
            "sector_code": "OC01",
            "investor": "scrt",
            "net_qty": net,
        }
        return InvestorRecord(**(base | kw))

    def opt_tick(at: datetime, seq: int, qty: int, *, day: date = D, strike: str = "1100") -> Any:
        return OptionTickRecord(
            ts=at,
            trade_date=day,
            session="day",
            received_at=at,
            tr_id="H0IOCNT0",
            code="B01610W1",
            seq=seq,
            price=Decimal("5.20"),
            qty=qty,
            mrkt_cls="",
            expiry="202610",
            strike=Decimal(strike),
            cp="C",
        )

    def iv_row(at: datetime, forward: float | None) -> OptionIvRecord:
        (row,) = _outputs(1.0)[3]
        return row.model_copy(
            update={
                "ts": at,
                "mrkt_cls": "",
                "expiry": "202610",
                "strike": Decimal("1100.00"),
                "cp": "C",
                "forward": forward,
            }
        )

    t0 = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    prev = date(2026, 9, 25)
    with PostgresSink(url, service="engine") as s:
        s.write_investor(
            [
                inv(t0, 10),
                inv(t0 + timedelta(seconds=60), 20),
                inv(t0 + timedelta(seconds=120), 30, quality="invalid"),  # 검증 실패 — 앞 행
                inv(t0 + timedelta(seconds=60), -5, sector_code="OP01"),
                inv(t0 + timedelta(seconds=600), 99),  # upto 뒤
                inv(t0, 7, trade_date=N, session="night"),
            ]
        )
        latest = s.investor_latest(D, "day", t0 + timedelta(seconds=300))
        assert sorted((r.sector_code, r.net_qty) for r in latest) == [("OC01", 20), ("OP01", -5)]
        assert s.investor_latest(D, "night", t0) == []

        s.write_option_iv([iv_row(t0, 1095.5), iv_row(t0 + timedelta(seconds=30), 1096.0)])
        s.write_opt_ticks(
            [
                opt_tick(t0 + timedelta(seconds=10), 1, 30),  # F 1095.5 (10초 앞)
                opt_tick(t0 + timedelta(seconds=40), 2, 5),  # F 1096.0
                opt_tick(t0 + timedelta(minutes=30), 3, 7),  # lookback 10분 밖 — 뺀다
                opt_tick(t0 - timedelta(seconds=5), 4, 9),  # 앞선 F 없음 — 뺀다
                opt_tick(t0 + timedelta(seconds=50), 5, 0),  # 체결량 0 — 뺀다
                opt_tick(t0 + timedelta(seconds=45), 6, 2, strike="1102.5"),  # 그 종목 F 없음
                opt_tick(t0 - timedelta(days=3), 7, 1, day=prev),
            ]
        )
        assert s.opt_tick_days(date(2026, 9, 29), 20) == [prev, D]
        assert s.opt_tick_days(D, 20) == [prev] and s.opt_tick_days(date(2026, 9, 29), 1) == [D]
        hist = s.opt_tick_history(D, D, timedelta(minutes=10))
        assert hist == [
            (D, "C", Decimal("1100.00"), 30, 1095.5),
            (D, "C", Decimal("1100.00"), 5, 1096.0),
        ]

        body = {"rt_cd": "0", "output1": {"futs_shrn_iscd": "A01612", "basis": "5.97"}}
        envs = [
            RawEnvelope(
                received_at=t0 + timedelta(seconds=i),
                source="kis_rest",
                tr_id=tr,
                key=f"k{i}",
                payload=payload,
                trade_date=D,
                session="day",
            )
            for i, (tr, payload) in enumerate(
                [
                    ("FHKIF03020200", body),
                    ("FHKIF03020200", {"rt_cd": "1", "msg1": "오류"}),  # output1 없음 — 뺀다
                    ("FHMIF10000000", body),  # 다른 TR
                    ("FHKIF03020200", body | {"output1": {"futs_shrn_iscd": "A01703"}}),
                ]
            )
        ]
        s.write_raw(envs)
        end = t0 + timedelta(hours=1)
        got = s.kis_rest_outputs("FHKIF03020200", t0 - timedelta(seconds=1), end)
        assert [(ts, d, ss, o["futs_shrn_iscd"]) for ts, d, ss, o in got] == [
            (t0.astimezone(UTC), D, "day", "A01612"),
            ((t0 + timedelta(seconds=3)).astimezone(UTC), D, "day", "A01703"),
        ]
        assert s.kis_rest_outputs("FHKIF03020200", t0, end)[0][3] == {
            "futs_shrn_iscd": "A01703"
        }  # after 는 열린 끝


# ── 섀도 운영 점검 읽기 (scripts/shadow_report.py) ──


def test_shadow_report_reads_count_outputs_by_flag_and_the_engine_failures(
    timescale: PgContainer,
) -> None:
    """(표, 이름, 행 플래그)별 행·사이클·invalid·예외·null — 기간 밖 행은 세지 않고, 플래그가
    기간 중에 바뀌면 두 줄. null 묶음은 고른 칸만(없으면 None), 예외(`error`) 행은 빼고. health 는
    engine 의 고른 종류만 — 태그가 기간 안이거나 태그 없이 시각이 창 안."""
    url = timescale.dsn(timescale.fresh_database("shadow"))
    migrate(url)
    d1, d2, before = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 18)
    t1 = datetime(2026, 9, 21, 10, 0, tzinfo=KST)
    t2 = t1 + timedelta(seconds=30)

    def metric(at: datetime, td: date, scope: str, value: float | None, **kw: Any) -> MetricRecord:
        base: dict[str, Any] = {"quality": "ok", "flag": "shadow", "payload": {}}
        row = {"ts": at, "trade_date": td, "session": "day", "metric": "vex", "scope": scope}
        return MetricRecord.model_validate(row | {"value": value} | base | kw)

    rows = [
        metric(t1, d1, "all", 1.5e8, payload={"expiries": ["WKM:261001"]}),
        metric(t1, d1, "0dte", None, payload={"expiries": []}),  # 빈 범위 — 명세 null
        metric(t2, d1, "all", None, payload={"error": "ValueError"}, quality="invalid"),
        metric(t2, d2, "nearest", None, payload={"expiries": ["M:202610"]}, flag="visible"),
        metric(t1 - timedelta(days=3), before, "all", None),  # 기간 밖
    ]
    flip = LevelRecord(
        ts=t1,
        trade_date=d1,
        session="day",
        scope="all",
        name="flip",
        value=None,
        detail={"crossings": [], "multi_cross": False},
        quality="ok",
    )
    cells = [
        OiChangeRecord(
            ts=at,
            trade_date=d1,
            session="day",
            mrkt_cls="WKM",
            expiry="261001",
            strike=Decimal("1100.00"),
            cp="C",
            oi=oi,
            prev_ts=prev,
            prev_oi=None if prev is None else 10,
            change=None if prev is None else oi - 10,
            quality="ok",
        )
        for at, oi, prev in ((t1, 10, None), (t2, 15, t1))
    ]

    def tag(_: datetime) -> tuple[date, str]:
        return d1, "day"

    def ev(kind: str, text: str, at: datetime, service: str = "engine") -> AuthHealthEvent:
        return AuthHealthEvent(kind, text, at, "warning", service=service)

    start = datetime(2026, 9, 18, 17, 50, tzinfo=KST)
    end = datetime(2026, 9, 22, 17, 50, tzinfo=KST)
    boom = "vex/all/: ValueError: boom"
    with PostgresSink(url, service="engine") as sink:
        sink.write_metrics(rows)
        sink.write_levels([flip])
        sink.write_oi_changes(cells)
        sink.write_health(
            [
                ev("engine_metric_failed", boom, t2),
                ev("engine_metric_failed", boom, t2 + timedelta(minutes=10)),  # 10분 뒤 한 번 더
                ev("engine_series_stale", "M:202610: 옛 행", t2),  # 고르지 않은 종류
                ev("engine_metric_failed", "x", t2, service="poller"),  # engine 이 아니다
            ],
            tagger=tag,
        )
        sink.write_health(
            [
                ev("engine_daily_failed", "일별 지표 실패(2026-09-21): E: x", t1.replace(hour=16)),
                ev("engine_daily_failed", "일별 지표 실패(2026-09-17): E: x", start - timedelta(1)),
            ]
        )
        counts = sink.engine_output_counts(d1, d2)
        nulls = sink.engine_null_groups(d1, d2, ("expiries", "crossings"))
        kinds = ("engine_metric_failed", "engine_daily_failed", "engine_cycle_failed")
        failures = sink.engine_failures(d1, d2, start, end, kinds)
    assert counts == [
        ("levels", "flip", "visible", 1, 1, 0, 0, 1),
        ("metrics", "vex", "shadow", 3, 2, 1, 1, 1),
        ("metrics", "vex", "visible", 1, 1, 0, 0, 1),
        ("oi_changes", "oi_changes", "shadow", 2, 2, 0, 0, 1),
    ]
    assert nulls == [
        ("levels", "flip", "visible", "all", "ok", {"expiries": None, "crossings": []}, 1),
        ("metrics", "vex", "shadow", "0dte", "ok", {"expiries": [], "crossings": None}, 1),
        (
            "metrics",
            "vex",
            "visible",
            "nearest",
            "ok",
            {"expiries": ["M:202610"], "crossings": None},
            1,
        ),
        ("oi_changes", "oi_changes", "shadow", "", "ok", {"first_snapshot": True}, 1),
    ]
    assert failures == [
        ("engine_daily_failed", "일별 지표 실패(2026-09-21): E: x", 1),
        ("engine_metric_failed", boom, 2),
    ]

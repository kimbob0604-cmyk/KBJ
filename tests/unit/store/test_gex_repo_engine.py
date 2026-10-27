"""engine 저장·입력 읽기 — DB 없이 (KBJ `kbj.store.repos.gex`, 0006_gex.sql — GX 003·004).

KBJ P7(묶음 ENG): GX `tests/unit/test_store_engine.py`(32 항목)를 KBJ 저장소로 옮겼다 — 단언은 같고
대상만 바꿨다(GX `data.store.PostgresSink` → `PgGexRepo`, GX 003/004 → KBJ 0006, 표 이름에 스키마
`prv_gex.`). 30 항목 승격, 2 항목 legacy 에 남김(대체 기록 — legacy/gexlab/MIGRATION.md 'P7'):

- GX `test_applied_migrations_are_left_alone`(GX 001·002 를 고치지 않았나) — KBJ 는 GX
  마이그레이션을 돌리지 않는다(D-P7-4, 스키마 정본 0006). KBJ 의 같은 성질은
  `tests/test_store_layout.py`(적용된 마이그레이션 체크섬)가 본다.
  legacy 에 그대로 둔다(GX 자기 통합 DB 가 쓴다)
- GX `test_shadow_report_reads_…`(섀도 운영 점검 SQL) — 도구 `shadow_report` 는 legacy 에 남는다
  (D-P7-4). legacy 에 그대로 둔다
- GX `stats.by_table` 단언 한 줄(싱크 통계)은 KBJ 저장소에 통계가 없어 뺐다 — 같은 내용(표마다 묶음
  하나·행 수)은 묶음·행 수 단언으로 본다

- 0006 표 정의: 키·청크·세션·품질·옵션 IV 의 출처 세 필드(검증 수정 3)·계산 당시 플래그
- 레코드(`kbj.core.gex.records`) 필드 = 표 열, 행 튜플(jsonb 정규화), 값 검증(유한·조합)
- 저장소: 산출은 DO UPDATE(같은 사이클 재계산 멱등 — oi_changes 첫 스냅샷 칸은 덮지 않는다),
  입력 읽기 SQL(세션 태그·invalid 제외·최신 행), 일별 지표 입력 읽기, 플로우·선물 입력 읽기
실제 DB 는 tests/integration/test_gex_engine_pg.py.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ValidationError

from kbj.core.gex.records import (
    LevelRecord,
    MetricRecord,
    OiChangeRecord,
    OptionIvRecord,
    StrikeGexRecord,
)
from kbj.store.repos import gex as store

KST = ZoneInfo("Asia/Seoul")
T = datetime(2026, 9, 28, 10, 0, 30, tzinfo=KST)
D = date(2026, 9, 28)
MIG = Path(__file__).resolve().parents[3] / "kbj" / "store" / "migrations"
GEX_SQL = (MIG / "0006_gex.sql").read_text(encoding="utf-8")
STAMP: dict[str, Any] = {"ts": T, "trade_date": D, "session": "day"}


# ── 0006 DDL 읽기 (GX tests/unit/test_store_sql.py 의 TABLES·hypertables·unique_keys 와 같은 뜻) ──


def _strip(sql_text: str) -> str:
    return "\n".join(line.split("--", 1)[0].rstrip() for line in sql_text.splitlines())


def _tables(sql_text: str) -> dict[str, dict[str, str]]:
    """표 이름(스키마 뺌) → 열 → 정의(쉼표 뺌). 표 단위 CHECK 는 `#n`, UNIQUE 는 `UNIQUE#n`."""
    out: dict[str, dict[str, str]] = {}
    body = _strip(sql_text)
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS prv_gex\.(\w+) \((.*?)\n\);", body, re.S):
        cols: dict[str, str] = {}
        n = 0
        for line in m.group(2).splitlines():
            s = line.strip().rstrip(",")
            if not s:
                continue
            head = s.split()[0]
            if head in {"CHECK", "UNIQUE"}:
                n += 1
                cols[f"{'#' if head == 'CHECK' else 'UNIQUE#'}{n}"] = s
            else:
                cols[head] = s[len(head) :].strip()
        out[m.group(1)] = cols
    return out


TABLES = _tables(GEX_SQL)


def hypertables(sql_text: str) -> dict[str, tuple[str, str]]:
    return {
        m.group(1): (m.group(2), m.group(3))
        for m in re.finditer(
            r"create_hypertable\('prv_gex\.(\w+)', by_range\('(\w+)', INTERVAL '([^']+)'\)",
            sql_text,
        )
    }


def unique_keys(cols: dict[str, str]) -> list[tuple[str, ...]]:
    return [
        tuple(c.strip() for c in v[v.index("(") + 1 : v.rindex(")")].split(","))
        for k, v in cols.items()
        if k.startswith("UNIQUE#")
    ]


# ── 가짜 연결 (GX test_store_sql.Factory 와 같은 뜻 — 쓴 묶음·읽은 SQL·커밋 수를 남긴다) ──


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, query: Any, rows: Sequence[tuple[Any, ...]]) -> None:
        self.conn.batches.append((query.as_string(None), list(rows)))

    def execute(self, query: Any, params: tuple[Any, ...]) -> None:
        self.conn.reads.append((query.as_string(None), params))
        self._rows = self.conn.factory.results.pop(0) if self.conn.factory.results else []

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Conn:
    def __init__(self, factory: Factory) -> None:
        self.factory = factory
        self.batches: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.reads: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, exc_type: object, *exc: object) -> None:
        if exc_type is None:
            self.commits += 1

    def cursor(self) -> _Cursor:
        return _Cursor(self)


class Factory:
    """부를 때마다 같은 가짜 연결 하나(묶음·읽기를 한 곳에 모은다)."""

    def __init__(self, results: list[list[tuple[Any, ...]]] | None = None) -> None:
        self.results = list(results or [])
        self.conns = [_Conn(self)]

    def __call__(self) -> Any:
        return self.conns[0]


def _sink(f: Factory) -> store.PgGexRepo:
    return store.PgGexRepo(f)


def _from(q: str) -> str:
    return q.split(" FROM ")[1].split()[0]


# ── 0006 표 정의 ──


def test_engine_tables_are_daily_hypertables_keyed_by_the_cycle_ts() -> None:
    ht = hypertables(GEX_SQL)
    assert {
        k: v
        for k, v in ht.items()
        if k in ("levels", "metrics", "strike_gex", "option_iv", "oi_changes")
    } == dict.fromkeys(
        ("levels", "metrics", "strike_gex", "option_iv", "oi_changes"), ("ts", "1 day")
    )
    assert unique_keys(TABLES["levels"]) == [("ts", "scope", "name")]
    assert unique_keys(TABLES["metrics"]) == [("ts", "metric", "scope", "key")]
    assert unique_keys(TABLES["strike_gex"]) == [("ts", "mrkt_cls", "expiry", "strike")]
    # 시리즈 키에 시장분류가 든다 — WKM·WKI 261001 이 같은 6자리 만기다
    for t in ("option_iv", "oi_changes"):
        assert unique_keys(TABLES[t]) == [("ts", "mrkt_cls", "expiry", "strike", "cp")], t
    for t in ("levels", "metrics", "strike_gex", "option_iv", "oi_changes"):
        assert "CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid'))" in TABLES[t]["quality"]


def test_option_iv_keeps_source_rescaled_and_t_kis_together() -> None:
    cols = TABLES["option_iv"]
    assert "CHECK (source IN ('self', 'kis'))" in cols["source"]
    assert cols["rescaled"].startswith("boolean NOT NULL") and cols["t_kis"].startswith("numeric")
    checks = [v for k, v in cols.items() if k.startswith("#")]
    assert "CHECK ((iv IS NULL) = (source IS NULL))" in checks
    assert "CHECK (NOT rescaled OR t_kis IS NOT NULL)" in checks
    assert "CHECK (flag IN ('off', 'shadow', 'visible'))" in TABLES["metrics"]["flag"]
    assert "CHECK (scope IN ('all', 'nearest', '0dte'))" in TABLES["levels"]["scope"]


def test_levels_and_oi_changes_keep_the_flag_of_their_computation() -> None:
    """GX 004_flags: metrics 처럼 levels·oi_changes 도 계산 당시 기능 플래그를 남긴다(설계 §4 —
    shadow 는 저장만). 이미 있는 행의 기본값은 그때 쓰던 값 — 레벨은 늘 발행했으니 visible,
    OI 증감은 새 지표 기본 shadow. KBJ 0006 은 004 의 열을 표 정의에 합쳤다(같은 정의)."""
    check = "CHECK (flag IN ('off', 'shadow', 'visible'))"
    assert TABLES["levels"]["flag"] == f"text NOT NULL DEFAULT 'visible' {check}"
    assert TABLES["oi_changes"]["flag"] == f"text NOT NULL DEFAULT 'shadow' {check}"


# ── 레코드 ──


def _level(**kw: Any) -> LevelRecord:
    base: dict[str, Any] = {
        **STAMP,
        "scope": "all",
        "name": "flip",
        "value": 1090.5,
        "detail": {"multi_cross": True, "crossings": [1080.25, 1090.5]},
        "quality": "estimated",
        "reasons": ("s_ref_stale",),
        "flag": "visible",
    }
    return LevelRecord.model_validate(base | kw)


def _metric(**kw: Any) -> MetricRecord:
    base: dict[str, Any] = {
        **STAMP,
        "metric": "net_gex",
        "scope": "nearest",
        "value": -1.5e10,
        "payload": {"excluded_oi_ratio": 0.02, "expiries": ["WKM:260904"]},
        "quality": "ok",
        "flag": "visible",
    }
    return MetricRecord.model_validate(base | kw)


def _strike(**kw: Any) -> StrikeGexRecord:
    base: dict[str, Any] = {
        **STAMP,
        "mrkt_cls": "WKM",
        "expiry": "260904",
        "strike": Decimal("1095.00"),
        "gex_call": 2.5e9,
        "gex_put": -1.0e9,
        "gex": 1.5e9,
        "forward": 1095.1,
        "excluded_oi_ratio": 0.05,
        "quality": "ok",
    }
    return StrikeGexRecord.model_validate(base | kw)


def _iv(**kw: Any) -> OptionIvRecord:
    base: dict[str, Any] = {
        **STAMP,
        "mrkt_cls": "WKI",
        "expiry": "261001",
        "strike": Decimal("1100.00"),
        "cp": "P",
        "quote_source": "board",
        "price": Decimal("6.25"),
        "price_kind": "mid",
        "prev_session": False,
        "oi": 120,
        "iv": 0.31,
        "source": "kis",
        "rescaled": True,
        "t_kis": 0.5 / 365,
        "reason": "below_intrinsic",
        "excluded": None,
        "delta": -0.45,
        "gamma": 0.012,
        "forward": 1095.1,
        "t_years": 53 / 525600,
        "quality": "estimated",
    }
    return OptionIvRecord.model_validate(base | kw)


def _oi(**kw: Any) -> OiChangeRecord:
    base: dict[str, Any] = {
        **STAMP,
        "mrkt_cls": "",
        "expiry": "202610",
        "strike": Decimal("1100.00"),
        "cp": "C",
        "oi": 150,
        "prev_ts": T - timedelta(seconds=30),
        "prev_oi": 140,
        "change": 10,
        "quality": "ok",
        "flag": "shadow",
    }
    return OiChangeRecord.model_validate(base | kw)


RECORDS: list[tuple[store.GxTable, type[BaseModel]]] = [
    (store.LEVELS, LevelRecord),
    (store.METRICS, MetricRecord),
    (store.STRIKE_GEX, StrikeGexRecord),
    (store.OPTION_IV, OptionIvRecord),
    (store.OI_CHANGES, OiChangeRecord),
]


@pytest.mark.parametrize(("table", "model"), RECORDS, ids=lambda x: getattr(x, "name", ""))
def test_record_fields_are_exactly_the_table_columns(
    table: store.GxTable, model: type[BaseModel]
) -> None:
    assert tuple(model.model_fields) == table.columns
    assert table.upsert  # 같은 사이클을 다시 계산하면 최신 값
    assert table in store.ENGINE_TABLES


def test_level_and_metric_rows_are_canonical_json() -> None:
    row = store.level_row(_level())
    assert row[0].tzinfo is UTC and row[0] == T
    assert row[3:6] == ("all", "flip", 1090.5)
    assert json.loads(row[6]) == {"crossings": [1080.25, 1090.5], "multi_cross": True}
    assert row[6] == '{"crossings":[1080.25,1090.5],"multi_cross":true}'  # 키 정렬·공백 없음
    assert row[7:] == ("estimated", '["s_ref_stale"]', "visible")
    assert store.level_row(_level(flag="shadow"))[-1] == "shadow"
    m = store.metric_row(_metric())
    assert m[3:7] == ("net_gex", "nearest", "", -1.5e10)
    assert json.loads(m[7]) == {"excluded_oi_ratio": 0.02, "expiries": ["WKM:260904"]}
    assert m[8:] == ("ok", "visible")


def test_attr_rows_follow_the_column_order() -> None:
    def as_dict(table: store.GxTable, rec: BaseModel) -> dict[str, Any]:
        return dict(zip(table.columns, store.attr_row(table, rec), strict=True))

    got = as_dict(store.OPTION_IV, _iv())
    assert got["source"] == "kis" and got["rescaled"] is True and got["t_kis"] == 0.5 / 365
    assert got["strike"] == Decimal("1100.00") and got["ts"].tzinfo is UTC
    s = as_dict(store.STRIKE_GEX, _strike())
    assert (s["gex_call"], s["gex_put"], s["gex"]) == (2.5e9, -1.0e9, 1.5e9)
    o = as_dict(store.OI_CHANGES, _oi())
    assert o["prev_ts"].tzinfo is UTC and o["change"] == 10 and o["flag"] == "shadow"


def test_level_and_oi_flags_default_to_their_catalog_defaults_and_are_checked() -> None:
    """레벨은 Phase 2 핵심(기본 visible), OI 증감은 새 지표(기본 shadow) — engine 은 늘 적는다."""
    base = _level().model_dump()
    del base["flag"]
    assert LevelRecord.model_validate(base).flag == "visible"
    oi = _oi().model_dump()
    del oi["flag"]
    assert OiChangeRecord.model_validate(oi).flag == "shadow"
    with pytest.raises(ValidationError):
        _level(flag="hidden")
    with pytest.raises(ValidationError):
        _oi(flag="on")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_refused(bad: float) -> None:
    with pytest.raises(ValidationError):
        _level(value=bad)
    with pytest.raises(ValidationError):
        _metric(value=bad)
    with pytest.raises(ValidationError):
        _strike(gex=bad)
    with pytest.raises(ValidationError):
        _iv(gamma=bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"iv": None},  # 출처만 있다
        {"source": None},  # IV 만 있다
        {"rescaled": True, "t_kis": None},  # 옮겼다는데 T_KIS 가 없다
        {"excluded": "below_min_premium"},  # 뺀 종목에 그릭스
        {"source": "model"},  # 출처는 self·kis
        {"expiry": "2610"},
        {"ts": datetime(2026, 9, 28, 10, 0)},  # noqa: DTZ001 — naive
    ],
)
def test_option_iv_record_refuses_inconsistent_rows(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _iv(**bad)


def test_option_iv_record_for_an_excluded_option_has_no_greeks() -> None:
    r = _iv(
        excluded="no_price",
        delta=None,
        gamma=None,
        iv=None,
        source=None,
        price=None,
        price_kind=None,
        rescaled=False,
        t_kis=None,
        reason=None,
        quality="invalid",
    )
    assert r.excluded == "no_price" and r.iv is None


# ── 저장소 ──


def test_engine_writes_are_upserts_one_transaction_per_batch() -> None:
    f = Factory()
    s = _sink(f)
    s.write_levels([_level(), _level(name="call_wall", value=1100.0)])
    s.write_metrics([_metric()])
    s.write_strike_gex([_strike()])
    s.write_option_iv([_iv()])
    s.write_oi_changes([_oi()])
    s.write_levels([])
    batches = f.conns[0].batches
    assert [q.split('"')[3] for q, _ in batches] == [
        "levels",
        "metrics",
        "strike_gex",
        "option_iv",
        "oi_changes",
    ]
    assert all(q.startswith('INSERT INTO "prv_gex".') for q, _ in batches)
    assert all("DO UPDATE SET" in q for q, _ in batches)
    assert '"detail" = EXCLUDED."detail"' in batches[0][0] and "%s::jsonb" in batches[0][0]
    assert len(batches[0][1]) == 2 and f.conns[0].commits == 5
    assert [len(rows) for _, rows in batches] == [2, 1, 1, 1, 1]


def test_a_first_snapshot_oi_cell_never_overwrites_a_stored_cell() -> None:
    """재기동한 engine 이 세션 중간에 같은 최신 체인 행을 첫 스냅샷(증감 없음)으로 다시 써도 저장된
    증감·이상치가 남는다 — change 가 NULL 인 새 행은 같은 키를 DO UPDATE 하지 않는다. 증감이 있는
    행(이상치로 고친 앞 칸·같은 사이클 재계산)은 그대로 고친다."""
    assert store.OI_CHANGES.keep_if_null == "change"
    q = store.OI_CHANGES.insert_sql().as_string(None)  # pyright: ignore[reportArgumentType]
    assert '"outlier" = EXCLUDED."outlier"' in q
    assert q.endswith('WHERE EXCLUDED."change" IS NOT NULL')
    every = (
        store.RAW_MESSAGES, store.FUT_TICKS, store.OPT_TICKS, store.CHAIN_SNAPSHOTS,
        store.FUT_BOARD, store.INVESTOR_FLOW, store.SERIES_EXPIRIES, store.SESSION_LOG,
        store.HEALTH_EVENTS, *store.ENGINE_TABLES,
    )  # fmt: skip
    assert [t.name for t in every if t.keep_if_null is not None] == ["oi_changes"]


def _chain_db_row(**kw: Any) -> tuple[Any, ...]:
    base: dict[str, Any] = dict.fromkeys(store.CHAIN_SNAPSHOTS.columns)
    base |= {
        "ts": T.astimezone(UTC),
        "trade_date": D,
        "session": "day",
        "mrkt_cls": "WKM",
        "expiry": "260904",
        "strike": Decimal("1095.00"),
        "cp": "C",
        "source": "board",
        "last": Decimal("5.20"),
        "bid": Decimal("5.15"),
        "ask": Decimal("5.25"),
        "oi": 1200,
        "volume": 300,
        "iv_kis": Decimal("31.5"),
        "quality": "ok",
    }
    return tuple((base | kw)[c] for c in store.CHAIN_SNAPSHOTS.columns)


def _fut_db_row(**kw: Any) -> tuple[Any, ...]:
    base: dict[str, Any] = dict.fromkeys(store.FUT_BOARD.columns)
    base |= {
        "ts": T.astimezone(UTC),
        "trade_date": D,
        "session": "day",
        "market": "F",
        "code": "A01612",
        "source": "board",
        "price": Decimal("1095.10"),
        "remaining_days": 74,
        "quality": "ok",
    }
    return tuple((base | kw)[c] for c in store.FUT_BOARD.columns)


def test_engine_reads_ask_the_session_up_to_the_cycle_and_skip_invalid_rows() -> None:
    f = Factory(
        results=[
            [(T.astimezone(UTC),)],
            [(None,)],
            [_chain_db_row(), _chain_db_row(source="fill", bid=None, ask=None)],
            [_fut_db_row()],
            [("WKM", "260904", D, "kis"), ("", "202610", date(2026, 10, 8), "calendar")],
        ]
    )
    s = _sink(f)
    assert s.chain_max_ts(D, "day") == T
    assert s.chain_max_ts(D, "night") is None  # 아직 행이 없다
    rows = s.chain_latest(D, "day", T)
    assert [(r.source, r.bid) for r in rows] == [("board", Decimal("5.15")), ("fill", None)]
    assert rows[0].ts == T and rows[0].iv_kis == Decimal("31.5")
    fut = s.futures_latest(D, "day", T)
    assert [(q.code, q.price, q.remaining_days) for q in fut] == [
        ("A01612", Decimal("1095.10"), 74)
    ]
    since = D - timedelta(days=14)
    assert s.expiry_dates(since) == [
        ("WKM", "260904", D, "kis"),
        ("", "202610", date(2026, 10, 8), "calendar"),
    ]
    reads = f.conns[0].reads
    assert [_from(q) for q, _ in reads] == [
        '"prv_gex"."chain_snapshots"',
        '"prv_gex"."chain_snapshots"',
        '"prv_gex"."chain_snapshots"',
        '"prv_gex"."fut_board"',
        '"prv_gex"."series_expiries"',
    ]
    assert reads[0][1] == (D, "day", "invalid")
    assert reads[2][1] == (D, "day", T.astimezone(UTC), "invalid")  # 세션 태그로만, upto 까지
    assert "DISTINCT ON (mrkt_cls, expiry, strike, cp, source)" in reads[2][0]
    assert "ORDER BY mrkt_cls, expiry, strike, cp, source, ts DESC" in reads[2][0]
    assert "DISTINCT ON (market, code, source)" in reads[3][0]
    assert "price IS NOT NULL" in reads[3][0]
    assert "(source = 'kis') DESC, ts DESC" in reads[4][0] and reads[4][1] == (since, "invalid")
    assert all("%s" in q and "2026" not in q for q, _ in reads)  # 값은 파라미터로만


def test_chain_max_ts_can_stop_at_a_cutoff_for_the_catch_up() -> None:
    """engine 따라잡기는 알림 여유보다 오래된 행만 본다 — upto 까지의 max(ts)."""
    f = Factory(results=[[(T.astimezone(UTC),)]])
    s = _sink(f)
    assert s.chain_max_ts(D, "day", upto=T + timedelta(seconds=5)) == T
    ((query, params),) = f.conns[0].reads
    assert "AND ts <= %s" in query
    assert params == (D, "day", "invalid", (T + timedelta(seconds=5)).astimezone(UTC))


def test_engine_reads_refuse_naive_upto() -> None:
    s = _sink(Factory())
    with pytest.raises(ValueError):
        s.chain_latest(D, "day", datetime(2026, 9, 28, 10, 0))  # noqa: DTZ001
    with pytest.raises(ValueError):
        s.chain_max_ts(D, "day", upto=datetime(2026, 9, 28, 10, 0))  # noqa: DTZ001


def _metric_db_row(**kw: Any) -> tuple[Any, ...]:
    base: dict[str, Any] = {
        "ts": T.astimezone(UTC),
        "trade_date": D,
        "session": "day",
        "metric": "atm_iv",
        "scope": "series",
        "key": "M:202610",
        "value": Decimal("0.2512"),
        "payload": {"strikes": [1095.0]},
        "quality": "ok",
        "flag": "visible",
    }
    return tuple((base | kw)[c] for c in store.METRICS.columns)


def test_daily_metric_inputs_are_read_with_parameters_only() -> None:
    """일별 지표 입력(metrics §5.4·§5.5): 그 세션 마지막 사이클의 월물 `atm_iv`, 일별 이력, KRX
    코스피200 월물 옵션 정규 행·선물 정규 정산가."""
    first, last = date(2025, 9, 26), date(2026, 9, 25)
    f = Factory(
        results=[
            [_metric_db_row()],
            [_metric_db_row(metric="atm_iv_daily", scope="all", key="", payload={"source": "krx"})],
            [(date(2026, 9, 23),)],
            [(Decimal("1100.00"), "C", Decimal("47.05"), Decimal("34.00"), 86)],
            [(date(2026, 9, 23), "202612", Decimal("1122.06"))],
        ]
    )
    s = _sink(f)
    (close,) = s.metric_last(D, "day", "atm_iv", "series", "M:")
    assert (close.key, close.value, close.ts) == ("M:202610", 0.2512, T)
    (hist,) = s.metric_history("atm_iv_daily", "all", "", first, last)
    assert hist.payload == {"source": "krx"} and hist.key == ""
    assert s.krx_option_days(first, last) == [date(2026, 9, 23)]
    assert s.krx_iv_rows(date(2026, 9, 23), "202610") == [
        (Decimal("1100.00"), "C", Decimal("47.05"), Decimal("34.00"), 86)
    ]
    assert s.krx_futures_settles(first, last) == [(date(2026, 9, 23), "202612", Decimal("1122.06"))]
    reads = f.conns[0].reads
    assert [_from(q) for q, _ in reads] == [
        '"prv_gex"."metrics"',
        '"prv_gex"."metrics"',
        '"prv_gex"."krx_opt_daily"',
        '"prv_gex"."krx_opt_daily"',
        '"prv_gex"."krx_fut_daily"',
    ]
    assert reads[0][1] == (D, "day", "atm_iv", "series", "M:%") * 2
    assert "ts = (SELECT max(ts)" in reads[0][0] and "ESCAPE" in reads[0][0]
    assert reads[1][1] == ("atm_iv_daily", "all", "", first, last)
    assert reads[2][1] == ("kospi200", "day", first, last)
    assert reads[3][1] == (date(2026, 9, 23), "day", "kospi200", "202610")
    assert reads[4][1] == ("kospi200", "day", first, last)
    assert "setl_prc IS NOT NULL" in reads[4][0]
    assert all("2026" not in q and "kospi200" not in q for q, _ in reads)  # 값은 파라미터로만


def test_like_prefix_escapes_wildcards() -> None:
    assert store._like_prefix("M:") == "M:%"  # pyright: ignore[reportPrivateUsage]
    assert store._like_prefix("a_b%c\\") == "a\\_b\\%c\\\\%"  # pyright: ignore[reportPrivateUsage]


def test_flow_inputs_are_read_with_parameters_only() -> None:
    """플로우·선물 입력(metrics §6·§7): 그 세션 투자자별 최신 행, 옵션 틱 거래일(가장 최근 n 개),
    틱 표본과 그 앞 engine F, KIS REST 응답의 output1(원문)."""
    inv_cols = store.INVESTOR_FLOW.columns
    inv = dict.fromkeys(inv_cols) | {
        "ts": T.astimezone(UTC),
        "trade_date": D,
        "session": "day",
        "market_code": "K2I",
        "sector_code": "OC01",
        "investor": "scrt",
        "net_qty": -120,
        "net_value": -35,
        "quality": "ok",
    }
    out1 = {"futs_shrn_iscd": "A01612", "basis": "5.97"}
    f = Factory(
        results=[
            [tuple(inv[c] for c in inv_cols)],
            [(date(2026, 9, 25),), (date(2026, 9, 23),)],
            [(D, "C", Decimal("1100.00"), 30, Decimal("1095.5"))],
            [(T.astimezone(UTC), D, "day", out1), (T.astimezone(UTC), None, None, None)],
        ]
    )
    s = _sink(f)
    (row,) = s.investor_latest(D, "day", T)
    assert (row.market_code, row.sector_code, row.investor, row.net_qty) == (
        "K2I",
        "OC01",
        "scrt",
        -120,
    )
    assert s.opt_tick_days(D, 20) == [date(2026, 9, 23), date(2026, 9, 25)]  # 오름차순
    lookback = timedelta(minutes=10)
    first, last = date(2026, 9, 1), date(2026, 9, 25)
    hist = s.opt_tick_history(first, last, lookback)
    assert hist == [(D, "C", Decimal("1100.00"), 30, 1095.5)]
    after = T - timedelta(days=1)
    assert s.kis_rest_outputs("FHKIF03020200", after, T) == [(T, D, "day", out1)]
    reads = f.conns[0].reads
    assert [_from(q) for q, _ in reads] == [
        '"prv_gex"."investor_flow"',
        "(SELECT",
        '"prv_gex"."opt_ticks"',
        '"prv_gex"."raw_messages"',
    ]
    assert reads[0][1] == (D, "day", T.astimezone(UTC), "invalid")
    assert "DISTINCT ON (market_code, sector_code, investor)" in reads[0][0]
    assert reads[1][1] == (D, 20) and '"opt_ticks"' in reads[1][0]
    assert reads[2][1] == (lookback, first, last)
    assert "JOIN LATERAL" in reads[2][0] and '"option_iv"' in reads[2][0]
    assert "o.ts <= t.ts" in reads[2][0] and "o.forward IS NOT NULL" in reads[2][0]
    assert reads[3][1] == ("output1", "FHKIF03020200", "kis_rest", after.astimezone(UTC), T)
    assert all("2026" not in q and "FHKIF" not in q for q, _ in reads)  # 값은 파라미터로만
    with pytest.raises(ValueError):
        s.opt_tick_days(D, 0)
    with pytest.raises(ValueError):
        s.kis_rest_outputs("FHKIF03020200", after, T, output="x'; --")
    with pytest.raises(ValueError):
        s.investor_latest(D, "day", datetime(2026, 9, 28, 10, 0))  # noqa: DTZ001


# ── 선물 미결제약정 패널 읽기(metrics §32, ADR 0042) ──


def test_futures_oi_reads_use_parameters_and_pick_day_session_last_rows() -> None:
    """KRX 코스피200 정규 행·investor_flow 날마다 정규장 마지막 행 — 값은 파라미터로만."""
    first, last = date(2026, 8, 3), date(2026, 11, 4)
    cols = store.INVESTOR_FLOW.columns
    inv_row = dict.fromkeys(cols)
    inv_row.update(
        ts=T, trade_date=D, session="day", market_code="K2I", sector_code="F001",
        investor="frgn", net_qty=3451, net_value=345, quality="ok",
    )  # fmt: skip
    f = Factory(
        results=[
            [(D, "202612", Decimal("404.00"), Decimal("4.00"), 203000, T)],
            [tuple(inv_row[c] for c in cols)],
        ]
    )
    s = _sink(f)
    assert s.krx_futures_days(first, last) == [
        (D, "202612", Decimal("404.00"), Decimal("4.00"), 203000, T)
    ]
    (rec,) = s.investor_closes(first, last, ("K2I", "WKM"), T)
    assert (rec.investor, rec.net_qty, rec.net_value) == ("frgn", 3451, 345)
    reads = f.conns[0].reads
    assert [_from(q) for q, _ in reads] == [
        '"prv_gex"."krx_fut_daily"',
        '"prv_gex"."investor_flow"',
    ]
    assert reads[0][1] == ("kospi200", "day", first, last)
    assert "expiry IS NOT NULL" in reads[0][0] and "acc_opnint_qty" in reads[0][0]
    assert reads[1][1] == (first, last, "day", T.astimezone(UTC), "invalid", ["K2I", "WKM"])
    q1 = reads[1][0]
    assert "DISTINCT ON (trade_date, market_code, sector_code, investor)" in q1
    assert q1.rstrip().endswith("ts DESC")
    assert all("2026" not in q and "kospi200" not in q for q, _ in reads)


def test_memory_futures_oi_reads_match_the_pg_rules() -> None:
    from kbj.core.gex.records import InvestorRecord

    m = store.MemoryGexRepo()
    m.krx_futures.extend([
        {"trade_date": D, "session": "day", "family": "kospi200", "expiry": "202703",
         "close": Decimal("405"), "cmp_prev": Decimal("4"), "acc_opnint_qty": 30500, "ts": T},
        {"trade_date": D, "session": "day", "family": "kospi200", "expiry": "202612",
         "close": Decimal("404"), "cmp_prev": Decimal("4"), "acc_opnint_qty": 203000, "ts": T},
        {"trade_date": D, "session": "night", "family": "kospi200", "expiry": "202612",
         "close": Decimal("1"), "cmp_prev": None, "acc_opnint_qty": 1, "ts": T},
        {"trade_date": D, "session": "day", "family": "mini_kospi200", "expiry": "202611",
         "close": Decimal("1"), "cmp_prev": None, "acc_opnint_qty": 1, "ts": T},
    ])  # fmt: skip
    assert [r[1] for r in m.krx_futures_days(D, D)] == ["202612", "202703"]

    def inv(ts: datetime, qty: int, *, session: str = "day", market: str = "K2I",
            quality: str = "ok") -> InvestorRecord:  # fmt: skip
        return InvestorRecord.model_validate({
            "ts": ts, "trade_date": D, "session": session, "market_code": market,
            "sector_code": "F001", "investor": "frgn", "net_qty": qty, "net_value": qty,
            "quality": quality,
        })  # fmt: skip

    m.seed_investor([
        inv(T, 1), inv(T + timedelta(minutes=5), 2),
        inv(T + timedelta(minutes=10), 3, quality="invalid"),
        inv(T + timedelta(minutes=20), 9),  # upto 뒤
        inv(T + timedelta(hours=5), 7, session="night"), inv(T, 8, market="999"),
    ])  # fmt: skip
    (got,) = m.investor_closes(D, D, ("K2I",), T + timedelta(minutes=15))
    assert got.net_qty == 2

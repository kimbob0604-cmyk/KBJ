"""이관 변환 순수 함수(kbj/store/legacy_import/transforms.py)와 매핑 표 정의 시험 — DB·파일 없음.

- 버림 규칙: 네이버·출처 빈 행·KIS 아닌 수급·형식 오류·비밀처럼 보이는 키(사유 이름이 그대로
  보고에 나간다)
- 값 규칙: 날짜 TEXT → date, 코드 앞자리 0 채움, 억원 → 원 정수, REAL → Decimal(짧은 표기),
  NaN → None, 오프셋 없는 시각은 NULL(원문은 detail), 자유 문장 가림
- 매핑의 대상 열·키가 마이그레이션 SQL(0002·0003·0005)과 같다
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from kbj.core.time import KST
from kbj.store.legacy_import import transforms as tf
from kbj.store.legacy_import.mappings import MAPPINGS, TARGETS, Target, for_phase
from kbj.store.legacy_import.transforms import Drop

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "kbj" / "store" / "migrations"


def _one(cands: list[tf.Candidate]) -> dict[str, object]:
    assert len(cands) == 1 and not isinstance(cands[0], Drop), cands
    out = cands[0]
    assert isinstance(out, dict)
    return out


def _drops(cands: list[tf.Candidate]) -> list[str]:
    return [c.reason for c in cands if isinstance(c, Drop)]


# ── 값 헬퍼 ──


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("2026-10-02", date(2026, 10, 2)),
        ("20261002", date(2026, 10, 2)),
        (" 2026-10-02 15:40:00", date(2026, 10, 2)),
        ("2026-02-30", None),
        ("n/a", None),
        (None, None),
        (date(2026, 10, 5), date(2026, 10, 5)),
    ],
)
def test_parse_date(raw: object, want: date | None) -> None:
    assert tf.parse_date(raw) == want


def test_aware_times_only_naive_is_unknown() -> None:
    assert tf.parse_aware("2026-10-02T15:40:59+09:00") == datetime(
        2026, 10, 2, 6, 40, 59, tzinfo=UTC
    )
    assert tf.parse_aware("2026-10-02 15:42:00") is None  # 시간대를 지어내지 않는다
    assert tf.parse_aware("") is None and tf.parse_aware("garbage") is None
    # 쓰는 코드로 시간대를 확인한 원본만 그 시간대로 읽는다(SD ops_state = KST)
    assert tf.parse_naive_in("2026-10-02 07:00:00", KST) == datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    assert tf.parse_naive_in("2026-10-02 07:00:00", UTC) == datetime(2026, 10, 2, 7, 0, tzinfo=UTC)
    assert tf.parse_naive_in("2026-10-02T07:00:00+00:00", KST) == datetime(
        2026, 10, 2, 7, 0, tzinfo=UTC
    )  # 오프셋이 있으면 그것을 따른다
    assert tf.parse_naive_in("2026-10-02", KST) is None and tf.parse_naive_in(None, KST) is None


@pytest.mark.parametrize(
    ("raw", "want"),
    [("005930", "005930"), (5930, "005930"), ("5930", "005930"), (" 0009k0 ", "0009K0"),
     ("KR7005930003", None), ("", None), (None, None), (True, None)],
)  # fmt: skip
def test_kr_code_keeps_leading_zeros(raw: object, want: str | None) -> None:
    assert tf.kr_code(raw) == want


def test_decimals_eok_and_integers() -> None:
    assert tf.dec(1.1) == Decimal("1.1")  # float 끝자리 잡음 없이
    assert tf.dec(float("nan")) is None and tf.dec(float("inf")) is None and tf.dec("") is None
    assert tf.dec("1,234.5") == Decimal("1234.5")
    with pytest.raises(ValueError):
        tf.dec("abc")
    assert tf.whole(1200.0) == 1200 and tf.whole(None) is None
    with pytest.raises(ValueError, match="정수"):
        tf.whole(1234.5)
    assert tf.eok_to_won(123.456789) == 12_345_678_900  # 억원 → 원
    assert tf.eok_to_won(-3.25) == -325_000_000
    assert tf.eok_to_won(None) is None


# ── ops.kv ──


def test_kv_values_are_kept_as_strings_and_secret_like_keys_are_dropped() -> None:
    run = tf.kv_row("sd", naive_tz=KST)
    out = _one(run({"key": "watchdog_alerted", "value": "0", "updated_at": "2026-10-02 06:10:00"}))
    assert out == {
        "namespace": "sd",
        "key": "watchdog_alerted",
        "value": "0",  # 숫자처럼 보여도 문자열 그대로(손실 없음)
        # SD server.py:_ops_set 이 now_kst() 를 오프셋 없이 적는다 — KST 06:10 = UTC 전날 21:10
        "updated_at": datetime(2026, 10, 1, 21, 10, tzinfo=UTC),
    }
    for key in ("kis_token_cache", "telegram_token_hint", "app_key", "DB_PASSWORD", "api-key"):
        assert _drops(run({"key": key, "value": "x"})) == ["secret_like_key"], key
    assert _one(tf.kv_row("board.kr")({"k": "data_version", "v": "3"}))["updated_at"] is None
    # 시간대를 모르는 원본(naive_tz 없음)은 오프셋 없는 시각을 지어내지 않는다
    naive: dict[str, object] = {"key": "k", "value": "v", "updated_at": "2026-10-02 06:10:00"}
    assert _one(tf.kv_row("board.kr")(naive))["updated_at"] is None


def test_sd_ops_state_mapping_reads_naive_times_as_kst() -> None:
    m = next(m for m in MAPPINGS if m.name == "sd.ops_state")
    out = _one(
        m.transform(
            {"key": "closing_brief_date", "value": "x", "updated_at": "2026-10-02 07:00:00"}
        )
    )
    assert out["updated_at"] == datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


def test_fetch_progress_row_becomes_one_json_value_with_padded_code() -> None:
    out = _one(tf.sd_fetch_progress({"stock_code": 5930, "quarterly_status": "DONE", "x": None}))
    assert out["namespace"] == "sd.fetch_progress" and out["key"] == "005930"
    assert out["value"] == {"quarterly_status": "DONE", "x": None}
    assert _drops(tf.sd_fetch_progress({"stock_code": "?"})) == ["bad_code"]


# ── ops.job_run ──


def test_run_log_ids_are_stable_and_naive_times_stay_raw() -> None:
    run = tf.run_log_row("legacy.board.kr")
    row = {"asof": "2026-10-02", "step": "px", "ok": 1, "note": "", "ts": "2026-10-02 15:42:00"}
    a, b = _one(run(row)), _one(run(dict(row)))
    assert a == b and str(a["run_id"]).startswith(
        "legacy.board.kr:2026-10-02:2026-10-02 15:42:00:px:"
    )
    assert a["finished_at"] is None and a["detail"] == {
        "step": "px",
        "note": "",
        "ts_raw": "2026-10-02 15:42:00",
    }
    failed = _one(run({**row, "ok": 0, "ts": "2026-10-02T15:42:00+09:00", "note": "다름"}))
    assert failed["status"] == "failed" and failed["run_id"] != a["run_id"]
    assert failed["finished_at"] == datetime(2026, 10, 2, 6, 42, tzinfo=UTC)
    assert failed["source"] == "legacy_import" and failed["attempt"] == 1
    assert _drops(run({**row, "asof": None})) == ["no_asof"]


def test_run_log_notes_are_masked() -> None:
    note = "GET https://x.invalid/a?serviceKey=SYNTHETICSECRETVALUE123456 failed"
    out = _one(tf.run_log_row("j")({"asof": "2026-10-02", "step": "s", "ok": 0, "note": note}))
    detail = out["detail"]
    assert isinstance(detail, dict) and "SYNTHETICSECRETVALUE123456" not in json.dumps(detail)


# ── 유니버스·스냅·일봉 ──


def test_index_universe_row() -> None:
    out = _one(
        tf.sd_index_universe(
            {"stock_code": "990030", "source": "VALUECHAIN_V2", "rank": 2, "market_cap": 1.1e13,
             "added_date": "2026-06-01", "removed_date": "2026-09-01", "is_active": 0}
        )
    )  # fmt: skip
    assert (out["market"], out["code"], out["as_of"], out["source"]) == (
        "KR", "990030", date(2026, 6, 1), "sd.valuechain_v2",
    )  # fmt: skip
    assert out["flags"] == {
        "rank": 2, "market_cap": "11000000000000.0", "removed_date": "2026-09-01",
        "is_active": False,
    }  # fmt: skip


def test_board_snap_converts_eok_to_won_and_drops_naver() -> None:
    row = {
        "code": "990010", "asof": "2026-10-02", "name": "합성전자", "market": "KOSPI",
        "close": 51000.0, "chg_pct": 1.25, "volume": 120000.0, "turnover": 61.2, "mktcap": 3040.5,
        "turnover_is_estimate": 0, "source": "krx",
    }  # fmt: skip
    out = _one(tf.board_snap(row))
    assert out["turnover"] == 6_120_000_000 and out["mktcap"] == 304_050_000_000
    assert out["close"] == Decimal("51000.0") and out["volume"] == 120000
    assert (out["venue"], out["segment"], out["quality"]) == ("KRX", "KOSPI", "ok")
    assert _drops(tf.board_snap({**row, "source": "naver"})) == ["naver"]
    assert _drops(tf.board_snap({**row, "source": None})) == ["no_source"]
    assert _one(tf.board_snap({**row, "close": None}))["quality"] == "invalid"


def test_us_snap_marks_turnover_as_estimate_and_keeps_raw_fields() -> None:
    row = {
        "ticker": "zza", "asof": "2026-10-02", "name": "Synthetic Alpha", "exchange": "NASDAQ",
        "close": 101.25, "chg_pct": 1.5, "volume": 120000.0, "turnover": 12150000.0,
        "mktcap": 2.1e9, "sector": "Technology", "industry": "Software",
        "sector_raw": "Computer Software", "industry_raw": "", "source": "nasdaq-screener",
    }  # fmt: skip
    out = _one(tf.us_snap(row))
    assert out["code"] == "ZZA" and out["turnover_is_estimate"] is True
    assert out["extra"] == {"sector_raw": "Computer Software"} and out["venue"] == ""


def test_daily_bar_rules() -> None:
    run = tf.daily_bar("KR")
    row = {"code": 9900, "asof": "2026-09-28", "open": 4100.0, "high": 4150.0, "low": 4050.0,
           "close": 4120.0, "volume": 500.0, "source": "datago"}  # fmt: skip
    out = _one(run(row))
    assert out["code"] == "009900" and out["adjusted"] is False and out["venue"] == ""
    assert out["asset"] == "stock" and out["market"] == "KR"
    krx = _one(run({**row, "source": "krx"}))
    assert krx["venue"] == "KRX" and krx["adjusted"] is False
    assert _one(run({**row, "source": "kis"}))["adjusted"] is None  # 모르면 NULL
    assert _drops(run({**row, "volume": 1234.5})) == ["volume_not_integer"]
    assert _drops(run({**row, "asof": "n/a"})) == ["bad_date"]
    assert _drops(run({**row, "source": "Naver"})) == ["naver"]
    us = _one(tf.daily_bar("US")({**row, "code": None, "ticker": "zzb", "source": "yahoo"}))
    assert us["code"] == "ZZB" and us["market"] == "US" and us["venue"] == ""


# ── 수급 ──


def test_sd_flow_cache_expands_kis_rows_as_estimates() -> None:
    row: dict[str, object] = {
        "code": "990010", "source": "kis", "dates_json": json.dumps(["2026-10-01", "2026-10-02"]),
        "foreign_shares_json": "[1200, -300]", "inst_shares_json": "[-800, 100]",
        "foreign_value_json": "[61200000, -15450000.4]", "inst_value_json": "[-40800000, 5150000]",
    }  # fmt: skip
    cands = tf.sd_flow_cache(row)
    rows = [c for c in cands if isinstance(c, dict)]
    assert len(rows) == 4 and not _drops(cands)
    first = rows[0]
    assert (first["investor"], first["net_qty"], first["net_value"]) == ("foreign", 1200, 61200000)
    assert {r["quality"] for r in rows} == {"estimated"}  # 순매매량 × 종가 추정
    assert rows[2]["net_value"] == -15450000  # 반올림
    assert _drops(tf.sd_flow_cache({**row, "source": "naver_mobile_api"})) == ["naver"]
    assert _drops(tf.sd_flow_cache({**row, "source": "pykrx"})) == ["not_kis"]
    assert _drops(tf.sd_flow_cache({**row, "inst_shares_json": "[1]"})) == ["array_length"]
    assert _drops(tf.sd_flow_cache({**row, "dates_json": "{bad"})) == ["bad_json"]


def test_et_stockflows_units() -> None:
    base = {"code": "990010", "as_of": "2026-10-01", "source": "kis"}
    eok = tf.et_stockflows({**base, "unit": "억원", "기관": 12.5, "외국인": -3.25})
    assert [(r["investor"], r["net_value"], r["net_qty"]) for r in eok if isinstance(r, dict)] == [
        ("institution", 1_250_000_000, None),
        ("foreign", -325_000_000, None),
    ]
    qty = tf.et_stockflows({**base, "unit": "주", "기관": 1500, "개인": -700})
    assert [(r["investor"], r["net_qty"]) for r in qty if isinstance(r, dict)] == [
        ("institution", 1500),
        ("individual", -700),
    ]
    assert _drops(tf.et_stockflows({**base, "unit": "주", "source": "naver", "기관": 1})) == [
        "naver"
    ]
    assert _drops(tf.et_stockflows({**base, "unit": "만원", "기관": 1})) == ["unknown_unit"]


def test_kr_flows_zero_is_an_estimate() -> None:
    row = {"code": "990010", "date": "20261002", "unit": "억원", "source": "KIS stock_investor",
           "f": 0.0, "o": 4.75, "p": -4.75}  # fmt: skip
    rows = [r for r in tf.kr_flows(row) if isinstance(r, dict)]
    assert [(r["investor"], r["net_value"], r["quality"]) for r in rows] == [
        ("foreign", 0, "estimated"),  # 원본이 빈 값을 0.0 으로 채웠다 — 가를 수 없다
        ("institution", 475_000_000, "ok"),
        ("individual", -475_000_000, "ok"),
    ]
    assert rows[0]["trade_date"] == date(2026, 10, 2) and rows[0]["source"] == "kis"
    assert _drops(tf.kr_flows({**row, "unit": "주"})) == ["unknown_unit"]


# ── 인박스 ──


def test_inbox_item() -> None:
    item = {"update_id": 900001, "chat_id": -1009900000001, "date": "2026-10-01T21:10:00+09:00",
            "text": "t", "urls": ["https://x.com/a/status/1"], "x_ids": ["1"], "author": "a",
            "kind": "x", "text_via": "message"}  # fmt: skip
    out = _one(tf.et_inbox(item))
    assert out["date"] == datetime(2026, 10, 1, 12, 10, tzinfo=UTC) and out["received_at"] is None
    assert out["urls"] == ["https://x.com/a/status/1"] and out["kind"] == "x"
    assert _one(tf.et_inbox({**item, "text_via": None}))["text_via"] is None
    assert _drops(tf.et_inbox({**item, "update_id": None})) == ["no_update_id"]
    assert _drops(tf.et_inbox({**item, "kind": "photo"})) == ["bad_kind"]


# ── 매핑 표 정의 = 마이그레이션 SQL ──


def _sql_tables() -> dict[str, tuple[list[str], list[str], set[str]]]:
    """{스키마.표: (열, 기본키 열, 기본값 없는 NOT NULL 열)}."""
    out: dict[str, tuple[list[str], list[str], set[str]]] = {}
    for p in sorted(MIGRATIONS.glob("*.sql")):
        body = "\n".join(line.split("--", 1)[0] for line in p.read_text("utf-8").splitlines())
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS ([\w.]+) \((.*?)\n\);", body, re.S):
            cols: list[str] = []
            pk: list[str] = []
            required: set[str] = set()
            for line in m.group(2).splitlines():
                s = line.strip().rstrip(",")
                if not s:
                    continue
                if pm := re.match(r"PRIMARY KEY \(([^)]*)\)", s):
                    pk = [c.strip() for c in pm.group(1).split(",")]
                    continue
                if re.match(r"(CHECK|UNIQUE|CONSTRAINT)\b", s):
                    continue
                name = s.split()[0]
                cols.append(name)
                if "PRIMARY KEY" in s:
                    pk = [name]
                if "NOT NULL" in s and "DEFAULT" not in s and "GENERATED" not in s:
                    required.add(name)
                if "PRIMARY KEY" in s and "GENERATED" not in s:
                    required.add(name)
            out[m.group(1)] = (cols, pk, required)
    return out


@pytest.mark.parametrize("target", TARGETS, ids=lambda t: t.table)
def test_target_columns_and_keys_match_the_migrations(target: Target) -> None:
    cols, pk, required = _sql_tables()[target.table]
    assert set(target.columns) <= set(cols), set(target.columns) - set(cols)
    assert list(target.key) == pk, "ON CONFLICT 키 = PRIMARY KEY"
    assert required <= set(target.columns), required - set(target.columns)


def test_p2_mappings_are_named_once_and_ordered_by_priority() -> None:
    names = [m.name for m in MAPPINGS]
    assert len(names) == len(set(names)) and for_phase("P2") == MAPPINGS
    assert for_phase("P3") == ()
    by_table: dict[str, list[str]] = {}
    for m in MAPPINGS:
        by_table.setdefault(m.target.table, []).append(m.on_conflict)
    # 같은 대상에 여러 원본이면 앞의 것만 update 일 수 있고, 뒤의 겹칠 수 있는 원본은 nothing
    daily = [m for m in MAPPINGS if m.target.table == "prv_market.daily_bar"]
    assert [(m.name, m.on_conflict) for m in daily] == [
        ("board.px", "update"), ("backtest.px", "nothing"),
        ("us.px", "update"), ("us_backtest.px", "nothing"),
    ]  # fmt: skip
    flows = [m.name for m in MAPPINGS if m.target.table == "prv_flows.stock_investor_daily"]
    assert flows == ["kr.flows", "board.stockflows", "sd.flow_cache"]


def test_mapping_targets_never_write_pub_schemas() -> None:
    """이관 원본은 전부 로그인 등급·개인·운영 — 공개 스키마에는 쓰지 않는다(corp_code 는 다시
    받는다)."""
    assert all(not m.target.table.startswith("pub_") for m in MAPPINGS)

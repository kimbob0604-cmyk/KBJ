"""이관 매핑 — 원본 표(SQLite·JSON) → 대상 표(docs/p2_design.md §8.4, conflict_map §1.5).

- `Target` = 대상 표 하나(열 순서·키·합을 대조할 숫자 열·원본 표시 열·jsonb 열·날짜 키 열). 키는
  마이그레이션(0002~0005)의 PRIMARY KEY 와 같다 — `tests/unit/store/test_import_transforms.py` 가
  SQL 파일과 맞춰 본다.
- `TableMapping` = 원본 표 하나 → 대상 표 하나. 같은 대상 표에 여러 원본이 들어가면 **목록 순서가
  우선순위**다: 먼저 오는 매핑은 `update`(같은 키를 고친다), 뒤에 오는 겹칠 수 있는 매핑은
  `nothing` — 다른 원본이 이미 넣은 키는 `other_origin` 으로 세고 넣지 않는다(예: ET px 는 board.db
  우선, backtest.db 는 board.db 에 없는 날짜만 — §8.4).
- 이 단계(P2)에 옮기는 것만 `MAPPINGS` 에 있다. P3 이후 매핑은 `LATER` 에 이름만(§8.4 표).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from kbj.core.time import KST
from kbj.store.legacy_import import transforms as tf

OnConflict = Literal["update", "nothing"]


@dataclass(frozen=True)
class Target:
    table: str  # '스키마.표'
    columns: tuple[str, ...]  # 쓰는 열(이 순서로 COPY)
    key: tuple[str, ...]  # ON CONFLICT 키 = 표의 PRIMARY KEY
    sums: tuple[str, ...] = ()  # 원본·대상 합을 맞춰 볼 숫자 열
    origin: str | None = "loaded_by"  # 이 원본에서 온 행을 가르는 열(없으면 키만으로 센다)
    jsonb: frozenset[str] = frozenset()
    date_keys: frozenset[str] = frozenset()  # 키 다이제스트에서 날짜로 적을 키 열

    def __post_init__(self) -> None:
        missing = set(self.key) - set(self.columns)
        if missing or (self.origin is not None and self.origin not in self.columns):
            raise ValueError(f"{self.table}: 키·원본 열은 columns 안에 있어야 한다")
        if not set(self.sums) <= set(self.columns) - set(self.key):
            raise ValueError(f"{self.table}: sums 는 키가 아닌 열")

    @property
    def schema(self) -> str:
        return self.table.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.table.split(".", 1)[1]


@dataclass(frozen=True)
class TableMapping:
    """원본 표 하나 → 대상 표 하나(design §1.8 의 `Mapping`)."""

    name: str  # 'board.px' — ops.legacy_import.mapping, loaded_by 'legacy:<name>'
    source: str  # --source/--json 이름
    source_table: str  # 원본 표 이름(SQLite) 또는 파일 이름(JSON)
    target: Target
    transform: tf.Transform = field(repr=False)
    select_sql: str | None = None  # SQLite 원본(결정적 순서 — ORDER BY rowid)
    phase: str = "P2"
    on_conflict: OnConflict = "update"

    @property
    def origin(self) -> str:
        return f"legacy:{self.name}"

    @property
    def key_cols(self) -> tuple[str, ...]:
        return self.target.key

    @property
    def sum_cols(self) -> tuple[str, ...]:
        return self.target.sums

    @property
    def is_json(self) -> bool:
        return self.select_sql is None


Mapping = TableMapping  # 설계서 이름(§1.8)

# ── 대상 표 (0002·0003·0005 의 열과 같다) ─────────────────────────────────────────────────

KV = Target(
    "ops.kv",
    ("namespace", "key", "value", "updated_at"),
    key=("namespace", "key"),
    origin=None,
    jsonb=frozenset({"value"}),
)
JOB_RUN = Target(
    "ops.job_run",
    (
        "run_id", "job", "as_of", "attempt", "status", "started_at", "finished_at", "detail",
        "source",
    ),
    key=("run_id",),
    origin=None,
    jsonb=frozenset({"detail"}),
)  # fmt: skip
UNIVERSE = Target(
    "prv_market.universe",
    (
        "market", "code", "as_of", "source", "name", "kind", "listed_on", "flags", "quality",
        "received_at", "loaded_by",
    ),
    key=("market", "code", "as_of", "source"),
    jsonb=frozenset({"flags"}),
    date_keys=frozenset({"as_of"}),
)  # fmt: skip
STOCK_SNAPSHOT = Target(
    "prv_market.stock_snapshot",
    (
        "market", "code", "trade_date", "source", "venue", "name", "segment", "sector",
        "industry", "close", "chg_pct", "volume", "turnover", "turnover_is_estimate", "mktcap",
        "shares", "extra", "quality", "received_at", "loaded_by",
    ),
    key=("market", "code", "trade_date", "source", "venue"),
    sums=("close", "volume", "turnover", "mktcap"),
    jsonb=frozenset({"extra"}),
    date_keys=frozenset({"trade_date"}),
)  # fmt: skip
DAILY_BAR = Target(
    "prv_market.daily_bar",
    (
        "market", "asset", "code", "trade_date", "source", "venue", "open", "high", "low",
        "close", "volume", "turnover", "adjusted", "quality", "received_at", "loaded_by",
    ),
    key=("market", "asset", "code", "trade_date", "source", "venue"),
    sums=("open", "high", "low", "close", "volume"),
    date_keys=frozenset({"trade_date"}),
)  # fmt: skip
STOCK_INVESTOR_DAILY = Target(
    "prv_flows.stock_investor_daily",
    (
        "code", "trade_date", "investor", "source", "venue", "net_qty", "net_value", "unit",
        "quality", "received_at", "loaded_by",
    ),
    key=("code", "trade_date", "investor", "source", "venue"),
    sums=("net_qty", "net_value"),
    date_keys=frozenset({"trade_date"}),
)  # fmt: skip
TG_INBOX = Target(
    "prv_alerts.tg_inbox",
    (
        "update_id", "chat_id", "date", "text", "urls", "x_ids", "author", "kind", "text_via",
        "received_at",
    ),
    key=("update_id",),
    origin=None,
)  # fmt: skip

TARGETS: tuple[Target, ...] = (
    KV,
    JOB_RUN,
    UNIVERSE,
    STOCK_SNAPSHOT,
    DAILY_BAR,
    STOCK_INVESTOR_DAILY,
    TG_INBOX,
)


def _all(table: str) -> str:
    return f"SELECT * FROM {table} ORDER BY rowid"  # noqa: S608 — 상수 표 이름만


# ── P2 매핑 (§8.4 의 'P2' 행) — 순서가 우선순위 ───────────────────────────────────────────

MAPPINGS: tuple[TableMapping, ...] = (
    # 운영 키값
    # SD ops_state.updated_at 은 server.py:_ops_set 이 오프셋 없는 KST 로 적는다(transforms)
    TableMapping(
        "sd.ops_state", "sd", "ops_state", KV, tf.kv_row("sd", naive_tz=KST), _all("ops_state")
    ),
    TableMapping(
        "sd.fetch_progress",
        "sd",
        "fetch_progress",
        KV,
        tf.sd_fetch_progress,
        _all("fetch_progress"),
    ),
    TableMapping("board.meta", "board", "meta", KV, tf.kv_row("board.kr"), _all("meta")),
    TableMapping("us.meta", "us", "meta", KV, tf.kv_row("board.us"), _all("meta")),
    # 실행 기록 — 이력이라 DO NOTHING
    TableMapping(
        "board.run_log",
        "board",
        "run_log",
        JOB_RUN,
        tf.run_log_row("legacy.board.kr"),
        _all("run_log"),
        on_conflict="nothing",
    ),
    TableMapping(
        "us.run_log",
        "us",
        "run_log",
        JOB_RUN,
        tf.run_log_row("legacy.board.us"),
        _all("run_log"),
        on_conflict="nothing",
    ),
    # 유니버스
    TableMapping(
        "sd.index_universe",
        "sd",
        "index_universe",
        UNIVERSE,
        tf.sd_index_universe,
        _all("index_universe"),
    ),
    # 스냅 — 네이버 행은 버린다
    TableMapping("board.snap", "board", "snap", STOCK_SNAPSHOT, tf.board_snap, _all("snap")),
    TableMapping("us.snap", "us", "snap", STOCK_SNAPSHOT, tf.us_snap, _all("snap")),
    # 일봉 — board.db 우선, backtest.db 는 겹치지 않는 것만
    TableMapping("board.px", "board", "px", DAILY_BAR, tf.daily_bar("KR"), _all("px")),
    TableMapping(
        "backtest.px",
        "backtest",
        "px",
        DAILY_BAR,
        tf.daily_bar("KR"),
        _all("px"),
        on_conflict="nothing",
    ),
    TableMapping("us.px", "us", "px", DAILY_BAR, tf.daily_bar("US"), _all("px")),
    TableMapping(
        "us_backtest.px",
        "us_backtest",
        "px",
        DAILY_BAR,
        tf.daily_bar("US"),
        _all("px"),
        on_conflict="nothing",
    ),
    # 종목 수급 — KIS 행만. monitor/kr 캐시(금액) 우선, ET stockflows·SD flow_cache 는
    # 겹치지 않는 것만
    TableMapping("kr.flows", "krflows", "flows.json", STOCK_INVESTOR_DAILY, tf.kr_flows),
    TableMapping(
        "board.stockflows",
        "stockflows",
        "stockflows.json",
        STOCK_INVESTOR_DAILY,
        tf.et_stockflows,
        on_conflict="nothing",
    ),
    TableMapping(
        "sd.flow_cache",
        "sd",
        "flow_cache",
        STOCK_INVESTOR_DAILY,
        tf.sd_flow_cache,
        _all("flow_cache"),
        on_conflict="nothing",
    ),
    # 인박스 — 웹훅이 먼저 받은 같은 update_id 는 그대로 둔다
    TableMapping(
        "board.inbox", "inbox", "inbox.json", TG_INBOX, tf.et_inbox, on_conflict="nothing"
    ),
)

# P3 이후(§8.4 표 — 이 단계에서는 옮기지 않는다). (원본, 대상, 단계)
LATER: tuple[tuple[str, str, str], ...] = (
    ("SD ohlcv", "market.backfill 로 KRX 에서 다시 받는다", "P3"),
    ("ETF etf.db etf_px", "KRX etp/etf_bydd_trd 로 다시 받는다", "P3"),
    ("ET alltime·label·lowlabel·split_check·state/<날짜>/*.json", "prv_board.*", "P3"),
    ("SD disclosure_history·dart_disclosure_overhang·earnings_actual", "pub_filings.*", "P4"),
    ("SD financial_quarterly·ET state/financials.json", "pub_fin.quarterly", "P4"),
    ("SD consensus_*·valuation_band·earnings_surprise·earnings_alert_queue", "prv_fin.*", "P4"),
    ("ET sector_map·knowledge/*.yaml", "pub_themes.*", "P5"),
    ("ETF fund·holding·etf_ticker·change_log·etf_meta·etf_aum", "prv_etf.*", "P5"),
    ("SD alert_rules·alert_history(_v2)", "prv_alerts.rule·event", "P8"),
    ("SD analysis_journal·recommendation_history·trade_journal", "prv_journal.*", "P8"),
    ("GX investor_flow 등 Postgres 표", "prv_gex.* (pg_dump --data-only)", "P7"),
)

# 이관하지 않는 것(§8.4): 캐시·네이버 출처·토큰 캐시. 토큰 캐시는 열지도 않는다(sources.py).
NEVER: tuple[str, ...] = (
    "SD stocks·financial(네이버)",
    "SD chart_cache·misc_cache·yinfo_cache·discover_results·llm_cache.db(캐시)",
    "SD dart_corp_map·ET .dart_corp.json·dart-report 캐시(corpCode.xml 로 다시 받는다)",
    "토큰 캐시(cache/kis_token.json·state/.kis_token.json·state/kis.token.json)",
)


def for_phase(phase: str) -> tuple[TableMapping, ...]:
    return tuple(m for m in MAPPINGS if m.phase == phase)

"""규칙 알림·백테스트·저널의 행 자료형 — 0021 `prv_alerts.*`·`prv_journal.*`(P8 묶음 FND).

근거: docs/p8_design.md §3.3·D-P8-15, docs/metrics.md §30.
저장소(`kbj.store.repos.rules`·`personal`·
`journal`)·엔진(`kbj.engines.rules`·`backtest`)·작업(EVL·BKT)·API·저널(JNL)이 같이 쓴다.

- 수치 행은 `source`·`as_of`(또는 그에 해당하는 시각 칸)·`quality`(절대 규칙 1). 값이 없으면 None —
  0 으로 채우지 않는다. 시각은 aware 만.
- 표 하나에 쓰는 주체 하나(D-P8-15): `P8_TABLE_WRITERS` 가 표 → 쓰기 주체(작업 또는 쓰기 모듈)를
  적는다. 등록부 시험·저장소 함수 이름이 이것과 맞는다.
- 돈(매매 가격·수수료·세금·일지 금액)은 Decimal(원), 시세(진입가·슬롯 가격)는 float(일봉 `Bar` 와
  같다).
- 이 모듈은 `kbj.core` 밖을 모른다(계약 ③).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from kbj.core.quality import Quality

__all__ = [
    "ALLOWED_SIGNAL_STATUSES",
    "BACKTEST_MODES",
    "JOURNAL_SOURCES",
    "MATCH_REASONS",
    "OUTCOME_REASONS",
    "P8_TABLE_WRITERS",
    "RULE_SOURCES",
    "SIGNAL_MODES",
    "SIGNAL_STATUSES",
    "SKIP_REASONS",
    "TRADE_SIDES",
    "AnalysisRow",
    "BacktestMode",
    "BacktestRun",
    "BacktestSignal",
    "MatchRow",
    "OutcomeRow",
    "PickRow",
    "QuoteSlot",
    "RuleRow",
    "RuleVersion",
    "SignalMode",
    "SignalRow",
    "SignalStatus",
    "TradeRow",
    "TradeSide",
    "new_rule_id",
    "signal_id_prefix",
]

SignalMode = Literal["intraday", "close"]
SignalStatus = Literal["sent", "cooldown", "suppressed", "dry", "skipped"]
BacktestMode = Literal["close", "intraday_replay"]
TradeSide = Literal["buy", "sell"]

# 0021 CHECK 와 같은 목록(tests/test_store_layout.py 가 대조)
SIGNAL_MODES: Final[tuple[str, ...]] = ("intraday", "close")
SIGNAL_STATUSES: Final[tuple[str, ...]] = ("sent", "cooldown", "suppressed", "dry", "skipped")
# 쿨다운 상태기가 '허락한 신호'로 세는 상태(§30.3 — 실시간 sent·suppressed, 백테스트·발송 꺼짐 dry)
ALLOWED_SIGNAL_STATUSES: Final[tuple[str, ...]] = ("sent", "suppressed", "dry")
SKIP_REASONS: Final[tuple[str, ...]] = (
    "split_suspect", "halted", "no_live_flow", "truncated", "no_data", "error",
)  # fmt: skip
OUTCOME_REASONS: Final[tuple[str, ...]] = (
    "halted", "delisted", "not_due", "split_suspect", "no_index",
)  # fmt: skip
MATCH_REASONS: Final[tuple[str, ...]] = ("data_revised", "logic")
BACKTEST_MODES: Final[tuple[str, ...]] = ("close", "intraday_replay")
RULE_SOURCES: Final[tuple[str, ...]] = ("api", "cli", "import:sd", "example")
JOURNAL_SOURCES: Final[tuple[str, ...]] = ("api", "cli", "import:sd", "import:csv")
TRADE_SIDES: Final[tuple[str, ...]] = ("buy", "sell")

# 표 → 쓰기 주체 하나(§3.3). 작업 이름이면 config/jobs.yaml writes 와 맞는다(등록부 시험).
P8_TABLE_WRITERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "prv_alerts.rule": "kbj.services.personal.rules",
        "prv_alerts.rule_version": "kbj.services.personal.rules",
        "prv_alerts.watch_quote_slot": "rules.intraday",
        "prv_alerts.signal_intraday": "rules.intraday",
        "prv_alerts.signal_close": "rules.close",
        "prv_alerts.signal_outcome": "rules.outcome",
        "prv_alerts.backtest_run": "rules.backtest_nightly",
        "prv_alerts.backtest_signal": "rules.backtest_nightly",
        "prv_alerts.match_check": "rules.backtest_nightly",
        "prv_journal.analysis": "kbj.services.personal.journal",
        "prv_journal.trade": "kbj.services.personal.journal",
        "prv_journal.pick": "kbj.store.legacy_import",
    }
)

_RULE_ID = re.compile(r"r_[0-9a-z]{12}")
_SIGNAL_ID = re.compile(r"(si|sc)_[0-9A-HJKMNP-TV-Z]{26}")  # ULID(크록퍼드 base32)
_CODE = re.compile(r"[0-9A-Z]{6}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_RUN_ID = re.compile(r"bt_[0-9A-HJKMNP-TV-Z]{26}")
_B32 = "0123456789abcdefghijklmnopqrstuvwxyz"


def new_rule_id(seed: bytes) -> str:
    """규칙 id `r_` + 소문자 영숫자 12자 — 부르는 쪽이 넘긴 무작위 바이트(≥ 8)로 만든다(시계·난수를
    스스로 읽지 않는다)."""
    if len(seed) < 8:
        raise ValueError("seed 는 8바이트 이상")
    n = int.from_bytes(seed, "big")
    out: list[str] = []
    for _ in range(12):
        n, r = divmod(n, 36)
        out.append(_B32[r])
    return "r_" + "".join(out)


def signal_id_prefix(mode: SignalMode) -> str:
    return "si_" if mode == "intraday" else "sc_"


# ── 검증 도구 ───────────────────────────────────────────────────────────────────────────────


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _text(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} 가 비었다: {value!r}")


def _match(what: str, value: object, pat: re.Pattern[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not pat.fullmatch(value):
        raise ValueError(f"{what} 형식이 틀렸다: {value!r}")


def _choice(what: str, value: object, allowed: Iterable[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    allowed_t = tuple(allowed)
    if value not in allowed_t:
        raise ValueError(f"{what} 는 {allowed_t} 중 하나: {value!r}")


def _day(what: str, value: object) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{what} 는 date 여야 한다: {value!r}")


def _aware(what: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, datetime):
        raise TypeError(f"{what} 는 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


def _num(what: str, value: object, *, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{what} 는 수여야 한다: {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{what} 는 유한한 수: {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{what} 는 0 보다 커야 한다: {value!r}")


def _int(what: str, value: object, *, lo: int | None = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} 는 정수여야 한다: {value!r}")
    if lo is not None and value < lo:
        raise ValueError(f"{what} 는 {lo} 이상: {value!r}")


def _dec(what: str, value: object, *, lo: Decimal | None = None) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{what} 는 Decimal 이어야 한다(float 아님): {value!r}")
    if not value.is_finite():
        raise ValueError(f"{what} 는 유한한 수: {value!r}")
    if lo is not None and value < lo:
        raise ValueError(f"{what} 는 {lo} 이상: {value!r}")


def _quality(obj: object) -> None:
    q: object = getattr(obj, "quality")  # noqa: B009
    try:
        _set(obj, "quality", Quality(cast("str", q)))
    except ValueError:
        raise ValueError(f"quality 는 ok·stale·estimated·invalid 중 하나: {q!r}") from None


def _frozen_map(obj: object, name: str) -> None:
    raw: object = getattr(obj, name)
    if not isinstance(raw, Mapping):
        raise TypeError(f"{name} 는 Mapping 이어야 한다: {raw!r}")
    m = cast("Mapping[object, object]", raw)
    for k in m:
        _text(f"{name} 키", k)
    _set(obj, name, MappingProxyType(dict(m)))


def _str_tuple(obj: object, name: str) -> None:
    raw: object = getattr(obj, name)
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise TypeError(f"{name} 는 문자열 목록이어야 한다: {raw!r}")
    items = tuple(cast("Iterable[object]", raw))
    for r in items:
        _text(name, r)
    _set(obj, name, cast("tuple[str, ...]", items))


def _empty_map() -> Mapping[str, Any]:
    return MappingProxyType({})


# ── 규칙 ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleRow:
    """규칙 머리(`prv_alerts.rule`). 수정은 새 판(`RuleVersion`), 삭제는 보관(`archived_at`)."""

    rule_id: str
    name: str
    current_version: int
    enabled: bool
    archived_at: datetime | None
    source: str  # api · cli · import:sd · example
    created_at: datetime
    updated_at: datetime
    loaded_by: str

    def __post_init__(self) -> None:
        _match("rule_id", self.rule_id, _RULE_ID)
        _text("name", self.name)
        _int("current_version", self.current_version, lo=1)
        if type(self.enabled) is not bool:  # pyright: ignore[reportUnnecessaryComparison]
            raise TypeError("enabled 는 bool")
        _aware("archived_at", self.archived_at, optional=True)
        _choice("source", self.source, RULE_SOURCES)
        _aware("created_at", self.created_at)
        _aware("updated_at", self.updated_at)
        _text("loaded_by", self.loaded_by)


@dataclass(frozen=True)
class RuleVersion:
    """규칙 한 판(`prv_alerts.rule_version`) — 바꾸지 않는다. dsl 은 정규화한 DSL(JSON 모양)."""

    rule_id: str
    version: int
    dsl: Mapping[str, Any]
    dsl_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        _match("rule_id", self.rule_id, _RULE_ID)
        _int("version", self.version, lo=1)
        _frozen_map(self, "dsl")
        _match("dsl_hash", self.dsl_hash, _HEX64)
        _aware("created_at", self.created_at)


# ── 장중 슬롯 시세(D-P8-9 — 장중 재생 입력) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class QuoteSlot:
    """관심종목 장중 10분 슬롯 시세(`prv_alerts.watch_quote_slot`) — 부분 봉 = (시가, 지금까지 고가·
    저가, 현재가, 누적 거래량). as_of = slot_ts. 장중 값이라 quality 는 보통 estimated."""

    slot_ts: datetime
    code: str
    open: float | None
    high: float | None
    low: float | None
    price: float | None
    volume: int | None  # 누적
    turnover: int | None  # 누적(원)
    change_pct: float | None
    flags: tuple[str, ...]
    quality: Quality
    source: str
    received_at: datetime | None = None

    def __post_init__(self) -> None:
        _aware("slot_ts", self.slot_ts)
        _match("code", self.code, _CODE)
        for k in ("open", "high", "low", "price"):
            _num(k, getattr(self, k), positive=True)
        _num("change_pct", self.change_pct)
        _int("volume", self.volume, lo=0)
        _int("turnover", self.turnover, lo=0)
        _str_tuple(self, "flags")
        _quality(self)
        _text("source", self.source)
        _aware("received_at", self.received_at, optional=True)
        if self.price is None and self.quality is Quality.OK:
            raise ValueError("현재가가 없는 슬롯은 quality 가 ok 일 수 없다")

    @property
    def as_of(self) -> datetime:
        return self.slot_ts


# ── 신호·성과 ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SignalRow:
    """신호 한 건(`prv_alerts.signal_intraday`·`signal_close` — 읽기는 뷰 `prv_alerts.signal`).

    status: sent(보냄) · cooldown(쿨다운으로 막힘 — 기록) · suppressed(notifier 문지기가 막음) ·
    dry(발송 꺼짐·백테스트) · skipped(평가 못 함 — 사유 skip_reason). as_of = eval_ts."""

    signal_id: str
    mode: SignalMode
    rule_id: str
    version: int
    dsl_hash: str
    code: str
    eval_ts: datetime
    trade_date: date
    entry_price: float | None
    evidence: Mapping[str, Any]
    input_fp: str
    status: SignalStatus
    skip_reason: str | None
    ticket: str | None  # notifier dedup 키(보냈을 때)
    quality: Quality
    source: str  # 계산한 작업(KBJ:rules.intraday · KBJ:rules.close)

    def __post_init__(self) -> None:
        _match("signal_id", self.signal_id, _SIGNAL_ID)
        _choice("mode", self.mode, SIGNAL_MODES)
        if not self.signal_id.startswith(signal_id_prefix(self.mode)):
            raise ValueError(f"signal_id 접두가 mode 와 다르다: {self.signal_id!r}")
        _match("rule_id", self.rule_id, _RULE_ID)
        _int("version", self.version, lo=1)
        _match("dsl_hash", self.dsl_hash, _HEX64)
        _match("code", self.code, _CODE)
        _aware("eval_ts", self.eval_ts)
        _day("trade_date", self.trade_date)
        _num("entry_price", self.entry_price, positive=True)
        _frozen_map(self, "evidence")
        _match("input_fp", self.input_fp, _HEX16)
        _choice("status", self.status, SIGNAL_STATUSES)
        if (self.status == "skipped") != (self.skip_reason is not None):
            raise ValueError("skip_reason 은 status=skipped 일 때만(그때는 필수)")
        _choice("skip_reason", self.skip_reason, SKIP_REASONS, optional=True)
        _text("ticket", self.ticket, optional=True)
        _quality(self)
        _text("source", self.source)

    @property
    def as_of(self) -> datetime:
        return self.eval_ts


@dataclass(frozen=True)
class OutcomeRow:
    """알림 후 성과(`prv_alerts.signal_outcome` — metrics §30.4). 값이 없으면 reason."""

    signal_id: str
    horizon: int
    due_date: date
    close_at: float | None
    ret: float | None
    index_code: str | None
    excess: float | None
    mfe: float | None
    mae: float | None
    reason: str | None
    quality: Quality
    computed_at: datetime
    source: str

    def __post_init__(self) -> None:
        _match("signal_id", self.signal_id, _SIGNAL_ID)
        _int("horizon", self.horizon, lo=1)
        _day("due_date", self.due_date)
        _num("close_at", self.close_at, positive=True)
        for k in ("ret", "excess", "mfe", "mae"):
            _num(k, getattr(self, k))
        _text("index_code", self.index_code, optional=True)
        _choice("reason", self.reason, OUTCOME_REASONS, optional=True)
        _quality(self)
        _aware("computed_at", self.computed_at)
        _text("source", self.source)
        if self.ret is None and self.reason is None:
            raise ValueError("ret 가 없으면 reason 이 있어야 한다(지어내지 않음)")
        if self.ret is None and self.quality is Quality.OK:
            raise ValueError("ret 가 없는 행은 quality 가 ok 일 수 없다")

    @property
    def as_of(self) -> datetime:
        return self.computed_at


# ── 백테스트·일치 감시 ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BacktestRun:
    """야간 백테스트 실행 한 번(`prv_alerts.backtest_run`).
    summary = `kbj.engines.backtest.stats` 모양."""

    run_id: str
    rule_id: str
    version: int
    dsl_hash: str
    mode: BacktestMode
    start: date
    end: date
    params: Mapping[str, Any]
    ledger_as_of: datetime
    summary: Mapping[str, Any]
    created_at: datetime
    quality: Quality
    source: str

    def __post_init__(self) -> None:
        _match("run_id", self.run_id, _RUN_ID)
        _match("rule_id", self.rule_id, _RULE_ID)
        _int("version", self.version, lo=1)
        _match("dsl_hash", self.dsl_hash, _HEX64)
        _choice("mode", self.mode, BACKTEST_MODES)
        _day("start", self.start)
        _day("end", self.end)
        if self.start > self.end:
            raise ValueError("start > end")
        _frozen_map(self, "params")
        _aware("ledger_as_of", self.ledger_as_of)
        _frozen_map(self, "summary")
        _aware("created_at", self.created_at)
        _quality(self)
        _text("source", self.source)

    @property
    def as_of(self) -> datetime:
        return self.ledger_as_of


@dataclass(frozen=True)
class BacktestSignal:
    """백테스트 신호 한 건(`prv_alerts.backtest_signal`). rets = {"h1": ret, …}(없으면 None)."""

    run_id: str
    code: str
    trade_date: date
    eval_ts: datetime
    mode: BacktestMode
    entry_price: float | None
    rets: Mapping[str, float | None]
    status: SignalStatus
    input_fp: str

    def __post_init__(self) -> None:
        _match("run_id", self.run_id, _RUN_ID)
        _match("code", self.code, _CODE)
        _day("trade_date", self.trade_date)
        _aware("eval_ts", self.eval_ts)
        _choice("mode", self.mode, BACKTEST_MODES)
        _num("entry_price", self.entry_price, positive=True)
        _frozen_map(self, "rets")
        for v in self.rets.values():
            _num("rets 값", v)
        _choice("status", self.status, SIGNAL_STATUSES)
        _match("input_fp", self.input_fp, _HEX16)


@dataclass(frozen=True)
class MatchRow:
    """일치 감시 하루·규칙 한 줄(`prv_alerts.match_check` — D-P8-14). diffs 는 차이마다
    {code, eval_ts, side(live_only·backtest_only), reason(data_revised·logic)}."""

    trade_date: date
    rule_id: str
    version: int
    live_n: int
    backtest_n: int
    diffs: tuple[Mapping[str, Any], ...]
    computed_at: datetime
    quality: Quality
    source: str

    def __post_init__(self) -> None:
        _day("trade_date", self.trade_date)
        _match("rule_id", self.rule_id, _RULE_ID)
        _int("version", self.version, lo=1)
        _int("live_n", self.live_n, lo=0)
        _int("backtest_n", self.backtest_n, lo=0)
        items: list[Mapping[str, Any]] = []
        for d in cast("Iterable[object]", self.diffs):
            if not isinstance(d, Mapping):
                raise TypeError("diffs 는 Mapping 목록")
            dm = cast("Mapping[str, Any]", d)
            _choice("diffs.reason", dm.get("reason"), MATCH_REASONS)
            items.append(MappingProxyType(dict(dm)))
        _set(self, "diffs", tuple(items))
        _aware("computed_at", self.computed_at)
        _quality(self)
        _text("source", self.source)

    @property
    def logic_n(self) -> int:
        return sum(1 for d in self.diffs if d.get("reason") == "logic")


# ── 저널 ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalysisRow:
    """분석 일지(`prv_journal.analysis` — SD `analysis_journal` 칸 + 출처). 금액은 원(Decimal).
    값 출처 `value_sources` = {칸: kbj·consensus·manual}(prefill — JNL)."""

    id: int | None
    code: str
    analyzed_on: date
    chain_ids: tuple[str | None, str | None, str | None]
    price: Decimal | None
    ret_52w: float | None
    fwd_per: float | None
    band_pos: float | None
    ev_ebitda: float | None
    mktcap: Decimal | None
    opm: float | None
    eps: Mapping[str, Decimal | None]  # bear·base·bull
    per: Mapping[str, float | None]
    tp: Mapping[str, Decimal | None]
    tp_source: str | None
    conviction: str | None  # 반영도
    memo: str | None
    tags: tuple[str, ...]
    conclusion: str | None
    value_sources: Mapping[str, str]
    source: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _int("id", self.id, lo=1)
        _match("code", self.code, _CODE)
        _day("analyzed_on", self.analyzed_on)
        if len(self.chain_ids) != 3:
            raise ValueError("chain_ids 는 셋(빈칸 None)")
        for c in self.chain_ids:
            _text("chain_id", c, optional=True)
        _dec("price", self.price, lo=Decimal(0))
        _dec("mktcap", self.mktcap, lo=Decimal(0))
        for k in ("ret_52w", "fwd_per", "band_pos", "ev_ebitda", "opm"):
            _num(k, getattr(self, k))
        for name in ("eps", "per", "tp"):
            _frozen_map(self, name)
            m = cast("Mapping[str, object]", getattr(self, name))
            if set(m) - {"bear", "base", "bull"}:
                raise ValueError(f"{name} 키는 bear·base·bull 만")
        _str_tuple(self, "tags")
        _frozen_map(self, "value_sources")
        _choice("source", self.source, JOURNAL_SOURCES)
        _aware("updated_at", self.updated_at)


@dataclass(frozen=True)
class TradeRow:
    """매매 기록(`prv_journal.trade`). fee·tax 는 원. signal_id 로 알림과 잇는다(선택)."""

    id: int | None
    code: str
    name_at_trade: str | None
    side: TradeSide
    trade_date: date
    price: Decimal
    qty: int
    fee: Decimal
    tax: Decimal
    strategy: str | None
    memo: str | None
    signal_id: str | None
    source: str
    created_at: datetime

    def __post_init__(self) -> None:
        _int("id", self.id, lo=1)
        _match("code", self.code, _CODE)
        _text("name_at_trade", self.name_at_trade, optional=True)
        _choice("side", self.side, TRADE_SIDES)
        _day("trade_date", self.trade_date)
        _dec("price", self.price, lo=Decimal(0))
        if self.price <= 0:
            raise ValueError("price 는 0 보다 커야 한다")
        _int("qty", self.qty, lo=1)
        _dec("fee", self.fee, lo=Decimal(0))
        _dec("tax", self.tax, lo=Decimal(0))
        _match("signal_id", self.signal_id, _SIGNAL_ID, optional=True)
        _choice("source", self.source, JOURNAL_SOURCES)
        _aware("created_at", self.created_at)


@dataclass(frozen=True)
class PickRow:
    """추천 이력(`prv_journal.pick` — SD `recommendation_history` 이관 전용). source = 추천 출처
    이름(SD 칸 그대로 — 원천 시스템 이름이 아니다)."""

    date: date
    source: str
    rank: int | None
    code: str
    name: str | None
    market: str | None
    score: float | None
    price_at_rec: Decimal | None
    tags: Mapping[str, Any] = field(default_factory=_empty_map)

    def __post_init__(self) -> None:
        _day("date", self.date)
        _text("source", self.source)
        _int("rank", self.rank, lo=1)
        _match("code", self.code, _CODE)
        _num("score", self.score)
        _dec("price_at_rec", self.price_at_rec, lo=Decimal(0))
        _frozen_map(self, "tags")

"""engine 사이클 평가 — 입력 행 → core 계산 → 저장·발행할 산출 (docs/phase3_design.md §1·§2).

부작용 없다(입출력은 services/engine/service.py). 계산은 모두 core 순수 함수이고 여기는 입력을
고르고 잇는다. 흐름은 `scripts/make_golden.py` `core_golden` 과 같다(운영 engine 몫을 흉내 낸 것):

1. 근월물·S_ref: `core.forward.select_s_ref(선물 행, session, 근월물 코드, as_of)` — 주간 전광판
   `F`, 야간 단건 `CM`(분기 B, 2026-09-29 사용자 결정). 없으면 그 사이클은 F 를 만들지 않는다
   (`no_s_ref` — 서비스가 직전 산출을 stale 로 두고 health). 근월물이 바뀌면(롤) 확정 베이시스를
   비운다
2. 시리즈 = (시장분류, 6자리 만기) — WKM·WKI 261001 이 겹친다. 행은 그 세션 것만(야간에 주간
   전광판 행을 섞지 않는다 — 읽는 쪽이 세션 태그로 고른다). 만기일은 `series_expiries`(KIS 먼저),
   거기 없으면 캘린더 계산값(원천 calendar) + health `engine_no_expiry`. 그것도 못 세면 평가하지
   않고(`no_expiry`) 범위 all 은 invalid·nearest·0dte 는 estimated(`series_no_expiry`) [확인 필요]
   - 만기 시각(만기일 15:20 KST)이 그 시리즈 최신 행 시각에 지났으면 평가하지 않는다(core_golden 과
     같다). 사이클 시각엔 지났지만 최신 행은 그 전인 시리즈(15:20 을 걸친 사이클)는 이번 사이클에
     행이 온 때만 한 번 평가하고(그 시리즈의 마지막 스냅샷) 그 뒤엔 뺀다 [확인 필요]
   - 평가 시각 = 그 시리즈 최신 행 시각(core_golden 의 전광판 시각). ±1σ Δt 는 사이클 시각
   - 같은 종목에 전광판·보강 행이 다 있으면 전광판 행(호가가 있다 — `scripts.validate_greeks.
     series_rows` 와 같다) [확인 필요]. OI 없음(None)은 0 으로 두고 입력 품질 estimated
     (`oi_missing`) [확인 필요]
   - 옛 행(metrics §0 — REST 90초 미갱신): 최신 전광판 행이 사이클 시각보다 90초 넘게 앞이면(전광판
     행이 없는 야간 B 시리즈는 최신 보강 행이 20분 넘게 앞이면 [확인 필요]) 그 시리즈 입력 stale
     (`rows_stale`) + health `engine_series_stale` — 그 시리즈의 `strike_gex`·`option_iv` 행도 stale
   - `core.gex.evaluate_expiry(…, forward_basis=확정 베이시스, forward_basis_age=basis_age(…),
     strikes=마스터 전 행사가, 분기 월물이면 같은 결제월 선물가)`. 시리즈 하나의 예외는 그 시리즈만
     `failed` + health — 그 시리즈가 드는 범위는 invalid(`series_failed`)
   - 확정 베이시스 갱신은 F 품질 ok 이고 S_ref 품질도 ok 이고 옛 행이 아니며 F 에 쓴 행사가의 콜·풋
     행이 모두 사이클 시각의 90초 안일 때만(`confirm_basis`) — 나이 0 으로 다시
3. 범위 all·nearest·0dte(0dte = 만기일이 귀속 거래일, 야간은 T+1): 콜월·풋월·절대감마·Flip(다중
   교차)·전환점 거리·±1σ(달력·거래 기준 — 범위의 기준 만기 = F 있는 가장 이른 만기)·기대범위 상위
   레벨(`levels`), 순GEX·DEX(`metrics`). 시리즈마다 ATM IV·만기별 감마(`metrics` scope series)
   - 산출 품질 = core 품질 ⊕ S_ref 품질 ⊕ 범위 입력 품질(행 품질·실패한 시리즈). ±1σ 는 기준 만기의
     입력 품질만
   - 레벨·지표 하나의 예외는 그것만 invalid(값 없음) + health — 사이클은 계속
4. 등록부(`services/engine/extended.py` `REGISTRY` — 계약은 registry.py)의 확장 지표 — 플래그 off 와
   주기가 안 된 지표(`due`, Charm 2분)는 부르지 않고, 예외는 그 지표만 invalid
   - Phase 2 핵심(3 의 레벨·순GEX·DEX·ATM IV·만기별 감마)도 행마다 계산 당시 플래그
     (`registry.core_flag` — 기본 visible, shadow 면 저장만·발행 안 함). 늘 계산한다(사이클 품질·
     레벨 사이 의존 — 핵심 off 는 로더가 막는다)
5. 행: `levels`·`metrics`·`strike_gex`(시리즈마다 행사가별)·`option_iv`(IV 를 시도한 종목 — 가격·F
   없는 종목은 뺀다)

KIS 그릭스는 어디에도 쓰지 않는다(자체 IV·자체 T 만 — metrics §1.7).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, cast, get_args

from core.calendar import Session, TradingCalendar, expiry_at
from core.forward import (
    FuturesQuote,
    SRef,
    basis_age,
    confirm_basis,
    is_quarterly_monthly,
    select_s_ref,
)
from core.gex import (
    ExpiryEval,
    OptionEval,
    OptionQuote,
    Scope,
    dex,
    evaluate_expiry,
    net_gex,
    select_scope,
)
from core.gex import strike_gex as expiry_strike_gex
from core.levels import (
    abs_gamma_strike,
    atm_iv,
    call_wall,
    expected_move,
    gamma_by_expiry,
    gamma_flip,
    put_wall,
    top_levels_in_range,
)
from core.preprocess import Quality, worst
from services.bus import BasisBook, BasisEntry, MrktCls, SeriesKey, series_label
from services.engine.records import (
    LevelRecord,
    MetricRecord,
    OptionIvRecord,
    StrikeGexRecord,
)
from services.engine.registry import (
    CycleView,
    Flag,
    MetricPlugin,
    SeriesGap,
    core_flag,
    resolve_flag,
)
from services.poller.context import Series, estimate_last_trade_date
from services.poller.records import ChainRecord, FuturesRecord

SCOPES: tuple[Scope, ...] = ("all", "nearest", "0dte")
MOVE_BASES = ("calendar", "trading")
_CLASSES = frozenset(get_args(MrktCls))
_IV_SOURCE = {"model": "self", "kis": "kis"}
# 체인 행 신선도 — metrics §0 'stale: 입력 스냅샷이 §6.1 기준보다 오래됨(REST 90초 미갱신)'.
# 시리즈의 최신 전광판 행(30초 주기)이 사이클 시각보다 90초 넘게 앞이면 그 시리즈 입력은 stale.
# 전광판 행이 없는 시리즈(야간 B — 단건 보강만)는 최신 행으로 보되, 보강 2(1초 1건, ATM 가까운
# 순으로 추적 만기 전 행사가를 돈다)만 받는 차기 위클리는 순환의 월물 꼬리(위클리에 없는 먼 행사가
# 약 460건 ≈ 8분) 동안 행이 오지 않으므로 순환 한 바퀴(야간 B 약 960건 ≈ 16분 — poller 가 보강 2
# 행을 ok 로 보는 한도) + 여유로 20분 [확인 필요]
BOARD_STALE_S = 90.0
FILL_STALE_S = 1200.0
# 확정 베이시스는 F 에 쓴 행사가의 콜·풋 행이 모두 사이클 시각의 90초(REST) 안일 때만 — 옛 옵션
# 가격으로 만든 F 와 지금 S_ref 의 차는 베이시스가 아니다 [확인 필요]
BASIS_ROWS_MAX_AGE_S = 90.0

HealthSeverity = Literal["info", "warning", "critical"]
SeriesStatus = Literal["evaluated", "expired", "no_expiry", "failed"]
CycleStatus = Literal["ok", "no_s_ref"]


@dataclass(frozen=True)
class ExpiryInfo:
    """시리즈 최종거래일과 원천(`series_expiries` — KIS 값, 없으면 캘린더 계산)."""

    last_trade_date: date
    source: Literal["kis", "calendar"]


@dataclass(frozen=True)
class CycleInput:
    """한 사이클의 입력 — 서비스가 DB·Redis 에서 읽어 넘긴다.

    chain: 그 세션의 시리즈·종목별 as_of 까지 최신 체인 행. futures: 그 세션 선물 최신 행.
    near_code: 선물 근월물 코드(모르면 None — S_ref 없음). strikes: 마스터 전 행사가(ATM 선정용,
    없으면 가격이 온 행사가). futures_months: 선물 코드 → 결제월 YYYYMM(분기 월물의 같은 결제월
    선물가). fresh_since: 직전 사이클 as_of — 이보다 늦은 행이 온 시리즈가 '이번 사이클에 행이 온'
    시리즈(만기 걸침 규칙).
    """

    as_of: datetime
    trade_date: date
    session: Session
    chain: Sequence[ChainRecord]
    futures: Sequence[FuturesRecord]
    expiries: Mapping[SeriesKey, ExpiryInfo]
    near_code: str | None
    strikes: Mapping[SeriesKey, Sequence[Decimal]] = field(
        default_factory=dict[SeriesKey, Sequence[Decimal]]
    )
    futures_months: Mapping[str, str] = field(default_factory=dict[str, str])
    fresh_since: datetime | None = None


@dataclass(frozen=True)
class EngineHealth:
    """health 한 건 — 서비스가 health_events·로그로 낸다. subject 는 같은 경고 묶기용."""

    kind: str
    severity: HealthSeverity
    detail: str
    subject: str = ""


@dataclass(frozen=True)
class SeriesOutcome:
    key: SeriesKey
    label: str
    status: SeriesStatus
    expiry: ExpiryInfo | None = None
    ev: ExpiryEval | None = None
    now: datetime | None = None  # 평가 시각(그 시리즈 최신 행)
    input_quality: Quality = "ok"
    input_reasons: tuple[str, ...] = ()
    basis: Decimal | None = None  # 넘긴 확정 베이시스
    basis_age: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class CycleResult:
    as_of: datetime
    trade_date: date
    session: Session
    status: CycleStatus
    quality: Quality
    near_code: str | None
    s_ref: SRef | None
    basis: BasisBook
    series: tuple[SeriesOutcome, ...] = ()
    levels: tuple[LevelRecord, ...] = ()
    metrics: tuple[MetricRecord, ...] = ()
    strike_gex: tuple[StrikeGexRecord, ...] = ()
    option_iv: tuple[OptionIvRecord, ...] = ()
    health: tuple[EngineHealth, ...] = ()
    plugins_run: tuple[str, ...] = ()  # 이 사이클에 부른 확장 지표(주기 계산용)


# ── 입력 변환 ─────────────────────────────────────────────────────────────────


def futures_quote(r: FuturesRecord) -> FuturesQuote | None:
    """선물 행 → S_ref 후보. 시장·원천·값이 S_ref 에 맞지 않으면 None."""
    if r.market not in ("F", "CM") or r.price is None or r.price <= 0:
        return None
    try:
        return FuturesQuote(
            ts=r.ts,
            session=r.session,
            market=cast(Literal["F", "CM"], r.market),
            source=r.source,
            code=r.code,
            price=r.price,
            quality=r.quality,
        )
    except (TypeError, ValueError):
        return None


def near_code_from_quotes(rows: Iterable[FuturesRecord], session: Session) -> str | None:
    """근월물 코드 대체 — 그 세션 출처(주간 `F` 전광판·야간 `CM` 단건)의 값 있는 행 중 잔존일수가
    가장 짧은 종목(같으면 코드 순). 마스터·poller 문맥이 없을 때만 쓴다(만기 지난 근월물을 가리지
    못한다)."""
    market, source = ("F", "board") if session == "day" else ("CM", "single")
    cands = [
        r
        for r in rows
        if r.market == market
        and r.source == source
        and r.price is not None
        and r.price > 0
        and r.remaining_days is not None
    ]
    if not cands:
        return None
    return min(cands, key=lambda r: (r.remaining_days or 0, r.code)).code


QuoteKey = tuple[Decimal, str]  # (행사가, 콜풋)


def choose_rows(rows: Iterable[ChainRecord]) -> dict[QuoteKey, ChainRecord]:
    """종목마다 쓸 행 — 전광판 행 먼저 [확인 필요], 같은 원천이면 늦은 행. invalid 행은 뺀다."""
    chosen: dict[QuoteKey, ChainRecord] = {}
    for r in rows:
        if r.quality == "invalid":
            continue
        k = (r.strike, r.cp)
        old = chosen.get(k)
        if old is None or (old.source == "fill" and r.source == "board"):
            chosen[k] = r
        elif old.source == r.source and r.ts > old.ts:
            chosen[k] = r
    return chosen


def rows_age(
    chosen: Mapping[QuoteKey, ChainRecord], as_of: datetime
) -> tuple[float, float, str] | None:
    """시리즈 입력의 나이 — (최신 행 나이 초, stale 한도 초, 본 원천). 전광판 행이 있으면 최신
    전광판 행(`BOARD_STALE_S`), 없으면(야간 B) 최신 보강 행(`FILL_STALE_S`). 쓸 행이 없으면 None."""
    board = [r.ts for r in chosen.values() if r.source == "board"]
    if board:
        newest, limit, source = max(board), BOARD_STALE_S, "board"
    elif chosen:
        newest, limit, source = max(r.ts for r in chosen.values()), FILL_STALE_S, "fill"
    else:
        return None
    return (as_of - newest).total_seconds(), limit, source


def forward_rows_fresh(
    strikes: Iterable[Decimal], chosen: Mapping[QuoteKey, ChainRecord], as_of: datetime
) -> bool:
    """F 에 쓴 행사가(`ForwardResult.strikes`)의 콜·풋 행이 모두 `BASIS_ROWS_MAX_AGE_S` 안인가."""
    ks = list(strikes)
    for k in ks:
        for cp in ("C", "P"):
            r = chosen.get((k, cp))
            if r is None or (as_of - r.ts).total_seconds() > BASIS_ROWS_MAX_AGE_S:
                return False
    return bool(ks)


def series_quotes(
    rows: Sequence[ChainRecord], expiry_date: date
) -> tuple[list[OptionQuote], Quality, tuple[str, ...]]:
    """한 시리즈의 체인 행 → OptionQuote(`choose_rows` — 같은 종목은 전광판 행)."""
    return _quotes(choose_rows(rows), expiry_date)


def _quotes(
    chosen: Mapping[QuoteKey, ChainRecord], expiry_date: date
) -> tuple[list[OptionQuote], Quality, tuple[str, ...]]:
    """입력 품질: 쓴 행 품질 중 가장 나쁜 것, OI 없는 행이 있으면 estimated(`oi_missing` — 0 으로
    둔다) [확인 필요], 못 읽는 행은 빼고 estimated(`bad_rows`). 행 나이는 여기서 보지 않는다
    (`rows_age`)."""
    quotes: list[OptionQuote] = []
    qualities: list[Quality] = []
    reasons: list[str] = []
    for (_, _), r in sorted(chosen.items()):
        try:
            q = OptionQuote(
                expiry=r.expiry,
                expiry_date=expiry_date,
                strike=r.strike,
                cp=r.cp,
                bid=r.bid,
                ask=r.ask,
                last=r.last,
                oi=r.oi if r.oi is not None else 0,
                kis_iv_pct=float(r.iv_kis) if r.iv_kis is not None else None,
                volume=r.volume,
                source=r.source,
            )
        except (TypeError, ValueError):
            if "bad_rows" not in reasons:
                reasons.append("bad_rows")
            continue
        quotes.append(q)
        qualities.append(r.quality)
        if r.oi is None and "oi_missing" not in reasons:
            reasons.append("oi_missing")
    quality = worst(*qualities)
    if quality != "ok":
        reasons.insert(0, f"rows_{quality}")
    if {"oi_missing", "bad_rows"} & set(reasons):
        quality = worst(quality, "estimated")
    return quotes, quality, tuple(reasons)


# ── 사이클 ────────────────────────────────────────────────────────────────────


def evaluate_cycle(
    inp: CycleInput,
    basis: BasisBook,
    *,
    cal: TradingCalendar,
    registry: Sequence[MetricPlugin] = (),
    flags: Mapping[str, Flag] | None = None,
    due: Callable[[MetricPlugin], bool] | None = None,
) -> CycleResult:
    """한 사이클. 예외를 올리지 않는다 — 시리즈·레벨·지표 단위로 격리한다(입력 모양이 틀린 것은
    호출 쪽 결함이라 올린다: naive as_of 등). due: 확장 지표를 이 사이클에 부를지(주기 — 없으면
    모두)."""
    if inp.as_of.tzinfo is None or inp.as_of.utcoffset() is None:
        raise ValueError("as_of 는 aware datetime")
    health: list[EngineHealth] = []
    book = _roll(basis, inp.near_code, health)
    quotes = [q for r in inp.futures if (q := futures_quote(r)) is not None]
    s_ref: SRef | None = None
    if inp.near_code is None:
        health.append(
            EngineHealth("engine_no_near_code", "warning", "선물 근월물 코드를 모른다 — F 없음")
        )
    else:
        s_ref = select_s_ref(quotes, session=inp.session, code=inp.near_code, now=inp.as_of)
        if s_ref is None:
            src = "전광판 F" if inp.session == "day" else "단건 CM"
            health.append(
                EngineHealth(
                    "engine_no_s_ref",
                    "warning",
                    f"S_ref 없음 — {inp.session} 세션 {src} 근월물 {inp.near_code} 시세가 없다 "
                    "(이 사이클 F 를 만들지 않는다, 직전 산출 stale)",
                    subject=inp.near_code,
                )
            )
    if s_ref is None:
        return CycleResult(
            inp.as_of,
            inp.trade_date,
            inp.session,
            "no_s_ref",
            "stale",
            inp.near_code,
            None,
            book,
            health=tuple(health),
        )

    run = _Cycle(inp, s_ref, book, quotes, cal, health, flags)
    run.series()
    run.scopes()
    run.plugins(registry, flags, due)
    return run.result()


def _roll(basis: BasisBook, near_code: str | None, health: list[EngineHealth]) -> BasisBook:
    """근월물이 바뀌면 확정 베이시스를 비운다(옛 근월물 기준 — metrics §1.3)."""
    if near_code is None or basis.near_code == near_code:
        return basis
    if basis.near_code is not None and basis.entries:
        health.append(
            EngineHealth(
                "engine_basis_rolled",
                "info",
                f"근월물 {basis.near_code} → {near_code} — 확정 베이시스 {len(basis.entries)}개를 "
                "비운다",
            )
        )
    return BasisBook(near_code=near_code, entries={})


class _Cycle:
    """한 사이클의 계산 상태(내부용)."""

    def __init__(
        self,
        inp: CycleInput,
        s_ref: SRef,
        book: BasisBook,
        quotes: list[FuturesQuote],
        cal: TradingCalendar,
        health: list[EngineHealth],
        flags: Mapping[str, Flag] | None = None,
    ) -> None:
        self.inp = inp
        self.flags = flags
        self.s_ref = s_ref
        self.entries: dict[str, BasisEntry] = dict(book.entries)
        self.near = book.near_code
        self.quotes = quotes
        self.cal = cal
        self.health = health
        self.outcomes: list[SeriesOutcome] = []
        self.levels: list[LevelRecord] = []
        self.metrics: list[MetricRecord] = []
        self.strikes: list[StrikeGexRecord] = []
        self.ivs: list[OptionIvRecord] = []
        self.scope_evals: dict[str, tuple[ExpiryEval, ...]] = {}
        self.scope_quality: dict[str, Quality] = {}
        self.scope_reasons: dict[str, tuple[str, ...]] = {}
        self.all_quality: Quality = "ok"
        self.plugins_run: list[str] = []

    @property
    def stamp(self) -> dict[str, Any]:
        inp = self.inp
        return {"ts": inp.as_of, "trade_date": inp.trade_date, "session": inp.session}

    # ── 시리즈 ──

    def series(self) -> None:
        groups: dict[SeriesKey, list[ChainRecord]] = {}
        for r in self.inp.chain:
            if r.session != self.inp.session or r.trade_date != self.inp.trade_date:
                continue  # 읽는 쪽이 이미 골랐다 — 방어선
            if r.mrkt_cls not in _CLASSES:
                continue
            key = cast(SeriesKey, (r.mrkt_cls, r.expiry))
            groups.setdefault(key, []).append(r)
        for key in sorted(groups):
            self.outcomes.append(self._one(key, groups[key]))

    def _one(self, key: SeriesKey, rows: list[ChainRecord]) -> SeriesOutcome:
        label = series_label(key)
        info = self.inp.expiries.get(key) or self._calendar_expiry(key, label)
        if info is None:
            return SeriesOutcome(key, label, "no_expiry")
        latest = max(r.ts for r in rows)
        exp_at = expiry_at(info.last_trade_date)
        fresh = self.inp.fresh_since is not None and latest > self.inp.fresh_since
        if latest >= exp_at or (self.inp.as_of >= exp_at and not fresh):
            self.entries.pop(label, None)
            return SeriesOutcome(key, label, "expired", info, now=latest)
        b: Decimal | None = None
        age: int | None = None
        stale = False
        try:
            b, age = self._basis(label)
            chosen = choose_rows(rows)
            qs, in_q, in_r = _quotes(chosen, info.last_trade_date)
            stale = self._stale_rows(label, chosen)
            if stale:
                in_q, in_r = (
                    worst(in_q, "stale"),
                    ("rows_stale", *(x for x in in_r if x != "rows_stale")),
                )
            same, same_q = self._same_month(key, info.last_trade_date)
            if same_q != "ok":
                in_q, in_r = worst(in_q, same_q), (*in_r, f"same_month_{same_q}")
            if not qs:
                raise ValueError("쓸 수 있는 종목 행이 없다")
            ev = evaluate_expiry(
                qs,
                latest,
                self.s_ref.price,
                same,
                forward_basis=b,
                forward_basis_age=age or 0,
                strikes=self.inp.strikes.get(key) or None,
            )
        except Exception as e:  # 시리즈 하나의 결함 — 그 시리즈만
            self.health.append(
                EngineHealth(
                    "engine_series_failed",
                    "warning",
                    f"{label}: 평가 실패 {type(e).__name__}: {e}"[:250],
                    subject=label,
                )
            )
            err = type(e).__name__
            return SeriesOutcome(
                key, label, "failed", info, now=latest, basis=b, basis_age=age, error=err
            )
        # 확정은 ok S_ref·옛 행 아님·F 에 쓴 행이 모두 90초 안일 때만 (metrics §0·§1.3)
        if (
            self.s_ref.quality == "ok"
            and not stale
            and forward_rows_fresh(ev.forward.strikes, chosen, self.inp.as_of)
        ):
            confirmed = confirm_basis(ev.forward, self.s_ref.price)
            if confirmed is not None:
                self.entries[label] = BasisEntry(
                    basis=confirmed,
                    trade_date=self.inp.trade_date,
                    session=self.inp.session,
                    confirmed_at=self.inp.as_of,
                )
        try:
            strikes, ivs = self._rows(key, ev, stale)
        except Exception as e:  # 행 만들기 결함 — 그 시리즈의 행만 빠진다(평가·레벨은 그대로)
            self._failed("engine_rows_failed", label, e)
        else:
            self.strikes.extend(strikes)
            self.ivs.extend(ivs)
        return SeriesOutcome(key, label, "evaluated", info, ev, latest, in_q, in_r, b, age)

    def _calendar_expiry(self, key: SeriesKey, label: str) -> ExpiryInfo | None:
        """`series_expiries` 에 없는 시리즈 — poller 가 KIS 값을 못 받았을 때와 같은 캘린더 계산값
        (원천 calendar — 만기 목록 품질 estimated). 셀 수 없으면 None(`no_expiry` — 범위 품질은
        `_scope_input`)."""
        calc = estimate_last_trade_date(Series(key[0], key[1]), self.cal)
        how = "평가하지 않는다" if calc is None else f"캘린더 계산값 {calc.isoformat()} 로 평가"
        self.health.append(
            EngineHealth(
                "engine_no_expiry",
                "warning",
                f"{label}: 최종거래일(series_expiries)이 없다 — {how}",
                subject=label,
            )
        )
        return None if calc is None else ExpiryInfo(calc, "calendar")

    def _stale_rows(self, label: str, chosen: Mapping[QuoteKey, ChainRecord]) -> bool:
        """시리즈 입력이 옛 행인가(`rows_age`) — 그러면 health(같은 시리즈는 서비스가 10분에
        한 번)."""
        a = rows_age(chosen, self.inp.as_of)
        if a is None or a[0] <= a[1]:
            return False
        age_s, limit, source = a
        self.health.append(
            EngineHealth(
                "engine_series_stale",
                "warning",
                f"{label}: 최신 {source} 행이 {age_s:.0f}초 전 — 한도 {limit:.0f}초를 넘어 입력 "
                "stale(베이시스 확정 안 함)",
                subject=label,
            )
        )
        return True

    def _basis(self, label: str) -> tuple[Decimal | None, int | None]:
        e = self.entries.get(label)
        if e is None:
            return None, None
        inp = self.inp
        try:
            age = basis_age(self.cal, e.trade_date, e.session, inp.trade_date, inp.session)
        except ValueError as err:  # 확정이 지금보다 뒤이거나 거래일이 아니다 — 쓰지 않는다
            self.entries.pop(label, None)
            self.health.append(
                EngineHealth(
                    "engine_basis_invalid",
                    "warning",
                    f"{label}: 확정 베이시스 나이를 셀 수 없다({err}) — 버린다"[:250],
                    subject=label,
                )
            )
            return None, None
        return e.basis, age

    def _same_month(self, key: SeriesKey, expiry_date: date) -> tuple[Decimal | None, Quality]:
        """분기 월물이면 같은 결제월 선물가(그 세션 출처, S_ref 와 같은 규칙). 없으면 None —
        0.5pt 비교를 건너뛴다(metrics §1.3)."""
        expiry = key[1]
        if key[0] != "" or not is_quarterly_monthly(expiry, expiry_date):
            return None, "ok"
        code = next((c for c, m in self.inp.futures_months.items() if m == expiry), None)
        if code is None:
            return None, "ok"
        q = select_s_ref(self.quotes, session=self.inp.session, code=code, now=self.inp.as_of)
        return (None, "ok") if q is None else (q.price, q.quality)

    def _rows(
        self, key: SeriesKey, ev: ExpiryEval, stale: bool = False
    ) -> tuple[list[StrikeGexRecord], list[OptionIvRecord]]:
        """만기 하나의 `strike_gex` 행(행사가별)과 `option_iv` 행(IV 를 시도한 종목 — 가격·F 가
        없어 역산하지 않은 종목은 뺀다). 옛 행으로 평가한 시리즈(stale)면 행 품질도 stale 이상."""
        cls, expiry = key
        table = expiry_strike_gex(ev)
        floor: Quality = "stale" if stale else "ok"
        strikes: list[StrikeGexRecord] = []
        for row in table.rows:
            strikes.append(
                StrikeGexRecord(
                    **self.stamp,
                    mrkt_cls=cls,
                    expiry=expiry,
                    strike=row.strike,
                    gex_call=row.gex_call,
                    gex_put=row.gex_put,
                    gex=row.gex,
                    forward=ev.F,
                    excluded_oi_ratio=table.excluded_oi_ratio,
                    quality=worst(table.quality, floor),
                )
            )
        ivs = [self._iv_row(cls, expiry, ev, o, floor) for o in ev.options if o.iv is not None]
        return strikes, ivs

    def _iv_row(
        self, cls: str, expiry: str, ev: ExpiryEval, o: OptionEval, floor: Quality = "ok"
    ) -> OptionIvRecord:
        iv, g, q = o.iv, o.greeks, o.quote
        if iv is None:
            raise ValueError("IV 를 시도하지 않은 종목")
        return OptionIvRecord(
            **self.stamp,
            mrkt_cls=cls,
            expiry=expiry,
            strike=q.strike,
            cp=q.cp,
            quote_source=q.source,
            price=o.choice.price,
            price_kind=o.choice.kind,
            prev_session=o.choice.prev_session,
            oi=q.oi,
            iv=iv.sigma,
            source=cast(Literal["self", "kis"], _IV_SOURCE[iv.source]) if iv.source else None,
            rescaled=iv.rescaled,
            t_kis=iv.t_kis,
            reason=iv.reason,
            excluded=o.reason,
            delta=None if g is None else g.delta,
            gamma=None if g is None else g.gamma,
            forward=ev.F,
            t_years=ev.T,
            quality=worst(o.quality, floor),
        )

    # ── 범위 ──

    def _evaluated(self) -> list[SeriesOutcome]:
        return [o for o in self.outcomes if o.ev is not None]

    def scopes(self) -> None:
        done = self._evaluated()
        evs = tuple(replace(o.ev, expiry=o.label) for o in done if o.ev is not None)
        by_label = {o.label: o for o in done}
        failed = [o for o in self.outcomes if o.status == "failed"]
        unknown = [o for o in self.outcomes if o.status == "no_expiry"]
        for scope in SCOPES:
            chosen = self._select(evs, scope)
            q, reasons = self._scope_input(chosen, scope, failed, unknown, by_label)
            self.scope_evals[scope] = chosen
            self.scope_quality[scope] = q
            self.scope_reasons[scope] = reasons
            self._levels(scope, chosen, q, reasons, by_label)
            self._exposures(scope, chosen, q, reasons)
        self._per_series(evs, by_label)

    def _select(self, evs: tuple[ExpiryEval, ...], scope: Scope) -> tuple[ExpiryEval, ...]:
        return select_scope(evs, scope, self.inp.trade_date)

    def _scope_input(
        self,
        chosen: tuple[ExpiryEval, ...],
        scope: Scope,
        failed: Sequence[SeriesOutcome],
        unknown: Sequence[SeriesOutcome],
        by_label: Mapping[str, SeriesOutcome],
    ) -> tuple[Quality, tuple[str, ...]]:
        """범위 입력 품질. 실패한 시리즈가 그 범위에 들면 invalid(`series_failed`). 최종거래일을
        모르는 시리즈(`no_expiry`)는 all 에서 그 OI 가 빠져 invalid, nearest·0dte 는 그 시리즈가 더
        이르거나 오늘 만기일 수 있어 estimated(`series_no_expiry`) [확인 필요]."""
        reasons: list[str] = []
        qs: list[Quality] = [self.s_ref.quality]
        if self.s_ref.quality != "ok":
            reasons.append(f"s_ref_{self.s_ref.quality}")
        for e in chosen:
            o = by_label[e.expiry]
            qs.append(o.input_quality)
            reasons.extend(f"{o.label}:{r}" for r in o.input_reasons)
        if any(self._in_scope(f, scope, chosen) for f in failed):
            qs.append("invalid")
            reasons.append("series_failed")
        if unknown:
            qs.append("invalid" if scope == "all" else "estimated")
            reasons.append("series_no_expiry")
        return worst(*qs), tuple(reasons)

    def _in_scope(self, o: SeriesOutcome, scope: Scope, chosen: tuple[ExpiryEval, ...]) -> bool:
        """실패한 시리즈가 그 범위에 들었을까 — all 은 늘, 0dte 는 만기일 = 귀속 거래일, nearest 는
        고른 만기일 이하(범위가 비었으면 든다). 만기일을 모르면 들 수 있다고 본다."""
        if o.expiry is None:
            return True
        d = o.expiry.last_trade_date
        if scope == "all":
            return True
        if scope == "0dte":
            return d == self.inp.trade_date
        first = min((e.expiry_date for e in chosen), default=None)
        return first is None or d <= first

    def _level(
        self,
        scope: Scope,
        name: str,
        fn: Callable[[], tuple[float | None, Quality, dict[str, Any]]],
        in_q: Quality,
        in_r: tuple[str, ...],
    ) -> None:
        flag = core_flag(name, self.flags, "levels")
        try:
            value, q, detail = fn()
            rec = LevelRecord(
                **self.stamp,
                scope=scope,
                name=name,
                value=value,
                detail=detail,
                quality=worst(q, in_q),
                reasons=in_r,
                flag=flag,
            )
        except Exception as e:  # 레벨 하나 — 그것만 invalid
            self._failed("engine_level_failed", f"{scope}/{name}", e)
            rec = LevelRecord(
                **self.stamp,
                scope=scope,
                name=name,
                value=None,
                detail={"error": type(e).__name__},
                quality="invalid",
                reasons=(*in_r, f"error:{type(e).__name__}"),
                flag=flag,
            )
        self.levels.append(rec)

    def _metric(
        self,
        metric: str,
        scope: Literal["all", "nearest", "0dte", "series"],
        key: str,
        fn: Callable[[], tuple[float | None, Quality, dict[str, Any]]],
        in_q: Quality,
        flag: Flag | None = None,
    ) -> None:
        """지표 한 행. flag 가 없으면 Phase 2 핵심 산출(`core_flag`), 등록부 지표는 그 플래그."""
        flag = core_flag(metric, self.flags) if flag is None else flag
        try:
            value, q, payload = fn()
            rec = MetricRecord(
                **self.stamp,
                metric=metric,
                scope=scope,
                key=key,
                value=value,
                payload=payload,
                quality=worst(q, in_q),
                flag=flag,
            )
        except Exception as e:  # 지표 하나 — 그것만 invalid
            self._failed("engine_metric_failed", f"{metric}/{scope}/{key}", e)
            rec = MetricRecord(
                **self.stamp,
                metric=metric,
                scope=scope,
                key=key,
                value=None,
                payload={"error": type(e).__name__},
                quality="invalid",
                flag=flag,
            )
        self.metrics.append(rec)

    def _failed(self, kind: str, what: str, e: Exception) -> None:
        self.health.append(
            EngineHealth(kind, "warning", f"{what}: {type(e).__name__}: {e}"[:250], subject=what)
        )

    def _levels(
        self,
        scope: Scope,
        chosen: tuple[ExpiryEval, ...],
        in_q: Quality,
        in_r: tuple[str, ...],
        by_label: Mapping[str, SeriesOutcome],
    ) -> None:
        walls = (("call_wall", call_wall), ("put_wall", put_wall), ("abs_gamma", abs_gamma_strike))
        for name, pick in walls:
            self._level(scope, name, lambda pick=pick: _wall(pick(chosen)), in_q, in_r)
        flip_box: list[Any] = []

        def flip() -> tuple[float | None, Quality, dict[str, Any]]:
            f = gamma_flip(chosen)
            flip_box.append(f)
            detail = {
                "multi_cross": f.multi_cross,
                "crossings": list(f.crossings),
                "f_ref": f.f_ref,
                "grid_points": len(f.profile),
            }
            return f.level, f.quality, detail

        self._level(scope, "flip", flip, in_q, in_r)

        def distance() -> tuple[float | None, Quality, dict[str, Any]]:
            if not flip_box:
                raise RuntimeError("Flip 을 계산하지 못했다")
            f = flip_box[0]
            return f.distance_pct, f.quality, {"f_ref": f.f_ref, "flip": f.level}

        self._level(scope, "flip_distance", distance, in_q, in_r)

        base = _base_expiry(chosen)
        move_q, move_r = self._base_input(base, by_label)
        moves: dict[str, Any] = {}
        for b in MOVE_BASES:

            def move(b: str = b) -> tuple[float | None, Quality, dict[str, Any]]:
                if base is None:  # 빈 범위(만기일이 아닌 날의 0dte 등) — 해당 없음, 품질 ok
                    return None, "ok", {"empty": True}
                atm = atm_iv(base)
                m = expected_move(
                    base.F,
                    atm,
                    self.inp.as_of,
                    cal=self.cal,
                    basis=cast(Literal["calendar", "trading"], b),
                )
                moves[b] = m
                detail = {
                    "expiry": base.expiry,
                    "F": m.F,
                    "lower": m.lower,
                    "upper": m.upper,
                    "minutes": m.minutes,
                    "dt_years": m.dt,
                    "atm_iv": atm.value,
                    "atm_iv_quality": atm.quality,
                    "atm_iv_reasons": list(atm.reasons),
                }
                return m.sigma, m.quality, detail

            self._level(scope, f"expected_move_{b}", move, move_q, move_r)

        def top() -> tuple[float | None, Quality, dict[str, Any]]:
            if base is None:
                return None, "ok", {"empty": True, "levels": []}
            m = moves.get("calendar")
            if m is None:
                raise RuntimeError("±1σ(달력)를 계산하지 못했다")
            t = top_levels_in_range(chosen, m)
            detail = {
                "from": base.expiry,
                "lower": t.lower,
                "upper": t.upper,
                "levels": [{"strike": float(r.strike), "gex_won": r.gex} for r in t.levels],
            }
            return float(len(t.levels)), t.quality, detail

        self._level(scope, "top_levels", top, in_q, in_r)

    def _exposures(
        self, scope: Scope, chosen: tuple[ExpiryEval, ...], in_q: Quality, in_r: tuple[str, ...]
    ) -> None:
        for metric, agg in (("net_gex", net_gex), ("dex", dex)):

            def fn(agg: Callable[..., Any] = agg) -> tuple[float | None, Quality, dict[str, Any]]:
                x = agg(chosen)
                payload = {
                    "excluded_oi_ratio": x.excluded_oi_ratio,
                    "expiries": list(x.expiries),
                    "reasons": list(in_r),
                }
                return x.value, x.quality, payload

            self._metric(metric, scope, "", fn, in_q)
            if scope == "all" and metric == "net_gex":
                self.all_quality = self.metrics[-1].quality  # 사이클 품질 = S_ref ⊕ 범위 all 순GEX

    def _base_input(
        self, base: ExpiryEval | None, by_label: Mapping[str, SeriesOutcome]
    ) -> tuple[Quality, tuple[str, ...]]:
        """±1σ 의 입력 품질 — S_ref 와 기준 만기의 행 품질만(범위의 다른 만기는 쓰지 않는다)."""
        reasons: list[str] = []
        qs: list[Quality] = [self.s_ref.quality]
        if self.s_ref.quality != "ok":
            reasons.append(f"s_ref_{self.s_ref.quality}")
        o = by_label.get(base.expiry) if base is not None else None
        if o is not None:
            qs.append(o.input_quality)
            reasons.extend(f"{o.label}:{r}" for r in o.input_reasons)
        return worst(*qs), tuple(reasons)

    def _per_series(
        self, evs: tuple[ExpiryEval, ...], by_label: Mapping[str, SeriesOutcome]
    ) -> None:
        for e in evs:
            o = by_label[e.expiry]
            in_q = worst(self.s_ref.quality, o.input_quality)

            def iv(e: ExpiryEval = e) -> tuple[float | None, Quality, dict[str, Any]]:
                a = atm_iv(e)
                payload = {"strikes": [float(k) for k in a.strikes], "reasons": list(a.reasons)}
                return a.value, a.quality, payload

            self._metric("atm_iv", "series", e.expiry, iv, in_q)
        list_q: Quality = (
            "estimated"
            if any(
                o.expiry is not None and o.expiry.source == "calendar" for o in by_label.values()
            )
            else "ok"
        )
        try:
            decay = gamma_by_expiry(evs, list_quality=list_q)
        except Exception as e:
            self._failed("engine_metric_failed", "expiry_gamma", e)
            decay = ()
        for g in decay:
            o = by_label[g.expiry]
            in_q = worst(self.s_ref.quality, o.input_quality)

            source = o.expiry.source if o.expiry is not None else None

            def fn(
                g: Any = g, source: str | None = source
            ) -> tuple[float | None, Quality, dict[str, Any]]:
                payload = {
                    "expiry_date": g.expiry_date.isoformat(),
                    "excluded_oi_ratio": g.excluded_oi_ratio,
                    "expiry_source": source,
                }
                return g.value, g.quality, payload

            self._metric("expiry_gamma", "series", g.expiry, fn, in_q)

    # ── 등록부 ──

    def plugins(
        self,
        registry: Sequence[MetricPlugin],
        flags: Mapping[str, Flag] | None,
        due: Callable[[MetricPlugin], bool] | None = None,
    ) -> None:
        if not registry:
            return
        done = self._evaluated()
        s_ref_r = () if self.s_ref.quality == "ok" else (f"s_ref_{self.s_ref.quality}",)
        view = CycleView(
            as_of=self.inp.as_of,
            trade_date=self.inp.trade_date,
            session=self.inp.session,
            s_ref=self.s_ref,
            evals=tuple(replace(o.ev, expiry=o.label) for o in done if o.ev is not None),
            scopes=dict(self.scope_evals),
            scope_quality=dict(self.scope_quality),
            calendar=self.cal,
            scope_reasons=dict(self.scope_reasons),
            series_quality={o.label: worst(self.s_ref.quality, o.input_quality) for o in done},
            series_reasons={o.label: (*s_ref_r, *o.input_reasons) for o in done},
            series_class={o.label: o.key[0] for o in done},
            gaps=tuple(
                SeriesGap(
                    o.label,
                    o.key[0],
                    "failed" if o.status == "failed" else "no_expiry",
                    None if o.expiry is None else o.expiry.last_trade_date,
                )
                for o in self.outcomes
                if o.status in ("failed", "no_expiry")
            ),
            input_quality={o.label: o.input_quality for o in done},
            input_reasons={o.label: o.input_reasons for o in done},
            strikes={
                o.label: tuple(sorted(ks)) for o in done if (ks := self.inp.strikes.get(o.key))
            },
        )
        for p in registry:
            flag = resolve_flag(p.flag, flags)
            if flag == "off" or (due is not None and not due(p)):
                continue
            self.plugins_run.append(p.name)
            try:
                values = list(p.compute(view))
            except Exception as e:  # 지표 하나 — 그것만 invalid
                self._failed("engine_metric_failed", p.name, e)
                self.metrics.append(
                    MetricRecord(
                        **self.stamp,
                        metric=p.name,
                        scope="all",
                        value=None,
                        payload={"error": type(e).__name__},
                        quality="invalid",
                        flag=flag,
                    )
                )
                continue
            for v in values:
                self._metric(
                    p.name,
                    v.scope,
                    v.key,
                    lambda v=v: (v.value, v.quality, dict(v.payload)),
                    "ok",
                    flag,
                )

    # ── 결과 ──

    def result(self) -> CycleResult:
        book = BasisBook(near_code=self.near, entries=self.entries)
        return CycleResult(
            as_of=self.inp.as_of,
            trade_date=self.inp.trade_date,
            session=self.inp.session,
            status="ok",
            quality=self.all_quality,
            near_code=self.inp.near_code,
            s_ref=self.s_ref,
            basis=book,
            series=tuple(self.outcomes),
            levels=tuple(self.levels),
            metrics=tuple(self.metrics),
            strike_gex=tuple(self.strikes),
            option_iv=tuple(self.ivs),
            health=tuple(self.health),
            plugins_run=tuple(self.plugins_run),
        )


def _wall(w: Any) -> tuple[float | None, Quality, dict[str, Any]]:
    strike = None if w.strike is None else float(w.strike)
    return strike, w.quality, {"gex_won": w.value}


def _base_expiry(chosen: Sequence[ExpiryEval]) -> ExpiryEval | None:
    """범위의 기준 만기 — F 있는 가장 이른 만기(`core.levels.reference_forward` 와 같게), F 있는
    만기가 없으면 가장 이른 만기(그 ±1σ 는 invalid)."""
    order = sorted(chosen, key=lambda e: (e.expiry_date, e.expiry))
    with_f = [e for e in order if e.F is not None]
    if with_f:
        return with_f[0]
    return order[0] if order else None

"""무결측 판정 — 저장된 데이터로 세션 하나의 스트림별 공백을 찾는다 (docs/phase1_design.md §9).

계산만 한다(부작용 없음). 입력은 `SessionInputs`(저장소 읽기 결과 — `load_inputs` 가 `GapReader`
로 모은다, 구현은 data/store.py `PostgresSink`), 결과는 `SessionReport`(스트림별 기대·실수신·최대
공백·공백 목록·판정). 쓰기(`collection_gaps`·`collection_reports`)·health·로그는 부르는 쪽
(services/scheduler/gaps.py `GapDaily`, scripts/nogap_report.py)이 한다.

세션: 주간 = 거래일 T 의 08:45~15:45, 야간 = T 로 귀속되는 밤(prev_trading_day(T) 18:00 ~ 다음 날
06:00 — 열린 밤만). 스트림 (설계 §9 표, 기본값은 `GapConfig` — §12 [확인 필요]):

- `board:<시리즈>` — 주간(야간 A). chain_snapshots source=board, 시장분류·만기별 서로 다른 ts.
  60초 넘게 새 스냅샷 없음
- `fut_board` — 주간·야간 A·B. fut_board 서로 다른 ts (주간 선물 전광판, 야간 B 선물 단건 CM). 60초
- `underlying` — 주간(야간 A). 기초자산 조회(poller 'underlying', display-board-top) 성공 응답.
  응답 필드가 미실측이라 원문만 남아 raw_messages 일 키 `underlying|…`·`rt_cd` 0 으로 센다.
  한도·주기는 설계 §9 표의 '선물 전광판·기초자산' 한 줄이라 fut_board 값(60초)
- `investor:<시장>/<업종>` — 주간 필수, 야간은 기록만. investor_flow 조합별 서로 다른 ts. 120초
- `fill1:<시리즈>` — 주간 월물, 야간 B 최근접·월물. raw_messages 일 키 `fill1:<시리즈>:…`·
  `rt_cd` 0. 120초
- `ws_connection` — 둘 다. health_events ws-gateway `ws_connected`·`ws_disconnected`·
  `ws_connect_failed`. 끊김 구간 전체
- `fut_trades` — 둘 다. minute_bars(봉 있는 분) vs fut_ticks(분별 체결 수). 봉 있는 분인데 체결 0건

- 공백 = 이웃한 두 점(구간 시작·스냅샷들·구간 끝) 사이가 한도를 **넘는** 것 — 한도와 같으면 공백이
  아니다. 스냅샷은 quality invalid 행을 뺀다(검증 실패는 새 스냅샷이 아니다)
- 추적 만기(board·fill1 의 시리즈와 그 구간)는 poller 와 같은 규칙으로 다시 만든다: 그 세션
  series_expiries(시리즈별 최종거래일 — KIS 값이 캘린더 값보다 먼저, 늦은 것) → 시각마다
  `select_targets`(만기일 15:20 이 지나면 다음 시리즈). 바뀌는 시각은 만기 시각뿐이라 그 사이는
  같다. series_expiries 가 없으면(또는 그것으로 추적 시리즈를 못 고르면) 데이터에 보인 시리즈만
  세션 전체로 보고(detail targets=observed — 공백은 그대로 공백), 통째로 안 온 추적 시리즈는 볼 수
  없으니 필수 표지 스트림 `board`·`fill1` 을 판정 불가(`unverified`)로 더한다 — 그 세션은 무결측이
  아니다. 데이터도 없으면 표지 한 줄뿐
- fill1 은 chain_snapshots 에서 보강 2 와 구분되지 않아(둘 다 source=fill) poller 가 원문에 붙인 일
  키로 센다. 응답 본문 `rt_cd` 가 0 인 것만
- 웹소켓 연결: 세션 시작 전 마지막 사건으로 시작 상태를 정하고(사건이 없으면 끊김) 끊김 사건부터
  다음 `ws_connected` 까지가 끊김 구간. ws-gateway 가 사건 없이 죽은 동안은 여기서 안 보인다 —
  그 구간은 `fut_trades`(봉은 있는데 체결 없음)가 잡는다
- 선물 체결: 분봉(KIS `F`·`CM`, 설계 §8 적재)에 봉이 있는 분인데 그 종목 웹소켓 체결이 0건인 분.
  봉이 없는 분·체결량 0 봉·종가 단일가(15:35~15:45) 봉은 보지 않는다. 봉 시각 표기가 봉 시작인지
  끝인지 미실측이라(§12) 봉 시각 m 의 체결 창을 [m − 60초, m + 60초) 로 넓게 본다(`bar_before_s`·
  `bar_after_s`). 분기 만기일 주간은 종목이 둘(만기 종목·차월물 — 설계 §8)이다 — 봉이 먼저 끝난
  종목이 만기 종목이고 15:10 전까지(15:10~15:20 은 그 종목 종가 단일가), 차월물은 15:20 +
  `switch_slack_s` 뒤부터 본다(ws-gateway 가 15:20 + 마감 여유 60초 뒤 구독을 옮긴다). 분봉 적재가
  덜 됐거나(`partial`) 실패했거나(`missing`) 봉이 없으면 판정 불가(`unverified`)
- 판정: 공백이 있으면 `gaps`, 없으면 `ok`, 입력이 모자라면 `unverified`. 세션이 깨끗하다 = 필수
  스트림이 모두 `ok`
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.calendar import KST, TradingCalendar, expiry_at, night_session_opens, session_bounds
from data.store import CollectionGapRecord, CollectionReportRecord
from services.poller.context import Series, Targets, select_targets
from services.poller.endpoints import INVESTOR_PAIRS

Session = Literal["day", "night"]
Status = Literal["ok", "gaps", "unverified"]
NightMode = Literal["A", "B", "C"]
MinuteStatus = Literal["loaded", "partial", "missing"]

WS_UP = "ws_connected"
WS_DOWN: frozenset[str] = frozenset({"ws_disconnected", "ws_connect_failed"})
WS_KINDS: tuple[str, ...] = (WS_UP, *sorted(WS_DOWN))
MINUTE = timedelta(minutes=1)


class GapConfig(BaseModel):
    """판정 한도·주기(초). 한도는 설계 §9 표, 주기는 PLAN §4.4(기대 수 계산용). 나머지는 §12
    [확인 필요]. night_mode·night_investor 는 poller 설정(services/poller/config.py)과 같게 둔다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    board_gap_s: float = Field(default=60.0, gt=0)
    board_period_s: float = Field(default=30.0, gt=0)
    fut_board_gap_s: float = Field(default=60.0, gt=0)
    fut_board_period_s: float = Field(default=30.0, gt=0)
    investor_gap_s: float = Field(default=120.0, gt=0)
    investor_period_s: float = Field(default=60.0, gt=0)
    fill1_gap_s: float = Field(default=120.0, gt=0)
    fill1_period_s: float = Field(default=60.0, gt=0)
    night_mode: NightMode = "B"
    night_investor: bool = True  # poller 가 야간 투자자별을 부르는가
    # 야간 투자자별 동작은 미실측(#13) — 값이 오는지 녹화로 볼 때까지 기록만(필수 아님)
    night_investor_required: bool = False
    # 봉 시각 m 의 체결 창 [m − before, m + after) — 봉 표기(시작·끝) 미실측이라 둘 다 덮는다
    bar_before_s: int = Field(default=60, ge=0)
    bar_after_s: int = Field(default=60, gt=0)
    # 주간 종가 단일가 — 이 안의 봉은 보지 않는다(체결 없는 구간)
    closing_auction: tuple[time, time] = (time(15, 35), time(15, 45))
    # 분기 만기일 만기 종목의 종가 단일가 시작(끝은 만기 시각 15:20)
    expiring_auction_start: time = time(15, 10)
    # 분기 만기일 차월물은 만기 시각 + 이만큼 뒤부터 본다(ws 마감 여유 60초 + 구독 전환)
    switch_slack_s: float = Field(default=120.0, ge=0)

    @model_validator(mode="after")
    def _minute_multiples(self) -> GapConfig:
        if self.bar_before_s % 60 or self.bar_after_s % 60:
            raise ValueError("bar_before_s·bar_after_s 는 60초 배수 — 체결을 분 단위로 센다")
        if self.closing_auction[0] >= self.closing_auction[1]:
            raise ValueError("closing_auction 은 (시작, 끝)")
        return self


# ── 결과 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Gap:
    """공백 하나 — [start, end]. expected 는 그 동안 왔어야 할 수(주기 수·봉 수·초), received 는
    그 안에 온 수(정의상 0)."""

    stream: str
    start: datetime
    end: datetime
    expected: int | None = None
    received: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True)
class StreamReport:
    stream: str
    required: bool
    status: Status
    span_start: datetime
    span_end: datetime
    expected: int | None = None
    received: int | None = None
    gaps: tuple[Gap, ...] = ()
    max_gap_s: float | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class SessionReport:
    trade_date: date
    session: Session
    start: datetime
    end: datetime
    streams: tuple[StreamReport, ...]

    @property
    def gaps(self) -> list[Gap]:
        return [g for s in self.streams for g in s.gaps]

    @property
    def problems(self) -> list[StreamReport]:
        """필수 스트림 중 ok 가 아닌 것."""
        return [s for s in self.streams if s.required and s.status != "ok"]

    @property
    def clean(self) -> bool:
        return not self.problems

    def stream(self, name: str) -> StreamReport:
        return next(s for s in self.streams if s.stream == name)

    def gap_records(self, detected_at: datetime) -> list[CollectionGapRecord]:
        return [
            CollectionGapRecord(
                stream=g.stream,
                start_ts=g.start,
                end_ts=g.end,
                trade_date=self.trade_date,
                session=self.session,
                expected=g.expected,
                received=g.received,
                detected_at=detected_at,
                detail=dict(g.detail),
            )
            for g in self.gaps
        ]

    def report_records(self, evaluated_at: datetime) -> list[CollectionReportRecord]:
        return [
            CollectionReportRecord(
                trade_date=self.trade_date,
                session=self.session,
                stream=s.stream,
                evaluated_at=evaluated_at,
                span_start=s.span_start,
                span_end=s.span_end,
                required=s.required,
                status=s.status,
                expected=s.expected,
                received=s.received,
                gaps=len(s.gaps),
                max_gap_s=s.max_gap_s,
                detail=dict(s.detail),
            )
            for s in self.streams
        ]


# ── 입력 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionInputs:
    """세션 하나를 판정하는 데 쓰는 저장된 값 (`GapReader` 읽기 결과 그대로)."""

    # (시장분류, 만기 6자리, 원천 kis|calendar, 최종거래일, 기록 시각)
    series_dates: Sequence[tuple[str, str, str, date, datetime]] = ()
    board: Sequence[tuple[str, str, datetime]] = ()  # (시장분류, 만기, ts)
    fut_board: Sequence[datetime] = ()
    underlying: Sequence[datetime] = ()  # 기초자산 조회 성공 응답 시각
    investor: Sequence[tuple[str, str, datetime]] = ()  # (시장, 업종, ts)
    fill1: Sequence[tuple[str, datetime]] = ()  # (시리즈 라벨, ts)
    ws_before: str | None = None  # 세션 시작 전 마지막 ws 연결 사건 종류
    ws_events: Sequence[tuple[datetime, str]] = ()  # 세션 안 (시각, 종류)
    bars: Sequence[tuple[str, datetime, int | None]] = ()  # (종목, 봉 시각, 체결량)
    tick_minutes: Sequence[tuple[str, datetime, int]] = ()  # (종목, 분, 체결 수)


class GapReader(Protocol):
    """판정 입력 읽기 (data/store.py `PostgresSink`). 실패는 예외 — 부르는 쪽이 처리한다."""

    def series_dates(
        self, trade_date: date, session: str
    ) -> list[tuple[str, str, str, date, datetime]]: ...

    def board_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]: ...

    def fut_board_times(self, trade_date: date, session: str) -> list[datetime]: ...

    def underlying_times(self, trade_date: date, session: str) -> list[datetime]: ...

    def investor_times(self, trade_date: date, session: str) -> list[tuple[str, str, datetime]]: ...

    def fill1_times(self, trade_date: date, session: str) -> list[tuple[str, datetime]]: ...

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]: ...

    def minute_bar_times(
        self, trade_date: date, session: str
    ) -> list[tuple[str, datetime, int | None]]: ...

    def fut_tick_minutes(
        self, trade_date: date, session: str
    ) -> list[tuple[str, datetime, int]]: ...


def session_span(
    trade_date: date, session: Session, cal: TradingCalendar
) -> tuple[datetime, datetime] | None:
    """(귀속 거래일, 세션) → [시작, 끝) KST. 그 세션이 캘린더상 열리지 않으면 None."""
    if not cal.is_trading_day(trade_date):
        return None
    if session == "day":
        return session_bounds(trade_date, "day")
    start = cal.prev_trading_day(trade_date)
    if not night_session_opens(start, cal):
        return None
    return session_bounds(start, "night")


def load_inputs(
    reader: GapReader, trade_date: date, session: Session, span: tuple[datetime, datetime]
) -> SessionInputs:
    start, end = span
    before, events = reader.ws_connection_events(start, end, WS_KINDS)
    return SessionInputs(
        series_dates=reader.series_dates(trade_date, session),
        board=reader.board_times(trade_date, session),
        fut_board=reader.fut_board_times(trade_date, session),
        underlying=reader.underlying_times(trade_date, session),
        investor=reader.investor_times(trade_date, session),
        fill1=reader.fill1_times(trade_date, session),
        ws_before=before,
        ws_events=events,
        bars=reader.minute_bar_times(trade_date, session),
        tick_minutes=reader.fut_tick_minutes(trade_date, session),
    )


# ── 스트림별 계산 ────────────────────────────────────────────────────────────


def interval_report(
    stream: str,
    times: Iterable[datetime],
    span: tuple[datetime, datetime],
    *,
    gap_s: float,
    period_s: float,
    required: bool,
    detail: Mapping[str, Any] | None = None,
) -> StreamReport:
    """주기 스트림: 구간 [a, b] 안 스냅샷 시각들. 이웃한 두 점(a·스냅샷·b)이 gap_s 를 넘게 떨어지면
    공백. expected = 구간의 주기 칸 수, received = 스냅샷이 하나라도 있는 칸 수."""
    a, b = span
    pts = sorted({t for t in times if a <= t <= b})
    gaps: list[Gap] = []
    longest = 0.0
    for x, y in pairwise([a, *pts, b]):
        d = (y - x).total_seconds()
        longest = max(longest, d)
        if d > gap_s:
            gaps.append(Gap(stream, x, y, expected=int(d // period_s), received=0))
    total = (b - a).total_seconds()
    expected = math.ceil(total / period_s) if total > 0 else 0
    slots = {int((t - a).total_seconds() // period_s) for t in pts}
    received = sum(1 for s in slots if s < expected)
    return StreamReport(
        stream,
        required,
        "gaps" if gaps else "ok",
        a,
        b,
        expected,
        received,
        tuple(gaps),
        longest,
        {"gap_s": gap_s, "period_s": period_s, **(detail or {})},
    )


def down_intervals(
    before: str | None, events: Iterable[tuple[datetime, str]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """ws 연결 사건 → [start, end] 안 끊김 구간. 시작 상태는 before(없거나 끊김 사건이면 끊김)."""
    down_since: datetime | None = None if before == WS_UP else start
    out: list[tuple[datetime, datetime]] = []
    for ts, kind in sorted(events):
        if ts < start or ts > end:
            continue
        if kind == WS_UP:
            if down_since is not None and ts > down_since:
                out.append((down_since, ts))
            down_since = None
        elif kind in WS_DOWN and down_since is None:
            down_since = ts
    if down_since is not None and end > down_since:
        out.append((down_since, end))
    return out


def connection_report(
    before: str | None,
    events: Iterable[tuple[datetime, str]],
    span: tuple[datetime, datetime],
    *,
    required: bool = True,
) -> StreamReport:
    """끊김 구간 전체가 공백. expected = 구간 초, received = 붙어 있던 초."""
    a, b = span
    downs = down_intervals(before, events, a, b)
    gaps = tuple(
        Gap("ws_connection", x, y, expected=int((y - x).total_seconds()), received=0)
        for x, y in downs
    )
    total = int((b - a).total_seconds())
    lost = sum(g.seconds for g in gaps)
    return StreamReport(
        "ws_connection",
        required,
        "gaps" if gaps else "ok",
        a,
        b,
        total,
        max(0, round(total - lost)),
        gaps,
        max((g.seconds for g in gaps), default=0.0),
        {"unit": "s", "state_before": before},
    )


@dataclass(frozen=True)
class _Check:
    """한 종목의 체결 대조 구간 [lo, hi] 과 그 안에서 빼는 구간들 [x, y)."""

    lo: datetime
    hi: datetime
    skip: tuple[tuple[datetime, datetime], ...] = ()

    def covers(self, t: datetime) -> bool:
        return self.lo <= t <= self.hi and not any(x <= t < y for x, y in self.skip)


def _kst_at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


def trade_checks(
    bars: Sequence[tuple[str, datetime, int | None]],
    trade_date: date,
    session: Session,
    span: tuple[datetime, datetime],
    cfg: GapConfig,
) -> dict[str, _Check]:
    """종목별 대조 구간 — 모듈 설명(분기 만기일 두 종목, 종가 단일가)."""
    a, b = span
    last: dict[str, datetime] = {}
    for code, ts, _ in bars:
        last[code] = max(last.get(code, ts), ts)
    codes = sorted(last, key=lambda c: (last[c], c))
    auction: tuple[tuple[datetime, datetime], ...] = ()
    if session == "day":
        lo, hi = cfg.closing_auction
        auction = ((_kst_at(trade_date, lo), _kst_at(trade_date, hi)),)
    if session == "day" and len(codes) >= 2:
        switch = expiry_at(trade_date)
        expiring = ((_kst_at(trade_date, cfg.expiring_auction_start), switch),)
        after = switch + timedelta(seconds=cfg.switch_slack_s)
        out = {codes[0]: _Check(a, switch, expiring)}
        out.update({c: _Check(after, b, auction) for c in codes[1:]})
        return out
    return {c: _Check(a, b, auction) for c in codes}


def trade_report(
    bars: Sequence[tuple[str, datetime, int | None]],
    tick_minutes: Sequence[tuple[str, datetime, int]],
    trade_date: date,
    session: Session,
    span: tuple[datetime, datetime],
    cfg: GapConfig,
    minute_status: MinuteStatus | None = None,
    *,
    required: bool = True,
) -> StreamReport:
    """봉 있는 분인데 그 종목 웹소켓 체결이 0건인 분이 공백 — 이어진 것(사이에 체결 있는 봉 없음)은
    한 공백으로. expected = 대조한 봉 수, received = 체결이 있던 봉 수."""
    a, b = span
    name = "fut_trades"
    base: dict[str, Any] = {
        "minute_status": minute_status,
        "window_s": [-cfg.bar_before_s, cfg.bar_after_s],
    }
    if minute_status in ("partial", "missing"):
        why = f"분봉 적재 {minute_status} — 봉 있는 분을 다 모른다"
        return StreamReport(name, required, "unverified", a, b, detail={**base, "reason": why})
    in_span = [(c, t, v) for c, t, v in bars if a <= t <= b]
    if not in_span:
        return StreamReport(
            name, required, "unverified", a, b, detail={**base, "reason": "분봉이 없다"}
        )
    checks = trade_checks(in_span, trade_date, session, span, cfg)
    ticks: dict[str, set[datetime]] = {}
    for code, minute, n in tick_minutes:
        if n > 0:
            ticks.setdefault(code, set()).add(
                minute.astimezone(UTC).replace(second=0, microsecond=0)
            )
    offsets = [timedelta(seconds=s) for s in range(-cfg.bar_before_s, cfg.bar_after_s, 60)]
    gaps: list[Gap] = []
    checked = covered = zero = skipped = 0
    per_code: dict[str, int] = {}
    for code in sorted(checks):
        chk = checks[code]
        have = ticks.get(code, set())
        run: list[datetime] = []
        for _, t, vol in sorted((x for x in in_span if x[0] == code), key=lambda x: x[1]):
            if vol == 0:
                zero += 1
                continue
            if not chk.covers(t):
                skipped += 1
                continue
            checked += 1
            per_code[code] = per_code.get(code, 0) + 1
            m = t.astimezone(UTC).replace(second=0, microsecond=0)
            if any(m + o in have for o in offsets):
                covered += 1
                if run:
                    gaps.append(_trade_gap(code, run))
                    run = []
            else:
                run.append(t)
        if run:
            gaps.append(_trade_gap(code, run))
    return StreamReport(
        name,
        required,
        "gaps" if gaps else "ok",
        a,
        b,
        checked,
        covered,
        tuple(gaps),
        max((g.seconds for g in gaps), default=0.0),
        {**base, "codes": per_code, "zero_volume": zero, "outside": skipped},
    )


def _trade_gap(code: str, run: Sequence[datetime]) -> Gap:
    return Gap(
        "fut_trades",
        run[0],
        run[-1] + MINUTE,
        expected=len(run),
        received=0,
        detail={"code": code},
    )


# ── 추적 만기 ────────────────────────────────────────────────────────────────


def dates_from_rows(rows: Iterable[tuple[str, str, str, date, datetime]]) -> dict[Series, date]:
    """series_expiries 행 → 시리즈별 최종거래일 — KIS 값이 캘린더 값보다 먼저, 같으면 늦은 기록."""
    best: dict[Series, tuple[int, datetime, date]] = {}
    for cls, mtrt, source, last, ts in rows:
        s = Series(cls, mtrt)
        cand = (1 if source == "kis" else 0, ts, last)
        cur = best.get(s)
        if cur is None or cand[:2] > cur[:2]:
            best[s] = cand
    return {s: v[2] for s, v in best.items()}


Pick = Callable[[Targets], Iterable[Series]]


def role_spans(
    dates: Mapping[Series, date], start: datetime, end: datetime, pick: Pick
) -> dict[Series, tuple[datetime, datetime]]:
    """[start, end] 동안 pick(추적 대상)에 든 시리즈와 그 구간 — 바뀌는 시각은 만기 시각뿐."""
    cuts = sorted({start, end, *(e for d in dates.values() if start < (e := expiry_at(d)) < end)})
    spans: dict[Series, tuple[datetime, datetime]] = {}
    for a, b in pairwise(cuts):
        t = select_targets(dates, a)
        if t is None:
            continue
        for s in pick(t):
            lo, hi = spans.get(s, (a, b))
            spans[s] = (min(lo, a), max(hi, b))
    return spans


def _tracked(t: Targets) -> tuple[Series, ...]:
    return t.tracked


def _monthly(t: Targets) -> tuple[Series, ...]:
    return (t.monthly,) if t.monthly is not None else ()


def _nearest_and_monthly(t: Targets) -> tuple[Series, ...]:
    """야간 B 보강 1: 최근접(60초) + 월물(120초, 최근접과 다를 때) — poller `_fill_plan`."""
    out = [t.nearest]
    if t.monthly is not None and t.monthly != t.nearest:
        out.append(t.monthly)
    return tuple(out)


def _series_streams(
    prefix: str,
    spans: Mapping[Series, tuple[datetime, datetime]],
    times: Mapping[str, list[datetime]],
    session_span_: tuple[datetime, datetime],
    *,
    gap_s: float,
    period_s: float,
    required: bool,
) -> list[StreamReport]:
    """시리즈별 주기 스트림. 추적 구간을 모르면(spans 없음) 데이터에 보인 시리즈를 세션 전체로
    판정하고, 통째로 안 온 추적 시리즈는 볼 수 없으니 표지 스트림 `prefix` 를 판정 불가로 더한다.
    데이터도 없으면 그 표지 한 줄뿐."""
    detail: dict[str, Any] = {"targets": "series_expiries"}
    out: list[StreamReport] = []
    if not spans:
        a, b = session_span_
        observed = sorted(times)
        why = (
            "추적 만기를 모른다(series_expiries 로 추적 시리즈를 못 고른다) — 관측 시리즈만 판정, "
            "통째로 안 온 시리즈는 못 본다"
            if observed
            else "추적 만기를 모른다 — series_expiries 도 데이터도 없다"
        )
        out.append(
            StreamReport(
                prefix, required, "unverified", a, b, detail={"reason": why, "observed": observed}
            )
        )
        spans = {_parse_label(k): session_span_ for k in observed}
        detail = {"targets": "observed"}
    return out + [
        interval_report(
            f"{prefix}:{s.label}",
            times.get(s.label, []),
            span,
            gap_s=gap_s,
            period_s=period_s,
            required=required,
            detail=detail,
        )
        for s, span in sorted(spans.items(), key=lambda kv: (kv[1][0], kv[0].label))
    ]


def _parse_label(label: str) -> Series:
    cls, _, mtrt = label.partition(":")
    return Series("" if cls == "M" else cls, mtrt)


def _by_label(rows: Iterable[tuple[str, str, datetime]]) -> dict[str, list[datetime]]:
    out: dict[str, list[datetime]] = {}
    for cls, mtrt, ts in rows:
        out.setdefault(Series(cls, mtrt).label, []).append(ts)
    return out


# ── 세션 ─────────────────────────────────────────────────────────────────────


def evaluate_session(
    inputs: SessionInputs,
    cal: TradingCalendar,
    trade_date: date,
    session: Session,
    cfg: GapConfig | None = None,
    *,
    minute_status: MinuteStatus | None = None,
) -> SessionReport:
    """세션 하나의 스트림별 판정. 캘린더상 열리지 않은 세션이면 ValueError."""
    cfg = cfg or GapConfig()
    span = session_span(trade_date, session, cal)
    if span is None:
        raise ValueError(f"{trade_date} {session}: 캘린더상 열리지 않은 세션")
    a, b = span
    mode: NightMode = "A" if session == "day" else cfg.night_mode
    dates = dates_from_rows(inputs.series_dates)
    streams: list[StreamReport] = []
    if mode == "A":
        streams += _series_streams(
            "board",
            role_spans(dates, a, b, _tracked),
            _by_label(inputs.board),
            span,
            gap_s=cfg.board_gap_s,
            period_s=cfg.board_period_s,
            required=True,
        )
    if mode in ("A", "B"):
        streams.append(
            interval_report(
                "fut_board",
                inputs.fut_board,
                span,
                gap_s=cfg.fut_board_gap_s,
                period_s=cfg.fut_board_period_s,
                required=True,
            )
        )
    if mode == "A":  # 기초자산 조회는 선물 전광판과 같이 분기 A 에서만 부른다(poller)
        streams.append(
            interval_report(
                "underlying",
                inputs.underlying,
                span,
                gap_s=cfg.fut_board_gap_s,
                period_s=cfg.fut_board_period_s,
                required=True,
            )
        )
    if session == "day" or cfg.night_investor:
        required = session == "day" or cfg.night_investor_required
        combos: dict[str, list[datetime]] = {}
        for mkt, sector, ts in inputs.investor:
            combos.setdefault(f"{mkt}/{sector}", []).append(ts)
        for mkt, sector in INVESTOR_PAIRS:
            key = f"{mkt}/{sector}"
            streams.append(
                interval_report(
                    f"investor:{key}",
                    combos.get(key, []),
                    span,
                    gap_s=cfg.investor_gap_s,
                    period_s=cfg.investor_period_s,
                    required=required,
                )
            )
    if mode in ("A", "B"):
        fill1: dict[str, list[datetime]] = {}
        for label, ts in inputs.fill1:
            fill1.setdefault(label, []).append(ts)
        pick = _monthly if mode == "A" else _nearest_and_monthly
        streams += _series_streams(
            "fill1",
            role_spans(dates, a, b, pick),
            fill1,
            span,
            gap_s=cfg.fill1_gap_s,
            period_s=cfg.fill1_period_s,
            required=True,
        )
    streams.append(connection_report(inputs.ws_before, inputs.ws_events, span))
    streams.append(
        trade_report(
            inputs.bars, inputs.tick_minutes, trade_date, session, span, cfg, minute_status
        )
    )
    return SessionReport(trade_date, session, a, b, tuple(streams))

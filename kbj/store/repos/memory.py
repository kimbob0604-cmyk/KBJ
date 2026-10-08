"""메모리 저장소 — 시험·시뮬레이션용(docs/p3_design.md §1.1·§8.3). Postgres 구현과 **같은 규칙**.

같은 시험 묶음(`tests/unit/store/repo_cases.py`)이 이 구현과 Pg 구현(통합 시험)을 모두 돌린다.
기본 키·덮어쓰기·원장 우선순위·잠정→확정 차이 기록·manual 우선 규칙을 Pg 와 똑같이 지킨다.
스레드 하나에서 쓴다고 가정한다(가짜 시계 시뮬레이션). 받은 시각·쓴 작업 이름도 함께 둔다.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from kbj.core.quality import Quality
from kbj.core.rows import (
    AllTime,
    Bar,
    BoardArtifact,
    BoardDayRecord,
    Change,
    EtfDay,
    EtfMeta,
    EtfQuote,
    Fund,
    HoldingRow,
    IndexBar,
    IndexQuote,
    IntradayInvestor,
    InvestorDay,
    Label,
    LedgerCheck,
    RankRow,
    ReconcileRow,
    Revision,
    SectorQuote,
    Snap,
    SplitCheck,
    SplitEvent,
    StockDay,
    UniverseRow,
    pick_best,
)
from kbj.store.repos._common import require_aware, require_loaded_by, window_start
from kbj.store.repos.etf import best_etf_days, check_changes, check_holdings
from kbj.store.repos.flows import (
    RevisionReport,
    StoredInvestorDay,
    best_investor_days,
    plan_investor_upsert,
)
from kbj.store.repos.market import (
    Asset,
    best_universe,
    group_best_bars,
    snap_extra,
    snap_rank,
)


@dataclass
class Stored[T]:
    row: T
    received_at: datetime | None
    loaded_by: str


def _days_desc(dates: Collection[date], upto: date) -> list[date]:
    return sorted({d for d in dates if d <= upto}, reverse=True)


def _in(code: str, wanted: Collection[str] | None) -> bool:
    return wanted is None or code in wanted


# ── 시세 ────────────────────────────────────────────────────────────────────────────────


class MemoryMarketRepo:
    def __init__(self) -> None:
        # (asset, code, date, source, venue) → 일봉
        self.bars: dict[tuple[str, str, date, str, str], Stored[Bar]] = {}
        self.snaps: dict[tuple[str, date, str, str], Stored[Snap]] = {}
        self.snap_notes: dict[tuple[str, date, str, str], str] = {}
        self.universe_rows: dict[tuple[str, date, str], Stored[UniverseRow]] = {}
        self.index_rows: dict[tuple[str, datetime, str], Stored[IndexQuote]] = {}
        self.sector_rows: dict[tuple[str, str, datetime, str], Stored[SectorQuote]] = {}
        self.rank_rows_: dict[tuple[str, datetime, int, str], Stored[RankRow]] = {}
        self.reconcile_rows: dict[tuple[date, str, str], ReconcileRow] = {}

    def upsert_daily_bars(
        self,
        rows: Sequence[Bar],
        *,
        loaded_by: str,
        asset: Asset = "stock",
        received_at: datetime | None = None,
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for b in rows:
            self.bars[(asset, b.code, b.date, b.source, b.venue)] = Stored(
                b, received_at, loaded_by
            )
        return len(rows)

    def upsert_index_bars(
        self, rows: Sequence[IndexBar], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for b in rows:
            bar = Bar(
                code=b.code, date=b.date, open=b.open, high=b.high, low=b.low, close=b.close,
                volume=b.volume, turnover=b.turnover, source=b.source, venue="", quality=b.quality,
            )  # fmt: skip
            self.bars[("index", b.code, b.date, b.source, "")] = Stored(bar, received_at, loaded_by)
        return len(rows)

    def _days(self, asset: str, upto: date) -> list[date]:
        return _days_desc([k[2] for k in self.bars if k[0] == asset], upto)

    def trading_days(self, upto: date, n: int, *, asset: Asset = "stock") -> list[date]:
        if n <= 0:
            raise ValueError("n 은 1 이상")
        return self._days(asset, upto)[:n]

    def _window(
        self, asset: str, codes: Collection[str] | None, upto: date, n: int
    ) -> dict[str, list[Bar]]:
        start = window_start(self._days(asset, upto), n)
        if start is None:
            return {}
        wanted = None if codes is None else set(codes)
        rows = [
            s.row
            for (a, code, d, _, _), s in sorted(self.bars.items(), key=lambda kv: kv[0][1:3])
            if a == asset and start <= d <= upto and _in(code, wanted)
        ]
        return group_best_bars(rows)

    def series(
        self, codes: Collection[str] | None, upto: date, n: int, *, asset: Asset = "stock"
    ) -> dict[str, list[Bar]]:
        return self._window(asset, codes, upto, n)

    def index_series(
        self, codes: Collection[str] | None, upto: date, n: int
    ) -> dict[str, list[IndexBar]]:
        return {
            c: [
                IndexBar(
                    code=b.code, date=b.date, name=None, open=b.open, high=b.high, low=b.low,
                    close=b.close, volume=b.volume, turnover=b.turnover, source=b.source,
                    quality=b.quality,
                )
                for b in rows
            ]
            for c, rows in self._window("index", codes, upto, n).items()
        }  # fmt: skip

    def upsert_snapshots(
        self, rows: Sequence[Snap], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for s in rows:
            key = (s.code, s.date, s.source, s.venue)
            self.snaps[key] = Stored(s, received_at, loaded_by)
            self.snap_notes.pop(key, None)  # 새로 쓴 행은 대조 사유가 없다(Pg: extra 를 바꿔 쓴다)
        return len(rows)

    def snapshot(self, asof: date) -> tuple[dict[str, Snap], date | None]:
        days = _days_desc([k[1] for k in self.snaps], asof)
        if not days:
            return {}, None
        used = days[0]
        rows = [s.row for k, s in sorted(self.snaps.items()) if k[1] == used]
        best = pick_best(rows, lambda s: s.code, snap_rank)
        return dict(sorted(best.items())), used

    def snapshots(self, day: date, *, source: str | None = None) -> list[Snap]:
        return [
            s.row
            for k, s in sorted(self.snaps.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][3]))
            if k[1] == day and (source is None or k[2].lower() == source.lower())
        ]

    def snapshot_extra(self, day: date, code: str, source: str, venue: str) -> dict[str, Any]:
        """Pg 의 extra jsonb 와 같은 모양(시험용)."""
        key = (code, day, source, venue)
        return snap_extra(self.snaps[key].row, self.snap_notes.get(key))

    def set_snapshot_quality(
        self, day: date, code: str, *, source: str, venue: str, quality: Quality, note: str
    ) -> bool:
        key = (code, day, source, venue)
        st = self.snaps.get(key)
        if st is None:
            return False
        st.row = replace(st.row, quality=Quality(quality))
        self.snap_notes[key] = note
        return True

    def upsert_universe(
        self, rows: Sequence[UniverseRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for u in rows:
            self.universe_rows[(u.code, u.as_of, u.source)] = Stored(u, received_at, loaded_by)
        return len(rows)

    def universe(self, asof: date) -> list[UniverseRow]:
        latest: dict[str, date] = {}
        for _, d, src in self.universe_rows:
            if d <= asof and (src not in latest or d > latest[src]):
                latest[src] = d
        rows = [
            s.row for (_, d, src), s in sorted(self.universe_rows.items()) if latest.get(src) == d
        ]
        return best_universe(rows)

    def put_index_quotes(
        self, rows: Sequence[IndexQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for q in rows:
            self.index_rows[(q.code, q.ts, q.source)] = Stored(q, received_at, loaded_by)
        return len(rows)

    def index_quotes(self, start: datetime, end: datetime) -> list[IndexQuote]:
        require_aware("start", start)
        require_aware("end", end)
        keys = sorted(
            (k for k in self.index_rows if start <= k[1] < end), key=lambda k: (k[1], k[0], k[2])
        )
        return [self.index_rows[k].row for k in keys]

    def put_sector_quotes(
        self, rows: Sequence[SectorQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for q in rows:
            self.sector_rows[(q.market, q.code, q.ts, q.source)] = Stored(q, received_at, loaded_by)
        return len(rows)

    def sector_quotes(self, start: datetime, end: datetime) -> list[SectorQuote]:
        require_aware("start", start)
        require_aware("end", end)
        keys = sorted(
            (k for k in self.sector_rows if start <= k[2] < end),
            key=lambda k: (k[2], k[0], k[1], k[3]),
        )
        return [self.sector_rows[k].row for k in keys]

    def put_rank_rows(
        self, rows: Sequence[RankRow], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for q in rows:
            self.rank_rows_[(q.market, q.ts, q.rank, q.venue)] = Stored(q, received_at, loaded_by)
        return len(rows)

    def rank_rows(self, start: datetime, end: datetime) -> list[RankRow]:
        require_aware("start", start)
        require_aware("end", end)
        keys = sorted(
            (k for k in self.rank_rows_ if start <= k[1] < end),
            key=lambda k: (k[1], k[0], k[3], k[2]),
        )
        return [self.rank_rows_[k].row for k in keys]

    def put_reconcile(self, rows: Sequence[ReconcileRow]) -> int:
        for r in rows:
            self.reconcile_rows[(r.trade_date, r.code, r.field)] = r
        return len(rows)

    def reconcile(self, day: date) -> list[ReconcileRow]:
        return [r for k, r in sorted(self.reconcile_rows.items()) if k[0] == day]


# ── 수급 ────────────────────────────────────────────────────────────────────────────────


class MemoryFlowsRepo:
    def __init__(self) -> None:
        # (code, date, investor, source, venue) → 행
        self.stock: dict[tuple[str, date, str, str, str], Stored[InvestorDay]] = {}
        self.market: dict[tuple[str, date, str, str, str], Stored[InvestorDay]] = {}
        self.intraday_rows: dict[tuple[str, datetime, str, str], Stored[IntradayInvestor]] = {}
        self.revision_rows: dict[tuple[str, date, str, str], Revision] = {}
        self.check_rows: dict[tuple[str, date, str, str], LedgerCheck] = {}

    @staticmethod
    def _key(r: InvestorDay) -> tuple[str, date, str, str, str]:
        return (r.code, r.date, r.investor.value, r.source, r.venue)

    def upsert_investor_days(
        self,
        rows: Sequence[InvestorDay],
        *,
        revise: bool,
        loaded_by: str,
        now: datetime,
        received_at: datetime | None = None,
    ) -> RevisionReport:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        require_aware("now", now)
        if not rows:
            return RevisionReport(0, 0, ())
        codes = {r.code for r in rows}
        dates = {r.date for r in rows}
        existing = [
            StoredInvestorDay(s.row, s.received_at)
            for k, s in self.stock.items()
            if k[0] in codes and k[1] in dates
        ]
        plan = plan_investor_upsert(rows, existing, revise=revise, now=now)
        for d in plan.delete:
            self.stock.pop(self._key(d), None)
        for r in plan.write:
            self.stock[self._key(r)] = Stored(r, received_at, loaded_by)
        for v in plan.revisions:
            self.revision_rows[(v.code, v.trade_date, v.investor.value, v.venue)] = v
        return RevisionReport(len(plan.write), plan.skipped, plan.revisions)

    def _range(
        self,
        table: dict[tuple[str, date, str, str, str], Stored[InvestorDay]],
        codes: Collection[str] | None,
        start: date,
        end: date,
    ) -> list[InvestorDay]:
        wanted = None if codes is None else set(codes)
        return [
            s.row for k, s in sorted(table.items()) if start <= k[1] <= end and _in(k[0], wanted)
        ]

    def window(
        self, codes: Collection[str] | None, end: date, n: int
    ) -> dict[str, list[InvestorDay]]:
        start = window_start(_days_desc([k[1] for k in self.stock], end), n)
        if start is None:
            return {}
        return best_investor_days(self._range(self.stock, codes, start, end))

    def days(
        self, codes: Collection[str] | None, start: date, end: date
    ) -> dict[str, list[InvestorDay]]:
        return best_investor_days(self._range(self.stock, codes, start, end))

    def upsert_market_days(
        self, rows: Sequence[InvestorDay], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for r in rows:
            self.market[self._key(r)] = Stored(r, received_at, loaded_by)
        return len(rows)

    def market_days(self, end: date, n: int) -> dict[str, list[InvestorDay]]:
        start = window_start(_days_desc([k[1] for k in self.market], end), n)
        if start is None:
            return {}
        return best_investor_days(self._range(self.market, None, start, end))

    def put_intraday(
        self,
        rows: Sequence[IntradayInvestor],
        *,
        loaded_by: str,
        received_at: datetime | None = None,
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for q in rows:
            self.intraday_rows[(q.code, q.ts, q.investor.value, q.venue)] = Stored(
                q, received_at, loaded_by
            )
        return len(rows)

    def intraday(self, start: datetime, end: datetime) -> list[IntradayInvestor]:
        require_aware("start", start)
        require_aware("end", end)
        keys = sorted(
            (k for k in self.intraday_rows if start <= k[1] < end),
            key=lambda k: (k[1], k[0], k[2], k[3]),
        )
        return [self.intraday_rows[k].row for k in keys]

    def revisions(self, day: date) -> list[Revision]:
        return [v for k, v in sorted(self.revision_rows.items()) if k[1] == day]

    def put_checks(self, rows: Sequence[LedgerCheck]) -> int:
        for c in rows:
            self.check_rows[(c.domain, c.trade_date, c.code, c.check_id)] = c
        return len(rows)

    def checks(self, day: date, domain: str | None = None) -> list[LedgerCheck]:
        return [
            c
            for k, c in sorted(self.check_rows.items())
            if k[1] == day and (domain is None or k[0] == domain)
        ]


# ── 신고가 보드 ───────────────────────────────────────────────────────────────────────────


class MemoryBoardRepo:
    def __init__(self) -> None:
        self.alltime_rows: dict[str, Stored[AllTime]] = {}
        self.label_rows: dict[tuple[str, date, str], Label] = {}
        self.stock_day_rows: dict[tuple[str, date], StockDay] = {}
        self.artifact_rows: dict[tuple[date, str], BoardArtifact] = {}
        self.split_rows: dict[str, SplitCheck] = {}

    def alltime(self, codes: Collection[str] | None = None) -> dict[str, AllTime]:
        wanted = None if codes is None else set(codes)
        return {c: s.row for c, s in sorted(self.alltime_rows.items()) if _in(c, wanted)}

    def put_alltime(self, rows: Sequence[AllTime], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        for a in rows:
            self.alltime_rows[a.code] = Stored(a, now, loaded_by)
        return len(rows)

    def labels(self, asof: date, basis: str) -> dict[str, Label]:
        return {
            k[0]: v for k, v in sorted(self.label_rows.items()) if k[1] == asof and k[2] == basis
        }

    def put_day(self, rec: BoardDayRecord, *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        day = rec.trade_date
        self.label_rows = {k: v for k, v in self.label_rows.items() if k[1] != day}
        self.stock_day_rows = {k: v for k, v in self.stock_day_rows.items() if k[1] != day}
        self.artifact_rows = {k: v for k, v in self.artifact_rows.items() if k[0] != day}
        for lb in rec.labels:
            key = (lb.code, day, lb.basis)
            if key in self.label_rows:  # Pg 기본 키 위반과 같게
                raise ValueError(f"라벨 기본 키가 겹친다: {key}")
            self.label_rows[key] = lb
        for s in rec.stock_days:
            if (s.code, day) in self.stock_day_rows:
                raise ValueError(f"종목 하루 기본 키가 겹친다: {s.code}")
            self.stock_day_rows[(s.code, day)] = s
        for a in rec.artifacts:
            self.artifact_rows[(day, a.name)] = a
        return len(rec.labels) + len(rec.stock_days) + len(rec.artifacts)

    def stock_days(self, asof: date) -> dict[str, StockDay]:
        return {k[0]: v for k, v in sorted(self.stock_day_rows.items()) if k[1] == asof}

    def artifact(self, asof: date, name: str) -> BoardArtifact | None:
        return self.artifact_rows.get((asof, name))

    def last_day(self, upto: date) -> date | None:
        days = [d for d, _ in self.artifact_rows if d <= upto]
        return max(days) if days else None

    def split_cleared(self) -> set[str]:
        return {c for c, s in self.split_rows.items() if s.verdict == "none"}

    def split_unknown(self) -> list[tuple[str, str]]:
        return [(c, s.note) for c, s in sorted(self.split_rows.items()) if s.verdict == "unknown"]

    def put_split_check(self, rows: Sequence[SplitCheck], *, now: datetime) -> int:
        require_aware("now", now)
        for c in rows:
            self.split_rows[c.code] = c
        return len(rows)


# ── ETF ─────────────────────────────────────────────────────────────────────────────────


class MemoryEtfRepo:
    def __init__(self) -> None:
        self.days_: dict[tuple[str, date, str, str], Stored[EtfDay]] = {}
        self.quote_rows: dict[tuple[str, datetime, str], Stored[EtfQuote]] = {}
        self.meta_rows: dict[str, EtfMeta] = {}
        self.split_rows: dict[tuple[str, date], SplitEvent] = {}
        self.fund_rows: dict[str, Fund] = {}
        self.holding_rows: dict[tuple[str, date], dict[str, HoldingRow]] = {}
        self.change_rows: dict[date, list[Change]] = {}
        self.change_versions: dict[date, str] = {}

    def upsert_etf_days(
        self, rows: Sequence[EtfDay], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for d in rows:
            self.days_[(d.code, d.date, d.source, d.venue)] = Stored(d, received_at, loaded_by)
        return len(rows)

    def etf_days(
        self, end: date, n: int, codes: Collection[str] | None = None
    ) -> dict[str, list[EtfDay]]:
        start = window_start(_days_desc([k[1] for k in self.days_], end), n)
        if start is None:
            return {}
        wanted = None if codes is None else set(codes)
        return best_etf_days(
            s.row
            for k, s in sorted(self.days_.items())
            if start <= k[1] <= end and _in(k[0], wanted)
        )

    def put_quotes(
        self, rows: Sequence[EtfQuote], *, loaded_by: str, received_at: datetime | None = None
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        for q in rows:
            self.quote_rows[(q.code, q.ts, q.source)] = Stored(q, received_at, loaded_by)
        return len(rows)

    def quotes(self, start: datetime, end: datetime) -> list[EtfQuote]:
        require_aware("start", start)
        require_aware("end", end)
        keys = sorted(
            (k for k in self.quote_rows if start <= k[1] < end), key=lambda k: (k[1], k[0], k[2])
        )
        return [self.quote_rows[k].row for k in keys]

    def upsert_meta(self, rows: Sequence[EtfMeta], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        for m in rows:
            self.meta_rows[m.code] = m
        return len(rows)

    def meta(self) -> dict[str, EtfMeta]:
        return dict(sorted(self.meta_rows.items()))

    def put_split_event(self, ev: SplitEvent, *, loaded_by: str, now: datetime) -> bool:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        key = (ev.code, ev.effective_date)
        old = self.split_rows.get(key)
        if old is not None and old.origin == "manual" and ev.origin != "manual":
            return False
        self.split_rows[key] = ev
        return True

    def split_events(self) -> dict[str, list[SplitEvent]]:
        out: dict[str, list[SplitEvent]] = {}
        for (code, _), ev in sorted(self.split_rows.items()):
            out.setdefault(code, []).append(ev)
        return out

    def upsert_funds(self, rows: Sequence[Fund], *, loaded_by: str, now: datetime) -> int:
        require_loaded_by(loaded_by)
        require_aware("now", now)
        for f in rows:
            self.fund_rows[f.fund_id] = f
        return len(rows)

    def funds(self) -> dict[str, Fund]:
        return dict(sorted(self.fund_rows.items()))

    def put_holdings(
        self,
        fund_id: str,
        asof: date,
        rows: Sequence[HoldingRow],
        *,
        loaded_by: str,
        received_at: datetime | None = None,
    ) -> int:
        require_loaded_by(loaded_by)
        require_aware("received_at", received_at)
        check_holdings(rows)
        if rows:
            self.holding_rows[(fund_id, asof)] = {h.code: h for h in rows}
        else:
            self.holding_rows.pop((fund_id, asof), None)
        return len(rows)

    def holdings(self, fund_id: str, asof: date) -> dict[str, HoldingRow]:
        return dict(sorted(self.holding_rows.get((fund_id, asof), {}).items()))

    def holding_dates(self) -> dict[str, list[date]]:
        out: dict[str, list[date]] = {}
        for fid, asof in sorted(self.holding_rows):
            out.setdefault(fid, []).append(asof)
        return out

    def put_changes(
        self, run_date: date, rows: Sequence[Change], *, engine_version: str, now: datetime
    ) -> int:
        require_aware("now", now)
        check_changes(run_date, rows, engine_version)
        keys = [(c.fund_id, c.code, c.kind) for c in rows]
        if len(set(keys)) != len(keys):
            raise ValueError("변동 기본 키가 겹친다")
        if rows:
            self.change_rows[run_date] = list(rows)
            self.change_versions[run_date] = engine_version
        else:
            self.change_rows.pop(run_date, None)
            self.change_versions.pop(run_date, None)
        return len(rows)

    def changes(self, run_date: date) -> list[Change]:
        return sorted(self.change_rows.get(run_date, []), key=lambda c: (c.fund_id, c.kind, c.code))

    def last_change_run(self, upto: date) -> date | None:
        days = [d for d in self.change_rows if d <= upto]
        return max(days) if days else None


@dataclass
class MemoryRepos:
    """메모리 저장소 묶음 — `kbj.store.repos.Repos` 와 같은 모양(시험이 내부 표를 들여다볼 수 있게
    구체 형으로 둔다)."""

    market: MemoryMarketRepo = field(default_factory=MemoryMarketRepo)
    flows: MemoryFlowsRepo = field(default_factory=MemoryFlowsRepo)
    board: MemoryBoardRepo = field(default_factory=MemoryBoardRepo)
    etf: MemoryEtfRepo = field(default_factory=MemoryEtfRepo)


def memory_repos() -> MemoryRepos:
    return MemoryRepos()

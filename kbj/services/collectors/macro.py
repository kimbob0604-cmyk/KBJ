"""공개 매크로 수집 — `macro.morning`(06:20)·`macro.monthly`(09:10)·`macro.evening`(17:00) →
`pub_macro`.

근거: docs/p5_design.md §1.2·§3.1·§3.2·§4.1, D-P5-5~8, metrics §16. 다시 만든 원본 기능은 bok 8탭·
아침 매크로(코드 없음 — docs/inventory.md 기능 기준, U3)와 SD `api_macro`·`api_global_macro` 의 공개
몫.

경계(D-P5-7 — 계약 ⑭ 예정): **공개 작업**이라 로그인 원천(`kbj.data.private`)·로그인
저장소(`macro_prv`)·
P3 도우미(`_p3` — KIS·KRX 를 끌어온다)를 import 하지 않는다. 저장소는 `kbj.store.repos.public` 만.
로그인 수집(`kbj.services.collectors.macro_prv`)이 이 모듈의 도우미(`run_keys`·`stamp`·`meta_of`
…)를
가져다 쓴다(로그인 → 공개 방향만).

시리즈 목록은 `config/macro.yaml`(tier public) 한 곳. `item` 이 null(실측 전 #43~#45)인 시리즈는
부르지
않는다 — 그 데이터셋의 시리즈가 모두 실측 전이면 키는 `skipped`(받은 척하지 않는다). 데이터 키마다
따로
받는다(한 키의 실패가 다른 키를 멈추지 않는다 — 절대 규칙 4):

| 결과 | 뜻 | 작업 상태 |
|---|---|---|
| collected | 받아 썼다 | 다 이러면 ok |
| not_ready | 공표 전(그 기간 값이 아직 없음) — 받은 만큼은 썼다 | not_ready(재시도) |
| skipped | 부를 시리즈가 없다(실측 전) | 실패·공표 전이 없으면 skipped |
| failed | 출처 오류(사유 — 키는 가렸다) | failed(재시도) |

- 값이 없는 칸은 None + invalid(0 금지). 행 `as_of` = 받은 시각, `released_at` = 처음 본 시각(같은
  값이 이미 있으면 그때 시각을 지킨다 — 출처가 공표 시각을 주지 않아서 [제안]).
- 캘린더(D-P5-8): FRED 발표일 + 수기 공식 일정 → 공개 행. 발표 결과는 같은 시리즈의 공표 값만
  (`kbj.engines.macro.calendar.attach_actuals` — 지어내지 않는다). 예상치는 여기 없다(로그인 작업).
- 자원: `macro_config`·`public_repos`(없으면 설정·`connect` 로), `fred`·`treasury`·`nyfed`·`ecos`·
  `kosis`(없으면 `from_settings(settings, redis)`).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from kbj.config.macro import MacroConfig, SeriesCfg, load_macro
from kbj.core.p5_rows import CalendarEvent, MacroSeriesMeta, MacroSeriesRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.http import FetchError
from kbj.data.public.ecos.client import EcosClient
from kbj.data.public.fred.client import FredClient
from kbj.data.public.kosis.client import KosisClient
from kbj.data.public.nyfed.client import NyfedClient
from kbj.data.public.treasury.client import TreasuryClient
from kbj.data.spec import DataKey
from kbj.engines.macro.calendar import attach_actuals, fred_release_events, manual_events
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = [
    "KeyRun",
    "NotConfigured",
    "NotReady",
    "evening",
    "meta_of",
    "monthly",
    "morning",
    "period_of",
    "run_keys",
    "source_period",
    "stamp",
]

LOOKBACK_DAYS: Final[Mapping[str, int]] = {
    "D": 45,
    "W": 120,
    "M": 3 * 366,
    "Q": 5 * 366,
    "A": 10 * 366,
}
REASON_MAX: Final = 200
ECOS_CYCLE: Final[Mapping[str, str]] = {"D": "D", "M": "M", "Q": "Q", "A": "A"}
KOSIS_PRD: Final[Mapping[str, str]] = {"M": "M", "Q": "Q", "A": "Y"}
KOSIS_ORG: Final = "101"  # 통계청(kbj/data/public/kosis/datasets.py — orgId 는 모두 101)


class NotReady(Exception):
    """공표 전 — 그 기간 값이 아직 없다(받은 만큼은 썼다). 다음 실행이 다시."""


class NotConfigured(Exception):
    """부를 시리즈가 없다(설정 item 이 null — 실측 전). 키는 skipped."""


class MacroRepoLike(Protocol):
    def upsert_meta(
        self, rows: Sequence[MacroSeriesMeta], *, loaded_by: str, now: datetime
    ) -> int: ...

    def upsert_series(
        self, rows: Sequence[MacroSeriesRow], *, loaded_by: str, received_at: datetime
    ) -> int: ...

    def series(
        self, series_ids: Collection[str], start: str | None = None, end: str | None = None
    ) -> dict[str, list[MacroSeriesRow]]: ...


# ── 공용 도우미(로그인 수집도 쓴다) ──────────────────────────────────────────────────────────


def period_of(freq: str, d: date) -> str:
    """관측 날짜 → 저장 기간 문자열(D `YYYY-MM-DD`·M `YYYY-MM`·Q `YYYY-Qn`·A `YYYY`, W 는 날짜)."""
    if freq in ("D", "W"):
        return d.isoformat()
    if freq == "M":
        return f"{d.year:04d}-{d.month:02d}"
    if freq == "Q":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    if freq == "A":
        return f"{d.year:04d}"
    raise ValueError(f"모르는 주기: {freq!r}")


def source_period(freq: str, raw: str) -> str:
    """ECOS `TIME`·KOSIS `PRD_DE`(YYYYMMDD·YYYYMM·YYYYQn·YYYY·YYYY0n 분기) → 저장 기간 문자열."""
    s = raw.strip()
    if freq in ("D", "W") and len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if freq == "M" and len(s) == 6:
        return f"{s[:4]}-{s[4:]}"
    if freq == "Q":
        if len(s) == 6 and s[4] == "Q":
            return f"{s[:4]}-Q{s[5]}"
        if len(s) == 6 and s[4] == "0" and s[5] in "1234":  # KOSIS 분기 'YYYY0n' [실측 필요 #44]
            return f"{s[:4]}-Q{s[5]}"
    if freq == "A" and len(s) == 4:
        return s
    raise ValueError(f"기간 형식이 주기 {freq} 와 맞지 않는다: {s[:12]!r}")


def as_of_period(kind_value: str) -> str:
    """등록부 as_of(월 `YYYY-MM`·분기 `YYYYQn`·날짜) → 저장 기간 문자열."""
    if len(kind_value) == 6 and kind_value[4] == "Q":
        return f"{kind_value[:4]}-Q{kind_value[5]}"
    return kind_value


def meta_of(s: SeriesCfg, tab_of: Mapping[str, str] | None = None) -> MacroSeriesMeta:
    return MacroSeriesMeta(
        s.id, (tab_of or {}).get(s.id, s.tab), s.title, s.unit, s.freq, s.tier, s.source, s.dataset
    )


def stamp(
    rows: Sequence[MacroSeriesRow],
    existing: Mapping[str, Sequence[MacroSeriesRow]],
    now: datetime,
) -> list[MacroSeriesRow]:
    """released_at = 처음 본 시각. 이미 같은 값이 있으면 그때 시각을 지킨다(값이 바뀌면 지금)."""
    old = {(r.series_id, r.period): r for rs in existing.values() for r in rs}
    out: list[MacroSeriesRow] = []
    for r in rows:
        prev = old.get((r.series_id, r.period))
        if r.released_at is not None:
            out.append(r)
        elif prev is not None and prev.value == r.value and prev.released_at is not None:
            out.append(replace(r, released_at=prev.released_at))
        else:
            out.append(replace(r, released_at=now if r.value is not None else None))
    return out


def write_series(
    repo: MacroRepoLike,
    series: Sequence[SeriesCfg],
    rows: Sequence[MacroSeriesRow],
    *,
    job: str,
    now: datetime,
) -> int:
    """메타 + 관측값(처음 본 시각 붙임). 쓴 관측 행 수."""
    repo.upsert_meta([meta_of(s) for s in series], loaded_by=job, now=now)
    if not rows:
        return 0
    ids = sorted({r.series_id for r in rows})
    start = min(r.period for r in rows)
    stamped = stamp(rows, repo.series(ids, start), now)
    return repo.upsert_series(stamped, loaded_by=job, received_at=now)


def require_periods(series: Sequence[SeriesCfg], rows: Sequence[MacroSeriesRow], want: str) -> None:
    """시리즈마다 `want` 기간(이후) 값이 있어야 — 없으면 NotReady(공표 전)."""
    have: dict[str, set[str]] = {}
    for r in rows:
        if r.value is not None:
            have.setdefault(r.series_id, set()).add(r.period)
    late = [s.id for s in series if not any(p >= want for p in have.get(s.id, set()))]
    if late:
        raise NotReady(f"{want} 공표 전: {', '.join(late[:4])}")


def ready_series(cfg: MacroConfig, dataset: str, tier: str = "public") -> list[SeriesCfg]:
    """그 데이터셋의 그 등급 시리즈 중 항목 코드가 확인된 것. 없으면 NotConfigured(실측 번호)."""
    all_ = [s for s in cfg.series if s.dataset == dataset and s.tier == tier]
    ready = [s for s in all_ if s.ready]
    if not ready:
        probes = sorted({s.probe for s in all_ if s.probe}) or ["설정에 시리즈 없음"]
        raise NotConfigured(f"{dataset}: 항목 코드 실측 전({', '.join(probes)})")
    return ready


@dataclass
class KeyRun:
    """데이터 키별 결과 모음."""

    collected: list[DataKey] = field(default_factory=list[DataKey])
    rows: int = 0
    not_ready: dict[str, str] = field(default_factory=dict[str, str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])
    failed: dict[str, str] = field(default_factory=dict[str, str])

    def result(self, extra: Mapping[str, Any] | None = None) -> JobResult:
        detail: dict[str, Any] = {**(extra or {})}
        for name, d in (
            ("not_ready", self.not_ready),
            ("skipped", self.skipped),
            ("failed", self.failed),
        ):
            if d:
                detail[name] = dict(list(d.items())[:8])
        if self.failed:
            status = "failed"
            detail["reason"] = (
                f"키 {len(self.failed)}개 실패: "
                + "; ".join(f"{k} {v}" for k, v in list(self.failed.items())[:3])[:REASON_MAX]
            )
        elif self.not_ready:
            status = "not_ready"
            detail["reason"] = "공표 전: " + ", ".join(list(self.not_ready)[:4])
        elif self.skipped:
            status = "skipped"
        else:
            status = "ok"
        return JobResult(status, tuple(self.collected), self.rows, detail)  # pyright: ignore[reportArgumentType]


def run_keys(
    ctx: JobContext,
    handlers: Mapping[str, Callable[[DataKey], int]],
    extra: Mapping[str, Any] | None = None,
) -> JobResult:
    """키마다 처리기 하나(`데이터셋 id → fn(key) → 쓴 행 수`). 모르는 데이터셋 키는 실패로."""
    run = KeyRun()
    for key in ctx.keys:
        label = key.label()
        fn = handlers.get(key.dataset_id)
        if fn is None:
            run.failed[label] = "이 작업이 모르는 데이터셋"
            continue
        try:
            run.rows += fn(key)
        except NotReady as e:
            run.not_ready[label] = str(e)[:REASON_MAX]
        except NotConfigured as e:
            run.skipped[label] = str(e)[:REASON_MAX]
        except FetchError as e:
            run.failed[label] = e.reason[:REASON_MAX]
        else:
            run.collected.append(key)
    return run.result(extra)


def config(ctx: JobContext) -> MacroConfig:
    return ctx.resources.get("macro_config") or load_macro(settings=ctx.settings)


def client[T](ctx: JobContext, name: str, make: Callable[[], T]) -> T:
    got = ctx.resources.get(name)
    return got if got is not None else make()


def _settings(ctx: JobContext) -> Any:
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(출처 키)")
    return ctx.settings


def _repos(ctx: JobContext) -> PublicRepos:
    return ctx.resources.get("public_repos") or pg_public_repos(ctx.resource("connect"))


def _row(
    s: SeriesCfg,
    period: str,
    value: Any,
    quality: Quality,
    now: datetime,
    source: str | None = None,
) -> MacroSeriesRow:
    q = quality if value is not None else Quality.INVALID
    return MacroSeriesRow(s.id, period, value, s.unit, source or s.source, now, q)


def _day(key: DataKey) -> date:
    return date.fromisoformat(key.as_of[:10])


# ── macro.morning — FRED 정부·발표일·재무부·뉴욕연은 ───────────────────────────────────────────


def _fred_gov(ctx: JobContext, cfg: MacroConfig, repos: PublicRepos, key: DataKey) -> int:
    series = ready_series(cfg, "FRED:gov_series")
    gov = [s.item for s in series if s.item]
    fred = client(
        ctx,
        "fred",
        lambda: FredClient.from_settings(_settings(ctx), ctx.resource("redis"), gov_series=gov),
    )
    day = _day(key)
    rows: list[MacroSeriesRow] = []
    for s in series:
        start = day - timedelta(days=LOOKBACK_DAYS[s.freq])
        for o in fred.observations(s.item or "", start, day):
            rows.append(_row(s, period_of(s.freq, o.date), o.value, o.quality, ctx.now))
    n = write_series(repos.macro, series, rows, job=ctx.job, now=ctx.now)
    require_periods([s for s in series if s.freq == "D"], rows, day.isoformat())
    return n


def calendar_window(cfg: MacroConfig, day: date) -> tuple[date, date]:
    w = cfg.calendar.window_days
    return day - timedelta(days=w.past), day + timedelta(days=w.future)


def _fred_releases(ctx: JobContext, cfg: MacroConfig, repos: PublicRepos, key: DataKey) -> int:
    gov = [s.item for s in cfg.series if s.dataset == "FRED:gov_series" and s.item]
    fred = client(
        ctx,
        "fred",
        lambda: FredClient.from_settings(_settings(ctx), ctx.resource("redis"), gov_series=gov),
    )
    start, end = calendar_window(cfg, _day(key))
    rel = fred.release_dates(start, end)
    events: list[CalendarEvent] = fred_release_events((r.name, r.date) for r in rel)
    events += [e for e in manual_events(cfg.calendar.manual) if start <= e.date <= end]
    ids = sorted({e.actual_series for e in events if e.actual_series})
    stored = repos.macro.series(ids) if ids else {}
    events = attach_actuals(events, stored, ctx.now)
    return (
        repos.macro.upsert_calendar(events, loaded_by=ctx.job, received_at=ctx.now) if events else 0
    )


def _treasury(ctx: JobContext, cfg: MacroConfig, repos: PublicRepos, key: DataKey) -> int:
    series = ready_series(cfg, "TREASURY:yield_curve")
    tr = client(
        ctx, "treasury", lambda: TreasuryClient.from_settings(_settings(ctx), ctx.resource("redis"))
    )
    day = _day(key)
    years = sorted({day.year, (day - timedelta(days=LOOKBACK_DAYS["D"])).year})
    pts = [p for y in years for p in tr.yield_curve(y)]
    rows = [
        _row(s, period_of(s.freq, p.date), p.value, p.quality, ctx.now)
        for s in series
        for p in pts
        if p.tenor == s.item and p.date <= day and p.date > day - timedelta(days=LOOKBACK_DAYS["D"])
    ]
    n = write_series(repos.macro, series, rows, job=ctx.job, now=ctx.now)
    require_periods(series, rows, day.isoformat())
    return n


def _nyfed(ctx: JobContext, cfg: MacroConfig, repos: PublicRepos, key: DataKey) -> int:
    series = ready_series(cfg, "NYFED:rates")
    ny = client(
        ctx, "nyfed", lambda: NyfedClient.from_settings(_settings(ctx), ctx.resource("redis"))
    )
    day = _day(key)
    rows: list[MacroSeriesRow] = []
    for s in series:
        for p in ny.rates(s.item or "", day - timedelta(days=LOOKBACK_DAYS["D"]), day):
            rows.append(_row(s, period_of(s.freq, p.date), p.value, p.quality, ctx.now))
    n = write_series(repos.macro, series, rows, job=ctx.job, now=ctx.now)
    require_periods(series, rows, day.isoformat())
    return n


def morning(ctx: JobContext) -> JobResult:
    cfg = config(ctx)
    repos = _repos(ctx)
    return run_keys(
        ctx,
        {
            "FRED:gov_series": lambda k: _fred_gov(ctx, cfg, repos, k),
            "FRED:release_dates": lambda k: _fred_releases(ctx, cfg, repos, k),
            "TREASURY:yield_curve": lambda k: _treasury(ctx, cfg, repos, k),
            "NYFED:rates": lambda k: _nyfed(ctx, cfg, repos, k),
        },
    )


# ── macro.monthly·evening — ECOS 한은 작성 표·KOSIS ───────────────────────────────────────────


def _ecos_range(freq: str, end: date) -> tuple[str, str]:
    start = end - timedelta(days=LOOKBACK_DAYS[freq])
    if freq == "D":
        return f"{start:%Y%m%d}", f"{end:%Y%m%d}"
    if freq == "M":
        return f"{start:%Y%m}", f"{end:%Y%m}"
    if freq == "Q":
        return (
            f"{start.year}Q{(start.month - 1) // 3 + 1}",
            f"{end.year}Q{(end.month - 1) // 3 + 1}",
        )
    return f"{start.year}", f"{end.year}"


def ecos_rows(
    fetch: Callable[[str, str, str, str, tuple[str, ...]], Sequence[Any]],
    series: Sequence[SeriesCfg],
    end: date,
    now: datetime,
) -> list[MacroSeriesRow]:
    """ECOS 시리즈(item = 항목 코드, 여러 단계는 '/' 로 잇는다 [실측 필요 #43]) → 행."""
    rows: list[MacroSeriesRow] = []
    for s in series:
        stat = s.dataset.split(":", 1)[1]
        a, b = _ecos_range(s.freq, end)
        items = tuple((s.item or "").split("/"))
        for r in fetch(stat, ECOS_CYCLE[s.freq], a, b, items):
            rows.append(_row(s, source_period(s.freq, r.time), r.value, r.quality, now))
    return rows


def _ecos(ctx: JobContext, cfg: MacroConfig, repos: PublicRepos, key: DataKey) -> int:
    series = ready_series(cfg, key.dataset_id)
    ecos = client(
        ctx, "ecos", lambda: EcosClient.from_settings(_settings(ctx), ctx.resource("redis"))
    )
    want = as_of_period(key.as_of)
    end = ctx.now.astimezone(KST).date()
    rows = ecos_rows(ecos.search, series, end, ctx.now)
    n = write_series(repos.macro, series, rows, job=ctx.job, now=ctx.now)
    require_periods(series, rows, want)
    return n


def _kosis(ctx: JobContext, cfg: MacroConfig, repos: PublicRepos, key: DataKey) -> int:
    series = ready_series(cfg, key.dataset_id)
    kosis = client(
        ctx, "kosis", lambda: KosisClient.from_settings(_settings(ctx), ctx.resource("redis"))
    )
    want = as_of_period(key.as_of)
    end = ctx.now.astimezone(KST).date()
    rows: list[MacroSeriesRow] = []
    for s in series:
        tbl = s.dataset.split(":", 1)[1]
        obj_l1, _, itm = (s.item or "").partition("/")  # item = '<objL1>/<itmId>' [실측 필요 #44]
        if not itm:
            raise NotConfigured(f"{s.id}: KOSIS item 은 '<objL1>/<itmId>'")
        a, b = _ecos_range(s.freq, end)
        a, b = a.replace("Q", "0"), b.replace("Q", "0")  # KOSIS 분기 'YYYY0n' [실측 필요 #44]
        for r in kosis.parameter_data(
            KOSIS_ORG, tbl, obj_l1, itm, KOSIS_PRD[s.freq], start=a, end=b
        ):
            rows.append(_row(s, source_period(s.freq, r.prd_de), r.value, r.quality, ctx.now))
    n = write_series(repos.macro, series, rows, job=ctx.job, now=ctx.now)
    require_periods(series, rows, want)
    return n


def _by_source(
    ctx: JobContext, cfg: MacroConfig, repos: PublicRepos
) -> dict[str, Callable[[DataKey], int]]:
    out: dict[str, Callable[[DataKey], int]] = {}
    for k in ctx.keys:
        if k.source == "ECOS":
            out[k.dataset_id] = lambda key: _ecos(ctx, cfg, repos, key)
        elif k.source == "KOSIS":
            out[k.dataset_id] = lambda key: _kosis(ctx, cfg, repos, key)
    return out


def monthly(ctx: JobContext) -> JobResult:
    cfg = config(ctx)
    repos = _repos(ctx)
    return run_keys(ctx, _by_source(ctx, cfg, repos))


def evening(ctx: JobContext) -> JobResult:
    cfg = config(ctx)
    repos = _repos(ctx)
    return run_keys(ctx, _by_source(ctx, cfg, repos))

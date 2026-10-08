"""작업 등록부 — `config/jobs.yaml` 해석·검증(설계 §6.2·§6.3·§6.5·§6.6).

PLAN §2 "스케줄은 등록부 하나".

- 모양: `version: 1` · `defaults`(tz·retry·deadline_min·enabled) · `services`(상시 프로세스 — 작업이
  아님) · `jobs` · `retired`(폐지하는 legacy 정기 작업과 사유). 모르는 키는 오류(`extra="forbid"`).
- `defaults` 는 해석할 때 각 작업에 채운다(작업이 `retry` 를 주면 통째로 바꾼다).
- 시각 트리거는 하나만: `cron`(+`tz`) · `state_enter`(세션 상태 진입) · `equity`(주식 정규장 기준 —
  지연 개장 반영, `TradingCalendar.equity_bounds`) · `manual`(run-once 로만).
- `Registry.validate(catalog, …)` 는 오류 문장 목록을 돌려준다(빈 목록 = 통과). §6.6 의 1~9 를 본다.
  10(P2 에 켜진 작업 목록)은 단계마다 바뀌므로 시험(`test_registry_validation.py`)이 고정한다.
- 같은 `(source, dataset)` 은 등록부 전체(작업 + 외부 서비스)에서 한 곳만 `collects` 에 둔다.
  거래소 구분(venue)이 있어도 데이터셋 단위로 한 곳이다. `market.backfill` 같은 백필 작업은
  `backfill_of` 로 원래 작업의 데이터 키를 같은 선점 규칙으로 쓴다(정적 겹침 아님).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.core.calendar import State
from kbj.core.cron import CronSpec
from kbj.data.catalog import claimable
from kbj.data.limits import REQUIRED_SOURCES, Limits
from kbj.data.spec import AsOfKind, DatasetSpec, Tier, Venue
from kbj.services.scheduler.conditions import DATE_KINDS, When, parse_anchor
from kbj.services.scheduler.handlers import HandlerMissing, parse_owner, resolve

__all__ = [
    "CollectSpec",
    "DependsSpec",
    "EquitySpec",
    "JobSpec",
    "NotifySpec",
    "Registry",
    "RegistryError",
    "RetiredSpec",
    "RetrySpec",
    "ScheduleSpec",
    "ServiceSpec",
    "migration_tables",
]

_NAME: Final = re.compile(
    r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$"
)  # 설계 §6.3 + 영역 이름의 _ (market_stats)
_SERVICE_NAME: Final = re.compile(r"^[a-z]+(\.[a-z0-9_]+)*$")
_PHASE: Final = re.compile(r"^P[2-9]$")
_HHMM: Final = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_TABLE: Final = re.compile(r"^(?P<schema>(?:pub|prv)_[a-z][a-z0-9_]*|ops)\.[a-z][a-z0-9_]*$")
_DDL: Final = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|(?:MATERIALIZED\s+)?VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
_SAMPLE_DAY: Final = date(2026, 10, 7)  # 하루 호출 추정에 쓰는 평일(수)
U2_KINDS: Final = ("brief.morning", "brief.closing")
DAILY_DEDUP: Final = "daily"
MIGRATIONS_DIR: Final = Path(__file__).resolve().parents[2] / "store" / "migrations"


BudgetCap = tuple[int | None, int | None]  # (하루 상한, 데이터셋별 하루 상한)


class RegistryError(ValueError):
    """등록부 파일을 읽지 못했다(형식 오류). 내용 검증 오류는 `Registry.validate` 가 목록으로."""


def _hhmm(v: str | None) -> str | None:
    if v is not None and not _HHMM.fullmatch(v):
        raise ValueError(f"시각은 'HH:MM'(24시간): {v!r}")
    return v


def hhmm_time(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrySpec(_Model):
    """재시도. `max`+`backoff_s`(n 번째 실패 뒤 backoff_s[n-1], 모자라면 마지막 값) 또는
    `every_s`+`until`(그 지역 시각 until 까지 every_s 마다)."""

    max: int = Field(default=0, ge=0, le=50)
    backoff_s: tuple[int, ...] = ()
    every_s: int | None = Field(default=None, gt=0)
    until: str | None = None

    _until = field_validator("until")(_hhmm)

    @model_validator(mode="after")
    def _shape(self) -> RetrySpec:
        if any(b <= 0 for b in self.backoff_s):
            raise ValueError("backoff_s 는 양수")
        if self.every_s is not None or self.until is not None:
            if self.every_s is None or self.until is None:
                raise ValueError("every_s 와 until 은 함께 준다")
            if self.max or self.backoff_s:
                raise ValueError("every_s·until 과 max·backoff_s 는 함께 쓰지 않는다")
        elif self.max and not self.backoff_s:
            raise ValueError("max > 0 이면 backoff_s 가 있어야 한다")
        return self

    def delay_s(self, failed_attempts: int) -> int:
        """`failed_attempts` 번 실패한 뒤 기다릴 초."""
        if self.every_s is not None:
            return self.every_s
        i = min(max(failed_attempts, 1), len(self.backoff_s)) - 1
        return self.backoff_s[i]

    def allows(self, failed_attempts: int, next_local: datetime) -> bool:
        """다음 시도를 해도 되는가(`next_local` 은 작업 시각대 벽시계)."""
        if self.until is not None:
            return next_local.time() <= hhmm_time(self.until)
        return failed_attempts <= self.max

    def attempts_from(self, first: time) -> int:
        """하루 최대 시도 수(첫 시도 포함) — 호출 추정용."""
        if self.every_s is not None and self.until is not None:
            span = (
                datetime.combine(_SAMPLE_DAY, hhmm_time(self.until))
                - datetime.combine(_SAMPLE_DAY, first)
            ).total_seconds()
            return int(span // self.every_s) + 1 if span >= 0 else 1
        return 1 + self.max


class EquitySpec(_Model):
    """주식 정규장 기준 시각(지연 개장 반영). `start` 한 번, 또는 `start`~`end` 를 `every_min` 마다
    (끝 포함). 값은 `open`·`close`·`open+30`·`close-10`."""

    start: str
    end: str | None = None
    every_min: int | None = Field(default=None, gt=0, le=240)

    @model_validator(mode="after")
    def _shape(self) -> EquitySpec:
        parse_anchor(self.start)
        if (self.end is None) != (self.every_min is None):
            raise ValueError("equity: end 와 every_min 은 함께 준다")
        if self.end is not None:
            parse_anchor(self.end)
        return self


class ScheduleSpec(_Model):
    cron: str | None = None
    tz: str = "Asia/Seoul"
    when: When = "always"
    month_days: tuple[int, ...] = ()
    state_enter: tuple[State, ...] = ()
    equity: EquitySpec | None = None
    manual: bool = False
    catch_up_until: str | None = None
    triggered_by: tuple[str, ...] = ()  # 이벤트로도 돈다(예: 공시 접수) — 연결은 그 작업의 단계에서

    _catch = field_validator("catch_up_until")(_hhmm)

    @model_validator(mode="after")
    def _shape(self) -> ScheduleSpec:
        triggers = [
            self.cron is not None,
            bool(self.state_enter),
            self.equity is not None,
            self.manual,
        ]
        if sum(triggers) != 1:
            raise ValueError("트리거는 cron·state_enter·equity·manual 중 하나만")
        if self.cron is not None:
            CronSpec.parse(self.cron)
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"tz 가 IANA 이름이 아니다: {self.tz!r}") from None
        if self.when == "month_days":
            if not self.month_days or any(not 1 <= d <= 31 for d in self.month_days):
                raise ValueError("when: month_days 는 1~31 날짜 목록이 있어야 한다")
        elif self.month_days:
            raise ValueError("month_days 는 when: month_days 와 함께만")
        if self.catch_up_until is not None and self.cron is None:
            raise ValueError("catch_up_until 은 cron 작업에만")
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def cron_spec(self) -> CronSpec | None:
        return None if self.cron is None else CronSpec.parse(self.cron)

    def first_time(self) -> time | None:
        """하루 첫 실행 벽시계 시각(평일 기준 — 재시도 until 검사·호출 추정용)."""
        spec = self.cron_spec()
        if spec is not None:
            for d in range(7):
                times = spec.times_on(_SAMPLE_DAY + timedelta(days=d))
                if times:
                    return times[0]
            return None
        if self.equity is not None:
            base, minutes = parse_anchor(self.equity.start)
            start = datetime.combine(_SAMPLE_DAY, time(9, 0) if base == "open" else time(15, 30))
            return (start + timedelta(minutes=minutes)).time()
        return None

    def fires_per_day(self) -> int:
        """평일 하루 실행 수 추정(호출 예산 검사용). 수동은 0, 상태 진입은 상태 수."""
        spec = self.cron_spec()
        if spec is not None:
            return max(len(spec.times_on(_SAMPLE_DAY + timedelta(days=d))) for d in range(7))
        if self.equity is not None:
            if self.equity.end is None or self.equity.every_min is None:
                return 1
            a, b = parse_anchor(self.equity.start), parse_anchor(self.equity.end)
            base = {"open": 0, "close": 390}  # 평소 09:00~15:30 = 390분
            span = (base[b[0]] + b[1]) - (base[a[0]] + a[1])
            return max(span // self.equity.every_min + 1, 0)
        return len(self.state_enter)


class CollectSpec(_Model):
    """수집 데이터 키 규칙 `{source, dataset, as_of}` — 카탈로그 논리 데이터셋."""

    source: str
    dataset: str
    as_of: AsOfKind
    venues: tuple[Venue, ...] = ()  # 비우면 데이터셋의 거래소 전부

    @property
    def dataset_id(self) -> str:
        return f"{self.source}:{self.dataset}"


class DependsSpec(_Model):
    """의존. 굳은 의존(`hard`)은 그 작업의 같은(또는 앞 — `as_of: prev`) as_of 실행이 `ok` 일 때까지
    기다리고, 그것이 실패로 끝나면 이 작업도 실패한다. 무른 의존은 기다리지 않고 상태만 기록한다.

    `wait_min`(무른 의존만 — ADR 0018): 굳은 의존이 다 준비된 때부터 최대 이 분 동안, 그 의존의 같은
    as_of 실행이 실행기에서 **진행 중**(발화해 제 의존을 기다리거나·돌거나·재시도를 기다리는 중)이면
    끝나기를 기다린다. 끝나면 결과(ok·failed·timeout·skipped)를 묻지 않고 시작하고, 시한이 지나면
    기다리지 않고 시작한다 — 무른 의존이라 그 실패가 이 작업을 실패시키지 않는다. 아직 발화하지 않은
    실행은 기다리지 않으므로 의존 작업이 이 작업보다 늦게 발화하면 안 된다(등록부 검증).
    """

    job: str
    as_of: Literal["same", "prev"] = "same"
    hard: bool = True
    wait_min: int | None = Field(default=None, gt=0, le=240)

    @model_validator(mode="after")
    def _shape(self) -> DependsSpec:
        if self.wait_min is not None and self.hard:
            raise ValueError("wait_min 은 무른 의존(hard: false)에만 — 굳은 의존은 이미 기다린다")
        if self.wait_min is not None and self.as_of != "same":
            raise ValueError("wait_min 은 as_of same 의존에만")
        return self


class NotifySpec(_Model):
    kind: str
    when: Literal["always", "changed", "failed"] = "always"


class _Owned(_Model):
    owner: str
    runner: Literal["kbj", "external"] = "kbj"
    phase: str
    collects: tuple[CollectSpec, ...] = ()
    absorbs: tuple[str, ...] = ()
    note: str = ""

    @field_validator("phase")
    @classmethod
    def _phase(cls, v: str) -> str:
        if not _PHASE.fullmatch(v):
            raise ValueError(f"phase 는 P2~P9: {v!r}")
        return v


class ServiceSpec(_Owned):
    """상시 프로세스 — 하트비트로만 감시한다. 외부(legacy GX) 서비스는 받는 데이터 키만 등록한다."""

    name: str
    heartbeat_s: int | None = Field(default=None, gt=0)
    enabled: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _SERVICE_NAME.fullmatch(v):
            raise ValueError(f"서비스 이름 형식: {v!r}")
        return v


class JobSpec(_Owned):
    name: str
    enabled: bool = False
    schedule: ScheduleSpec
    retry: RetrySpec = RetrySpec()
    deadline_min: int = Field(default=120, gt=0)
    depends_on: tuple[DependsSpec, ...] = ()
    budget: str | None = None
    writes: tuple[str, ...] = ()
    notify: NotifySpec | None = None
    as_of: AsOfKind | None = None  # 작업 실행의 as_of(비우면 collects·조건에서 정한다)
    backfill_of: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME.fullmatch(v):
            raise ValueError(f"작업 이름은 '영역.작업' 소문자: {v!r}")
        return v

    @property
    def external(self) -> bool:
        return self.runner == "external"

    def run_as_of(self) -> str:
        """실행 as_of 종류.

        명시 > collects 가 한 종류면 그것 > 거래일 작업이면 trade_date > run_date.
        """
        if self.as_of is not None:
            return self.as_of
        kinds = {c.as_of for c in self.collects}
        if len(kinds) == 1:
            return next(iter(kinds))
        if self.schedule.when == "trading_day":
            return "trade_date"
        return "run_date"


class RetiredSpec(_Model):
    legacy: str
    reason: str = Field(min_length=1)


class Defaults(_Model):
    tz: str = "Asia/Seoul"
    retry: RetrySpec = RetrySpec()
    deadline_min: int = Field(default=120, gt=0)
    enabled: bool = False


def _with_defaults(job: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(job)
    for key in ("retry", "deadline_min", "enabled"):
        if key not in out and key in defaults:
            out[key] = defaults[key]
    sched = out.get("schedule")
    if isinstance(sched, Mapping) and "tz" not in sched and "tz" in defaults:
        out["schedule"] = {**sched, "tz": defaults["tz"]}
    return out


def migration_tables(directory: Path = MIGRATIONS_DIR) -> frozenset[str]:
    """마이그레이션 SQL 이 만드는 표·뷰 이름(`스키마.표`)."""
    names: set[str] = set()
    for f in sorted(directory.glob("*.sql")):
        names.update(m["name"].lower() for m in _DDL.finditer(f.read_text(encoding="utf-8")))
    return frozenset(names)


class Registry(_Model):
    version: Literal[1]
    defaults: Defaults = Defaults()
    services: tuple[ServiceSpec, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    retired: tuple[RetiredSpec, ...] = ()

    # ── 읽기 ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Registry:
        """dict → 등록부(defaults 를 작업에 채운다). 형식 오류는 `RegistryError`."""
        raw = dict(data)
        defaults = raw.get("defaults") or {}
        if not isinstance(defaults, Mapping):
            raise RegistryError("defaults 는 mapping 이어야 한다")
        jobs = raw.get("jobs") or []
        if not isinstance(jobs, list):
            raise RegistryError("jobs 는 목록이어야 한다")
        raw["jobs"] = [
            _with_defaults(j, defaults) if isinstance(j, Mapping) else j
            for j in jobs  # pyright: ignore[reportUnknownVariableType]
        ]
        try:
            return cls.model_validate(raw)
        except ValidationError as e:
            lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
            raise RegistryError("등록부 형식 오류:\n  " + "\n  ".join(lines)) from None

    @classmethod
    def load(cls, path: Path) -> Registry:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise RegistryError(f"{path}: 읽지 못했다({type(e).__name__})") from None
        if not isinstance(data, Mapping):
            raise RegistryError(f"{path}: 최상위가 mapping 이 아니다")
        return cls.parse(data)  # pyright: ignore[reportUnknownArgumentType]

    def by_name(self, name: str) -> JobSpec:
        for j in self.jobs:
            if j.name == name:
                return j
        raise KeyError(f"등록부에 없는 작업: {name!r}")

    def enabled_jobs(self) -> list[JobSpec]:
        return [j for j in self.jobs if j.enabled]

    def owners_of(self) -> dict[str, list[str]]:
        """데이터셋 id → 그것을 collects 에 둔 작업·서비스 이름들."""
        out: dict[str, list[str]] = {}
        for owner in (*self.services, *self.jobs):
            for c in owner.collects:
                out.setdefault(c.dataset_id, []).append(owner.name)
        return out

    # ── 검증(§6.6) ───────────────────────────────────────────────────────────────────

    def validate(  # pyright: ignore[reportIncompatibleMethodOverride] — 설계 §1.7 API 이름
        self,
        catalog: Mapping[str, DatasetSpec],
        *,
        budgets: Mapping[str, BudgetCap] | None = None,
        notify_policies: Mapping[str, str] | None = None,
        tables: Iterable[str] | None = None,
        check_imports: bool = True,
    ) -> list[str]:
        """오류 문장 목록(빈 목록 = 통과).

        - `budgets`: 예산 이름 → (하루 상한, 데이터셋별 하루 상한) — 없으면 예산 검사를 건너뛴다
          (`budgets_from_limits`).
        - `notify_policies`: 알림 종류 → dedup 방식(notify.yaml) — 없으면 알림 검사를 건너뛴다.
        - `tables`: 마이그레이션이 만든 표 — 켜진 작업의 `writes` 만 본다(꺼진 작업의 표는 그
          단계에서 생긴다 — 묶음 D 요청).
        """
        errs: list[str] = []
        errs += self._check_names()
        errs += self._check_owners(check_imports)
        errs += self._check_depends()
        errs += self._check_collects(catalog)
        errs += self._check_writes(catalog, None if tables is None else frozenset(tables))
        errs += self._check_schedule()
        if notify_policies is not None:
            errs += self._check_notify(notify_policies)
        if budgets is not None:
            errs += self._check_budgets(catalog, budgets)
        return errs

    def _check_names(self) -> list[str]:
        names = [s.name for s in self.services] + [j.name for j in self.jobs]
        dup = sorted(n for n, c in Counter(names).items() if c > 1)
        return [f"이름이 겹친다: {n}" for n in dup]

    def _check_owners(self, check_imports: bool) -> list[str]:
        errs: list[str] = []
        for o in (*self.services, *self.jobs):
            if o.runner == "external":
                if not o.owner.startswith("legacy:"):
                    errs.append(f"{o.name}: runner external 이면 owner 는 'legacy:<경로>'")
                if o.enabled:
                    errs.append(
                        f"{o.name}: runner external 은 enabled: false 여야 한다"
                        "(kbj 가 돌리지 않는다)"
                    )
                continue
            if isinstance(o, ServiceSpec):
                if not re.fullmatch(r"kbj(\.[a-z_][a-z0-9_]*)+", o.owner):
                    errs.append(f"{o.name}: 서비스 owner 는 kbj 패키지 경로: {o.owner!r}")
                continue
            try:
                parse_owner(o.owner)
            except ValueError as e:
                errs.append(f"{o.name}: {e}")
                continue
            if o.enabled and check_imports:
                try:
                    resolve(o.owner)
                except HandlerMissing as e:
                    errs.append(f"{o.name}: enabled 인데 처리기를 부를 수 없다 — {e}")
        return errs

    def _check_depends(self) -> list[str]:
        errs: list[str] = []
        jobs = {j.name: j for j in self.jobs}
        for j in self.jobs:
            for d in j.depends_on:
                dep = jobs.get(d.job)
                if dep is None:
                    errs.append(f"{j.name}: depends_on 대상이 없다: {d.job}")
                    continue
                if d.as_of == "prev" and dep.run_as_of() not in DATE_KINDS:
                    errs.append(f"{j.name}: depends_on {d.job} as_of prev 는 날짜 as_of 작업만")
                if d.hard and d.as_of == "same" and dep.run_as_of() != j.run_as_of():
                    errs.append(
                        f"{j.name}: 굳은 의존 {d.job} 의 as_of 종류가 다르다"
                        f"({dep.run_as_of()} ≠ {j.run_as_of()})"
                    )
                if d.wait_min is not None:
                    errs += _check_soft_wait(j, dep, d.wait_min)
            for b in j.backfill_of:
                if b not in jobs:
                    errs.append(f"{j.name}: backfill_of 대상이 없다: {b}")
            for t in j.schedule.triggered_by:
                if t not in jobs:
                    errs.append(f"{j.name}: triggered_by 대상이 없다: {t}")
        # 순환
        graph = {j.name: [d.job for d in j.depends_on if d.job in jobs] for j in self.jobs}
        state: dict[str, int] = {}

        def visit(n: str, path: list[str]) -> list[str] | None:
            state[n] = 1
            for m in graph[n]:
                if state.get(m) == 1:
                    return [*path, n, m]
                if m not in state and (cyc := visit(m, [*path, n])) is not None:
                    return cyc
            state[n] = 2
            return None

        for n in graph:
            if n not in state and (cyc := visit(n, [])) is not None:
                errs.append("depends_on 이 순환한다: " + " → ".join(cyc))
                break
        return errs

    def _check_collects(self, catalog: Mapping[str, DatasetSpec]) -> list[str]:
        errs: list[str] = []
        for ds, owners in sorted(self.owners_of().items()):
            if len(owners) > 1:
                errs.append(f"같은 데이터셋 {ds} 를 여러 곳이 받는다: {', '.join(owners)}")
        for o in (*self.services, *self.jobs):
            for c in o.collects:
                spec = catalog.get(c.dataset_id)
                if spec is None:
                    errs.append(f"{o.name}: 카탈로그에 없는 데이터셋 {c.dataset_id}")
                    continue
                if c.as_of != spec.as_of_kind:
                    errs.append(
                        f"{o.name}: {c.dataset_id} as_of {c.as_of} ≠ 카탈로그 {spec.as_of_kind}"
                    )
                bad = [v.value for v in c.venues if v not in spec.venues]
                if bad:
                    errs.append(f"{o.name}: {c.dataset_id} 에 없는 거래소 구분 {bad}")
                if not claimable(spec):
                    errs.append(
                        f"{o.name}: {c.dataset_id} 는 즉석 조회라 등록부 수집 대상이 아니다"
                    )
        return errs

    def _check_writes(
        self, catalog: Mapping[str, DatasetSpec], tables: frozenset[str] | None
    ) -> list[str]:
        errs: list[str] = []
        for j in self.jobs:
            for w in j.writes:
                if not _TABLE.fullmatch(w):
                    errs.append(f"{j.name}: writes 형식은 '<pub_|prv_|ops>.<표>': {w!r}")
            specs = [catalog[c.dataset_id] for c in j.collects if c.dataset_id in catalog]
            private = [s.id for s in specs if s.tier is not Tier.PUBLIC]
            pub_writes = [w for w in j.writes if w.startswith("pub_")]
            if pub_writes and private:
                errs.append(
                    f"{j.name}: 로그인 등급 출처({', '.join(private)})를 받는 작업이 공개 표 "
                    f"{', '.join(pub_writes)} 에 쓴다(ADR 0002)"
                )
            for s in specs:
                if s.store and s.store not in j.writes:
                    errs.append(f"{j.name}: {s.id} 의 저장 표 {s.store} 가 writes 에 없다")
            if j.enabled and tables is not None:
                for w in j.writes:
                    if w not in tables:
                        errs.append(f"{j.name}: enabled 인데 표 {w} 를 만드는 마이그레이션이 없다")
        return errs

    def _check_schedule(self) -> list[str]:
        errs: list[str] = []
        for j in self.jobs:
            first = j.schedule.first_time()
            if (
                j.retry.until is not None
                and first is not None
                and hhmm_time(j.retry.until) <= first
            ):
                errs.append(
                    f"{j.name}: retry.until {j.retry.until} 이 첫 실행 {first:%H:%M} "
                    "보다 늦어야 한다"
                )
            cu = j.schedule.catch_up_until
            if cu is not None and first is not None and hhmm_time(cu) <= first:
                errs.append(
                    f"{j.name}: catch_up_until {cu} 이 첫 실행 {first:%H:%M} 보다 늦어야 한다"
                )
            if j.schedule.manual and j.enabled and not j.backfill_of and not j.collects:
                errs.append(f"{j.name}: 수동 작업은 할 일(collects·backfill_of)이 있어야 한다")
        return errs

    def _check_notify(self, policies: Mapping[str, str]) -> list[str]:
        errs: list[str] = []
        for j in self.jobs:
            if j.notify is not None and j.notify.kind not in policies:
                errs.append(f"{j.name}: notify.yaml 에 없는 알림 종류 {j.notify.kind}")
        for kind in U2_KINDS:  # U2 — 아침 브리핑 1회·마감 요약 1회
            senders = [j.name for j in self.jobs if j.notify is not None and j.notify.kind == kind]
            if len(senders) != 1:
                errs.append(f"U2: {kind} 를 보내는 작업은 정확히 1개여야 한다: {senders}")
            if policies.get(kind) != DAILY_DEDUP:
                errs.append(f"U2: {kind} 의 정책이 하루 1회(dedup: daily)가 아니다")
        return errs

    def daily_calls(self, catalog: Mapping[str, DatasetSpec]) -> dict[str, int]:
        """예산 이름(데이터셋별 예산이면 `<예산>:<데이터셋>`) → 하루 최대 호출 추정.

        데이터 키 수(거래소 구분 포함) × 하루 실행 수 × 시도 수(재시도 포함). 이벤트·수동은 0.
        페이지가 여러 쪽인 응답은 더 들 수 있다 — 하한 추정이다.
        """
        out: Counter[str] = Counter()
        for j in self.jobs:
            fires = j.schedule.fires_per_day()
            first = j.schedule.first_time() or time(0, 0)
            tries = j.retry.attempts_from(first)
            for c in j.collects:
                spec = catalog.get(c.dataset_id)
                if spec is None or spec.budget is None or c.as_of == "event":
                    continue
                keys = len(c.venues or spec.venues) or 1
                out[f"{spec.budget}:{spec.id}"] += keys * fires * tries
                out[spec.budget] += keys * fires * tries
        return dict(out)

    def _check_budgets(
        self, catalog: Mapping[str, DatasetSpec], caps: Mapping[str, BudgetCap]
    ) -> list[str]:
        errs: list[str] = []
        for j in self.jobs:
            needed = {
                b
                for c in j.collects
                if c.dataset_id in catalog and (b := catalog[c.dataset_id].budget) is not None
            }
            if j.budget is not None and j.budget not in caps:
                errs.append(f"{j.name}: limits.yaml 에 없는 예산 {j.budget}")
            for b in sorted(needed):
                if j.budget != b:
                    errs.append(f"{j.name}: 데이터셋 예산 {b} 를 쓰는데 budget 이 {j.budget}")
        calls = self.daily_calls(catalog)
        for name, (total, per_dataset) in sorted(caps.items()):
            if total is not None and calls.get(name, 0) > total:
                errs.append(f"예산 {name}: 하루 추정 {calls[name]} > 상한 {total}")
            if per_dataset is not None:
                for k, v in sorted(calls.items()):
                    if k.startswith(name + ":") and v > per_dataset:
                        errs.append(f"예산 {k}: 하루 추정 {v} > 데이터셋별 상한 {per_dataset}")
        return errs

    # ── inventory (c) 대응(§6.6-8) ─────────────────────────────────────────────────────

    def legacy_mentions(self) -> Counter[str]:
        c: Counter[str] = Counter()
        for o in (*self.services, *self.jobs):
            c.update(o.absorbs)
        c.update(r.legacy for r in self.retired)
        return c

    def legacy_coverage(self, legacy_jobs: Sequence[str]) -> list[str]:
        """legacy 정기 작업 목록의 줄마다 absorbs·retired 에 정확히 한 번 나오는지."""
        seen = self.legacy_mentions()
        errs: list[str] = []
        dup = sorted(n for n, k in Counter(legacy_jobs).items() if k > 1)
        errs += [f"legacy 목록에 같은 줄이 두 번: {n}" for n in dup]
        for line in dict.fromkeys(legacy_jobs):
            n = seen.get(line, 0)
            if n != 1:
                errs.append(f"legacy 작업 {line!r} 가 absorbs·retired 에 {n}번 나온다(정확히 1번)")
        return errs


def _check_soft_wait(j: JobSpec, dep: JobSpec, wait_min: int) -> list[str]:
    """무른 의존 `wait_min` 이 실행기에서 뜻대로 도는지(DependsSpec 설명 — ADR 0018).

    실행기는 진행 중인 실행만 기다린다. 그래서 의존 작업은 kbj 가 돌려야 하고(external 은 진행
    기록이 없다), 이 작업보다 늦게 발화하면 안 되며(아직 발화하지 않은 실행은 기다리지 않는다),
    기다림이 이 작업의 마감보다 짧아야 한다.
    """
    errs: list[str] = []
    where = f"{j.name}: 무른 의존 {dep.name} wait_min"
    if dep.external:
        errs.append(
            f"{where} — runner external 작업은 kbj 실행기에 진행 기록이 없다(기다릴 수 없다)"
        )
    if wait_min >= j.deadline_min:
        errs.append(f"{where} {wait_min}분이 마감 {j.deadline_min}분보다 짧아야 한다")
    dep_first = dep.schedule.first_time()
    own_first = j.schedule.first_time()
    if dep_first is None:
        errs.append(f"{where} — 발화 시각이 정해지지 않은 작업(수동·상태 진입)은 기다릴 수 없다")
    elif own_first is not None and dep.schedule.tz == j.schedule.tz and dep_first > own_first:
        errs.append(
            f"{where} — 의존이 {dep_first:%H:%M} 에 발화해 이 작업({own_first:%H:%M})보다 늦다"
            "(아직 발화하지 않은 실행은 기다리지 않는다)"
        )
    return errs


def read_legacy_jobs(path: Path) -> list[str]:
    """`legacy_jobs.txt` — 빈 줄과 `#` 로 시작하는 주석 줄을 뺀 줄 목록(줄 안 주석은 없다)."""
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def budgets_from_limits(limits: Limits) -> dict[str, BudgetCap]:
    """`kbj.data.limits.Limits` → 예산 상한 표. 일 예산이 있는 출처만."""
    out: dict[str, BudgetCap] = {}
    for name in REQUIRED_SOURCES:
        src = limits.source(name)
        if src.daily_cap is not None or src.daily_cap_per_dataset is not None:
            out[name] = (src.daily_cap, src.daily_cap_per_dataset)
    return out

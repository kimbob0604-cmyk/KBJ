"""5필드 cron 식 — 작업 등록부(`config/jobs.yaml`)의 시각 규칙 (docs/p2_design.md §1.7·§6.2).

외부 라이브러리 대신 쓰는 작은 부분집합이다 [제안]. 순수 계산(시계·I/O 없음, 계약 ③).

- 필드: 분(0~59) 시(0~23) 일(1~31) 월(1~12) 요일(0~7, 0·7 = 일요일). 이름(`MON`·`JAN`)·`L`·`W`·`#`·
  `?` 는 받지 않는다(받으면 `ValueError` — 조용히 다르게 해석하지 않는다).
- 항목: `*`, `a`, `a-b`, `*/n`, `a-b/n`, `a/n`(a 부터 끝까지 n 간격), 쉼표 목록.
- 일·요일: 둘 다 제한이면 **둘 중 하나**가 맞으면 된다(Vixie cron 관례). 하나라도 `*` 면 다른
  하나만 본다.
- 시각은 **그 지역 벽시계**로 본다. aware 시각을 주면 그 tzinfo 의 벽시계 필드로 비교하고, 돌려주는
  시각에도 같은 tzinfo 를 붙인다. 서머타임으로 없는 시각(봄 01:59 → 03:00 사이)은 등록부에 두지
  않는다 — 실행기(`kbj.services.scheduler.runner`)가 UTC 로 바꿀 때 한 번 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

__all__ = ["CronSpec"]

# (이름, 하한, 상한)
_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("분", 0, 59),
    ("시", 0, 23),
    ("일", 1, 31),
    ("월", 1, 12),
    ("요일", 0, 7),
)
_MAX_DAYS = 366 * 8  # next_after 가 찾는 범위 — 윤년 2월 29일(최대 8년 간격)까지


def _int(token: str, expr: str) -> int:
    if not token.isdigit():
        raise ValueError(f"cron 숫자가 아니다: {token!r} ({expr!r})")
    return int(token)


def _parse_field(text: str, name: str, lo: int, hi: int, expr: str) -> frozenset[int]:
    if not text:
        raise ValueError(f"cron {name} 필드가 비었다: {expr!r}")
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        base = part
        if "/" in part:
            base, _, step_s = part.partition("/")
            step = _int(step_s, expr)
            if step < 1:
                raise ValueError(f"cron 간격은 1 이상: {part!r} ({expr!r})")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a_s, _, b_s = base.partition("-")
            a, b = _int(a_s, expr), _int(b_s, expr)
            if a > b:
                raise ValueError(f"cron 범위가 거꾸로다: {part!r} ({expr!r})")
        else:
            a = _int(base, expr)
            b = hi if "/" in part else a
        if a < lo or b > hi:
            raise ValueError(f"cron {name} 값은 {lo}~{hi}: {part!r} ({expr!r})")
        out.update(range(a, b + 1, step))
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class CronSpec:
    """파싱한 cron 식. `CronSpec.parse("5 8 * * 1-5")`."""

    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = 일요일(7 은 0 으로 접는다)
    days_any: bool  # 일 필드가 '*'
    weekdays_any: bool  # 요일 필드가 '*'

    @classmethod
    def parse(cls, expr: str) -> CronSpec:
        parts = expr.split()
        if len(parts) != len(_FIELDS):
            raise ValueError(f"cron 은 5필드(분 시 일 월 요일)여야 한다: {expr!r}")
        sets = [
            _parse_field(p, name, lo, hi, expr)
            for p, (name, lo, hi) in zip(parts, _FIELDS, strict=True)
        ]
        weekdays = frozenset(0 if d == 7 else d for d in sets[4])
        spec = cls(
            expr=" ".join(parts),
            minutes=sets[0],
            hours=sets[1],
            days=sets[2],
            months=sets[3],
            weekdays=weekdays,
            days_any=parts[2] == "*",
            weekdays_any=parts[4] == "*",
        )
        if not spec._possible():
            raise ValueError(f"cron 이 맞는 날이 없다: {expr!r}")
        return spec

    def _possible(self) -> bool:
        # 일 필드만 제한일 때 월마다 최대 일수 안에 하나라도 있어야 한다(예: '0 0 31 2 *' 는 없다)
        if not self.weekdays_any:
            return True
        longest = {
            1: 31,
            2: 29,
            3: 31,
            4: 30,
            5: 31,
            6: 30,
            7: 31,
            8: 31,
            9: 30,
            10: 31,
            11: 30,
            12: 31,
        }
        return any(d <= longest[m] for m in self.months for d in self.days)

    def day_matches(self, d: date) -> bool:
        """그 날짜가 일·월·요일 조건에 맞는가(시·분은 보지 않는다)."""
        if d.month not in self.months:
            return False
        dom = d.day in self.days
        dow = (d.weekday() + 1) % 7 in self.weekdays
        if self.days_any and self.weekdays_any:
            return True
        if self.days_any:
            return dow
        if self.weekdays_any:
            return dom
        return dom or dow

    def matches(self, local: datetime) -> bool:
        """그 분(초는 무시)이 맞는가. `local` 은 그 지역 벽시계(aware 면 그 tzinfo 의 필드)."""
        return (
            local.minute in self.minutes
            and local.hour in self.hours
            and self.day_matches(local.date())
        )

    def times_on(self, d: date) -> list[time]:
        """그 날 맞는 벽시계 시각들(오름차순). 날짜가 맞지 않으면 빈 목록."""
        if not self.day_matches(d):
            return []
        return [time(h, m) for h in sorted(self.hours) for m in sorted(self.minutes)]

    def next_after(self, local: datetime) -> datetime:
        """`local` 보다 **뒤**(같은 분 제외)의 첫 맞는 분. tzinfo 는 `local` 것을 그대로 붙인다."""
        tz = local.tzinfo
        start = local.replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
        d = start.date()
        first = start.time()
        for _ in range(_MAX_DAYS):
            for t in self.times_on(d):
                if d != start.date() or t >= first:
                    return datetime.combine(d, t).replace(tzinfo=tz)
            d += timedelta(days=1)
        raise ValueError(f"cron 이 {_MAX_DAYS}일 안에 맞는 시각이 없다: {self.expr!r}")

    def prev_at_or_before(self, local: datetime) -> datetime | None:
        """`local` 과 같은 분이거나 그 앞의 마지막 맞는 분 — **같은 날짜 안에서만**. 없으면 None.

        캐치업(놓친 하루 1회 실행)을 찾을 때 쓴다.
        """
        tz = local.tzinfo
        cur = local.replace(second=0, microsecond=0, tzinfo=None)
        best: datetime | None = None
        for t in self.times_on(cur.date()):
            if t <= cur.time():
                best = datetime.combine(cur.date(), t)
        return None if best is None else best.replace(tzinfo=tz)

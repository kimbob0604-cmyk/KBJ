"""가짜 시계 — GEXLAB `tests/fakes/kis_server.py:FakeClock` 승격(설계 §1.1, §10.1).

레이트리미터 `Clock`(now_us·sleep)과 aware datetime(`now`)을 한 시각으로 낸다. `sleep` 은 시각만
옮긴다(실제로 자지 않는다). 하루 시뮬레이션에서는 리미터·auth·스케줄러·notifier·가짜 서버가 모두 이
시계 하나를 본다.

- `now()` 는 UTC aware datetime. KST 가 필요하면 부르는 쪽이 `astimezone` 한다
- `monotonic()` 은 초 단위 float — `kbj.data.http.get_capped(clock=...)` 같은 경과 시간용
- naive datetime 은 받지 않는다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

US = 1_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FakeClock:
    """레이트리미터 `Clock`(now_us·sleep) + aware datetime. sleep 은 시각만 옮긴다."""

    def __init__(self, start: datetime) -> None:
        self.t = 0
        self.slept: list[float] = []
        self.set(start)

    def set(self, when: datetime) -> None:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        self.t = (when - EPOCH) // timedelta(microseconds=1)

    def now_us(self) -> int:
        return self.t

    def sleep(self, seconds: float, /) -> None:
        if seconds > 0:
            self.slept.append(seconds)
            self.t += round(seconds * US)

    def advance(self, seconds: float) -> None:
        """시각을 앞으로(sleep 과 같지만 `slept` 에 남기지 않는다). 뒤로 돌릴 수 없다."""
        if seconds < 0:
            raise ValueError("시계는 되돌지 않는다")
        self.t += round(seconds * US)

    def now(self) -> datetime:
        return EPOCH + timedelta(microseconds=self.t)

    def monotonic(self) -> float:
        return self.t / US

    def __call__(self) -> datetime:
        """`now: Callable[[], datetime]` 자리에 그대로 넘길 수 있게."""
        return self.now()

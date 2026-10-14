"""웹소켓 재연결 지수 백오프 (PLAN §6.5: 1초 → 60초)."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Backoff:
    """연결이 끊길 때마다 `next_delay()` 만큼 쉬고, 연결되면 `reset()`.

    jitter 는 지연의 ±비율(0 이면 없음). 여러 프로세스가 같은 순간 재접속하지 않게 쓸 수 있다.
    """

    initial: float = 1.0
    factor: float = 2.0
    maximum: float = 60.0
    jitter: float = 0.0
    rand: Callable[[], float] = field(default=random.random, repr=False)  # 테스트 주입용
    attempt: int = 0

    def __post_init__(self) -> None:
        if self.initial <= 0 or self.factor < 1 or self.maximum < self.initial:
            raise ValueError("initial > 0, factor >= 1, maximum >= initial 이어야 한다")
        if not 0 <= self.jitter < 1:
            raise ValueError("jitter 는 0 이상 1 미만")

    def next_delay(self) -> float:
        base = min(self.maximum, self.initial * self.factor**self.attempt)
        self.attempt += 1
        if self.jitter:
            base *= 1 + self.jitter * (2 * self.rand() - 1)
        return min(self.maximum, base)

    def reset(self) -> None:
        self.attempt = 0

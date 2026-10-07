"""재시도 간격 — GEXLAB `services/runtime.py:Backoff` 그대로."""

from __future__ import annotations


class Backoff:
    """재시도 간격(1초 → 최대, 두 배씩). 성공하면 `reset`."""

    def __init__(self, initial: float = 1.0, maximum: float = 30.0) -> None:
        if not 0 < initial <= maximum:
            raise ValueError("0 < initial <= maximum")
        self.initial = initial
        self.maximum = maximum
        self._next = initial

    def next(self) -> float:
        d = self._next
        self._next = min(self._next * 2, self.maximum)
        return d

    def reset(self) -> None:
        self._next = self.initial

"""pytest 공통 — 컨테이너 시험 마크 별칭(GEXLAB tests/conftest.py 방식, pyproject `markers`).

- `integration`: compose·컨테이너 스택(TimescaleDB·Redis)이 필요한 시험. Docker 가 없으면 그 시험의
  conftest 가 건너뛴다(묶음 G·I)
- `docker`: 옛 이름 — `integration` 의 별칭으로만 남긴다. 둘 중 하나가 붙은 시험에는 다른 하나도
  붙인다(`-m` 거르기 전에). 그래서 `-m "not docker"`·`-m "not integration"` 어느 쪽으로 걸러도
  컨테이너 시험이 빠진다 — 단위 실행은 컨테이너 없이 돈다
- `network`(외부 API 실호출)·`sim`(하루 운영 시뮬레이션)은 별칭 없이 그대로
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import pytest

CONTAINER_MARKS: tuple[str, ...] = ("integration", "docker")


class _Markable(Protocol):
    def get_closest_marker(self, name: str) -> object | None: ...

    def add_marker(self, marker: str) -> None: ...


def alias_container_marks(items: Sequence[_Markable]) -> None:
    """`integration`·`docker` 중 하나라도 붙은 시험에 둘 다 붙인다."""
    for item in items:
        if any(item.get_closest_marker(m) is not None for m in CONTAINER_MARKS):
            for m in CONTAINER_MARKS:
                if item.get_closest_marker(m) is None:
                    item.add_marker(m)


@pytest.hookimpl(tryfirst=True)  # `-m` 거르기(_pytest.mark)보다 먼저
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    alias_container_marks(items)

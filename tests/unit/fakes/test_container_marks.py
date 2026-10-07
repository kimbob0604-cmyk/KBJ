"""tests/conftest.py 의 컨테이너 마크 별칭 — `integration`·`docker` 중 하나가 붙으면 둘 다 붙는다.

그래야 `-m "not integration"`·`-m "not docker"` 어느 쪽으로 걸러도 컨테이너 시험이 빠지고, 단위
실행(CI kbj 잡)이 Docker 없이 돈다. `network`·`sim` 은 별칭이 없다.
"""

from __future__ import annotations

from tests.conftest import CONTAINER_MARKS, alias_container_marks


class _Item:
    def __init__(self, *marks: str) -> None:
        self.marks = list(marks)

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.marks else None

    def add_marker(self, marker: str) -> None:
        self.marks.append(marker)


def test_integration_and_docker_are_aliases() -> None:
    assert CONTAINER_MARKS == ("integration", "docker")
    new, old, both = _Item("integration"), _Item("docker"), _Item("docker", "integration")
    alias_container_marks([new, old, both])
    for item in (new, old, both):
        assert sorted(item.marks) == ["docker", "integration"]


def test_other_marks_are_left_alone() -> None:
    net, sim, plain = _Item("network"), _Item("sim"), _Item()
    alias_container_marks([net, sim, plain])
    assert (net.marks, sim.marks, plain.marks) == (["network"], ["sim"], [])

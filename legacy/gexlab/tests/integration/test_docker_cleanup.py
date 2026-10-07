"""시험 컨테이너 뒷정리 — 지울 때 이미지가 선언한 익명 볼륨까지 지운다.

timescale 이미지는 `VOLUME /var/lib/postgresql/data` 를 선언해 컨테이너마다 익명 볼륨이 생긴다.
`docker rm -f` 만으로는(`--rm` 으로 띄웠어도 직접 지우면) 볼륨이 남아 실행마다 쌓인다. 이 시험은
컨테이너를 만들기만 하고(`docker create` — 기동하지 않는다) `remove_container` 로 지운 뒤 볼륨이
사라졌는지 본다. 남았으면 이 시험이 만든 볼륨만 치우고 실패한다.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import (
    IMAGE,
    _docker,
    _docker_ready,
    anonymous_volumes,
    remove_container,
)

pytestmark = pytest.mark.integration


def test_removing_a_test_container_also_removes_its_anonymous_volumes() -> None:
    reason = _docker_ready()
    if reason is not None:
        pytest.skip(f"Docker 없음 — {reason}")
    name = f"gexlab-test-vol-{uuid.uuid4().hex[:12]}"
    created = _docker("create", "--name", name, IMAGE)
    if created.returncode != 0:
        pytest.skip(f"컨테이너를 만들지 못했다: {created.stderr.strip()[:200]}")
    volumes: list[str] = []
    try:
        volumes = anonymous_volumes(name)
        assert volumes, "이미지가 선언한 익명 볼륨이 보여야 한다"
    finally:
        remove_container(name)
    left = [v for v in volumes if _docker("volume", "inspect", v).returncode == 0]
    for v in left:  # 이 시험이 만든 볼륨만 치운다
        _docker("volume", "rm", "-f", v)
    assert left == [], "컨테이너를 지운 뒤에도 익명 볼륨이 남았다"

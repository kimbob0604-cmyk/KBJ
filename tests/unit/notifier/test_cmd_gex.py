"""`/gex` 봇 명령(P7 OUT — docs/p7_design.md §1.6): 최신 GEX 레벨 요약 한 덩어리.

읽기 함수를 넘기면 동작하고(저장소만 읽는다), 넘기지 않으면 '준비 중(P7)'. 명령은 허용 대화(로그인
등급 채팅)에서만 받는다 — 그 거름은 InboundRouter(test_webhook·test_service 가 본다). 합성 입력.
"""

from __future__ import annotations

from datetime import date, timedelta

from kbj.reports.gex.lines import command_reader
from kbj.services.notifier.commands import default_registry
from kbj.store.repos.gex import MemoryGexRepo
from tests.unit.reports.gex.gex_world import at, levels

T = at(date(2026, 12, 10), 14, 0)


def test_읽기_함수가_있으면_요약_한_덩어리() -> None:
    repo = MemoryGexRepo()
    repo.write_levels(levels(T - timedelta(seconds=30), f=351.2, flip=350.0))
    reg = default_registry(gex=command_reader(repo, lambda: T))
    r = reg.dispatch("/gex", chat_id=1)
    assert r is not None and r.parse_mode == "HTML"
    assert r.text.startswith("📐 <b>GEX(all)</b> · 12/10 13:59 KST 주간 사이클")
    assert "F 351.20 · Flip 350.00(Flip 위 +0.34%)" in r.text
    assert "콜월 360.00 · 풋월 340.00" in r.text
    up = reg.dispatch("/GEX@kbj_bot", chat_id=1)  # 대소문자·봇 접미사
    assert up is not None and up.text == r.text
    assert "준비 중" not in reg.help_text().split("/gex")[1].split("\n")[0]


def test_산출이_없으면_그렇다고만() -> None:
    reg = default_registry(gex=command_reader(MemoryGexRepo(), lambda: T))
    r = reg.dispatch("/gex", chat_id=1)
    assert r is not None and "산출 없음" in r.text


def test_섀도_값은_싣지_않는다() -> None:
    repo = MemoryGexRepo()
    names = ("flip", "flip_distance", "call_wall", "put_wall", "expected_move")
    repo.write_levels(levels(T, flags=dict.fromkeys(names, "shadow")))
    r = default_registry(gex=command_reader(repo, lambda: T)).dispatch("/gex", chat_id=1)
    assert r is not None and "섀도 검증 중" in r.text and "351.20" not in r.text


def test_읽기_실패는_삼키지_않고_오류_답() -> None:
    def boom() -> str:
        raise RuntimeError("db down — secret=abc")

    r = default_registry(gex=boom).dispatch("/gex", chat_id=1)
    assert r is not None and "처리 중 오류: RuntimeError" in r.text and "secret" not in r.text


def test_읽기_함수가_없으면_준비_중() -> None:
    r = default_registry().dispatch("/gex", chat_id=1)
    assert r is not None and "준비 중" in r.text and "P7" in r.text

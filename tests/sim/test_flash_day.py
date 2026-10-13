"""수출 속보일 시뮬레이션(docs/p6_design.md §0.3 ①~⑥ — WRAP): 가짜 시계·가짜 관세청(합성)·운영
등록부의 `trade.customs_tenday`(이 사본에서만 켬)·실제 수집 처리기. 공표 → 20분 폴링이 받음 →
저장소·로그인 API(`/api/trade/flash`·띠 칩)·공개 파일(`public.export` 11:30 회차)·16:00 마감
요약 한 줄.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from kbj.core.cron import CronSpec
from kbj.core.time import KST
from tests.sim.p6 import JOB, ROOT, FlashDay

pytestmark = pytest.mark.sim

KINDS = ("exp_item", "exp_country", "imp_item", "imp_country")


def kst(m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(2026, m, d, h, mi, tzinfo=KST)


def _poll_times(day: FlashDay) -> list[str]:
    return [f"{t:%m-%d %H:%M}" for t in day.calls]


def test_공표일_11시_폴링이_받아_API_띠_공개파일_마감요약까지() -> None:
    """① 11-11(수) 10:47 공표 → 11:00 폴링이 받는다. ⑥ 같은 구간 재폴링 0(선점 done)."""
    day = FlashDay(kst(11, 11, 8, 50)).run_until(kst(11, 11, 17, 59))
    # 09:00~10:40 은 공표 전(not_ready), 11:00 에 받고 그 뒤로는 처리기를 부르지 않는다
    polls = ("09:00", "09:20", "09:40", "10:00", "10:20", "10:40", "11:00")
    assert _poll_times(day) == [f"11-11 {h}" for h in polls]
    assert [r.status for _, r in day.results] == ["not_ready"] * 6 + ["ok"]
    assert [r.status for r in day.records("202611-1") if r.status == "ok"] == ["ok"]
    for kind in KINDS:
        rows = day.segment_rows(kind, "2026-11", 1)
        assert rows and {r.first_seen_at for r in rows} == {kst(11, 11, 11, 0)}, kind
    # 로그인 API — 최신 구간이 맨 앞, 띠 칩은 그 구간
    flash = day.api(kst(11, 11, 11, 5), "/api/trade/flash?kind=exp_item&months=2")["data"]
    assert (flash["segments"][0]["ym"], flash["segments"][0]["seg"]) == ("2026-11", 1)
    ribbon = day.api(kst(11, 11, 11, 5), "/api/market/ribbon")["data"]
    chip = next(c for c in ribbon["chips"] if c["key"] == "export_flash")
    assert chip["value"] is not None and chip["detail"]["ym"] == "2026-11"
    # 공개 내보내기: public.export 는 11:30 에 돈다(D-P6-7) — 그 회차가 새 구간을 싣는다
    export_cron = "30 5,11,14,17 * * *"
    assert f'cron: "{export_cron}"' in (ROOT / "config" / "jobs.yaml").read_text("utf-8")
    assert CronSpec.parse(export_cron).matches(kst(11, 11, 11, 30))
    out = day.public(kst(11, 11, 11, 30))
    names = {f.name: f for f in out.files}
    assert {"trade/flash.json", "trade/ribbon.json"} <= set(names)
    exp_item = next(k for k in names["trade/flash.json"].data["kinds"] if k["kind"] == "exp_item")
    assert (exp_item["segments"][0]["ym"], exp_item["segments"][0]["seg"]) == ("2026-11", 1)
    assert names["trade/ribbon.json"].data["chips"][0]["detail"]["ym"] == "2026-11"
    # 16:00 마감 요약 한 줄(새 발송 종류 없음 — D-P6-8)
    line = day.closing_line(kst(11, 11, 16, 0))
    assert line is not None and line.startswith("수출 속보 11월 1~10일")
    assert "발표 당시" in line


def test_토요일_공표는_그날_받고_월요일_16시_마감요약에() -> None:
    """② 11-21(토) 공표 → 그날 11:00 에 받고, 마감 요약은 11-23(월) 16:00."""
    day = FlashDay(kst(11, 21, 8, 50)).run_until(kst(11, 23, 17, 0))
    ok = [t for t, r in day.results if r.status == "ok"]
    assert ok == [kst(11, 21, 11, 0)]
    assert {r.first_seen_at for r in day.segment_rows("exp_item", "2026-11", 2)} == {
        kst(11, 21, 11, 0)
    }
    # 22·23일은 같은 데이터 키(202611-2)가 done — 처리기를 부르지 않는다
    assert all(t.day == 21 for t in day.calls)
    line = day.closing_line(kst(11, 23, 16, 0))
    assert line is not None and line.startswith("수출 속보 11월 1~20일")
    assert day.closing_line(kst(11, 24, 16, 0)) is None  # 다음 거래일에는 다시 싣지 않는다


def test_월초_공표는_전달_말일까지_구간() -> None:
    """③ 12-01(화) 11월 전체(01~말일) — 데이터 키 202611-3."""
    day = FlashDay(kst(12, 1, 8, 50)).run_until(kst(12, 1, 12, 0))
    assert [t for t, r in day.results if r.status == "ok"] == [kst(12, 1, 11, 0)]
    assert [r.status for r in day.records("202611-3") if r.status == "ok"] == ["ok"]
    rows = day.segment_rows("exp_item", "2026-11", 3)
    assert rows and {r.span_text for r in rows} == {"01~30"}
    flash = day.api(kst(12, 1, 11, 5), "/api/trade/flash?kind=exp_item&months=2")["data"]
    assert (flash["segments"][0]["ym"], flash["segments"][0]["seg"]) == ("2026-11", 3)
    line = day.closing_line(kst(12, 1, 16, 0))
    assert line is not None and "11월 1~30일" in line


def test_공표가_하루_늦으면_다음날_받는다() -> None:
    """④ 공표가 11-12 09:20 으로 늦음 → 11일은 내내 not_ready, 12일 09:20 에 받는다."""
    day = FlashDay(kst(11, 11, 8, 50))
    day.world.delay_ten("2026-11", 1, kst(11, 12, 9, 20))
    day.run_until(kst(11, 12, 12, 0))
    first = [r for t, r in day.results if t.day == 11]
    assert len(first) == 27 and all(r.status == "not_ready" for r in first)  # 09:00~17:40
    assert [t for t, r in day.results if r.status == "ok"] == [kst(11, 12, 9, 20)]
    assert {r.first_seen_at for r in day.segment_rows("exp_item", "2026-11", 1)} == {
        kst(11, 12, 9, 20)
    }


def test_열_구성이_바뀌면_그_종류만_실패하고_옛_값_유지() -> None:
    """⑤ 수출 국가별 10일 응답에 열이 하나 늘었다 → 그 종류만 실패(삼키지 않는다), 나머지 셋은
    받고, 그 종류의 저장 값은 그대로(새 구간 없음)."""
    day = FlashDay(kst(11, 11, 10, 41))  # 10:47 공표 전 — 다음 폴링 11:00
    before = len(day.repos.trade.ten_day(kind="exp_country"))
    day.world.change_columns("exp_country")
    day.run_until(kst(11, 11, 11, 30))
    assert [t for t, _ in day.results] == [kst(11, 11, 11, 0), kst(11, 11, 11, 20)]
    for _, res in day.results:
        assert res.status == "failed"
        assert "15157941" in str(res.detail) and "TenDayColumnsChanged" in str(res.detail)
    for kind in ("exp_item", "imp_item", "imp_country"):
        assert day.segment_rows(kind, "2026-11", 1), kind
    assert day.segment_rows("exp_country", "2026-11", 1) == []
    assert len(day.repos.trade.ten_day(kind="exp_country")) == before
    assert day.segment_rows("exp_item", "2026-11", 1)
    # 실패는 실행 기록에 남는다(health 의 실패 경고 — 운영은 ops.watchdog 가 본다)
    assert any(r.status == "failed" for r in day.records("202611-1"))


def test_시뮬레이션_등록부는_운영_한_줄() -> None:
    """이 창은 운영 등록부의 같은 줄(cron·month_days·as_of)을 쓴다 — 켠 것은 이 사본뿐."""
    from kbj.services.scheduler.registry import Registry

    prod = Registry.load(ROOT / "config" / "jobs.yaml").by_name(JOB)
    assert not prod.enabled  # 운영은 키 수령·실측 뒤
    assert prod.run_as_of() == "ten_day"
